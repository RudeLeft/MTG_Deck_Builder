"""In-memory bitset facet index for near-real-time contextual Search.

The index precomputes, once per catalog snapshot, a bitset per facet value: bit
``i`` is set when card ``i`` satisfies that value's stored-column predicate.  A
criteria's result set is then the intersection (bitwise AND) of its active
filters' bitsets, and per-value contextual counts are popcounts of that
intersection AND the candidate value -- microseconds instead of a per-bucket
SQLite rescan.

The bitsets mirror ``SearchQueryBuilder`` exactly; anything a bitset cannot yet
reproduce (free-text name/rules/type-line search, mana-symbol minimums, format
legality, numeric ranges) makes :meth:`filter_bitset` return ``None`` so the
caller falls back to the canonical SQLite worker.  Coverage is verified
byte-identical against ``count_search`` before use.
"""

from __future__ import annotations

import re

from mtgdb.database.constants import ART_LAYOUTS, COLORS
from mtgdb.database.semantics import (
    _card_content_kind, _mana_cost_symbol_colors, _type_line_search_parts,
)
from mtgdb.search.context import (
    _CONTENT_KEYS, _GAME_KEYS, _LEGACY_TRAIT_KEYS, _MANA_FEATURE_KEYS,
    _PIP_KEYS, _SPECIAL_PROPERTY_KEYS, _STATUS_PROPERTY_KEYS,
    _catalog_values, _comma_members, _finite, _has_meaningful_mana_cost,
    _json_object, _keyword_values, _relaxed, _trait_keys,
)

_NUMERIC_FIELDS = ("cmc", "power", "toughness", "loyalty", "defense")

_ART_LAYOUT_KEYS = frozenset(str(v).casefold() for v in ART_LAYOUTS)
_PRODUCED_MEMBERS = (*COLORS, "C")
_GAME_PLATFORMS = ("paper", "mtgo", "arena")


def _type_key(value):
    return str(value or "").replace("’", "'").replace("‘", "'").casefold()


_NON_NUMERIC = re.compile(r"[^0-9.\-]")
_NUMERIC_PREFIX = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _glob_numeric(value):
    """True when a TEXT stat passes the search filter's GLOB numeric guard."""
    text = "" if value is None else str(value)
    return text != "" and _NON_NUMERIC.search(text) is None


def _cast_real(text):
    """Approximate SQLite ``CAST(x AS REAL)`` for a GLOB-numeric stored value."""
    match = _NUMERIC_PREFIX.match(str(text or "").strip())
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _trait_filter_keys(row):
    """Per-card property keys under SearchQueryBuilder.TRAIT_CLAUSES semantics.

    Distinct from context.py ``_trait_keys`` (which drives predictive counts):
    the *filter* clauses use SQL GLOB/CAST and LIKE, so top_heavy and
    color_indicator classify a few exotic rows differently.  This mirrors the
    clauses exactly so the bitset result set matches ``count_search``.
    """
    keys = set()
    keys.add("universes_beyond" if bool(row.get("universes_beyond"))
             else "not_universes_beyond")
    if bool(row.get("reserved")):
        keys.add("reserved")
    if bool(row.get("game_changer")):
        keys.add("game_changer")
    faces = row.get("card_faces")
    has_faces = faces is not None and str(faces) not in ("", "[]", "null")
    keys.add("multi_faced" if has_faces else "single_faced")
    mana = str(row.get("mana_cost") or "").upper()   # LIKE is case-insensitive
    if "/" in mana and "/P" not in mana:
        keys.add("hybrid_mana")
    if "/P" in mana:
        keys.add("phyrexian_mana")
    if "{X}" in mana:
        keys.add("has_x_cost")
    indicator = row.get("color_indicator")
    if indicator is not None and str(indicator) != "":
        keys.add("color_indicator")
    power = str(row.get("power") or "")
    toughness = str(row.get("toughness") or "")
    if "*" in power or "*" in toughness:
        keys.add("variable_stats")
    if (_glob_numeric(power) and _glob_numeric(toughness)
            and _cast_real(power) > _cast_real(toughness)):
        keys.add("top_heavy")
    return keys


