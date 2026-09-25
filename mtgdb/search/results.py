"""Compact Search result storage and Tk-free asynchronous view preparation."""

from __future__ import annotations

from array import array
from collections import OrderedDict
from dataclasses import dataclass
import json
import logging
import queue
import re
import threading
import time

from mtgdb.core.background_jobs import spawn_daemon


log = logging.getLogger("mtg")

SEARCH_RESULT_FIELDS = (
    "id", "name", "mana_cost", "cmc", "type_line", "oracle_text", "colors",
    "power", "toughness", "rarity", "set_code", "set_name",
    "collector_number", "released_at", "keywords",
)
SEARCH_RESULT_FIELD_SET = frozenset(SEARCH_RESULT_FIELDS)
EXPENSIVE_DERIVED_FIELDS = frozenset(("ability", "colors"))


@dataclass(frozen=True, slots=True)
class SearchResultRow:
    """Compact immutable projection used by broad interactive Search results."""

    id: object = None
    name: object = None
    mana_cost: object = None
    cmc: object = None
    type_line: object = None
    oracle_text: object = None
    colors: object = None
    power: object = None
    toughness: object = None
    rarity: object = None
    set_code: object = None
    set_name: object = None
    collector_number: object = None
    released_at: object = None
    keywords: object = None

    @classmethod
    def from_mapping(cls, row):
        if isinstance(row, cls):
            return row
        get = row.get
        return cls(*(get(field) for field in SEARCH_RESULT_FIELDS))

    def get(self, key, default=None):
        if key in SEARCH_RESULT_FIELD_SET:
            value = getattr(self, key)
            return default if value is None and default is not None else value
        return default


