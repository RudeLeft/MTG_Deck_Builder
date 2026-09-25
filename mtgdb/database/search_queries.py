"""Canonical SQL construction and execution for interactive card search."""

from __future__ import annotations

import math
import re

from mtgdb.database.constants import (
    ART_LAYOUTS, COLORS, CONTENT_TYPES, PLAYABLE_LEGALITY_STATUSES,
)
from mtgdb.database.schema import _CARD_COLUMN_NAMES
from mtgdb.database.semantics import (
    COLOR_BITS, TRAIT_COLOR_INDICATOR, TRAIT_HAS_X_COST, TRAIT_HYBRID_MANA,
    TRAIT_PHYREXIAN_MANA, TRAIT_TOP_HEAVY, TRAIT_VARIABLE_STATS,
    _escape_like, _normalize_rules_text, _type_key, pip_minimum_threshold)


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

    def add_content_filter(self, content_types):
        if content_types is None:
            content_types = {"card", "token", "emblem"}
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
        # Precomputed indexed column (content_kind == CARD_CONTENT_KIND at import)
        # replaces the per-row Python function on every candidate row.
        self.clauses.append(f"content_kind IN ({placeholders})")
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
        normalized = str(text_mode).casefold()
        # "none" excludes a card matching any chip, which is the only way to
        # ask for cards that never mention graveyards.
        joiner = " OR " if normalized in ("any", "none") else " AND "
        clause = "(" + joiner.join(groups) + ")"
        self.clauses.append(f"NOT {clause}" if normalized == "none" else clause)
        for params in group_params:
            self.params.extend(params)

    def add_type_filters(self, card_types, card_type_mode, supertypes,
                         supertype_mode, subtypes, subtype_mode,
                         keywords, keyword_mode, type_line):
        # Types and supertypes both live in the left type-line part, so both map
        # to the card_types membership table (as CARD_HAS_TYPE covered both).
        self._add_membership_terms(card_types, card_type_mode, "card_types")
        self._add_membership_terms(supertypes, supertype_mode, "card_types")
        self._add_membership_terms(subtypes, subtype_mode, "card_subtypes")

        keyword_terms = [
            str(value).strip().casefold()
            for value in (keywords or []) if str(value).strip()
        ]
        if keyword_terms:
            normalized = str(keyword_mode).casefold()
            joiner = " OR " if normalized in ("any", "none") else " AND "
            group = "(" + joiner.join(
                "EXISTS (SELECT 1 FROM card_keywords m "
                "WHERE m.card_id = cards.id AND m.term = ?)"
                for _ in keyword_terms) + ")"
            self.clauses.append(
                f"NOT {group}" if normalized == "none" else group)
            self.params.extend(keyword_terms)

        if type_line:
            self.clauses.append("type_line LIKE ? ESCAPE '\\'")
            self.params.append(f"%{_escape_like(type_line)}%")

    def _add_membership_terms(self, values, mode, table):
        """Filter by an indexed (card_id, term) membership table.

        Replaces the per-row CARD_HAS_TYPE / CARD_HAS_SUBTYPE functions: the
        selected value is normalized to the same term the import stored (every
        contiguous n-gram of the type-line words), so an EXISTS on the indexed
        term reproduces the contiguous-run match exactly. Any/All/None combine
        the per-term EXISTS clauses just as the function version did.
        """
        terms = []
        for value in (values or []):
            term = " ".join(_type_key(str(value)).split())
            if term:
                terms.append(term)
        if not terms:
            return
        normalized = str(mode).casefold()
        expr = (f"EXISTS (SELECT 1 FROM {table} m "
                f"WHERE m.card_id = cards.id AND m.term = ?)")
        group = "(" + (" OR " if normalized in ("any", "none") else " AND ").join(
            expr for _ in terms) + ")"
        # "none" excludes every card matching any selected value, which is the
        # only way to ask for a green non-creature or a creature without flying.
        self.clauses.append(f"NOT {group}" if normalized == "none" else group)
        self.params.extend(terms)

    def _add_color_set_filter(self, mask_column, selected, mode, members):
        """Filter a precomputed WUBRG(+C) bitmask column by within/includes/exact.

        ``identity_mask`` and ``produced_mask`` share this encoding, so both
        filters share one implementation. The integer AND replaces the former
        per-colour LIKE over the comma-joined string: includes means the card
        has all selected bits, exact means its bits equal the selected set, and
        within means it has no bit outside the selected set.
        """
        if not selected:
            return
        selected_mask = 0
        for color in selected:
            selected_mask |= COLOR_BITS[color]
        if mode == "includes":
            self.clauses.append(f"({mask_column} & ?) = ?")
            self.params.extend([selected_mask, selected_mask])
        elif mode == "exact":
            self.clauses.append(f"{mask_column} = ?")
            self.params.append(selected_mask)
        else:
            outside_mask = 0
            for color in members:
                if color not in selected:
                    outside_mask |= COLOR_BITS[color]
            self.clauses.append(f"({mask_column} & ?) = 0")
            self.params.append(outside_mask)

    def add_color_filter(self, colors, color_mode, scope="identity"):
        """Filter by colour identity, or by the card's own colours.

        Scryfall separates these and so does the stored schema: Ghostfire
        is a colourless card with a red identity. Both bitmask columns share one
        encoding, so only the column name changes.
        """
        mask_column = "colors_mask" if str(scope) == "colors" else "identity_mask"
        requested = [color for color in (colors or []) if color in (*COLORS, "C")]
        selected = [color for color in requested if color in COLORS]
        if selected:
            self._add_color_set_filter(mask_column, selected, color_mode, COLORS)
        elif "C" in requested:
            self.clauses.append(f"{mask_column} = 0")

    def add_produces_filter(self, produces, produces_mode):
        """Filter by the mana a card can actually produce.

        Distinct from colour identity: Birds of Paradise has identity ``G`` and
        produces every colour, and Command Tower has no identity at all. Here
        colourless is a real member (the ``C`` bit) rather than an empty value,
        so it participates like any other colour.
        """
        members = (*COLORS, "C")
        selected = [color for color in (produces or []) if color in members]
        if not selected:
            return
        self._add_color_set_filter(
            "produced_mask", selected, produces_mode, members)
        if produces_mode != "exact":
            # "within" alone would match the ~99k cards that produce nothing.
            self.clauses.append("produced_mask != 0")

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

    def add_stat_filters(self, loyalty_min, loyalty_max,
                         defense_min, defense_max):
        """Planeswalker starting loyalty and battle defense.

        Same shape as power and toughness, against columns that were
        stored from the first import and never exposed to a filter.
        """
        self._add_numeric_stat(
            "loyalty", self._finite_number(loyalty_min, "loyalty minimum"),
            self._finite_number(loyalty_max, "loyalty maximum"))
        self._add_numeric_stat(
            "defense", self._finite_number(defense_min, "defense minimum"),
            self._finite_number(defense_max, "defense maximum"))

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

    # A card has two faces when Scryfall gives it two, not when its layout
    # appears on a list somebody maintained. The list called Saga, Class,
    # Case, Leveler, Prototype, Mutate and Meld multi-faced -- 761 paper
    # printings with a single face -- while genuinely two-faced layouts this
    # build had never seen read as single-faced. Reading card_faces is also
    # self-correcting for the next layout Scryfall invents.
    HAS_FACES_CLAUSE = (
        "card_faces IS NOT NULL AND card_faces NOT IN ('', '[]', 'null')")
    # The six mana/stat/indicator traits below are precomputed once at import
    # into the trait_flags bitmask (mtgdb.database.semantics.derive_trait_flags)
    # and tested here with an indexed integer AND instead of a per-row LIKE/GLOB
    # scan. universes_beyond/reserved/game_changer are already plain indexed
    # columns and multi_faced/single_faced already read card_faces directly, so
    # none of those five need (or use) trait_flags.
    TRAIT_CLAUSES = {
        "universes_beyond": "universes_beyond = 1",
        "not_universes_beyond": "COALESCE(universes_beyond, 0) = 0",
        "reserved": "reserved = 1",
        "game_changer": "game_changer = 1",
        "multi_faced": HAS_FACES_CLAUSE,
        "single_faced": f"NOT ({HAS_FACES_CLAUSE})",
        "hybrid_mana": f"(trait_flags & {TRAIT_HYBRID_MANA}) != 0",
        "phyrexian_mana": f"(trait_flags & {TRAIT_PHYREXIAN_MANA}) != 0",
        "has_x_cost": f"(trait_flags & {TRAIT_HAS_X_COST}) != 0",
        "color_indicator": f"(trait_flags & {TRAIT_COLOR_INDICATOR}) != 0",
        "variable_stats": f"(trait_flags & {TRAIT_VARIABLE_STATS}) != 0",
        "top_heavy": f"(trait_flags & {TRAIT_TOP_HEAVY}) != 0",
    }

    def add_property_filters(self, properties, mode="any"):
        """Filter one independent group of stable boolean card properties."""
        fragments = []
        for key in sorted({str(value) for value in (properties or [])}):
            clause = self.TRAIT_CLAUSES.get(key)
            if clause:
                fragments.append(f"({clause})")
        if not fragments:
            return
        normalized = str(mode).casefold()
        joiner = " OR " if normalized in ("any", "none") else " AND "
        group = "(" + joiner.join(fragments) + ")"
        if normalized == "none":
            # A property clause over a NULL column (power/toughness on a
            # non-creature) evaluates to NULL, and `NOT NULL` is NULL rather
            # than TRUE -- so a bare `NOT (group)` would silently exclude every
            # card the group could not decide, dropping all ~58k cards with no
            # power/toughness from a "None" search. COALESCE folds that unknown
            # to "does not match", which is what None means: the property is
            # not present, so the card belongs in the result.
            self.clauses.append(f"COALESCE({group}, 0) = 0")
        else:
            self.clauses.append(group)

    def add_trait_filters(self, traits, trait_mode="any"):
        """Legacy compatibility wrapper for the former global property facet.

        Existing external ``CardDB.search(traits=..., trait_mode=...)`` calls
        retain their exact semantics. Interactive Search uses independent
        property groups so one picker's Match mode cannot affect another.
        """
        self.add_property_filters(traits, trait_mode)

    def add_layout_filter(self, layouts, layout_mode="any"):
        """Filter by the printed shape of the card.

        A card has exactly one layout, so All would always find nothing and
        the control offers only Any and None.
        """
        clean = sorted({str(value).strip() for value in (layouts or [])
                        if str(value).strip()})
        if not clean:
            return
        placeholders = ",".join("?" * len(clean))
        group = f"COALESCE(layout, '') IN ({placeholders})"
        self.clauses.append(
            f"NOT {group}" if str(layout_mode).casefold() == "none" else group)
        self.params.extend(clean)

    def add_pip_filters(self, pips, pip_min, pip_mode="all"):
        """Filter physical mana symbols by selected-color presence and total.

        Import-time per-color pip columns remain a cheap presence prefilter.
        The deterministic SQL helper parses the stored mana-cost string for the
        total so one hybrid symbol may represent both colors without counting
        twice toward Minimum.
        """
        selected = sorted({
            str(value).strip().upper() for value in (pips or [])
            if str(value).strip().upper() in (*COLORS, "C")
        })
        if not selected:
            return
        normalized = str(pip_mode or "all").casefold()
        if normalized not in {"any", "all", "none"}:
            normalized = "all"
        minimum = pip_minimum_threshold(pip_min)

        presence = [f"pips_{color.casefold()} > 0" for color in selected]
        if normalized == "all":
            self.clauses.extend(presence)
        elif normalized == "any":
            self.clauses.append("(" + " OR ".join(presence) + ")")
        else:
            self.clauses.extend(
                f"pips_{color.casefold()} = 0" for color in selected)

        self.clauses.append("MANA_COST_SYMBOL_MATCH(mana_cost, ?, ?, ?) = 1")
        self.params.extend((",".join(selected), normalized, minimum))

    GAME_PLATFORMS = ("paper", "mtgo", "arena")

    def add_games_filter(self, games):
        """Restrict to printings available on the selected platforms.

        A printing matches when it is available on any selected platform, so
        choosing Paper and Arena widens rather than narrows. Selecting none
        means no restriction, which is what an untouched control should do.
        """
        selected = [
            value for value in self.GAME_PLATFORMS
            if value in {str(item).casefold() for item in (games or ())}]
        if not selected or len(selected) == len(self.GAME_PLATFORMS):
            return
        self.clauses.append("(" + " OR ".join(
            "games LIKE ?" for _ in selected) + ")")
        self.params.extend(f"%{value}%" for value in selected)

    def add_release_filters(self, released_from, released_to):
        """Bound the printing release date. Stored as an ISO yyyy-mm-dd string."""
        for value, operator, label in (
                (released_from, ">=", "release year from"),
                (released_to, "<=", "release year to")):
            if value in (None, ""):
                continue
            year = self._finite_number(value, label)
            if year is None:
                continue
            boundary = f"{int(year):04d}-01-01" if operator == ">="                 else f"{int(year):04d}-12-31"
            self.clauses.append(
                f"released_at IS NOT NULL AND released_at <> '' "
                f"AND released_at {operator} ?")
            self.params.append(boundary)

    def add_rarity_and_format(self, rarities, fmt, fmt_status="playable"):
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
        # Playable is legal-or-restricted. Banned and restricted are the states
        # a deck check actually asks about and nothing could previously reach.
        chosen_status = str(fmt_status or "playable").casefold()
        if chosen_status == "banned":
            statuses = ("banned",)
        elif chosen_status == "restricted":
            statuses = ("restricted",)
        else:
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

    def add_printing_filters(self, set_types, set_codes, lang, paper_only=False):
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
        if lang:
            self.clauses.append("lang = ?")
            self.params.append(lang)
        if paper_only:
            self.clauses.append("paper = 1")
        return True

    def where_sql(self):
        """Return the canonical WHERE fragment and bound parameters.

        Contextual Search analysis needs the exact same semantic clauses as the
        foreground result query, but it must be able to aggregate/count without
        paying for the result-ordering step.  Keeping this on the canonical
        builder prevents the two paths from drifting.
        """
        where = (" WHERE " + " AND ".join(self.clauses)) if self.clauses else ""
        return where, list(self.params)

    def build_count(self):
        where, params = self.where_sql()
        return f"SELECT COUNT(*) AS match_count FROM cards{where}", params

    def build(self, *, set_codes, columns, ordered=True, limit=None):
        where, params = self.where_sql()
        one_selected_set = (
            set_codes is not None and self.chosen_sets is not None
            and len(self.chosen_sets) == 1
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
        order_sql = f" ORDER BY {order}" if ordered else ""
        limit_sql = f" LIMIT {int(limit)}" if limit is not None else ""
        sql = f"SELECT {projection} FROM cards{where}{order_sql}{limit_sql}"
        return sql, params


def _configured_search_builder(*, name="", names=None, text="", text_mode="all",
                               type_line="", card_types=None, card_type_mode="any",
                               supertypes=None, supertype_mode="all", subtypes=None,
                               subtype_mode="any", keywords=None, keyword_mode="any",
                               colors=None, color_mode="within", color_scope="identity",
                               produces=None, produces_mode="includes", traits=None,
                               trait_mode="any", mana_features=None,
                               mana_feature_mode="any", special_properties=None,
                               special_property_mode="any", status_properties=None,
                               status_property_mode="any", layouts=None, layout_mode="any",
                               pips=None, pip_mode="all", pip_min=None, loyalty_min=None, loyalty_max=None,
                               defense_min=None, defense_max=None, released_from=None,
                               released_to=None, games=None, cmc_min=None, cmc_max=None,
                               power_min=None, power_max=None, toughness_min=None,
                               toughness_max=None, rarities=None, fmt="",
                               fmt_status="playable", set_codes=None, set_types=None,
                               lang="", paper_only=False, content_types=None):
    """Create the one canonical builder used by results and context aggregates."""
    builder = SearchQueryBuilder()
    explicit_content = (
        {str(value).casefold() for value in content_types if str(value)}
        if content_types is not None else None
    )
    builder.add_art_filter(not (explicit_content and "art" in explicit_content))
    if not builder.add_content_filter(content_types):
        return None
    builder.add_name_and_rules(name, names, text, text_mode)
    builder.add_type_filters(
        card_types, card_type_mode, supertypes, supertype_mode,
        subtypes, subtype_mode, keywords, keyword_mode, type_line)
    builder.add_color_filter(colors, color_mode, color_scope)
    builder.add_produces_filter(produces, produces_mode)
    # Property groups are independent facets.  Groups AND with each other;
    # values inside each group use that group's own Any/All/None mode.
    builder.add_trait_filters(traits, trait_mode)  # legacy external API only
    builder.add_property_filters(mana_features, mana_feature_mode)
    builder.add_property_filters(special_properties, special_property_mode)
    builder.add_property_filters(status_properties, status_property_mode)
    builder.add_layout_filter(layouts, layout_mode)
    builder.add_pip_filters(pips, pip_min, pip_mode)
    builder.add_stat_filters(
        loyalty_min, loyalty_max, defense_min, defense_max)
    builder.add_release_filters(released_from, released_to)
    builder.add_games_filter(games)
    builder.add_numeric_filters(
        cmc_min, cmc_max, power_min, power_max, toughness_min, toughness_max)
    builder.add_rarity_and_format(rarities, fmt, fmt_status)
    if not builder.add_printing_filters(
            set_types, set_codes, lang, paper_only=paper_only):
        return None
    return builder


class CardSearchQueryMixin:
    """Provide the stable ``CardDB.search`` API over ``SearchQueryBuilder``."""

    def search(self, name="", names=None, text="", text_mode="all",
               type_line="", card_types=None, card_type_mode="any",
               supertypes=None, supertype_mode="all", subtypes=None,
               subtype_mode="any", keywords=None, keyword_mode="any", colors=None,
               color_mode="within", color_scope="identity",
               produces=None, produces_mode="includes",
               traits=None, trait_mode="any", mana_features=None,
               mana_feature_mode="any", special_properties=None,
               special_property_mode="any", status_properties=None,
               status_property_mode="any", layouts=None, layout_mode="any",
               pips=None, pip_mode="all", pip_min=None, loyalty_min=None, loyalty_max=None,
               defense_min=None, defense_max=None, released_from=None,
               released_to=None, games=None,
               cmc_min=None, cmc_max=None, power_min=None,
               power_max=None, toughness_min=None, toughness_max=None,
               rarities=None, fmt="", fmt_status="playable",
               set_codes=None, set_types=None,
               lang="", paper_only=False,
               content_types=None, connection=None, columns=None):
        """Search the local DB and return card dictionaries.

        One parameter per criterion, matching ``SearchCriteria``. The singular
        aliases this signature used to carry for "non-GUI callers" -- keyword,
        creature_type, characteristics, rarity, set_code -- had no caller in
        the project; ``characteristics`` was a second spelling of
        ``supertypes`` with a mode of its own, which is exactly the kind of
        silent alternative path a search API should not have.
        """
        values = locals().copy()
        values.pop("self", None)
        values.pop("connection", None)
        values.pop("columns", None)
        builder = _configured_search_builder(**values)
        if builder is None:
            return []
        sql, params = builder.build(set_codes=set_codes, columns=columns)
        if connection is None:
            with self._lock:
                return self._fetch_dicts(self.conn, sql, params)
        return self._fetch_dicts(connection, sql, params)

    def search_projection(self, *, connection=None, columns, limit=None, **criteria):
        """Ordered search returning ``(column_names, row_tuples)`` without dicts.

        The interactive Results path builds its ``SearchResultRow`` objects
        straight from these tuples, so materializing a throwaway dict per row
        (over ~100k rows) between SQLite and the row objects is pure overhead.
        Column order matches ``columns`` exactly (``build`` projects them as
        given), so callers map fields by position.  ``limit`` caps the row count
        for a fast first-screen fetch in the default (name) order.
        """
        builder = _configured_search_builder(**criteria)
        if builder is None:
            return (), []
        sql, params = builder.build(
            set_codes=criteria.get("set_codes"), columns=columns, limit=limit)
        if connection is None:
            with self._lock:
                return self._fetch_tuples(self.conn, sql, params)
        return self._fetch_tuples(connection, sql, params)

    def search_unordered(self, *, connection=None, columns=None, **criteria):
        """Return a narrow Search projection without result-ordering overhead."""
        builder = _configured_search_builder(**criteria)
        if builder is None:
            return []
        sql, params = builder.build(
            set_codes=criteria.get("set_codes"), columns=columns, ordered=False)
        if connection is None:
            with self._lock:
                return self._fetch_dicts(self.conn, sql, params)
        return self._fetch_dicts(connection, sql, params)

    @staticmethod
    def _fetch_dicts(connection, sql, params):
        """Run a projection and build plain-dict rows from tuples.

        ``dict(sqlite3.Row)`` walks the row's mapping protocol column by column
        and is roughly three times slower than zipping a plain tuple against the
        column names -- material on the ~100k-row contextual scans.  A private
        cursor with its own ``row_factory`` cleared yields plain tuples without
        disturbing the connection's Row factory that every other caller relies
        on.  The resulting dicts are identical to the former ``dict(row)`` path.
        """
        cursor = connection.cursor()
        try:
            cursor.row_factory = None
            cursor.execute(sql, params)
            keys = [description[0] for description in cursor.description]
            return [dict(zip(keys, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    @staticmethod
    def _fetch_tuples(connection, sql, params):
        """Run a projection and return ``(column_names, plain_tuples)``.

        Like ``_fetch_dicts`` but without building a dict per row -- the caller
        maps columns by position.  Uses a private cursor with its row_factory
        cleared so the connection's Row factory other callers rely on is left
        untouched.
        """
        cursor = connection.cursor()
        try:
            cursor.row_factory = None
            cursor.execute(sql, params)
            keys = tuple(description[0] for description in cursor.description)
            return keys, cursor.fetchall()
        finally:
            cursor.close()

    def count_search(self, *, connection=None, **criteria):
        """Count Search matches using SQL COUNT(*) and canonical criteria clauses."""
        builder = _configured_search_builder(**criteria)
        if builder is None:
            return 0
        sql, params = builder.build_count()
        if connection is None:
            with self._lock:
                row = self.conn.execute(sql, params).fetchone()
        else:
            row = connection.execute(sql, params).fetchone()
        return int(row[0] if row is not None else 0)