class _Bitsets:
    """Accumulate per-value bitsets over ``n`` cards via byte buffers."""

    def __init__(self, n):
        self._nbytes = (n + 7) // 8
        self._buffers = {}

    def set(self, value, index):
        buffer = self._buffers.get(value)
        if buffer is None:
            buffer = self._buffers[value] = bytearray(self._nbytes)
        buffer[index >> 3] |= 1 << (index & 7)

    def finish(self):
        return {value: int.from_bytes(bytes(buffer), "little")
                for value, buffer in self._buffers.items()}


class FacetIndex:
    """Bitset predicates over a fixed card population for one catalog snapshot.

    Build it with rows projected over ``INDEX_COLUMNS``.
    """

    # Columns the bitset predicates read.  A superset of CONTEXT_COLUMNS plus
    # ``paper`` (the paper_only filter) -- ``id`` is not needed (counts only).
    INDEX_COLUMNS = (
        "type_line", "keywords", "rarity", "layout", "released_at", "mana_cost",
        "produced_mana", "colors", "color_identity", "color_indicator",
        "reserved", "game_changer", "universes_beyond", "card_faces",
        "power", "toughness", "cmc", "loyalty", "defense", "legalities",
        "set_code", "set_type", "games", "lang", "paper",
    )

    def __init__(self, rows):
        rows = list(rows)
        self.n = len(rows)
        self.universe = (1 << self.n) - 1
        self._build(rows)
        self._build_extra(rows)

    def _build(self, rows):
        n = self.n
        nbytes = (n + 7) // 8
        content = _Bitsets(n)
        lang = _Bitsets(n)
        games = _Bitsets(n)
        rarity = _Bitsets(n)
        set_type = _Bitsets(n)
        set_code = _Bitsets(n)
        layout = _Bitsets(n)
        card_type = _Bitsets(n)
        subtype_word = _Bitsets(n)       # [\w-] tokens: CARD_HAS_SUBTYPE filter
        subtype_ctx_word = _Bitsets(n)   # whitespace tokens: predictive trie count
        keyword = _Bitsets(n)
        trait = _Bitsets(n)            # context predictive semantics (_trait_keys)
        trait_filter = _Bitsets(n)     # search-filter semantics (TRAIT_CLAUSES)
        release_year = _Bitsets(n)
        colors = _Bitsets(n)
        identity = _Bitsets(n)
        produced = _Bitsets(n)
        art_layout = bytearray(nbytes)
        paper = bytearray(nbytes)
        colors_empty = bytearray(nbytes)
        identity_empty = bytearray(nbytes)
        produced_empty = bytearray(nbytes)

        self._type_left_words = [()] * n     # tuple of word-tuples per face
        self._subtype_texts = [()] * n       # normalized subtype strings
        self._released = [""] * n

        def mark(buf, i):
            buf[i >> 3] |= 1 << (i & 7)

        def mark_colors(value, store, empty_buf, i):
            members = _comma_members(value)
            if not members:
                mark(empty_buf, i)
                return
            for member in members:
                store.set(member, i)

        for i, row in enumerate(rows):
            layout_value = row.get("layout")
            layout_key = str(layout_value or "").casefold().strip()
            content.set(_card_content_kind(layout_value, row.get("type_line")), i)
            if layout_key in _ART_LAYOUT_KEYS:
                mark(art_layout, i)
            if row.get("paper"):
                mark(paper, i)
            lang.set(str(row.get("lang") or ""), i)
            games_text = str(row.get("games") or "")
            for platform in _GAME_PLATFORMS:
                if platform in games_text:
                    games.set(platform, i)
            if str(row.get("rarity") or ""):
                rarity.set(str(row.get("rarity")), i)
            if str(row.get("set_type") or ""):
                set_type.set(str(row.get("set_type")), i)
            if str(row.get("set_code") or ""):
                set_code.set(str(row.get("set_code")), i)
            layout.set(str(row.get("layout") or ""), i)
            released = str(row.get("released_at") or "")
            self._released[i] = released
            year = released[:4]
            if len(year) == 4 and year.isdigit():
                release_year.set(year, i)

            left_sequences, subtype_texts = _type_line_search_parts(
                str(row.get("type_line") or ""))
            self._type_left_words[i] = left_sequences
            self._subtype_texts[i] = subtype_texts
            for seq in left_sequences:
                for word in seq:
                    card_type.set(word, i)
            filter_words = set()
            ctx_words = set()
            for text in subtype_texts:
                # Filter (CARD_HAS_SUBTYPE) tokenizes on the [\w-] regex boundary,
                # so "urza's saga" yields "urza"; the predictive trie count splits
                # on whitespace instead ("urza's", "saga").  They differ on
                # apostrophe subtypes, so keep both.
                filter_words.update(w for w in re.split(r"[^\w-]+", text) if w)
                ctx_words.update(text.split())
            for word in filter_words:
                subtype_word.set(word, i)
            for word in ctx_words:
                subtype_ctx_word.set(word, i)

            for value in _keyword_values(row.get("keywords")):
                keyword.set(value.casefold(), i)
            for key in _trait_keys(row):
                trait.set(key, i)
            for key in _trait_filter_keys(row):
                trait_filter.set(key, i)

            mark_colors(row.get("colors"), colors, colors_empty, i)
            mark_colors(row.get("color_identity"), identity, identity_empty, i)
            mark_colors(row.get("produced_mana"), produced, produced_empty, i)

        as_int = lambda buf: int.from_bytes(bytes(buf), "little")
        self.content = content.finish()
        self.lang = lang.finish()
        self.games = games.finish()
        self.rarity = rarity.finish()
        self.set_type = set_type.finish()
        self.set_code = set_code.finish()
        self.layout = layout.finish()
        self.card_type = card_type.finish()
        self.subtype_word = subtype_word.finish()
        self.subtype_ctx_word = subtype_ctx_word.finish()
        self.keyword = keyword.finish()
        self.trait = trait.finish()
        self.trait_filter = trait_filter.finish()
        self.release_year = release_year.finish()
        self.colors_has = colors.finish()
        self.identity_has = identity.finish()
        self.produced_has = produced.finish()
        self.art_layout = as_int(art_layout)
        self.paper = as_int(paper)
        self.colors_empty = as_int(colors_empty)
        self.identity_empty = as_int(identity_empty)
        self.produced_empty = as_int(produced_empty)

    def _build_extra(self, rows):
        """Numeric value bitsets, meaningful-mana, pip presence, and legality."""
        n = self.n
        nbytes = (n + 7) // 8
        numeric = {field: _Bitsets(n) for field in _NUMERIC_FIELDS}
        numeric_finite = {field: bytearray(nbytes) for field in _NUMERIC_FIELDS}
        meaningful = bytearray(nbytes)
        pip_repr = {color: bytearray(nbytes) for color in _PIP_KEYS}
        legality = {}   # fmt -> state -> bytearray

        def mark(buf, i):
            buf[i >> 3] |= 1 << (i & 7)

        for i, row in enumerate(rows):
            for field in _NUMERIC_FIELDS:
                value = _finite(row.get(field))
                if value is not None:
                    numeric[field].set(value, i)
                    mark(numeric_finite[field], i)
            if _finite(row.get("cmc")) is not None and _has_meaningful_mana_cost(row):
                mark(meaningful, i)
            represented = set()
            for symbol in _mana_cost_symbol_colors(str(row.get("mana_cost") or "")):
                represented |= set(symbol)
            for color in represented:
                if color in pip_repr:
                    mark(pip_repr[color], i)
            for fmt, state in _json_object(row.get("legalities")).items():
                fmt = str(fmt)
                key = str(state or "").casefold()
                states = legality.setdefault(fmt, {})
                buf = states.get(key)
                if buf is None:
                    buf = states[key] = bytearray(nbytes)
                mark(buf, i)

        as_int = lambda buf: int.from_bytes(bytes(buf), "little")
        self.numeric = {f: bs.finish() for f, bs in numeric.items()}
        self.numeric_sorted = {
            f: tuple(sorted(values)) for f, values in self.numeric.items()}
        self.numeric_finite = {f: as_int(buf) for f, buf in numeric_finite.items()}
        self.meaningful_mana = as_int(meaningful)
        self.pip_repr = {c: as_int(buf) for c, buf in pip_repr.items()}
        self.legality = {
            fmt: {state: as_int(buf) for state, buf in states.items()}
            for fmt, states in legality.items()}

    # -- public ----------------------------------------------------------
    @staticmethod
    def popcount(bitset):
        return int(bitset).bit_count()

    def representable(self, q):
        """True when every active filter has a bitset predicate."""
        if q.name or q.names or q.text:
            return False
        if q.pips or q.pip_min is not None:
            return False
        if q.fmt:
            return False
        for lo, hi in (
                (q.cmc_min, q.cmc_max), (q.power_min, q.power_max),
                (q.toughness_min, q.toughness_max),
                (q.loyalty_min, q.loyalty_max), (q.defense_min, q.defense_max)):
            if lo is not None or hi is not None:
                return False
        return True

    def _fragments(self, q):
        """Per-facet clause bitsets, or None if not bitset-representable.

        Keys match the facets ``_relaxed`` removes, so a facet's relaxed set is
        the AND of every fragment except that facet's own (games also drops the
        paper fragment, content drops the whole scope fragment).
        """
        if not self.representable(q):
            return None
        printing = self._printing_filter_split(q.set_types, q.set_codes, q.lang)
        if printing is None:
            return None
        set_type_bs, set_code_bs, lang_bs = printing
        return {
            "content": self._scope_bitset(q.content_types),
            "card_types": self._terms(q.card_types, q.card_type_mode, self._type_bitset),
            "supertypes": self._terms(q.supertypes, q.supertype_mode, self._type_bitset),
            "subtypes": self._terms(q.subtypes, q.subtype_mode, self._subtype_bitset),
            "keywords": self._terms(q.keywords, q.keyword_mode,
                                    lambda v: self.keyword.get(_type_key(v), 0)),
            "colors": self._color_filter(q.colors, q.color_mode, q.color_scope),
            "produces": self._produces_filter(q.produces, q.produces_mode),
            "traits": self._property_filter(q.traits, q.trait_mode),
            "mana_features": self._property_filter(q.mana_features, q.mana_feature_mode),
            "special_properties": self._property_filter(
                q.special_properties, q.special_property_mode),
            "status_properties": self._property_filter(
                q.status_properties, q.status_property_mode),
            "layouts": self._layout_filter(q.layouts, q.layout_mode),
            "released": self._release_filter(q.released_from, q.released_to),
            "games": self._games_filter(q.games),
            "paper": self.paper if q.paper_only else self.universe,
            "rarities": self._rarity_filter(q.rarities),
            "set_types": set_type_bs,
            "set_codes": set_code_bs,
            "lang": lang_bs,
        }

    # Facets that ``_relaxed`` drops together with their own fragment.
    _RELAX_EXTRA = {"games": ("paper",)}

    def filter_bitset(self, q):
        """Return the matching-card bitset, or None if not bitset-representable."""
        fragments = self._fragments(q)
        if fragments is None:
            return None
        result = self.universe
        for bitset in fragments.values():
            result &= bitset
        return result

    def _relaxed_bitset(self, fragments, facet):
        """AND of every fragment except the one(s) the facet relaxes."""
        drop = {facet, *self._RELAX_EXTRA.get(facet, ())}
        if facet == "content":
            drop = {"content"}
        result = self.universe
        for key, bitset in fragments.items():
            if key not in drop:
                result &= bitset
        return result

    def context_counts(self, q, vocabulary):
        """Return contextual counts for a representable criteria, else None.

        Mirrors the per-value counts the SQLite context worker emits, computed as
        popcounts of the relevant relaxed bitset.  Numeric ranges, mana-symbol
        (pip) and format counts are not produced here yet; the caller keeps the
        worker for those until they are added.
        """
        fragments = self._fragments(q)
        if fragments is None:
            return None
        base = self.universe
        for bitset in fragments.values():
            base &= bitset

        def relaxed(facet):
            return self._relaxed_bitset(fragments, facet)

        vocab = {key: _catalog_values(vocabulary.get(key)) for key in (
            "card_types", "supertypes", "subtypes", "keywords", "layouts",
            "rarities", "set_types")}
        set_codes_vocab = _catalog_values(vocabulary.get("sets"))

        out = {"result_count": self.popcount(base)}
        out["card_type_counts"] = self._predictive(
            relaxed("card_types"), q.card_types, q.card_type_mode,
            self._type_bitset, vocab["card_types"])
        out["supertype_counts"] = self._predictive(
            relaxed("supertypes"), q.supertypes, q.supertype_mode,
            self._type_bitset, vocab["supertypes"])
        out["subtype_counts"] = self._predictive(
            relaxed("subtypes"), q.subtypes, q.subtype_mode,
            self._subtype_context_bitset, vocab["subtypes"])
        out["keyword_counts"] = self._predictive(
            relaxed("keywords"), q.keywords, q.keyword_mode,
            lambda v: self.keyword.get(_type_key(v), 0), vocab["keywords"])
        field = "colors" if q.color_scope == "colors" else "color_identity"
        out["color_counts"] = self._color_counts(
            relaxed("colors"), field, q.colors, q.color_mode, produced=False)
        out["produces_counts"] = self._color_counts(
            relaxed("produces"), "produced", q.produces, q.produces_mode,
            produced=True)
        out["layout_counts"] = self._predictive(
            relaxed("layouts"), q.layouts, q.layout_mode,
            lambda v: self.layout.get(v, 0), vocab["layouts"])
        out["rarity_counts"] = self._predictive(
            relaxed("rarities"), q.rarities, "any",
            lambda v: self.rarity.get(v, 0), vocab["rarities"])
        out["set_type_counts"] = self._predictive(
            relaxed("set_types"), q.set_types or (), "any",
            lambda v: self.set_type.get(v, 0), vocab["set_types"])
        out["set_counts"] = self._predictive(
            relaxed("set_codes"), q.set_codes or (), "any",
            lambda v: self.set_code.get(v, 0), set_codes_vocab)
        out["trait_counts"] = self._predictive(
            relaxed("traits"), q.traits, q.trait_mode,
            lambda v: self.trait.get(v, 0), _LEGACY_TRAIT_KEYS)
        out["mana_feature_counts"] = self._predictive(
            relaxed("mana_features"), q.mana_features, q.mana_feature_mode,
            lambda v: self.trait.get(v, 0), _MANA_FEATURE_KEYS)
        out["special_property_counts"] = self._predictive(
            relaxed("special_properties"), q.special_properties,
            q.special_property_mode, lambda v: self.trait.get(v, 0),
            _SPECIAL_PROPERTY_KEYS)
        out["status_property_counts"] = self._predictive(
            relaxed("status_properties"), q.status_properties,
            q.status_property_mode, lambda v: self.trait.get(v, 0),
            _STATUS_PROPERTY_KEYS)
        rc = relaxed("content")
        out["content_counts"] = {
            k: self.popcount(rc & self.content.get(k, 0)) for k in _CONTENT_KEYS}
        rg = relaxed("games")
        out["game_counts"] = {
            k: self.popcount(rg & self.games.get(k, 0)) for k in _GAME_KEYS}
        rr = relaxed("released")
        year_counts = {}
        for year, bitset in self.release_year.items():
            count = self.popcount(rr & bitset)
            if count:
                year_counts[year] = count
        out["release_years"] = tuple(sorted(year_counts))
        out["release_year_counts"] = year_counts
        rl = relaxed("lang")
        out["all_language_count"] = self.popcount(rl)
        out["english_count"] = self.popcount(rl & self.lang.get("en", 0))
        out["numeric_ranges"] = {}
        out["numeric_applicability"] = {}
        for facet in _NUMERIC_FIELDS:
            bounds, applicable = self._numeric(relaxed(facet), facet)
            out["numeric_ranges"][facet] = bounds
            out["numeric_applicability"][facet] = applicable
        out["pip_counts"] = self._pip_counts(relaxed("pips"), q.pip_mode)
        out["format_counts"] = self._format_counts(
            relaxed("format"), _catalog_values(vocabulary.get("formats")),
            q.fmt_status)
        return out

    def _numeric(self, relaxed, field):
        present = relaxed & self.numeric_finite[field]
        if field == "cmc":
            applicable = self.popcount(present & self.meaningful_mana)
        else:
            applicable = self.popcount(present)
        if not present:
            return None, applicable
        values = self.numeric_sorted[field]
        value_bitsets = self.numeric[field]
        low = next(v for v in values if relaxed & value_bitsets[v])
        high = next(v for v in reversed(values) if relaxed & value_bitsets[v])
        return (low, high), applicable

    def _pip_counts(self, relaxed, pip_mode):
        normalized = str(pip_mode or "all").casefold()
        total = self.popcount(relaxed)
        result = {}
        for color in _PIP_KEYS:
            has = self.popcount(relaxed & self.pip_repr.get(color, 0))
            result[color] = (total - has) if normalized == "none" else has
        return result

    def _format_counts(self, relaxed, formats, status):
        chosen = str(status or "playable").casefold()
        result = {}
        for fmt in formats:
            states = self.legality.get(str(fmt), {})
            if chosen == "playable":
                bitset = states.get("legal", 0) | states.get("restricted", 0)
            else:
                bitset = states.get(chosen, 0)
            result[fmt] = self.popcount(relaxed & bitset)
        return result

    def _predictive(self, relaxed, selected, mode, value_fn, vocabulary):
        keys = tuple(dict.fromkeys(str(v) for v in vocabulary if str(v)))
        allowed = set(keys)
        sel = [str(v) for v in (selected or []) if str(v) in allowed]
        normalized = str(mode or "any").casefold()
        result = {}
        if normalized == "all":
            base = relaxed
            for value in sel:
                base &= value_fn(value)
            for key in keys:
                result[key] = self.popcount(base & value_fn(key))
        elif normalized == "none":
            no_sel = relaxed
            for value in sel:
                no_sel &= self.universe ^ value_fn(value)
            total = self.popcount(no_sel)
            for key in keys:
                result[key] = total - self.popcount(no_sel & value_fn(key))
        else:
            for key in keys:
                result[key] = self.popcount(relaxed & value_fn(key))
        return result

    def _color_counts(self, relaxed, field, selected, mode, *, produced):
        if produced:
            store, empty, members = (
                self.produced_has, self.produced_empty, _PRODUCED_MEMBERS)
        elif field == "colors":
            store, empty, members = self.colors_has, self.colors_empty, COLORS
        else:
            store, empty, members = self.identity_has, self.identity_empty, COLORS
        selected = set(selected)
        normalized = str(mode or "within").casefold()
        result = {}
        for candidate in (*COLORS, "C"):
            target = set(selected)
            target.add(candidate)
            match = self._color_match(
                relaxed, store, empty, members, target, normalized, produced)
            if normalized == "within":
                if candidate == "C" and not produced:
                    match &= empty
                else:
                    match &= store.get(candidate, 0)
            result[candidate] = self.popcount(match)
        return result

    def _color_match(self, relaxed, store, empty, members, target, mode, produced):
        if not target:
            return relaxed
        if not produced and target == {"C"}:
            return relaxed & empty
        real_target = {c for c in target if c != "C" or produced}
        if mode == "includes":
            result = relaxed
            for color in real_target:
                result &= store.get(color, 0)
            return result
        if mode == "exact":
            result = relaxed
            for color in members:
                has = store.get(color, 0)
                result &= has if color in real_target else (self.universe ^ has)
            return result
        # within: card members subset of real_target.
        result = relaxed
        for color in members:
            if color not in real_target:
                result &= self.universe ^ store.get(color, 0)
        if produced:
            result &= self.universe ^ empty
        return result

    # -- filter fragments (mirror SearchQueryBuilder) --------------------
    def _scope_bitset(self, content_types):
        if content_types is None:
            allowed = {"card", "token", "emblem"}
            exclude_art = True
        else:
            allowed = {str(v).casefold() for v in content_types}
            exclude_art = "art" not in allowed
        if not allowed:
            return 0
        result = 0
        for kind, bitset in self.content.items():
            if kind in allowed:
                result |= bitset
        if exclude_art:
            result &= self.universe ^ self.art_layout
        return result

    def _terms(self, values, mode, bitset_fn):
        clean = [str(v).strip() for v in (values or []) if str(v).strip()]
        if not clean:
            return self.universe
        normalized = str(mode).casefold()
        if normalized in ("any", "none"):
            combined = 0
            for value in clean:
                combined |= bitset_fn(value)
            return (self.universe ^ combined) if normalized == "none" else combined
        combined = self.universe
        for value in clean:
            combined &= bitset_fn(value)
        return combined

    def _type_bitset(self, value):
        key = tuple(_type_key(value).split())
        if not key:
            return 0
        if len(key) == 1:
            return self.card_type.get(key[0], 0)
        width = len(key)
        result = 0
        for i, sequences in enumerate(self._type_left_words):
            for seq in sequences:
                if any(seq[j:j + width] == key
                       for j in range(len(seq) - width + 1)):
                    result |= 1 << i
                    break
        return result

    def _subtype_bitset(self, value):
        target = " ".join(_type_key(value).split())
        if not target:
            return 0
        # Fast path only for a single boundary-clean token; anything with a
        # space or punctuation (e.g. "urza's saga") uses the boundary regex.
        if re.fullmatch(r"[\w-]+", target):
            return self.subtype_word.get(target, 0)
        pattern = re.compile(r"(?<![\w-])" + re.escape(target) + r"(?![\w-])")
        result = 0
        for i, texts in enumerate(self._subtype_texts):
            if any(pattern.search(text) for text in texts):
                result |= 1 << i
        return result

    def _subtype_context_bitset(self, value):
        """Predictive-count subtype membership: whitespace-word trie subsequence.

        Matches _predict_type_line (not CARD_HAS_SUBTYPE): the trie splits subtype
        text on whitespace, so "Urza" does not match "Urza's Saga" here even
        though the filter regex does.
        """
        key = tuple(_type_key(value).split())
        if not key:
            return 0
        if len(key) == 1:
            return self.subtype_ctx_word.get(key[0], 0)
        width = len(key)
        result = 0
        for i, texts in enumerate(self._subtype_texts):
            for text in texts:
                seq = tuple(text.split())
                if any(seq[j:j + width] == key
                       for j in range(len(seq) - width + 1)):
                    result |= 1 << i
                    break
        return result

    def _color_set(self, store, empty, selected, mode, members):
        selected = [c for c in selected if c in members]
        if mode == "includes":
            combined = self.universe
            for color in selected:
                combined &= store.get(color, 0)
            return combined
        if mode == "exact":
            combined = self.universe
            for color in members:
                has = store.get(color, 0)
                combined &= has if color in selected else (self.universe ^ has)
            return combined
        # within: no colour outside the selected set is present.
        combined = self.universe
        for color in members:
            if color not in selected:
                combined &= self.universe ^ store.get(color, 0)
        return combined

    def _color_filter(self, colors, mode, scope):
        store = self.colors_has if str(scope) == "colors" else self.identity_has
        empty = self.colors_empty if str(scope) == "colors" else self.identity_empty
        requested = [c for c in (colors or []) if c in _PRODUCED_MEMBERS]
        selected = [c for c in requested if c in COLORS]
        if selected:
            return self._color_set(store, empty, selected, mode, COLORS)
        if "C" in requested:
            return empty
        return self.universe

    def _produces_filter(self, produces, mode):
        selected = [c for c in (produces or []) if c in _PRODUCED_MEMBERS]
        if not selected:
            return self.universe
        result = self._color_set(
            self.produced_has, self.produced_empty, selected, mode,
            _PRODUCED_MEMBERS)
        if mode != "exact":
            result &= self.universe ^ self.produced_empty
        return result

    def _property_filter(self, properties, mode):
        keys = sorted({str(v) for v in (properties or [])})
        # Filter uses TRAIT_CLAUSES (SQL) semantics; context predictive counts
        # separately use _trait_keys via self.trait.
        fragments = [self.trait_filter.get(key, 0) for key in keys
                     if key in _TRAIT_KEYS]
        if not fragments:
            return self.universe
        normalized = str(mode).casefold()
        if normalized == "all":
            combined = self.universe
            for frag in fragments:
                combined &= frag
            return combined
        combined = 0
        for frag in fragments:
            combined |= frag
        if normalized == "none":
            return self.universe ^ combined
        return combined

    def _layout_filter(self, layouts, mode):
        clean = sorted({str(v).strip() for v in (layouts or []) if str(v).strip()})
        if not clean:
            return self.universe
        combined = 0
        for value in clean:
            combined |= self.layout.get(value, 0)
        return (self.universe ^ combined
                if str(mode).casefold() == "none" else combined)

    def _release_filter(self, released_from, released_to):
        result = self.universe
        for value, op in ((released_from, ">="), (released_to, "<=")):
            if value in (None, ""):
                continue
            try:
                year = int(float(value))
            except (TypeError, ValueError):
                continue
            boundary = f"{year:04d}-01-01" if op == ">=" else f"{year:04d}-12-31"
            keep = 0
            for i, released in enumerate(self._released):
                if released and (released >= boundary if op == ">="
                                 else released <= boundary):
                    keep |= 1 << i
            result &= keep
        return result

    def _games_filter(self, games):
        selected = [g for g in _GAME_PLATFORMS
                    if g in {str(v).casefold() for v in (games or ())}]
        if not selected or len(selected) == len(_GAME_PLATFORMS):
            return self.universe
        combined = 0
        for platform in selected:
            combined |= self.games.get(platform, 0)
        return combined

    def _rarity_filter(self, rarities):
        clean = sorted({str(v).strip() for v in (rarities or []) if str(v).strip()})
        if not clean:
            return self.universe
        combined = 0
        for value in clean:
            combined |= self.rarity.get(value, 0)
        return combined

    def _printing_filter_split(self, set_types, set_codes, lang):
        """Return (set_type, set_code, lang) fragment bitsets, or None if empty.

        ``add_printing_filters`` aborts the whole search (no results) when a
        non-None set_types/set_codes selection is empty; that maps to None here.
        """
        set_type_bs = self.universe
        if set_types is not None:
            chosen = sorted({str(v) for v in set_types if v})
            if not chosen:
                return None
            set_type_bs = 0
            for value in chosen:
                set_type_bs |= self.set_type.get(value, 0)
        set_code_bs = self.universe
        if set_codes is not None:
            chosen = sorted({str(v) for v in set_codes if v})
            if not chosen:
                return None
            set_code_bs = 0
            for value in chosen:
                set_code_bs |= self.set_code.get(value, 0)
        lang_bs = self.lang.get(str(lang), 0) if lang else self.universe
        return set_type_bs, set_code_bs, lang_bs


_TRAIT_KEYS = frozenset((
    "universes_beyond", "not_universes_beyond", "reserved", "game_changer",
    "multi_faced", "single_faced", "hybrid_mana", "phyrexian_mana",
    "has_x_cost", "color_indicator", "variable_stats", "top_heavy",
))