class CompactResultSelection:
    """Exact-printing selection stored as a compact source-index bitset.

    The bitset is keyed by immutable SearchResultStore source indexes, so a very
    large Shift selection does not allocate one Python string/set entry per card
    and remains authoritative across sort/filter view-index swaps.
    """

    SPARSE_LIMIT = 4096

    __slots__ = ("_store", "_bits", "_count", "_sparse")

    def __init__(self, store=None):
        self._store = None
        self._bits = bytearray()
        self._count = 0
        self._sparse = set()
        if store is not None:
            self.bind_store(store)

    def bind_store(self, store):
        self._store = store
        self._bits = bytearray((int(store.logical_count) + 7) // 8)
        self._count = 0
        self._sparse = set()

    def __len__(self):
        return self._count

    def __bool__(self):
        return bool(self._count)

    def _source_for_id(self, card_id):
        store = self._store
        return store.source_index_for_id(card_id) if store is not None else None

    def _contains_source(self, source):
        if source is None or source < 0:
            return False
        byte_index, bit = divmod(int(source), 8)
        return (byte_index < len(self._bits)
                and bool(self._bits[byte_index] & (1 << bit)))

    def __contains__(self, card_id):
        return self._contains_source(self._source_for_id(card_id))

    def clear(self):
        if self._count:
            self._bits[:] = b"\x00" * len(self._bits)
            self._count = 0
        self._sparse = set()

    def _iter_bit_sources(self):
        logical_count = int(getattr(self._store, "logical_count", 0) or 0)
        for byte_index, byte_value in enumerate(self._bits):
            value = int(byte_value)
            while value:
                low_bit = value & -value
                bit = low_bit.bit_length() - 1
                source = byte_index * 8 + bit
                if source < logical_count:
                    yield source
                value ^= low_bit

    def _rebuild_sparse_if_small(self):
        if self._count <= self.SPARSE_LIMIT:
            self._sparse = set(self._iter_bit_sources())
        else:
            self._sparse = None

    def _set_source(self, source):
        if source is None or source < 0:
            return False
        byte_index, bit = divmod(int(source), 8)
        if byte_index >= len(self._bits):
            return False
        mask = 1 << bit
        if self._bits[byte_index] & mask:
            return False
        self._bits[byte_index] |= mask
        self._count += 1
        if self._sparse is not None:
            self._sparse.add(int(source))
            if self._count > self.SPARSE_LIMIT:
                self._sparse = None
        return True

    def _clear_source(self, source):
        if source is None or source < 0:
            return False
        byte_index, bit = divmod(int(source), 8)
        if byte_index >= len(self._bits):
            return False
        mask = 1 << bit
        if not self._bits[byte_index] & mask:
            return False
        self._bits[byte_index] &= ~mask & 0xFF
        self._count -= 1
        if self._sparse is not None:
            self._sparse.discard(int(source))
        elif self._count <= self.SPARSE_LIMIT:
            self._rebuild_sparse_if_small()
        return True

    def add(self, card_id):
        self._set_source(self._source_for_id(card_id))

    def discard(self, card_id):
        self._clear_source(self._source_for_id(card_id))

    def update(self, card_ids):
        for card_id in card_ids:
            self.add(card_id)

    def difference_update(self, card_ids):
        for card_id in card_ids:
            self.discard(card_id)

    def replace(self, card_ids):
        self.clear()
        self.update(card_ids)

    def _select_identity_range(self, first, last):
        """Set one contiguous source range using byte slices, not Python IDs."""
        if first > last:
            first, last = last, first
        first_byte, first_bit = divmod(first, 8)
        last_byte, last_bit = divmod(last, 8)
        if first_byte == last_byte:
            self._bits[first_byte] |= (
                ((1 << (last_bit - first_bit + 1)) - 1) << first_bit)
            return
        self._bits[first_byte] |= (0xFF << first_bit) & 0xFF
        if last_byte - first_byte > 1:
            self._bits[first_byte + 1:last_byte] = (
                b"\xff" * (last_byte - first_byte - 1))
        self._bits[last_byte] |= (1 << (last_bit + 1)) - 1

    def select_view_range(self, first, last, *, additive=False):
        store = self._store
        if store is None or not store.visible_count:
            return
        # Each end is clamped on its own before they are ordered. Clamping the
        # smaller against zero and the larger against the end left a range that
        # began past the end inverted -- 20..30 of ten rows became 9..20 --
        # which the identity path then filled from the top of the list and
        # wrote off the end of the bitset.
        last_position = store.visible_count - 1
        lo = max(0, min(int(first), last_position))
        hi = max(0, min(int(last), last_position))
        if lo > hi:
            lo, hi = hi, lo
        if not additive:
            self.clear()
        if store.view_index is None:
            self._select_identity_range(lo, hi)
        else:
            for source in store.view_index[lo:hi + 1]:
                self._set_source(int(source))
        # Identity-range byte filling bypasses _set_source, so recompute only
        # for that fast path.  bytearray.bit_count is unavailable; int.from_bytes
        # performs the population count in optimized C. The indexed path goes
        # through _set_source, which keeps the count itself: it only ever adds,
        # so there was nothing for a second recount to correct.
        if store.view_index is None:
            self._count = int.from_bytes(self._bits, "little").bit_count()
            self._rebuild_sparse_if_small()

    def visible_count(self):
        store = self._store
        if store is None or not self._count:
            return 0
        if store.view_index is None:
            return self._count
        if self._count == store.logical_count:
            return store.visible_count
        if self._sparse is not None:
            return sum(
                1 for source in self._sparse
                if store.view_position_for_source(source) is not None
            )
        return sum(
            1 for source in store.view_index
            if self._contains_source(int(source))
        )

    def contains_visible_id(self, card_id):
        store = self._store
        if store is None or card_id not in self:
            return False
        return store.view_position_for_id(card_id) is not None

    def ids_in_view_order(self, limit=None):
        store = self._store
        if store is None or not self._count:
            return ()
        max_items = None if limit is None else max(0, int(limit))
        if max_items == 0:
            return ()
        if self._sparse is not None:
            candidates = []
            for source in self._sparse:
                position = store.view_position_for_source(source)
                if position is not None:
                    candidates.append((position, source))
            candidates.sort(key=lambda item: item[0])
            if max_items is not None:
                candidates = candidates[:max_items]
            return tuple(
                str(card_id)
                for _position, source in candidates
                for card_id in (store.row_at_source(source).id,)
                if card_id
            )
        chosen = []
        for position in range(store.visible_count):
            source = store.source_index_at_view(position)
            if not self._contains_source(source):
                continue
            card_id = store.row_at_source(source).id
            if card_id:
                chosen.append(str(card_id))
                if max_items is not None and len(chosen) >= max_items:
                    break
        return tuple(chosen)

    def diagnostics(self):
        return {
            "selected": self._count,
            "bytes": len(self._bits),
            "sparse_selected": (
                len(self._sparse) if self._sparse is not None else None),
        }


class SearchResultStore:
    """Complete logical Search result set with a compact swappable ordered index."""

    FULL_CACHE_LIMIT = 96
    VOCABULARY_CACHE_LIMIT = 32
    VOCABULARY_CACHE_BYTES = 4 * 1024 * 1024
    DERIVED_CACHE_LIMIT = 8
    DERIVED_CACHE_BYTES = 12 * 1024 * 1024

    def __init__(self, rows=()):
        self._rows = tuple(SearchResultRow.from_mapping(row) for row in rows)
        self._view_index = None  # None is the zero-allocation identity order.
        self._view_inverse = None  # packed source -> visible-position map (-1 = hidden).
        self._id_to_source = {
            str(row.id): index for index, row in enumerate(self._rows) if row.id
        }
        self._full_cache = OrderedDict()
        self._vocabulary_cache = OrderedDict()
        self._vocabulary_cache_bytes = 0
        self._derived_cache = OrderedDict()
        self._derived_cache_bytes = 0
        self._cache_lock = threading.RLock()

    @classmethod
    def from_rows(cls, rows):
        return cls(rows)

    @classmethod
    def from_projection(cls, columns, tuples):
        """Build directly from ordered cursor tuples for the result projection.

        Skips the throwaway dict-per-row that ``from_rows`` would parse: the
        query projects ``columns`` in order, so each ``SearchResultRow`` field
        is read by position.  ``from_mapping`` treats the resulting rows as an
        identity, so the store is byte-identical to the dict path.
        """
        positions = [columns.index(field) for field in SEARCH_RESULT_FIELDS]
        return cls(
            SearchResultRow(*(row[position] for position in positions))
            for row in tuples)

    @property
    def rows(self):
        return self._rows

    @property
    def logical_count(self):
        return len(self._rows)

    @property
    def visible_count(self):
        return len(self._rows) if self._view_index is None else len(self._view_index)

    @property
    def view_index(self):
        return self._view_index

    def reset_view(self):
        self._view_index = None
        self._view_inverse = None

    def swap_view_index(self, index):
        if index is None:
            self._view_index = None
            self._view_inverse = None
            return
        packed = array("I", (int(value) for value in index))
        inverse = array("i", [-1]) * len(self._rows)
        for position, source in enumerate(packed):
            inverse[source] = position
        self._view_index = packed
        self._view_inverse = inverse

    def source_index_at_view(self, view_index):
        position = int(view_index)
        if position < 0 or position >= self.visible_count:
            raise IndexError(position)
        if self._view_index is None:
            return position
        return self._view_index[position]

    def row_at_source(self, source_index):
        return self._rows[int(source_index)]

    def row_at_view(self, view_index):
        return self.row_at_source(self.source_index_at_view(view_index))

    def source_index_for_id(self, card_id):
        if not card_id:
            return None
        return self._id_to_source.get(str(card_id))

    def view_position_for_id(self, card_id):
        source = self.source_index_for_id(card_id)
        if source is None:
            return None
        return self.view_position_for_source(source)

    def view_position_for_source(self, source_index):
        source = int(source_index)
        if source < 0 or source >= len(self._rows):
            return None
        if self._view_index is None:
            return source
        position = self._view_inverse[source]
        return None if position < 0 else int(position)

    def ids_in_view_order(self, selected_ids):
        candidates = []
        for value in selected_ids:
            if not value:
                continue
            card_id = str(value)
            source = self._id_to_source.get(card_id)
            if source is None:
                continue
            position = source if self._view_index is None else self._view_inverse[source]
            if position >= 0:
                candidates.append((int(position), card_id))
        candidates.sort(key=lambda item: item[0])
        return tuple(card_id for _position, card_id in candidates)

    def hydrate(self, source_index, repository):
        source_index = int(source_index)
        with self._cache_lock:
            cached = self._full_cache.get(source_index)
            if cached is not None:
                self._full_cache.move_to_end(source_index)
                return cached
        row = self._rows[source_index]
        card_id = row.id
        full = repository.card_by_id(card_id) if card_id else None
        if not full:
            return {field: row.get(field) for field in SEARCH_RESULT_FIELDS}
        with self._cache_lock:
            self._full_cache[source_index] = full
            self._full_cache.move_to_end(source_index)
            while len(self._full_cache) > self.FULL_CACHE_LIMIT:
                self._full_cache.popitem(last=False)
        return full

    @staticmethod
    def _derived_weight(values):
        return 64 + sum(16 + len(str(value).encode("utf-8", "replace"))
                        for value in values)

    def derived_vector(self, key, cancelled=None):
        """Return a bounded per-generation display vector for expensive facets."""
        key = str(key)
        if key not in EXPENSIVE_DERIVED_FIELDS:
            return None
        with self._cache_lock:
            entry = self._derived_cache.get(key)
            if entry is not None:
                self._derived_cache.move_to_end(key)
                return entry[0]
        values = []
        for index, row in enumerate(self._rows):
            if cancelled is not None and index % 1024 == 0 and cancelled():
                return _CANCELLED
            values.append(table_value(row, key))
        vector = tuple(values)
        weight = self._derived_weight(vector)
        if weight <= self.DERIVED_CACHE_BYTES:
            with self._cache_lock:
                old = self._derived_cache.pop(key, None)
                if old is not None:
                    self._derived_cache_bytes -= old[1]
                self._derived_cache[key] = (vector, weight)
                self._derived_cache_bytes += weight
                self._derived_cache.move_to_end(key)
                while (len(self._derived_cache) > self.DERIVED_CACHE_LIMIT
                       or self._derived_cache_bytes > self.DERIVED_CACHE_BYTES):
                    _old_key, (_values, removed) = self._derived_cache.popitem(last=False)
                    self._derived_cache_bytes -= removed
        return vector

    def cached_vocabulary(self, signature):
        with self._cache_lock:
            entry = self._vocabulary_cache.get(signature)
            if entry is not None:
                self._vocabulary_cache.move_to_end(signature)
                return entry[0]
            return None

    @staticmethod
    def _vocabulary_weight(values):
        # Conservative Python-side estimate: tuple/list/object references plus text.
        return 64 + sum(48 + len(str(value).encode("utf-8", "replace")) for value in values)

    def remember_vocabulary(self, signature, values, limit=None):
        values = tuple(values)
        weight = self._vocabulary_weight(values)
        max_entries = self.VOCABULARY_CACHE_LIMIT if limit is None else max(1, int(limit))
        with self._cache_lock:
            old = self._vocabulary_cache.pop(signature, None)
            if old is not None:
                self._vocabulary_cache_bytes -= old[1]
            if weight > self.VOCABULARY_CACHE_BYTES:
                return
            self._vocabulary_cache[signature] = (values, weight)
            self._vocabulary_cache_bytes += weight
            self._vocabulary_cache.move_to_end(signature)
            while (len(self._vocabulary_cache) > max_entries
                   or self._vocabulary_cache_bytes > self.VOCABULARY_CACHE_BYTES):
                _key, (_values, removed_weight) = self._vocabulary_cache.popitem(last=False)
                self._vocabulary_cache_bytes -= removed_weight

    def diagnostics(self):
        with self._cache_lock:
            return {
                "logical_count": len(self._rows),
                "visible_count": self.visible_count,
                "full_cache_entries": len(self._full_cache),
                "vocabulary_cache_entries": len(self._vocabulary_cache),
                "vocabulary_cache_bytes": self._vocabulary_cache_bytes,
                "derived_cache_entries": len(self._derived_cache),
                "derived_cache_bytes": self._derived_cache_bytes,
                "indexed": self._view_index is not None,
                "inverse_indexed": self._view_inverse is not None,
            }


def table_value(card, key, qty=None):
    """Tk-free display value shared by Results preparation and table UI."""
    if key == "qty":
        return "" if qty is None else str(qty)
    if key == "cost":
        # The Cost column is drawn as mana symbols, but a filter still has to
        # compare against something. Without this the column's own filter
        # matched every row against "" and emptied the table, while its popup
        # cheerfully listed the costs it was refusing to find.
        return card.get("mana_cost") or ""
    if key == "name":
        return card.get("name", "")
    if key == "type":
        return card.get("type_line", "")
    if key == "set":
        name = card.get("set_name") or ""
        code = (card.get("set_code") or "").upper()
        return f"{name} ({code})" if name and code else (name or code)
    if key == "collector":
        return str(card.get("collector_number") or "")
    if key == "ability":
        raw = card.get("keywords") or []
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [value.strip() for value in raw.split(",") if value.strip()]
        return ", ".join(str(value) for value in (raw or ()))
    if key == "rarity":
        return (card.get("rarity") or "").title()
    if key == "cmc":
        mana_value = card.get("cmc")
        if mana_value is None:
            return ""
        try:
            number = float(mana_value)
            return str(int(number)) if number.is_integer() else str(number)
        except (TypeError, ValueError):
            return str(mana_value)
    if key == "power":
        return "" if card.get("power") is None else str(card.get("power"))
    if key == "toughness":
        return "" if card.get("toughness") is None else str(card.get("toughness"))
    if key == "colors":
        raw = card.get("colors") or ""
        if isinstance(raw, str):
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, list):
                    raw = decoded
            except Exception:
                pass
        if isinstance(raw, (list, tuple)):
            return ", ".join(str(value) for value in raw) or "Colorless"
        return ", ".join(value for value in str(raw).split(",") if value) or "Colorless"
    if key == "rules":
        return "  ".join((card.get("oracle_text") or "").splitlines())
    if key == "year":
        released = card.get("released_at") or ""
        return released[:4] if released[:4].isdigit() else ""
    return ""


