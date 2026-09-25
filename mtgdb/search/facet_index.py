"""In-memory bitset facet index for near-real-time contextual Search.

The index precomputes, once per catalog snapshot, a bitset per facet value: bit
``i`` is set when card ``i`` satisfies that value's stored-column predicate.  A
criteria's result set is then the intersection (bitwise AND) of its active
filters' bitsets, and per-value contextual counts are popcounts of that
intersection AND the candidate value -- microseconds instead of a per-bucket
SQLite rescan.

The bitsets mirror ``SearchQueryBuilder`` exactly.  Free-text name/rules search
is represented by scanning the stored name / normalized-oracle corpora once per
query into a bitset (see ``_free_text_bitset``); the only filter a bitset cannot
reproduce is a mana-symbol *minimum* count (its hybrid-counts-once rule is
per-query), which makes :meth:`filter_bitset` return ``None`` so the caller
falls back to the canonical SQLite worker.  Everything else -- format legality,
numeric ranges (mana value, power, toughness, loyalty, defense), default
mana-symbol color presence, and free text -- is represented, so the common
deck-building filters recompute in tens of milliseconds instead of a
multi-second SQLite rescan.  Coverage is verified byte-identical against
``count_search`` before use.
"""

from __future__ import annotations

import math
import re

from mtgdb.database.constants import (
    ART_LAYOUTS, COLORS, PLAYABLE_LEGALITY_STATUSES)
from mtgdb.database.semantics import (
    _card_content_kind, _cast_real, _glob_numeric, _mana_cost_symbol_colors,
    _normalize_rules_text, _type_line_search_parts,
    mana_cost_has_hybrid_symbol, mana_cost_has_phyrexian_symbol,
    pip_minimum_threshold,
)
from mtgdb.search.context import (
    _CONTENT_KEYS, _GAME_KEYS, _LEGACY_TRAIT_KEYS, _MANA_FEATURE_KEYS,
    _PIP_KEYS, _SPECIAL_PROPERTY_KEYS, _STATUS_PROPERTY_KEYS,
    _catalog_values, _comma_members, _finite,
    _has_meaningful_mana_cost, _json_object, _keyword_values, _relaxed,
    _trait_keys,
)

_NUMERIC_FIELDS = ("cmc", "power", "toughness", "loyalty", "defense")

# Every numeric SearchCriteria field.  The SQL builder rejects a non-finite value
# in any of them (SRCH-015), so the index declines those requests rather than
# answering (or, for an infinite release year, raising) on its own.
_NUMERIC_CRITERIA = (
    "pip_min", "cmc_min", "cmc_max", "power_min", "power_max",
    "toughness_min", "toughness_max", "loyalty_min", "loyalty_max",
    "defense_min", "defense_max", "released_from", "released_to",
)

_ART_LAYOUT_KEYS = frozenset(str(v).casefold() for v in ART_LAYOUTS)
_PRODUCED_MEMBERS = (*COLORS, "C")
_GAME_PLATFORMS = ("paper", "mtgo", "arena")

# SQLite's built-in LIKE / COLLATE NOCASE fold only ASCII A-Z, so the ``name``
# match must fold the same way (oracle_text_search is already casefolded at
# import, so plain substring matching mirrors its LIKE exactly).
_ASCII_LOWER = str.maketrans({c: c + 32 for c in range(0x41, 0x5B)})
_TEXT_QUOTES = {'"', "“", "”"}


