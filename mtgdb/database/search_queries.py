"""Canonical SQL construction and execution for interactive card search."""

from __future__ import annotations

import math
import re

from mtgdb.database.constants import (
    ART_LAYOUTS, COLORS, CONTENT_TYPES, PLAYABLE_LEGALITY_STATUSES,
)
from mtgdb.database.schema import _CARD_COLUMN_NAMES
from mtgdb.database.semantics import _escape_like, _normalize_rules_text


class SearchQueryBuilder:
    """Build one parameterized card search without owning a database connection."""

    def __init__(self):
        self.clauses = []
        self.params = []
        self.chosen_sets = None

    def add_art_filter(self, exclude_art):
        excluded = list(ART_LAYOUTS) if exclude_art else []
        if excluded:
            placeholders = ",".join("?" * len(excluded))
            self.clauses.append(
                f"(layout IS NULL OR layout NOT IN ({placeholders}))")
            self.params.extend(excluded)

    def add_content_filter(self, content_types, show_tokens):
        if content_types is None:
            content_types = (
                {"card", "token", "emblem"} if show_tokens else {"card"})
        else:
            content_types = {str(value).casefold() for value in content_types}
        if not content_types:
            return False
        allowed_content = set(CONTENT_TYPES)
        unknown_content = sorted(content_types - allowed_content)
        if unknown_content:
            raise ValueError(
                "Unknown card-content type(s): " + ", ".join(unknown_content))
        placeholders = ",".join("?" * len(content_types))
        self.clauses.append(
            f"CARD_CONTENT_KIND(layout, type_line) IN ({placeholders})")
        self.params.extend(sorted(content_types))
        return True

    def add_name_and_rules(self, name, names, text, text_mode):
        exact_names = []
        seen = set()
        for value in names or ():
            value = str(value or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                exact_names.append(value)
        if exact_names:
            placeholders = ",".join("?" * len(exact_names))
            self.clauses.append(
                f"name COLLATE NOCASE IN ({placeholders})")
            self.params.extend(exact_names)
        elif name:
            self.clauses.append("name LIKE ? ESCAPE '\\'")
            self.params.append(f"%{_escape_like(name)}%")
        if not text:
            return
        raw_terms = (
            [text] if isinstance(text, str)
            else [str(term).strip() for term in text if str(term).strip()]
        )
        groups = []
        group_params = []
        for raw_term in raw_terms:
            raw_term = str(raw_term or "").strip()
            if not raw_term:
                continue
            quoted = (
                len(raw_term) >= 2
                and raw_term[0] in {'"', "“", "”"}
                and raw_term[-1] in {'"', "“", "”"}
            )
            if quoted:
                phrase = _normalize_rules_text(raw_term[1:-1])
                if not phrase:
                    continue
                groups.append("oracle_text_search LIKE ? ESCAPE '\\'")
                group_params.append([f"%{_escape_like(phrase)}%"] )
                continue

            # Natural unquoted input is a word search: every normalized word in
            # this chip must occur somewhere in the card's complete Oracle text.
            # This makes `create token` match `Create a 1/1 ... creature token`
            # while quoted input remains available for exact contiguous phrases.
            words = [
                word for word in _normalize_rules_text(raw_term).split() if word
            ]
            if not words:
                continue
            groups.append("(" + " AND ".join(
                "oracle_text_search LIKE ? ESCAPE '\\'" for _ in words
            ) + ")")
            group_params.append([
                f"%{_escape_like(word)}%" for word in words
            ])

        if not groups:
            return
        joiner = " OR " if str(text_mode).casefold() == "any" else " AND "
        self.clauses.append("(" + joiner.join(groups) + ")")
        for params in group_params:
            self.params.extend(params)

    def add_type_filters(self, card_types, card_type_mode, supertypes,
                         supertype_mode, subtypes, subtype_mode,
                         keywords, keyword_mode, type_line):
        self._add_function_terms(
            card_types, card_type_mode, "CARD_HAS_TYPE(type_line, ?) = 1")
        self._add_function_terms(
            supertypes, supertype_mode,
            "CARD_HAS_TYPE(type_line, ?) = 1")
        self._add_function_terms(
            subtypes, subtype_mode, "CARD_HAS_SUBTYPE(type_line, ?) = 1")

        keyword_values = [
            str(value).strip() for value in (keywords or []) if str(value).strip()
        ]
        if keyword_values:
            joiner = " OR " if str(keyword_mode).casefold() == "any" else " AND "
            self.clauses.append("(" + joiner.join(
                "EXISTS (SELECT 1 FROM json_each(COALESCE(keywords, '[]')) "
                "AS keyword_value WHERE keyword_value.value = ? COLLATE NOCASE)"
                for _ in keyword_values) + ")")
            self.params.extend(keyword_values)

        if type_line:
            self.clauses.append("type_line LIKE ? ESCAPE '\\'")
            self.params.append(f"%{_escape_like(type_line)}%")

    def _add_function_terms(self, values, mode, sql_expr):
        clean = [str(value).strip() for value in (values or [])
                 if str(value).strip()]
        if not clean:
            return
        joiner = " OR " if str(mode).casefold() == "any" else " AND "
        self.clauses.append("(" + joiner.join(sql_expr for _ in clean) + ")")
        self.params.extend(clean)

    def add_color_filter(self, colors, color_mode):
        requested = [color for color in (colors or []) if color in (*COLORS, "C")]
        selected = [color for color in requested if color in COLORS]
        wants_colorless = "C" in requested
        if selected:
            if color_mode == "includes":
                for color in selected:
                    self.clauses.append("color_identity LIKE ?")
                    self.params.append(f"%{color}%")
            elif color_mode == "exact":
                self.clauses.append("COALESCE(color_identity, '') = ?")
                self.params.append(",".join(
                    color for color in COLORS if color in selected))
            else:
                for color in COLORS:
                    if color not in selected:
                        self.clauses.append("color_identity NOT LIKE ?")
                        self.params.append(f"%{color}%")
        elif wants_colorless:
            self.clauses.append("COALESCE(color_identity, '') = ''")

    def add_numeric_filters(self, cmc_min, cmc_max, power_min, power_max,
                            toughness_min, toughness_max):
        cmc_min = self._finite_number(cmc_min, "mana value minimum")
        cmc_max = self._finite_number(cmc_max, "mana value maximum")
        power_min = self._finite_number(power_min, "power minimum")
        power_max = self._finite_number(power_max, "power maximum")
        toughness_min = self._finite_number(toughness_min, "toughness minimum")
        toughness_max = self._finite_number(toughness_max, "toughness maximum")
        if cmc_min is not None:
            self.clauses.append("cmc >= ?")
            self.params.append(cmc_min)
        if cmc_max is not None:
            self.clauses.append("cmc <= ?")
            self.params.append(cmc_max)
        self._add_numeric_stat("power", power_min, power_max)
        self._add_numeric_stat("toughness", toughness_min, toughness_max)

    @staticmethod
    def _finite_number(value, label):
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a finite number")
        return number

    def _add_numeric_stat(self, field, minimum, maximum):
        if minimum is None and maximum is None:
            return
        self.clauses.append(
            f"{field} IS NOT NULL AND {field} <> '' "
            f"AND {field} NOT GLOB '*[^0-9.-]*'")
        if minimum is not None:
            self.clauses.append(f"CAST({field} AS REAL) >= ?")
            self.params.append(float(minimum))
        if maximum is not None:
            self.clauses.append(f"CAST({field} AS REAL) <= ?")
            self.params.append(float(maximum))

    def add_rarity_and_format(self, rarities, fmt):
        rarity_values = sorted({
            str(value).strip() for value in (rarities or []) if str(value).strip()
        })
        if rarity_values:
            placeholders = ",".join("?" * len(rarity_values))
            self.clauses.append(f"rarity IN ({placeholders})")
            self.params.extend(rarity_values)
        if not fmt:
            return
        fmt_value = str(fmt).strip()
        statuses = tuple(sorted(PLAYABLE_LEGALITY_STATUSES))
        placeholders = ",".join("?" * len(statuses))
        if re.fullmatch(r"[A-Za-z0-9_]+", fmt_value):
            self.clauses.append(
                f"json_extract(legalities, ?) IN ({placeholders})")
            self.params.append(f"$.{fmt_value}")
            self.params.extend(statuses)
        else:
            self.clauses.append(
                "EXISTS (SELECT 1 FROM json_each(COALESCE(legalities, '{}')) "
                "AS legality WHERE legality.key = ? "
                f"AND legality.value IN ({placeholders}))")
            self.params.append(fmt_value)
            self.params.extend(statuses)

    def add_printing_filters(self, set_types, set_codes, set_code, lang, paper_only=False):
        if set_types is not None:
            chosen_types = sorted({str(value) for value in set_types if value})
            if not chosen_types:
                return False
            placeholders = ",".join("?" * len(chosen_types))
            self.clauses.append(f"set_type IN ({placeholders})")
            self.params.extend(chosen_types)

        if set_codes is not None:
            self.chosen_sets = sorted({str(value) for value in set_codes if value})
            if not self.chosen_sets:
                return False
            placeholders = ",".join("?" * len(self.chosen_sets))
            self.clauses.append(f"set_code IN ({placeholders})")
            self.params.extend(self.chosen_sets)
        elif set_code:
            self.clauses.append("set_code = ?")
            self.params.append(set_code)
        if lang:
            self.clauses.append("lang = ?")
            self.params.append(lang)
        if paper_only:
            self.clauses.append("paper = 1")
        return True

    def build(self, *, set_code, set_codes, columns, limit):
        where = (" WHERE " + " AND ".join(self.clauses)) if self.clauses else ""
        one_selected_set = (
            bool(set_code) or
            (set_codes is not None and self.chosen_sets is not None
             and len(self.chosen_sets) == 1)
        )
        order = ("CAST(collector_number AS INTEGER), collector_number"
                 if one_selected_set else "name COLLATE NOCASE")
        if columns is None:
            projection = "*"
        else:
            chosen_columns = tuple(dict.fromkeys(str(value) for value in columns))
            if not chosen_columns or any(
                    column not in _CARD_COLUMN_NAMES for column in chosen_columns):
                raise ValueError("Invalid card-search projection")
            projection = ", ".join(chosen_columns)
        sql = f"SELECT {projection} FROM cards{where} ORDER BY {order}"
        params = list(self.params)
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return sql, params


class CardSearchQueryMixin:
    """Provide the stable ``CardDB.search`` API over ``SearchQueryBuilder``."""

    def search(self, name="", names=None, text="", text_mode="all", keyword="", type_line="",
               creature_type="", card_types=None, card_type_mode="any",
               supertypes=None, supertype_mode="all", characteristics=None,
               characteristic_mode="all", subtypes=None,
               subtype_mode="any", keywords=None, keyword_mode="any", colors=None,
               color_mode="within", cmc_min=None, cmc_max=None, power_min=None,
               power_max=None, toughness_min=None, toughness_max=None, rarity="",
               rarities=None, fmt="", set_code="", set_codes=None, set_types=None,
               lang="", paper_only=False, limit=None, exclude_art=True, show_tokens=True,
               content_types=None, connection=None, columns=None):
        """Search the local DB and return card dictionaries.

        The signature remains backward-compatible for non-GUI callers while the
        builder owns clause construction and parameter ordering.
        """
        if keyword and not keywords:
            keywords = [keyword]
        if creature_type and not subtypes:
            subtypes = [creature_type]
        if rarity and not rarities:
            rarities = [rarity]

        if supertypes is None and characteristics is not None:
            supertypes = characteristics
            supertype_mode = characteristic_mode

        builder = SearchQueryBuilder()
        # Explicit Content selection is authoritative. Art Series remains excluded
        # for legacy/default callers, but a caller that deliberately requests the
        # trusted ``art`` content kind must be able to reach those imported rows.
        explicit_content = (
            {str(value).casefold() for value in content_types if str(value)}
            if content_types is not None else None
        )
        builder.add_art_filter(exclude_art and not (explicit_content and "art" in explicit_content))
        if not builder.add_content_filter(content_types, show_tokens):
            return []
        builder.add_name_and_rules(name, names, text, text_mode)
        builder.add_type_filters(
            card_types, card_type_mode, supertypes, supertype_mode,
            subtypes, subtype_mode, keywords, keyword_mode, type_line)
        builder.add_color_filter(colors, color_mode)
        builder.add_numeric_filters(
            cmc_min, cmc_max, power_min, power_max, toughness_min, toughness_max)
        builder.add_rarity_and_format(rarities, fmt)
        if not builder.add_printing_filters(
                set_types, set_codes, set_code, lang, paper_only=paper_only):
            return []
        sql, params = builder.build(
            set_code=set_code, set_codes=set_codes, columns=columns, limit=limit)
        if connection is None:
            with self._lock:
                rows = self.conn.execute(sql, params).fetchall()
        else:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