def collector_key(collector_number):
    value = collector_number or ""
    digits = "".join(character for character in value if character.isdigit())
    return (int(digits) if digits else 0, value)


def table_sort_key(card, key, qty=None):
    name = (card.get("name") or "").casefold()
    if key == "qty":
        try:
            return (float(qty or 0), name)
        except (TypeError, ValueError):
            return (0.0, name)
    if key in ("cost", "cmc"):
        try:
            return (float(card.get("cmc") or 0), name)
        except (TypeError, ValueError):
            return (0.0, name)
    if key in ("power", "toughness"):
        raw = card.get(key)
        try:
            return (0, float(raw), name)
        except (TypeError, ValueError):
            return (1, str(raw or "").casefold(), name)
    if key == "collector":
        return (*collector_key(card.get("collector_number")), name)
    if key == "year":
        released = card.get("released_at") or ""
        try:
            return (int(released[:4]), name)
        except (TypeError, ValueError):
            return (0, name)
    value = table_value(card, key, qty=qty)
    return (str(value or "").casefold(), name)


def filter_numeric_value(card, key, qty=None):
    if key == "qty":
        raw = qty
    elif key == "cmc":
        raw = card.get("cmc")
    elif key == "year":
        raw = (card.get("released_at") or "")[:4]
    else:
        raw = card.get(key)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# The Cost column filters by which kinds of mana symbol appear in the printed