def _type_key(value):
    return str(value or "").replace("’", "'").replace("‘", "'").casefold()


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
    mana = str(row.get("mana_cost") or "")
    if mana_cost_has_hybrid_symbol(mana):
        keys.add("hybrid_mana")
    if mana_cost_has_phyrexian_symbol(mana):
        keys.add("phyrexian_mana")
    if "{X}" in mana.upper():
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
        "name", "oracle_text_search",
    )

    def __init__(self, rows):
        rows = list(rows)
        self.n = len(rows)
        self.universe = (1 << self.n) - 1
        # Small bounded cache of computed free-text bitsets, keyed by the
        # name/rules criteria: while a text chip is active every other filter
        # pick re-enters the fast path, and the text scan is by far its most
        # expensive fragment, so reusing it across picks keeps picks instant.
        self._text_cache = {}
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
        subtype_word = _Bitsets(n)       # whitespace tokens: subtype filter + count
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
        # Free-text search corpora scanned per query.  Names are ASCII-folded to
        # mirror LIKE/COLLATE NOCASE; oracle_text_search is already normalized.
        self._name_lower = [""] * n
        self._oracle_search = [""] * n

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
            self._name_lower[i] = str(row.get("name") or "").translate(_ASCII_LOWER)
            self._oracle_search[i] = str(row.get("oracle_text_search") or "")
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
            sub_words = set()
            for text in subtype_texts:
                # Whitespace tokens match both the subtype filter (the
                # whitespace-bounded CARD_HAS_SUBTYPE) and the predictive count.
                sub_words.update(text.split())
            for word in sub_words:
                subtype_word.set(word, i)

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
        # Separate value bitsets for the numeric *filter*: the range filter in
        # SearchQueryBuilder gates stats with the SQL GLOB guard and reads
        # CAST(... AS REAL), which differs from the float() the count side uses
        # for a handful of real values (e.g. power "+1").  Keeping a distinct set
        # lets filter_bitset match count_search exactly while the counts keep
        # matching the SQLite worker.
        numeric_filter = {field: _Bitsets(n) for field in _NUMERIC_FIELDS}
        meaningful = bytearray(nbytes)
        pip_repr = {color: bytearray(nbytes) for color in _PIP_KEYS}
        legality = {}   # fmt -> state -> bytearray

        def mark(buf, i):
            buf[i >> 3] |= 1 << (i & 7)

        for i, row in enumerate(rows):
            cmc_value = None
            for field in _NUMERIC_FIELDS:
                value = _finite(row.get(field))
                if value is not None:
                    numeric[field].set(value, i)
                    mark(numeric_finite[field], i)
                if field == "cmc":
                    # cmc is a REAL column; SQL filters it directly, so float()
                    # matches CAST here.
                    cmc_value = value
                    if value is not None:
                        numeric_filter[field].set(value, i)
                else:
                    raw = row.get(field)
                    if _glob_numeric(raw):
                        numeric_filter[field].set(_cast_real(raw), i)
            if cmc_value is not None and _has_meaningful_mana_cost(row):
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
        self.numeric_filter = {f: bs.finish() for f, bs in numeric_filter.items()}
        self.numeric_filter_sorted = {
            f: tuple(sorted(values)) for f, values in self.numeric_filter.items()}
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
        """True when every active filter has a bitset predicate.

        Free-text name/rules search is represented by scanning the stored name /
        oracle corpora once per query into a bitset (see ``_free_text_bitset``),
        so it no longer forces the SQLite fallback.  Only a mana-symbol *minimum*
        above one (its hybrid-counts-once rule is per-query) still falls back;
        everything else -- format legality, numeric ranges, mana-symbol color
        presence -- is represented.

        The Minimum box always holds a value: its untouched default is ``1``,
        which the SQL builder and the worker both read as plain colour presence
        (see ``pip_minimum_threshold``).  Treating any non-None value as an
        explicit minimum sent every request the real UI ever made down the slow
        SQLite path, so the index was built and never used.
        """
        for name in _NUMERIC_CRITERIA:
            value = getattr(q, name, None)
            if value in (None, ""):
                continue
            try:
                finite = math.isfinite(float(value))
            except (TypeError, ValueError):
                finite = False
            if not finite:
                # The SQL builder rejects it (SRCH-015), so the index must not
                # answer a request the search would refuse.
                return False
        return pip_minimum_threshold(q.pip_min) == 1

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
            "free_text": self._free_text_bitset(q),
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
            "format": self._format_filter(q.fmt, q.fmt_status),
            "pips": self._pip_filter(q.pips, q.pip_mode),
            "cmc": self._numeric_range_bitset("cmc", q.cmc_min, q.cmc_max),
            "power": self._numeric_range_bitset(
                "power", q.power_min, q.power_max),
            "toughness": self._numeric_range_bitset(
                "toughness", q.toughness_min, q.toughness_max),
            "loyalty": self._numeric_range_bitset(
                "loyalty", q.loyalty_min, q.loyalty_max),
            "defense": self._numeric_range_bitset(
                "defense", q.defense_min, q.defense_max),
        }

    def _free_text_bitset(self, q):
        """Bitset for the name/names/rules-text criteria (universe if none).

        Mirrors ``SearchQueryBuilder.add_name_and_rules`` exactly: exact
        ``names`` (case-insensitive equality) take precedence over a ``name``
        substring, ANDed with the rules-text clause.  Cached per criteria so
        successive picks with the same text reuse the scan.
        """
        if not q.name and not q.names and not q.text:
            return self.universe
        key = (str(q.name or ""), tuple(q.names or ()),
               tuple(q.text or ()), str(q.text_mode or ""))
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached
        value = (self._name_terms_bitset(q.name, q.names)
                 & self._text_bitset(q.text, q.text_mode))
        self._text_cache[key] = value
        if len(self._text_cache) > 16:
            self._text_cache.pop(next(iter(self._text_cache)))
        return value

    def _name_terms_bitset(self, name, names):
        """Mirror the name clause: exact ``names`` else a ``name`` substring."""
        exact = []
        seen = set()
        for value in names or ():
            value = str(value or "").strip()
            fold = value.casefold()
            if value and fold not in seen:
                seen.add(fold)
                exact.append(value.translate(_ASCII_LOWER))
        if exact:
            wanted = set(exact)
            result = 0
            for i, lowered in enumerate(self._name_lower):
                if lowered in wanted:
                    result |= 1 << i
            return result
        if name:
            needle = str(name).translate(_ASCII_LOWER)
            result = 0
            for i, lowered in enumerate(self._name_lower):
                if needle in lowered:
                    result |= 1 << i
            return result
        return self.universe

    def _text_bitset(self, text, text_mode):
        """Mirror the rules-text clause over the normalized oracle corpus.

        Each chip is a quoted contiguous phrase or an AND of normalized words;
        chips combine by OR for any/none and AND for all, with none negating.
        """
        raw_terms = ([text] if isinstance(text, str)
                     else [str(term).strip() for term in (text or [])
                           if str(term).strip()])
        groups = []
        for raw in raw_terms:
            raw = str(raw or "").strip()
            if not raw:
                continue
            quoted = (len(raw) >= 2 and raw[0] in _TEXT_QUOTES
                      and raw[-1] in _TEXT_QUOTES)
            if quoted:
                phrase = _normalize_rules_text(raw[1:-1])
                if not phrase:
                    continue
                bitset = 0
                for i, corpus in enumerate(self._oracle_search):
                    if phrase in corpus:
                        bitset |= 1 << i
                groups.append(bitset)
                continue
            words = [word for word in _normalize_rules_text(raw).split() if word]
            if not words:
                continue
            bitset = 0
            for i, corpus in enumerate(self._oracle_search):
                if all(word in corpus for word in words):
                    bitset |= 1 << i
            groups.append(bitset)
        if not groups:
            return self.universe
        mode = str(text_mode).casefold()
        if mode in ("any", "none"):
            result = 0
            for group in groups:
                result |= group
        else:
            result = self.universe
            for group in groups:
                result &= group
        if mode == "none":
            result = self.universe & ~result
        return result

    def _format_filter(self, fmt, status):
        """Mirror SearchQueryBuilder.add_rarity_and_format's legality clause."""
        if not fmt:
            return self.universe
        states = self.legality.get(str(fmt).strip(), {})
        chosen = str(status or "playable").casefold()
        if chosen == "banned":
            wanted = ("banned",)
        elif chosen == "restricted":
            wanted = ("restricted",)
        else:
            wanted = tuple(PLAYABLE_LEGALITY_STATUSES)
        result = 0
        for state in wanted:
            result |= states.get(state, 0)
        return result

    def _pip_filter(self, pips, mode):
        """Mirror add_pip_filters at the default minimum (color presence).

        The SQL applies a per-color presence prefilter plus a minimum-count
        helper; at the default minimum of 1 that reduces to color presence, so
        this ANDs/ORs the per-color pip-presence bitsets by Match mode.  An
        explicit minimum is rejected by ``representable`` and never reaches here.
        """
        selected = sorted({
            str(value).strip().upper() for value in (pips or [])
            if str(value).strip().upper() in (*COLORS, "C")})
        if not selected:
            return self.universe
        normalized = str(mode or "all").casefold()
        if normalized not in {"any", "all", "none"}:
            normalized = "all"
        if normalized == "any":
            result = 0
            for color in selected:
                result |= self.pip_repr.get(color, 0)
            return result
        if normalized == "none":
            result = self.universe
            for color in selected:
                result &= self.universe ^ self.pip_repr.get(color, 0)
            return result
        result = self.universe
        for color in selected:
            result &= self.pip_repr.get(color, 0)
        return result

    def _numeric_range_bitset(self, field, low, high):
        """Mirror the SQL numeric range (GLOB/CAST for stats, REAL for cmc)."""
        if low is None and high is None:
            return self.universe
        bitsets = self.numeric_filter[field]
        result = 0
        for value in self.numeric_filter_sorted[field]:
            if (low is None or value >= low) and (high is None or value <= high):
                result |= bitsets[value]
        return result

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
        popcounts of the relevant relaxed bitset.  Format legality, numeric
        ranges, and default mana-symbol presence are represented (their fragments
        are relaxed per facet); only an explicit mana-symbol minimum and
        free-text still force a ``None`` fallback.
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
            self._subtype_bitset, vocab["subtypes"])
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
        out["pip_counts"] = self._pip_counts(
            relaxed("pips"), q.pips, q.pip_mode)
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

    def _pip_counts(self, relaxed, selected, pip_mode):
        """Predict adding one mana-symbol color, mirroring ``_predict_pips``.

        Only reached at the default minimum (threshold 1), where the total-symbol
        count check reduces to color presence, so each candidate's count is the
        popcount of the relaxed set under the mode applied to
        ``selected + {candidate}``.  Ignoring ``selected`` (as an earlier version
        did) only matched when nothing was selected.
        """
        normalized = str(pip_mode or "all").casefold()
        if normalized not in {"any", "all", "none"}:
            normalized = "all"
        sel = [c for c in (selected or ()) if c in _PIP_KEYS]
        result = {}
        for candidate in _PIP_KEYS:
            wanted = list(dict.fromkeys([*sel, candidate]))
            if normalized == "none":
                bucket = relaxed
                for color in wanted:
                    bucket &= self.universe ^ self.pip_repr.get(color, 0)
            elif normalized == "any":
                # Self-excluding union: a candidate's own compatible population.
                bucket = relaxed & self.pip_repr.get(candidate, 0)
            else:  # all
                bucket = relaxed
                for color in wanted:
                    bucket &= self.pip_repr.get(color, 0)
            result[candidate] = self.popcount(bucket)
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
        """Whitespace-word subtype membership for both the filter and the count.

        Matches the whitespace-bounded CARD_HAS_SUBTYPE and the predictive trie:
        a value's words must appear as a contiguous run of whitespace tokens, so
        "Urza" does not match "Urza's Saga".
        """
        key = tuple(_type_key(value).split())
        if not key:
            return 0
        if len(key) == 1:
            return self.subtype_word.get(key[0], 0)
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
