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
    _card_content_kind, _type_line_search_parts,
)
from mtgdb.search.context import (
    _comma_members, _keyword_values, _trait_keys,
)

_ART_LAYOUT_KEYS = frozenset(str(v).casefold() for v in ART_LAYOUTS)
_PRODUCED_MEMBERS = (*COLORS, "C")
_GAME_PLATFORMS = ("paper", "mtgo", "arena")


def _type_key(value):
    return str(value or "").replace("’", "'").replace("‘", "'").casefold()


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
        "power", "toughness", "set_code", "set_type", "games", "lang", "paper",
    )

    def __init__(self, rows):
        rows = list(rows)
        self.n = len(rows)
        self.universe = (1 << self.n) - 1
        self._build(rows)

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
        subtype_word = _Bitsets(n)
        keyword = _Bitsets(n)
        trait = _Bitsets(n)
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
            self._released[i] = str(row.get("released_at") or "")

            left_sequences, subtype_texts = _type_line_search_parts(
                str(row.get("type_line") or ""))
            self._type_left_words[i] = left_sequences
            self._subtype_texts[i] = subtype_texts
            for seq in left_sequences:
                for word in seq:
                    card_type.set(word, i)
            sub_words = set()
            for text in subtype_texts:
                sub_words.update(text.split())
            for word in sub_words:
                subtype_word.set(word, i)

            for value in _keyword_values(row.get("keywords")):
                keyword.set(value.casefold(), i)
            for key in _trait_keys(row):
                trait.set(key, i)

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
        self.colors_has = colors.finish()
        self.identity_has = identity.finish()
        self.produced_has = produced.finish()
        self.art_layout = as_int(art_layout)
        self.paper = as_int(paper)
        self.colors_empty = as_int(colors_empty)
        self.identity_empty = as_int(identity_empty)
        self.produced_empty = as_int(produced_empty)

    # -- public ----------------------------------------------------------
    @staticmethod
    def popcount(bitset):
        return int(bitset).bit_count()

    def representable(self, q):
        """True when every active filter has a bitset predicate."""
        if q.name or q.names or q.text:
            return False
        if q.pips:
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

    def filter_bitset(self, q):
        """Return the matching-card bitset, or None if not bitset-representable."""
        if not self.representable(q):
            return None
        result = self._scope_bitset(q.content_types)
        if not result:
            return result
        result &= self._terms(q.card_types, q.card_type_mode, self._type_bitset)
        result &= self._terms(q.supertypes, q.supertype_mode, self._type_bitset)
        result &= self._terms(q.subtypes, q.subtype_mode, self._subtype_bitset)
        result &= self._terms(
            q.keywords, q.keyword_mode,
            lambda v: self.keyword.get(_type_key(v), 0))
        result &= self._color_filter(q.colors, q.color_mode, q.color_scope)
        result &= self._produces_filter(q.produces, q.produces_mode)
        result &= self._property_filter(q.traits, q.trait_mode)
        result &= self._property_filter(q.mana_features, q.mana_feature_mode)
        result &= self._property_filter(
            q.special_properties, q.special_property_mode)
        result &= self._property_filter(
            q.status_properties, q.status_property_mode)
        result &= self._layout_filter(q.layouts, q.layout_mode)
        result &= self._release_filter(q.released_from, q.released_to)
        result &= self._games_filter(q.games)
        result &= self._rarity_filter(q.rarities)
        printing = self._printing_filter(
            q.set_types, q.set_codes, q.lang, q.paper_only)
        if printing is None:
            return 0
        return result & printing

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
        if " " not in target:
            return self.subtype_word.get(target, 0)
        pattern = re.compile(r"(?<![\w-])" + re.escape(target) + r"(?![\w-])")
        result = 0
        for i, texts in enumerate(self._subtype_texts):
            if any(pattern.search(text) for text in texts):
                result |= 1 << i
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
        # A key with no clause is dropped exactly as the builder drops it.
        fragments = [self.trait.get(key, 0) for key in keys if key in _TRAIT_KEYS]
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

    def _printing_filter(self, set_types, set_codes, lang, paper_only):
        result = self.universe
        if set_types is not None:
            chosen = sorted({str(v) for v in set_types if v})
            if not chosen:
                return None
            combined = 0
            for value in chosen:
                combined |= self.set_type.get(value, 0)
            result &= combined
        if set_codes is not None:
            chosen = sorted({str(v) for v in set_codes if v})
            if not chosen:
                return None
            combined = 0
            for value in chosen:
                combined |= self.set_code.get(value, 0)
            result &= combined
        if lang:
            result &= self.lang.get(str(lang), 0)
        if paper_only:
            result &= self.paper
        return result


_TRAIT_KEYS = frozenset((
    "universes_beyond", "not_universes_beyond", "reserved", "game_changer",
    "multi_faced", "single_faced", "hybrid_mana", "phyrexian_mana",
    "has_x_cost", "color_indicator", "variable_stats", "top_heavy",
))