# cost, so a user picks recognizable options (with pips) instead of typing brace
# syntax.  This ordered list is the single source of truth shared by the popup
# editor (which builds the checkboxes) and ``cost_symbol_groups`` (which the
# matcher uses), so the two can never disagree about what a group means.
COST_SYMBOL_GROUP_LABELS = (
    ("W", "White"),
    ("U", "Blue"),
    ("B", "Black"),
    ("R", "Red"),
    ("G", "Green"),
    ("C", "Colorless"),
    ("generic", "Generic"),
    ("x", "X"),
    ("hybrid", "Hybrid"),
    ("phyrexian", "Phyrexian"),
    ("snow", "Snow"),
    ("none", "No mana cost"),
)
COST_SYMBOL_GROUP_ORDER = tuple(key for key, _label in COST_SYMBOL_GROUP_LABELS)

# The Colors column filters by the card's own colours (not its cost), offered as
# the same recognizable pip checkboxes.  "C" here means the card is colourless
# (an empty colour set), distinct from the Cost picker's colourless-mana pip.
COLOR_FILTER_GROUP_LABELS = (
    ("W", "White"),
    ("U", "Blue"),
    ("B", "Black"),
    ("R", "Red"),
    ("G", "Green"),
    ("C", "Colorless"),
)
COLOR_FILTER_GROUP_ORDER = tuple(key for key, _label in COLOR_FILTER_GROUP_LABELS)

