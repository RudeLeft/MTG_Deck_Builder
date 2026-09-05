"""Trusted observed set, format, type, subtype, and mechanic taxonomy queries."""

import hashlib
import json
import re
import sqlite3

from mtgdb.database.authorities import (
    MECHANIC_AUTHORITIES, SCRYFALL_CATALOGS, SUBTYPE_AUTHORITIES,
    UPSTREAM_FINGERPRINT_META_KEY, UPSTREAM_REPORT_META_KEY,
)
from mtgdb.database.constants import (
    ART_LAYOUTS, CONTENT_TYPES, KNOWN_LEGALITY_STATUSES,
    PLAYABLE_LEGALITY_STATUSES, RARITIES,
)
from mtgdb.database.schema import (
    CARD_TYPES_ERROR_META_KEY, RULES_SUPERTYPES_ERROR_META_KEY,
    RULES_SUPERTYPES_META_KEY,
)
from mtgdb.database.semantics import (
    _card_content_classification, _card_has_type, _semantic_type_face_parts,
    _type_key, _type_line_faces,
)


class CardTaxonomyMixin:
    """Provide authoritative discovery APIs over the committed card snapshot.

    Search-facing vocabulary is always an intersection of trusted Scryfall data
    (card fields/catalogs) and rows actually present in the local database. Saved
    UI state never contributes vocabulary here.
    """

    @staticmethod
    def _scope(content_types=None, paper_only=False, *, prefix="", games=None):
        field = lambda name: f"{prefix}{name}"
        clauses = []
        params = []
        if content_types is None:
            art_ph = ",".join("?" * len(ART_LAYOUTS))
            clauses.append(
                f"({field('layout')} IS NULL OR {field('layout')} NOT IN ({art_ph}))")
            params.extend(ART_LAYOUTS)
        else:
            chosen = {str(value).casefold() for value in content_types if str(value)}
            unknown = chosen - set(CONTENT_TYPES)
            if unknown:
                raise ValueError(
                    "Unknown card-content type(s): " + ", ".join(sorted(unknown)))
            if not chosen:
                return "0", []
            placeholders = ",".join("?" * len(chosen))
            clauses.append(
                f"CARD_CONTENT_KIND({field('layout')}, {field('type_line')}) "
                f"IN ({placeholders})")
            params.extend(sorted(chosen))
        platforms = [
            value for value in ("paper", "mtgo", "arena")
            if value in {str(item).casefold() for item in (games or ())}]
        if platforms and len(platforms) < 3:
            # Vocabulary must follow the visible PRINTING TYPE choice: picking
            # Arena alone should offer Arena's sets, not every digital set.
            clauses.append("(" + " OR ".join(
                f"{field('games')} LIKE ?" for _ in platforms) + ")")
            params.extend(f"%{value}%" for value in platforms)
        elif paper_only:
            clauses.append(f"{field('paper')} = 1")
        return " AND ".join(clauses) if clauses else "1", params

    def sets(self, allowed_types=None, content_types=None, paper_only=False,
             games=None):
        """Distinct Scryfall sets present locally, newest first."""
        scope, params = self._scope(content_types, paper_only, games=games)
        clauses = ["set_code IS NOT NULL", scope]
        if allowed_types is not None:
            allowed = sorted({str(value) for value in allowed_types if str(value)})
            if not allowed:
                return []
            placeholders = ",".join("?" * len(allowed))
            clauses.append(f"set_type IN ({placeholders})")
            params.extend(allowed)
        with self._lock:
            rows = self.conn.execute(
                "SELECT set_code, set_name, MAX(released_at) AS r FROM cards "
                f"WHERE {' AND '.join(clauses)} "
                "GROUP BY set_code ORDER BY r DESC, set_name",
                params,
            ).fetchall()
        return [(row["set_code"], row["set_name"] or row["set_code"]) for row in rows]

    def set_types(self, content_types=None, paper_only=False, games=None):
        """Observed Scryfall ``set_type`` values with local set counts."""
        scope, params = self._scope(content_types, paper_only, games=games)
        with self._lock:
            rows = self.conn.execute(
                "SELECT set_type, COUNT(DISTINCT set_code) AS n FROM cards "
                "WHERE set_type IS NOT NULL AND set_type <> '' "
                f"AND {scope} GROUP BY set_type ORDER BY n DESC, set_type",
                params,
            ).fetchall()
        return [(row["set_type"], row["n"]) for row in rows]

    @staticmethod
    def _preferred_values(observed, preferred):
        """Order observed values familiarly without inventing absent values."""
        present = {str(value) for value in observed if value not in (None, "")}
        ordered = [value for value in preferred if value in present]
        known = set(ordered)
        ordered.extend(sorted(present - known, key=str.casefold))
        return ordered

    def layouts(self, content_types=None, paper_only=False, games=None):
        """Observed printed shapes in the current scope, most common first.

        Layout is Scryfall's own field, so the vocabulary is whatever the
        local rows actually carry -- including a shape this build has never
        heard of, which stays selectable rather than disappearing.
        """
        scope, params = self._scope(content_types, paper_only, games=games)
        with self._lock:
            rows = self.conn.execute(
                "SELECT layout, COUNT(*) AS total FROM cards "
                f"WHERE layout IS NOT NULL AND layout <> '' AND {scope} "
                "GROUP BY layout ORDER BY total DESC, layout", params).fetchall()
        return [(row["layout"], row["total"]) for row in rows]

    def rarities(self, content_types=None, paper_only=False):
        scope, params = self._scope(content_types, paper_only)
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT rarity FROM cards "
                "WHERE rarity IS NOT NULL AND rarity <> '' "
                f"AND {scope}", params).fetchall()
        return self._preferred_values((row["rarity"] for row in rows), RARITIES)

    def formats_by_status(self, content_types=None, paper_only=False,
                          games=None):
        """Formats that have at least one scoped card in each legality state.

        The Format picker offers one list per legality, so a state that no
        format can satisfy is never offered: only Vintage and Old School
        restrict anything, and listing every format under Restricted sent the
        user to a guaranteed-empty result. One grouped scan answers all three
        states, because the scan itself is the expensive part.
        """
        scope, scope_params = self._scope(
            content_types, paper_only, prefix="cards.", games=games)
        grouped = {"playable": set(), "banned": set(), "restricted": set()}
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT DISTINCT legal.key AS format, legal.value AS status "
                    "FROM cards, json_each(COALESCE(cards.legalities, '{}')) AS legal "
                    "WHERE legal.key IS NOT NULL AND legal.key <> '' "
                    f"AND {scope}", scope_params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        for row in rows:
            name = str(row["format"] or "")
            status = str(row["status"] or "").casefold()
            if not name:
                continue
            if status in PLAYABLE_LEGALITY_STATUSES:
                grouped["playable"].add(name)
            if status in grouped:
                grouped[status].add(name)
        return {
            state: tuple(sorted(values, key=str.casefold))
            for state, values in grouped.items()
        }

    def formats(self, content_types=None, paper_only=False):
        """Formats with at least one playable scoped card (legal or restricted)."""
        scope, scope_params = self._scope(content_types, paper_only, prefix="cards.")
        statuses = sorted(PLAYABLE_LEGALITY_STATUSES)
        status_placeholders = ",".join("?" * len(statuses))
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT DISTINCT legal.key AS format "
                    "FROM cards, json_each(COALESCE(cards.legalities, '{}')) AS legal "
                    "WHERE legal.key IS NOT NULL AND legal.key <> '' "
                    f"AND legal.value IN ({status_placeholders}) AND {scope}",
                    [*statuses, *scope_params]).fetchall()
            observed = [row["format"] for row in rows]
        except sqlite3.OperationalError:
            observed = []
            fallback_scope, fallback_params = self._scope(
                content_types, paper_only)
            with self._lock:
                rows = self.conn.execute(
                    "SELECT legalities FROM cards "
                    f"WHERE legalities IS NOT NULL AND {fallback_scope}",
                    fallback_params).fetchall()
            for row in rows:
                try:
                    value = json.loads(row["legalities"] or "{}")
                    if isinstance(value, dict):
                        observed.extend(
                            key for key, status in value.items()
                            if str(status).casefold() in PLAYABLE_LEGALITY_STATUSES)
                except (ValueError, TypeError):
                    pass
        return sorted(
            {str(value) for value in observed if value not in (None, "")},
            key=str.casefold)

    def _type_lines(self, content_types=None, paper_only=False):
        scope, params = self._scope(content_types, paper_only)
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT type_line FROM cards "
                "WHERE type_line IS NOT NULL AND type_line <> '' "
                f"AND {scope}", params).fetchall()
        return [row["type_line"] for row in rows]

    def card_types(self, content_types=None, paper_only=False):
        """Authoritative Scryfall card types that occur on scoped local rows.

        ``card-types`` is a first-class Scryfall catalog. Local Oracle type lines
        decide whether a catalog value is actually present; unmatched left-side
        words are never promoted into picker vocabulary. Missing/failed catalog
        refreshes yield no invented fallback values.
        """
        catalog = self.catalog("card-types")
        if not catalog:
            return []
        lines = self._type_lines(content_types, paper_only)
        return [
            value for value in catalog
            if any(_card_has_type(type_line, value) for type_line in lines)
        ]

    def card_type_taxonomy_status(self):
        """Return whether trusted Scryfall Card Type vocabulary is available.

        The error is retained independently of the last successful catalog so
        UI code can distinguish a missing authority snapshot from a legitimate
        Content/Paper scope with no matching Card Types.
        """
        values = self.catalog("card-types")
        error = str(self.get_meta(CARD_TYPES_ERROR_META_KEY, "") or "").strip()
        return bool(values), error

    def _verified_rules_supertypes(self):
        """Return the last strictly verified Wizards supertype vocabulary.

        The sync service is the only writer of this metadata. Invalid/missing
        metadata fails closed so picker vocabulary is never guessed from type
        lines or from Scryfall's broader catalog taxonomy.
        """
        raw = self.get_meta(RULES_SUPERTYPES_META_KEY, "")
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(values, list):
            return []
        seen, clean = set(), []
        for value in values:
            value = " ".join(str(value or "").split())
            key = _type_key(value)
            if value and key not in seen:
                seen.add(key)
                clean.append(value)
        return clean

    @staticmethod
    def _observed_left_phrase(type_line, candidate):
        """Return Oracle casing for an exact left-side candidate phrase."""
        target = tuple(_type_key(candidate).split())
        if not target:
            return None
        width = len(target)
        for left, right in _type_line_faces(type_line):
            words, _subtype = _semantic_type_face_parts(left, right)
            keys = tuple(_type_key(word) for word in words)
            for index in range(0, len(keys) - width + 1):
                if keys[index:index + width] == target:
                    return " ".join(words[index:index + width])
        return None

    def supertype_taxonomy_status(self):
        """Return whether verified Wizards vocabulary exists and the last failure.

        This distinguishes a legitimate empty current Content/Paper intersection
        from a missing authority snapshot, which the UI must not present as the
        same state.
        """
        values = self._verified_rules_supertypes()
        error = str(self.get_meta(RULES_SUPERTYPES_ERROR_META_KEY, "") or "").strip()
        return bool(values), error

    def supertypes(self, content_types=None, paper_only=False):
        """Official Wizards supertypes that occur on scoped Scryfall rows.

        Vocabulary comes only from the last strictly verified Comprehensive
        Rules parse. Scryfall's local Oracle type lines determine occurrence and
        display casing. No Supertype names are hardcoded or inferred here.
        """
        rules_values = self._verified_rules_supertypes()
        if not rules_values:
            return []
        lines = self._type_lines(content_types, paper_only)
        observed = []
        for candidate in rules_values:
            label = None
            for type_line in lines:
                label = self._observed_left_phrase(type_line, candidate)
                if label is not None:
                    break
            if label is not None:
                observed.append(label)
        return observed

    def subtype_catalog(self, content_types=None, paper_only=False):
        """Return authoritative observed ``(subtype, category)`` values only.

        Phrase boundaries and categories come exclusively from Scryfall catalog
        endpoints. The local type lines only decide whether a catalog value is
        actually present; unmatched type-line words are never invented as picker
        entries.
        """
        candidates = {}
        for authority in SUBTYPE_AUTHORITIES:
            catalog_name, category = authority.name, authority.label
            for value in self.catalog(catalog_name):
                key = " ".join(_type_key(value).split())
                if not key:
                    continue
                entry = candidates.setdefault(key, [value, set()])
                entry[1].add(category)
        if not candidates:
            return []

        # Scan each local type line once. Candidate phrases still come entirely
        # from Scryfall catalogs; this only answers which trusted phrases occur
        # in the current scoped snapshot.
        alternatives = sorted(candidates, key=lambda value: (-len(value), value))
        pattern = re.compile(
            r"(?<![\w-])(" + "|".join(re.escape(value) for value in alternatives)
            + r")(?![\w-])")
        observed = set()
        for type_line in self._type_lines(content_types, paper_only):
            for left, right in _type_line_faces(type_line):
                _type_words, subtype_text = _semantic_type_face_parts(left, right)
                normalized = " ".join(_type_key(subtype_text).split())
                if normalized:
                    observed.update(match.group(1) for match in pattern.finditer(normalized))

        found = [
            (candidates[key][0], " / ".join(sorted(candidates[key][1])))
            for key in observed
        ]
        return sorted(found, key=lambda item: (item[1].casefold(), item[0].casefold()))

    def subtypes(self, content_types=None, paper_only=False):
        return [
            value for value, _category
            in self.subtype_catalog(content_types, paper_only)
        ]

    def creature_types(self, content_types=None, paper_only=False):
        return [
            value for value, category
            in self.subtype_catalog(content_types, paper_only)
            if category == "Creature"
        ]

    def keywords(self, content_types=None, paper_only=False):
        """Distinct Scryfall card ``keywords`` actually present in scoped rows."""
        scope, params = self._scope(content_types, paper_only, prefix="cards.")
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT DISTINCT value AS k FROM cards, json_each(cards.keywords) "
                    "WHERE cards.keywords IS NOT NULL AND value <> '' "
                    f"AND {scope} ORDER BY k COLLATE NOCASE", params).fetchall()
            return [row["k"] for row in rows]
        except sqlite3.OperationalError:
            seen = set()
            fallback_scope, fallback_params = self._scope(
                content_types, paper_only)
            with self._lock:
                rows = self.conn.execute(
                    "SELECT keywords FROM cards "
                    f"WHERE keywords IS NOT NULL AND {fallback_scope}",
                    fallback_params).fetchall()
            for row in rows:
                try:
                    for value in json.loads(row["keywords"] or "[]"):
                        if value:
                            seen.add(value)
                except (ValueError, TypeError):
                    pass
            return sorted(seen, key=str.casefold)

    def keyword_catalog(self, content_types=None, paper_only=False):
        """Observed card keywords intersected with authoritative catalogs."""
        catalog_entries = {}
        for authority in MECHANIC_AUTHORITIES:
            name, label = authority.name, authority.label
            for value in self.catalog(name):
                catalog_entries.setdefault(_type_key(value), (value, label))
        if not catalog_entries:
            return []
        observed_keys = {
            _type_key(value) for value in self.keywords(content_types, paper_only)
        }
        values = [
            catalog_entries[key] for key in observed_keys
            if key in catalog_entries
        ]
        return sorted(values, key=lambda item: (item[1].casefold(), item[0].casefold()))

    def _observed_legality_schema(self):
        """Return all observed legality keys and status values without judging them."""
        format_keys, statuses = set(), set()
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT legal.key AS format, legal.value AS status "
                    "FROM cards, json_each(COALESCE(cards.legalities, '{}')) AS legal "
                    "WHERE legal.key IS NOT NULL AND legal.key <> ''").fetchall()
            for row in rows:
                format_keys.add(str(row["format"]))
                if row["status"] not in (None, ""):
                    statuses.add(str(row["status"]))
        except sqlite3.OperationalError:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT legalities FROM cards WHERE legalities IS NOT NULL").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["legalities"] or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                for key, status in payload.items():
                    if str(key):
                        format_keys.add(str(key))
                    if status not in (None, ""):
                        statuses.add(str(status))
        return sorted(format_keys, key=str.casefold), sorted(statuses, key=str.casefold)

    @staticmethod
    def _cover_subtype_words(text, candidate_sequences):
        """Return normalized words not covered by any trusted subtype phrase."""
        words = tuple(" ".join(_type_key(text).split()).split())
        if not words:
            return ()
        covered = [False] * len(words)
        for candidate in candidate_sequences:
            width = len(candidate)
            if not width:
                continue
            for index in range(0, len(words) - width + 1):
                if words[index:index + width] == candidate:
                    for position in range(index, index + width):
                        covered[position] = True
        return tuple(word for word, is_covered in zip(words, covered) if not is_covered)

    def _subtype_authority_gaps(self):
        """Detect subtype-bearing type lines not fully covered by known authorities.

        Uncovered text is diagnostic evidence only; it is never promoted into
        picker vocabulary. This lets a new subtype family remain importable while
        making the authority gap observable.
        """
        candidates = []
        for authority in SUBTYPE_AUTHORITIES:
            for value in self.catalog(authority.name):
                sequence = tuple(" ".join(_type_key(value).split()).split())
                if sequence:
                    candidates.append(sequence)
        candidates = tuple(sorted(set(candidates), key=lambda item: (-len(item), item)))
        card_types = self.catalog("card-types")
        gaps, examples = set(), []
        for type_line in self._type_lines():
            for left, right in _type_line_faces(type_line):
                _words, subtype_text = _semantic_type_face_parts(left, right)
                if not subtype_text:
                    continue
                uncovered = self._cover_subtype_words(subtype_text, candidates)
                if not uncovered:
                    continue
                affected = [value for value in card_types if _card_has_type(left, value)]
                for value in affected:
                    gaps.add(value)
                if affected and len(examples) < 25:
                    examples.append({
                        "card_types": affected,
                        "type_line": str(type_line),
                        "uncovered_words": list(uncovered),
                    })
        return sorted(gaps, key=str.casefold), examples

    def _mechanic_authority_gaps(self):
        trusted = set()
        for authority in MECHANIC_AUTHORITIES:
            trusted.update(_type_key(value) for value in self.catalog(authority.name))
        if not trusted:
            return []
        return sorted(
            (value for value in self.keywords() if _type_key(value) not in trusted),
            key=str.casefold)

    def upstream_compatibility_snapshot(self):
        """Return an internal snapshot of upstream enum/taxonomy structure.

        This snapshot is diagnostic only. Dynamic values such as new Set Types,
        Formats, or Rarities remain accepted; the snapshot merely makes changes
        observable between database refreshes.
        """
        with self._lock:
            layout_rows = self.conn.execute(
                "SELECT DISTINCT layout FROM cards WHERE layout IS NOT NULL AND layout <> ''"
            ).fetchall()
        layouts = sorted({str(row["layout"]) for row in layout_rows}, key=str.casefold)
        format_keys, legality_statuses = self._observed_legality_schema()
        subtype_gaps, subtype_examples = self._subtype_authority_gaps()
        mechanic_gaps = self._mechanic_authority_gaps()
        snapshot = {
            "version": 1,
            "layouts": layouts,
            "layout_classes": {
                value: _card_content_classification(value, "") for value in layouts
            },
            "legality_statuses": legality_statuses,
            "format_keys": format_keys,
            "set_types": [value for value, _count in self.set_types()],
            "rarities": self.rarities(),
            "card_types": self.card_types(),
            "supertypes": self.supertypes(),
            "recognized_catalogs": sorted(SCRYFALL_CATALOGS),
            "subtype_authority_gaps": subtype_gaps,
            "subtype_gap_examples": subtype_examples,
            "mechanic_authority_gaps": mechanic_gaps[:100],
        }
        canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        snapshot["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return snapshot

    @staticmethod
    def _snapshot_list(snapshot, key):
        values = snapshot.get(key, []) if isinstance(snapshot, dict) else []
        return {str(value) for value in values if str(value)}

    def refresh_compatibility_diagnostics(self):
        """Persist a latest upstream fingerprint/report without affecting behavior."""
        previous_raw = self.get_meta(UPSTREAM_FINGERPRINT_META_KEY, "")
        try:
            previous = json.loads(previous_raw) if previous_raw else {}
        except (TypeError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        current = self.upstream_compatibility_snapshot()
        change_keys = (
            "layouts", "legality_statuses", "format_keys", "set_types", "rarities",
            "card_types", "supertypes", "subtype_authority_gaps",
            "mechanic_authority_gaps",
        )
        changes = {
            key: sorted(
                self._snapshot_list(current, key) - self._snapshot_list(previous, key),
                key=str.casefold)
            for key in change_keys
        }
        unknown_layouts = sorted(
            (value for value, classification in current["layout_classes"].items()
             if classification == "unknown"), key=str.casefold)
        unknown_legality = sorted(
            self._snapshot_list(current, "legality_statuses") - KNOWN_LEGALITY_STATUSES,
            key=str.casefold)
        report = {
            "version": 1,
            "fingerprint": current["fingerprint"],
            "previous_fingerprint": previous.get("fingerprint", ""),
            "unknown_layouts": unknown_layouts,
            "unknown_legality_statuses": unknown_legality,
            "subtype_authority_gaps": current["subtype_authority_gaps"],
            "mechanic_authority_gaps": current["mechanic_authority_gaps"],
            "new_values": changes,
            "needs_attention": bool(
                unknown_layouts or unknown_legality
                or (previous and (
                    changes["subtype_authority_gaps"]
                    or changes["mechanic_authority_gaps"]))),
        }
        self.set_meta_many({
            UPSTREAM_FINGERPRINT_META_KEY: json.dumps(current, ensure_ascii=False, sort_keys=True),
            UPSTREAM_REPORT_META_KEY: json.dumps(report, ensure_ascii=False, sort_keys=True),
        })
        return report

    def upstream_compatibility_report(self):
        """Return the last persisted internal compatibility report."""
        raw = self.get_meta(UPSTREAM_REPORT_META_KEY, "")
        try:
            value = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            value = {}
        return value if isinstance(value, dict) else {}