_COST_TOKEN = re.compile(r"\{([^}]+)\}")


def card_color_groups(card):
    """Return the card's colours as filter groups, or ``{"C"}`` when colourless."""
    raw = card.get("colors") or ""
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, list):
                raw = decoded
        except Exception:
            pass
    if isinstance(raw, (list, tuple)):
        letters = [str(value).upper() for value in raw]
    else:
        letters = [part.strip().upper() for part in str(raw).split(",")
                   if part.strip()]
    groups = {letter for letter in letters if letter in ("W", "U", "B", "R", "G")}
    return frozenset(groups) if groups else frozenset({"C"})


def cost_symbol_groups(mana_cost):
    """Classify a printed mana cost into the symbol groups it contains.

    Only the front face's cost is read (matching how the Cost column is drawn).
    A hybrid/phyrexian symbol contributes both its class and its colour(s), so a
    ``{W/U}`` card matches White, Blue, and Hybrid, and ``{W/P}`` matches White
    and Phyrexian.  A cost with no brace symbols is ``{"none"}`` so lands and
    other no-cost objects are pickable too.
    """
    front = str(mana_cost or "").split("//")[0]
    groups = set()
    for token in _COST_TOKEN.findall(front):
        t = token.upper().strip()
        if "/" in t:
            parts = [p for p in t.split("/") if p]
            if "P" in parts:
                groups.add("phyrexian")
            elif "2" in parts:
                groups.add("hybrid")
                groups.add("generic")
            else:
                groups.add("hybrid")
            for part in parts:
                if part in ("W", "U", "B", "R", "G"):
                    groups.add(part)
                elif part == "C":
                    groups.add("C")
        elif t in ("W", "U", "B", "R", "G"):
            groups.add(t)
        elif t == "C":
            groups.add("C")
        elif t in ("X", "Y", "Z"):
            groups.add("x")
        elif t == "S":
            groups.add("snow")
        elif t.isdigit():
            groups.add("generic")
    return frozenset(groups) if groups else frozenset({"none"})


def row_passes_filters(
        card, filters, *, qty=None, skip_col=None, derived=None, source_index=None):
    for key, rule in filters.items():
        if key == skip_col:
            continue
        kind = rule.get("kind")
        if kind == "numeric":
            value = filter_numeric_value(card, key, qty=qty)
            if value is None:
                return False
            lo, hi = rule.get("min"), rule.get("max")
            if lo is not None and value < lo:
                return False
            if hi is not None and value > hi:
                return False
        elif kind == "values":
            allowed = rule.get("values", ())
            vector = derived.get(key) if derived else None
            value = (vector[source_index] if vector is not None
                     and source_index is not None else table_value(card, key, qty=qty))
            if value not in allowed:
                return False
        elif kind in ("cost", "colors"):
            selected = set(rule.get("groups") or ())
            if not selected:
                continue
            present = (cost_symbol_groups(card.get("mana_cost"))
                       if kind == "cost" else card_color_groups(card))
            mode = rule.get("mode", "Any")
            if mode == "All":
                if not selected <= present:
                    return False
            elif mode == "None":
                if selected & present:
                    return False
            else:  # Any
                if not (selected & present):
                    return False
        elif kind == "text":
            needle = str(rule.get("value") or "").casefold()
            if not needle:
                continue
            vector = derived.get(key) if derived else None
            value = (vector[source_index] if vector is not None
                     and source_index is not None else table_value(card, key, qty=qty))
            hay = str(value or "").casefold()
            mode = rule.get("mode", "Contains")
            if mode == "Equals" and hay != needle:
                return False
            if mode == "Starts with" and not hay.startswith(needle):
                return False
            if mode == "Does not contain" and needle in hay:
                return False
            if mode == "Contains" and needle not in hay:
                return False
    return True


def freeze_filters(filters):
    frozen = {}
    for key, rule in (filters or {}).items():
        copy = dict(rule)
        if "values" in copy:
            copy["values"] = frozenset(copy["values"])
        if "groups" in copy:
            copy["groups"] = frozenset(copy["groups"])
        frozen[str(key)] = copy
    return frozen


def prepare_view_index(store, filters, sort_col=None, sort_desc=False, cancelled=None):
    rows = store.rows
    if not filters and not sort_col:
        return None
    requested = {
        key for key, rule in filters.items()
        if rule.get("kind") in ("values", "text")
        and key in EXPENSIVE_DERIVED_FIELDS
    }
    if sort_col in EXPENSIVE_DERIVED_FIELDS:
        requested.add(sort_col)
    derived = {}
    for key in requested:
        vector = store.derived_vector(key, cancelled=cancelled)
        if vector is _CANCELLED:
            return _CANCELLED
        if vector is not None:
            derived[key] = vector
    indices = []
    for index, row in enumerate(rows):
        if cancelled is not None and index % 1024 == 0 and cancelled():
            return _CANCELLED
        if row_passes_filters(
                row, filters, derived=derived, source_index=index):
            indices.append(index)
    if cancelled is not None and cancelled():
        return _CANCELLED
    if sort_col:
        vector = derived.get(sort_col)
        if vector is not None:
            indices.sort(
                key=lambda index: (
                    str(vector[index] or "").casefold(),
                    str(rows[index].get("name") or "").casefold()),
                reverse=bool(sort_desc))
        else:
            indices.sort(
                key=lambda index: table_sort_key(rows[index], sort_col),
                reverse=bool(sort_desc))
    if cancelled is not None and cancelled():
        return _CANCELLED
    return array("I", indices)


def filter_signature(filters):
    parts = []
    for key in sorted(filters):
        rule = filters[key]
        values = rule.get("values")
        # Cost/Colors filter rules carry their picked symbol groups under
        # "groups" (a set), not "values". Omitting it from the signature made
        # two different Cost/Colors selections that only differ by group hash
        # to the same cache key, so opening a second picker after changing
        # Cost/Colors reused the first selection's stale cached vocabulary
        # instead of recomputing it.
        groups = rule.get("groups")
        parts.append((
            key, rule.get("kind"), rule.get("min"), rule.get("max"),
            rule.get("mode"), rule.get("value"),
            tuple(sorted((str(value) for value in values), key=str.casefold))
            if values is not None else (),
            tuple(sorted((str(value) for value in groups), key=str.casefold))
            if groups is not None else (),
        ))
    return tuple(parts)


def prepare_vocabulary(store, key, filters, cancelled=None):
    signature = (str(key), filter_signature(filters))
    cached = store.cached_vocabulary(signature)
    if cached is not None:
        return cached
    requested = {
        filter_key for filter_key, rule in filters.items()
        if filter_key != key and rule.get("kind") in ("values", "text")
        and filter_key in EXPENSIVE_DERIVED_FIELDS
    }
    if key in EXPENSIVE_DERIVED_FIELDS:
        requested.add(key)
    derived = {}
    for derived_key in requested:
        vector = store.derived_vector(derived_key, cancelled=cancelled)
        if vector is _CANCELLED:
            return _CANCELLED
        if vector is not None:
            derived[derived_key] = vector
    seen = {}
    for index, row in enumerate(store.rows):
        if cancelled is not None and index % 1024 == 0 and cancelled():
            return _CANCELLED
        if row_passes_filters(
                row, filters, skip_col=key, derived=derived, source_index=index):
            vector = derived.get(key)
            value = vector[index] if vector is not None else table_value(row, key)
            seen.setdefault(value, None)
    if cancelled is not None and cancelled():
        return _CANCELLED
    values = tuple(sorted(seen, key=lambda value: str(value).casefold()))
    store.remember_vocabulary(signature, values)
    return values


_CANCELLED = object()


@dataclass(frozen=True, slots=True)
class ResultPreparationEvent:
    kind: str
    generation: int
    store: SearchResultStore
    payload: object
    elapsed: float
    key: str = ""


class ResultPreparationWorker:
    """Latest-wins single daemon worker for one Results preparation channel."""

    def __init__(self, name):
        self._name = str(name)
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._generation = 0
        self._events = queue.Queue()
        self._thread = spawn_daemon(self._run, self._name)

    @property
    def generation(self):
        with self._condition:
            return self._generation

    def submit_view(self, store, filters, sort_col=None, sort_desc=False):
        return self._submit(
            "view", store, freeze_filters(filters), sort_col, bool(sort_desc))

    def submit_vocabulary(self, store, key, filters):
        return self._submit("vocabulary", store, str(key), freeze_filters(filters))

    def _submit(self, kind, store, *args):
        with self._condition:
            if self._closed:
                raise RuntimeError("Results preparation worker is closed")
            self._generation += 1
            generation = self._generation
            self._pending = (kind, generation, store, args)
            self._condition.notify()
            return generation

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed and self._pending is None:
                    return
                request = self._pending
                self._pending = None
            kind, generation, store, args = request
            started = time.perf_counter()
            try:
                if kind == "view":
                    filters, sort_col, sort_desc = args
                    payload = prepare_view_index(
                        store, filters, sort_col=sort_col, sort_desc=sort_desc,
                        cancelled=lambda g=generation: g != self.generation)
                    key = ""
                else:
                    key, filters = args
                    payload = prepare_vocabulary(
                        store, key, filters,
                        cancelled=lambda g=generation: g != self.generation)
                if payload is _CANCELLED:
                    continue
                event_kind = kind
            except Exception as exc:
                log.exception("Results %s preparation failed", kind)
                payload = str(exc)
                event_kind = "error"
                key = args[0] if kind == "vocabulary" else ""
            self._events.put(ResultPreparationEvent(
                event_kind, generation, store, payload,
                time.perf_counter() - started, key=str(key)))

    def poll_latest(self):
        latest = None
        try:
            while True:
                event = self._events.get_nowait()
                if event.generation == self.generation:
                    latest = event
        except queue.Empty:
            return latest

    def shutdown(self, timeout=0.5):
        with self._condition:
            self._closed = True
            self._pending = None
            self._generation += 1
            self._condition.notify_all()
        self._thread.join(max(0.0, float(timeout)))
        return not self._thread.is_alive()
