"""Tk-free live faceted context for the interactive Search filters.

The foreground Search still runs only when the user asks for Results.  This
module analyzes the current draft ``SearchCriteria`` in a latest-wins worker so
all existing filter controls can react while that query is being composed.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
import json
import math
import queue
import sqlite3
import threading
import time

from mtgdb.core.background_jobs import spawn_daemon
from mtgdb.database.constants import COLORS
from mtgdb.database.semantics import (
    _card_content_kind, _mana_cost_symbol_colors, _mana_cost_symbol_match,
    _type_key, _type_line_search_parts,
)
from mtgdb.search.models import SearchCriteria


CONTEXT_COLUMNS = (
    "type_line", "keywords", "rarity", "layout", "cmc", "power",
    "toughness", "loyalty", "defense", "released_at", "mana_cost",
    "produced_mana", "colors", "color_identity", "color_indicator",
    "reserved", "game_changer", "universes_beyond", "card_faces",
    "set_code", "set_name", "set_type", "games", "lang", "legalities",
    "pips_w", "pips_u", "pips_b", "pips_r", "pips_g", "pips_c",
)

_CONTENT_KEYS = ("card", "token", "emblem", "art")
_GAME_KEYS = ("paper", "arena", "mtgo")
_PIP_KEYS = (*COLORS, "C")
_MANA_FEATURE_KEYS = ("hybrid_mana", "phyrexian_mana", "has_x_cost")
_SPECIAL_PROPERTY_KEYS = (
    "top_heavy", "variable_stats", "color_indicator", "multi_faced",
)
_STATUS_PROPERTY_KEYS = (
    "not_universes_beyond", "universes_beyond", "reserved", "game_changer",
)
_LEGACY_TRAIT_KEYS = (
    *_STATUS_PROPERTY_KEYS, "single_faced", "multi_faced",
    *_MANA_FEATURE_KEYS, "color_indicator", "top_heavy", "variable_stats",
)


@dataclass(frozen=True, slots=True)
class SearchContextSnapshot:
    """Predictive context for one live draft Search criteria."""

    result_count: int = 0
    card_type_counts: dict = field(default_factory=dict)
    supertype_counts: dict = field(default_factory=dict)
    subtype_counts: dict = field(default_factory=dict)
    keyword_counts: dict = field(default_factory=dict)
    color_counts: dict = field(default_factory=dict)
    produces_counts: dict = field(default_factory=dict)
    layout_counts: dict = field(default_factory=dict)
    rarity_counts: dict = field(default_factory=dict)
    numeric_ranges: dict = field(default_factory=dict)
    numeric_applicability: dict = field(default_factory=dict)
    release_years: tuple[str, ...] = ()
    release_year_counts: dict = field(default_factory=dict)
    # Legacy global trait counts remain for compatibility-only criteria.
    trait_counts: dict = field(default_factory=dict)
    mana_feature_counts: dict = field(default_factory=dict)
    special_property_counts: dict = field(default_factory=dict)
    status_property_counts: dict = field(default_factory=dict)
    pip_counts: dict = field(default_factory=dict)
    content_counts: dict = field(default_factory=dict)
    game_counts: dict = field(default_factory=dict)
    set_type_counts: dict = field(default_factory=dict)
    set_counts: dict = field(default_factory=dict)
    format_counts: dict = field(default_factory=dict)
    english_count: int = 0
    all_language_count: int = 0
    suggestions: tuple[tuple[str, int], ...] = ()
    mode_suggestions: tuple[tuple[str, str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class SearchContextEvent:
    generation: int
    signature: tuple
    kind: str
    payload: object
    elapsed: float = 0.0


def _catalog_values(entries):
    values = []
    for item in entries or ():
        if isinstance(item, (tuple, list)) and item:
            values.append(str(item[0]))
        elif item not in (None, ""):
            values.append(str(item))
    return tuple(dict.fromkeys(values))


def _catalog_signature(entries):
    return tuple(_catalog_values(entries))


def _phrase_trie(values):
    root = {}
    for value in _catalog_values(values):
        words = tuple(_type_key(value).split())
        if not words:
            continue
        node = root
        for word in words:
            node = node.setdefault(word, {})
        node.setdefault(None, []).append(value)
    return root


def _trie_matches(sequences, trie):
    if not trie:
        return set()
    seen = set()
    for sequence in sequences:
        for start in range(len(sequence)):
            node = trie
            for word in sequence[start:]:
                node = node.get(word)
                if node is None:
                    break
                seen.update(node.get(None, ()))
    return seen


def _keyword_values(raw):
    if isinstance(raw, (tuple, list)):
        return tuple(str(value) for value in raw if value not in (None, ""))
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if item not in (None, ""))


def _json_object(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _comma_members(raw):
    if isinstance(raw, (tuple, list, set, frozenset)):
        return {str(value) for value in raw if str(value)}
    return {
        value for value in (str(raw or "").split(",")) if value
    }


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _has_faces(value):
    if isinstance(value, (tuple, list)):
        return bool(value)
    if not value:
        return False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(parsed, list) and bool(parsed)


def _trait_keys(row):
    values = set()
    mana_cost = str(row.get("mana_cost") or "").upper()
    faces = _has_faces(row.get("card_faces"))
    values.add("universes_beyond" if bool(row.get("universes_beyond"))
               else "not_universes_beyond")
    if bool(row.get("reserved")):
        values.add("reserved")
    if bool(row.get("game_changer")):
        values.add("game_changer")
    values.add("multi_faced" if faces else "single_faced")
    if "/" in mana_cost and "/P" not in mana_cost:
        values.add("hybrid_mana")
    if "/P" in mana_cost:
        values.add("phyrexian_mana")
    if "{X}" in mana_cost:
        values.add("has_x_cost")
    if row.get("color_indicator") not in (None, "", "[]"):
        values.add("color_indicator")
    power = str(row.get("power") or "")
    toughness = str(row.get("toughness") or "")
    if "*" in power or "*" in toughness:
        values.add("variable_stats")
    p_num, t_num = _finite(power), _finite(toughness)
    if p_num is not None and t_num is not None and p_num > t_num:
        values.add("top_heavy")
    return values


class _PredictiveMembershipCounts:
    """Accumulate contextual multi-select counts without N×row rescans.

    ``Any`` is deliberately a compatibility count, not the total result after
    OR-ing the candidate with values already selected in this same facet.  The
    latter made an existing matching value contribute its rows to *every*
    candidate, so an impossible peer could suddenly look available simply
    because another OR value was selected.  For Any, each candidate therefore
    counts only rows that actually carry that candidate under all *other*
    Search dimensions.

    ``All`` and ``None`` remain prospective add-value counts because adding a
    value under either mode narrows the facet rather than broadening it.
    """

    def __init__(self, vocabulary, selected=(), mode="any"):
        self.vocabulary = tuple(dict.fromkeys(str(v) for v in vocabulary if str(v)))
        self.allowed = set(self.vocabulary)
        self.selected = {str(v) for v in selected if str(v)} & self.allowed
        self.mode = str(mode or "any").casefold()
        self._delta = Counter()
        self._global = 0

    def add(self, members, weight=1):
        members = {str(v) for v in members if str(v) in self.allowed}
        weight = int(weight)
        if weight <= 0:
            return
        if self.mode == "all":
            if self.selected.issubset(members):
                for value in members:
                    self._delta[value] += weight
            return
        if self.mode == "none":
            if not (self.selected & members):
                self._global += weight
                for value in members:
                    self._delta[value] -= weight
            return
        # Any: availability is the candidate's own compatibility with all
        # other dimensions, independent of selected OR peers.  This keeps an
        # impossible value at zero instead of letting an already-selected value
        # make every candidate appear usable.
        for value in members:
            self._delta[value] += weight

    def finish(self):
        return {
            value: max(0, int(self._global + self._delta.get(value, 0)))
            for value in self.vocabulary
        }


def _predict_set_candidates(rows, field, vocabulary, selected, mode="any"):
    predictor = _PredictiveMembershipCounts(vocabulary, selected, mode)
    for row in rows:
        value = row.get(field)
        predictor.add((str(value),) if value not in (None, "") else ())
    return predictor.finish()


def _predict_type_line(rows, *, card_types=(), selected_card_types=(), card_type_mode="any",
                       supertypes=(), selected_supertypes=(), supertype_mode="any",
                       subtypes=(), selected_subtypes=(), subtype_mode="any"):
    card_predictor = _PredictiveMembershipCounts(
        card_types, selected_card_types, card_type_mode) if card_types else None
    super_predictor = _PredictiveMembershipCounts(
        supertypes, selected_supertypes, supertype_mode) if supertypes else None
    subtype_predictor = _PredictiveMembershipCounts(
        subtypes, selected_subtypes, subtype_mode) if subtypes else None
    card_trie = _phrase_trie(card_types) if card_predictor else None
    super_trie = _phrase_trie(supertypes) if super_predictor else None
    subtype_trie = _phrase_trie(subtypes) if subtype_predictor else None
    for row in rows:
        left_sequences, subtype_texts = _type_line_search_parts(
            str(row.get("type_line") or ""))
        if card_predictor:
            card_predictor.add(_trie_matches(left_sequences, card_trie))
        if super_predictor:
            super_predictor.add(_trie_matches(left_sequences, super_trie))
        if subtype_predictor:
            sequences = tuple(tuple(text.split()) for text in subtype_texts)
            subtype_predictor.add(_trie_matches(sequences, subtype_trie))
    return (
        card_predictor.finish() if card_predictor else {},
        super_predictor.finish() if super_predictor else {},
        subtype_predictor.finish() if subtype_predictor else {},
    )


def _predict_keywords(rows, vocabulary, selected, mode):
    predictor = _PredictiveMembershipCounts(vocabulary, selected, mode)
    allowed = {value.casefold(): value for value in predictor.vocabulary}
    for row in rows:
        members = {
            allowed[value.casefold()] for value in _keyword_values(row.get("keywords"))
            if value.casefold() in allowed
        }
        predictor.add(members)
    return predictor.finish()


def _color_match(card_members, selected, mode, *, produced=False):
    selected = set(selected)
    if not selected:
        return True
    if not produced and selected == {"C"}:
        return not card_members
    if produced and "C" in selected:
        # Produced mana stores C as a real member.
        pass
    target = {value for value in selected if value != "C" or produced}
    normalized = str(mode or "within").casefold()
    if normalized == "includes":
        return target.issubset(card_members)
    if normalized == "exact":
        return card_members == target
    if produced and not card_members:
        return False
    return card_members.issubset(target)


def _predict_colors(rows, field, vocabulary, selected, mode, *, produced=False):
    result = {}
    selected = set(selected)
    normalized = str(mode or "within").casefold()
    for candidate in vocabulary:
        target = set(selected)
        if candidate not in target:
            target.add(candidate)
        count = 0
        for row in rows:
            members = _comma_members(row.get(field))
            matches = _color_match(members, target, mode, produced=produced)
            if not matches:
                continue
            # Within is a union/broadening mode.  Existing selected colours
            # must not keep an unrelated candidate above zero; the candidate
            # needs to occur on a matching card itself.  Colorless card colour
            # is represented by the empty set, while produced C is a real
            # stored member.
            if normalized == "within":
                contributes = (
                    (not members) if (candidate == "C" and not produced)
                    else candidate in members
                )
                if not contributes:
                    continue
            count += 1
        result[candidate] = count
    return result


def _predict_pips(rows, selected, minimum, mode="all"):
    """Predict one added mana-symbol color under total-symbol semantics."""
    selected = {str(v).upper() for v in selected if str(v).upper() in _PIP_KEYS}
    normalized = str(mode or "all").casefold()
    if normalized not in {"any", "all", "none"}:
        normalized = "all"
    result = {}
    for candidate in _PIP_KEYS:
        target = set(selected)
        target.add(candidate)
        target_csv = ",".join(sorted(target))
        count = 0
        for row in rows:
            if not _mana_cost_symbol_match(
                    row.get("mana_cost"), target_csv, normalized, minimum):
                continue
            if normalized == "any":
                # Any is a union/broadening mode. The candidate itself must
                # occur on the card, although already-selected colors may
                # contribute other physical symbols toward the total Minimum.
                represented = set().union(
                    *_mana_cost_symbol_colors(str(row.get("mana_cost") or ""))
                ) if row.get("mana_cost") else set()
                if candidate not in represented:
                    continue
            count += 1
        result[candidate] = count
    return result


def _predict_games(rows, selected):
    # Printing Type is a union facet.  Availability is each platform's own
    # compatible population under the other filters, not the OR total after
    # combining it with platforms already selected.
    result = {}
    for candidate in _GAME_KEYS:
        result[candidate] = sum(
            1 for row in rows
            if candidate in {
                value.casefold() for value in _comma_members(row.get("games"))
            })
    return result


def _predict_content(rows, selected):
    # Search Scope is another union facet.  A zero Token count must stay zero
    # even when Cards is selected, otherwise the default Cards population makes
    # every object class look available.
    result = {}
    for candidate in _CONTENT_KEYS:
        result[candidate] = sum(
            1 for row in rows
            if _card_content_kind(row.get("layout"), row.get("type_line")) == candidate
        )
    return result


def _has_meaningful_mana_cost(row):
    """Return whether Mana Value is a meaningful live filter for this row.

    A rules-derived mana value of 0 on an object with no mana cost (for example
    a Dungeon or ordinary land) is not enough to keep the interactive Mana
    Value range enabled.  A literal {0} cost is meaningful, and multi-face
    cards remain applicable when a real mana cost lives on one of their faces.
    """
    if str(row.get("mana_cost") or "").strip():
        return True
    raw_faces = row.get("card_faces")
    if isinstance(raw_faces, (tuple, list)):
        faces = raw_faces
    elif raw_faces:
        try:
            faces = json.loads(raw_faces)
        except (TypeError, ValueError, json.JSONDecodeError):
            faces = ()
    else:
        faces = ()
    if not isinstance(faces, (tuple, list)):
        return False
    return any(
        isinstance(face, dict) and str(face.get("mana_cost") or "").strip()
        for face in faces
    )


def _numeric_range(rows, field):
    values = [number for row in rows for number in (_finite(row.get(field)),)
              if number is not None]
    applicable = len(values)
    if field == "cmc":
        applicable = sum(
            1 for row in rows
            if _finite(row.get(field)) is not None and _has_meaningful_mana_cost(row)
        )
    return ((min(values), max(values)) if values else None, applicable)


def _release_context(rows):
    counts = Counter()
    for row in rows:
        year = str(row.get("released_at") or "")[:4]
        if len(year) == 4 and year.isdigit():
            counts[year] += 1
    return tuple(sorted(counts)), dict(counts)


def _format_counts(rows, vocabulary, status):
    allowed = set(_catalog_values(vocabulary))
    counts = Counter()
    chosen = str(status or "playable").casefold()
    for row in rows:
        legalities = _json_object(row.get("legalities"))
        for fmt, value in legalities.items():
            fmt = str(fmt)
            if fmt not in allowed:
                continue
            state = str(value or "").casefold()
            matches = (
                state in {"legal", "restricted"} if chosen == "playable"
                else state == chosen
            )
            if matches:
                counts[fmt] += 1
    return {fmt: int(counts.get(fmt, 0)) for fmt in _catalog_values(vocabulary)}


def _active_relaxations(criteria):
    """Existing filter groups only; labels are presentation, not new filters."""
    groups = []

    def add(label, condition, **changes):
        if condition:
            groups.append((label, replace(criteria, **changes)))

    add("Card Name", bool(criteria.name or criteria.names), name="", names=())
    add("Rules Text", bool(criteria.text), text=())
    add("Supertype", bool(criteria.supertypes), supertypes=())
    add("Card Type", bool(criteria.card_types), card_types=())
    add("Subtype", bool(criteria.subtypes), subtypes=())
    add("Mechanics", bool(criteria.keywords), keywords=())
    add("Mana Color", bool(criteria.colors), colors=())
    add("Mana Produced", bool(criteria.produces), produces=())
    add("Card Form", bool(criteria.layouts), layouts=())
    add("Card properties", bool(criteria.traits), traits=())
    add("Mana Cost Features", bool(criteria.mana_features), mana_features=())
    add("Special Properties", bool(criteria.special_properties), special_properties=())
    add("Product / Status", bool(criteria.status_properties), status_properties=())
    add("Mana Symbols in Cost", bool(criteria.pips), pips=(), pip_min=None)
    add("Mana Value", criteria.cmc_min is not None or criteria.cmc_max is not None,
        cmc_min=None, cmc_max=None)
    add("Power", criteria.power_min is not None or criteria.power_max is not None,
        power_min=None, power_max=None)
    add("Toughness", criteria.toughness_min is not None or criteria.toughness_max is not None,
        toughness_min=None, toughness_max=None)
    add("Loyalty", criteria.loyalty_min is not None or criteria.loyalty_max is not None,
        loyalty_min=None, loyalty_max=None)
    add("Defense", criteria.defense_min is not None or criteria.defense_max is not None,
        defense_min=None, defense_max=None)
    add("Released", criteria.released_from is not None or criteria.released_to is not None,
        released_from=None, released_to=None)
    add("Printing Type", bool(criteria.games), games=(), paper_only=False)
    add("Set Type", criteria.set_types is not None and bool(criteria.set_types), set_types=None)
    add("Exact Set", criteria.set_codes is not None and bool(criteria.set_codes), set_codes=None)
    add("English only", criteria.lang == "en", lang="")
    add("Format", bool(criteria.fmt), fmt="")
    add("Rarity", bool(criteria.rarities), rarities=())
    add("Search Scope", tuple(criteria.content_types) != ("card",),
        content_types=("card",))
    return tuple(groups)


def _mode_relaxations(criteria):
    candidates = []

    def add(label, current, replacement_mode, condition, **change):
        if condition and str(current) != replacement_mode:
            candidates.append((label, replacement_mode, replace(criteria, **change)))

    add("Card Type", criteria.card_type_mode, "any", bool(criteria.card_types),
        card_type_mode="any")
    add("Supertype", criteria.supertype_mode, "any", bool(criteria.supertypes),
        supertype_mode="any")
    add("Subtype", criteria.subtype_mode, "any", bool(criteria.subtypes),
        subtype_mode="any")
    add("Mechanics", criteria.keyword_mode, "any", bool(criteria.keywords),
        keyword_mode="any")
    add("Rules Text", criteria.text_mode, "any", bool(criteria.text), text_mode="any")
    add("Card Form", criteria.layout_mode, "any", bool(criteria.layouts),
        layout_mode="any")
    add("Card properties", criteria.trait_mode, "any", bool(criteria.traits),
        trait_mode="any")
    add("Mana Cost Features", criteria.mana_feature_mode, "any",
        bool(criteria.mana_features), mana_feature_mode="any")
    add("Special Properties", criteria.special_property_mode, "any",
        bool(criteria.special_properties), special_property_mode="any")
    add("Product / Status", criteria.status_property_mode, "any",
        bool(criteria.status_properties), status_property_mode="any")
    add("Mana Symbols in Cost", criteria.pip_mode, "any", bool(criteria.pips),
        pip_mode="any")
    return tuple(candidates)


def _relaxed(criteria, facet):
    if facet == "card_types":
        return replace(criteria, card_types=())
    if facet == "supertypes":
        return replace(criteria, supertypes=())
    if facet == "subtypes":
        return replace(criteria, subtypes=())
    if facet == "keywords":
        return replace(criteria, keywords=())
    if facet == "colors":
        return replace(criteria, colors=())
    if facet == "produces":
        return replace(criteria, produces=())
    if facet == "traits":
        return replace(criteria, traits=())
    if facet == "mana_features":
        return replace(criteria, mana_features=())
    if facet == "special_properties":
        return replace(criteria, special_properties=())
    if facet == "status_properties":
        return replace(criteria, status_properties=())
    if facet == "layouts":
        return replace(criteria, layouts=())
    if facet == "pips":
        return replace(criteria, pips=(), pip_min=None)
    if facet == "cmc":
        return replace(criteria, cmc_min=None, cmc_max=None)
    if facet == "power":
        return replace(criteria, power_min=None, power_max=None)
    if facet == "toughness":
        return replace(criteria, toughness_min=None, toughness_max=None)
    if facet == "loyalty":
        return replace(criteria, loyalty_min=None, loyalty_max=None)
    if facet == "defense":
        return replace(criteria, defense_min=None, defense_max=None)
    if facet == "released":
        return replace(criteria, released_from=None, released_to=None)
    if facet == "games":
        return replace(criteria, games=(), paper_only=False)
    if facet == "rarities":
        return replace(criteria, rarities=())
    if facet == "format":
        return replace(criteria, fmt="")
    if facet == "set_types":
        return replace(criteria, set_types=None)
    if facet == "set_codes":
        return replace(criteria, set_codes=None)
    if facet == "lang":
        return replace(criteria, lang="")
    if facet == "content":
        return replace(criteria, content_types=_CONTENT_KEYS)
    return criteria


_FACET_COLUMNS = {
    "card_types": ("type_line",),
    "supertypes": ("type_line",),
    "subtypes": ("type_line",),
    "keywords": ("keywords",),
    "colors": ("colors", "color_identity"),
    "produces": ("produced_mana",),
    "traits": (
        "mana_cost", "card_faces", "universes_beyond", "reserved",
        "game_changer", "color_indicator", "power", "toughness"),
    "mana_features": ("mana_cost",),
    "special_properties": ("card_faces", "color_indicator", "power", "toughness"),
    "status_properties": ("universes_beyond", "reserved", "game_changer"),
    "layouts": ("layout",),
    "pips": ("mana_cost", "pips_w", "pips_u", "pips_b", "pips_r", "pips_g", "pips_c"),
    "cmc": ("cmc",),
    "power": ("power",),
    "toughness": ("toughness",),
    "loyalty": ("loyalty",),
    "defense": ("defense",),
    "released": ("released_at",),
    "games": ("games",),
    "rarities": ("rarity",),
    "format": ("legalities",),
    "set_types": ("set_type",),
    "set_codes": ("set_code", "set_name"),
    "lang": ("lang",),
    "content": ("layout", "type_line"),
}


class SearchContextController:
    """Latest-wins live Search facet worker with cancellation and bounded LRU."""

    CACHE_LIMIT = 20

    def __init__(self, repository):
        self.repository = repository
        self.events = queue.Queue()
        self._condition = threading.Condition()
        self._generation = 0
        self._pending = None
        self._working_generation = None
        self._active_reader = None
        self._closed = False
        self._cache = OrderedDict()
        self._thread = spawn_daemon(self._run, "search-context")

    @property
    def generation(self):
        with self._condition:
            return self._generation

    @property
    def running(self):
        with self._condition:
            return self._pending is not None or self._working_generation is not None

    @staticmethod
    def _vocabulary_payload(**values):
        return {
            "card_types": tuple(values.get("card_types") or ()),
            "supertypes": tuple(values.get("supertypes") or ()),
            "subtypes": tuple(values.get("subtypes") or ()),
            "keywords": tuple(values.get("keywords") or ()),
            "layouts": tuple(values.get("layouts") or ()),
            "rarities": tuple(values.get("rarities") or ()),
            "formats": tuple(values.get("formats") or ()),
            "set_types": tuple(values.get("set_types") or ()),
            "sets": tuple(values.get("sets") or ()),
        }

    @staticmethod
    def _vocabulary_signature(vocabulary):
        return (
            _catalog_signature(vocabulary.get("card_types")),
            _catalog_signature(vocabulary.get("supertypes")),
            _catalog_signature(vocabulary.get("subtypes")),
            _catalog_signature(vocabulary.get("keywords")),
            _catalog_signature(vocabulary.get("layouts")),
            _catalog_signature(vocabulary.get("rarities")),
            _catalog_signature(vocabulary.get("formats")),
            _catalog_signature(vocabulary.get("set_types")),
            tuple((str(item[0]), str(item[1]) if len(item) > 1 else "")
                  if isinstance(item, (tuple, list)) and item else (str(item), "")
                  for item in vocabulary.get("sets", ())),
        )

    def request(self, criteria, *, card_types=(), supertypes=(), subtypes=(),
                keywords=(), layouts=(), rarities=(), formats=(), set_types=(),
                sets=()):
        vocabulary = self._vocabulary_payload(
            card_types=card_types, supertypes=supertypes, subtypes=subtypes,
            keywords=keywords, layouts=layouts, rarities=rarities,
            formats=formats, set_types=set_types, sets=sets)
        cache_key = (criteria.signature(), self._vocabulary_signature(vocabulary))
        with self._condition:
            if self._closed:
                raise RuntimeError("Search context controller is closed")
            self._generation += 1
            generation = self._generation
            reader = self._active_reader
            if reader is not None:
                try:
                    reader.interrupt()
                except Exception:
                    pass
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
                self._pending = None
                self.events.put(SearchContextEvent(
                    generation, criteria.signature(), "done", cached, 0.0))
                self._condition.notify_all()
                return generation
            self._pending = (generation, criteria, vocabulary, cache_key)
            self._condition.notify_all()
        return generation

    def invalidate(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            self._cache.clear()
            reader = self._active_reader
            if reader is not None:
                try:
                    reader.interrupt()
                except Exception:
                    pass
        self._clear_events()

    def _is_current(self, generation):
        with self._condition:
            return generation == self._generation and not self._closed

    def _rows_by_facet(self, generation, reader, criteria):
        grouped = defaultdict(lambda: {"criteria": None, "facets": [], "columns": set()})
        for facet, columns in _FACET_COLUMNS.items():
            relaxed = _relaxed(criteria, facet)
            bucket = grouped[relaxed.signature()]
            bucket["criteria"] = relaxed
            bucket["facets"].append(facet)
            bucket["columns"].update(columns)
        for bucket in grouped.values():
            if not self._is_current(generation):
                return
            rows = self.repository.context_rows(
                bucket["criteria"], reader, columns=tuple(sorted(bucket["columns"])))
            if not self._is_current(generation):
                return
            yield tuple(bucket["facets"]), rows

    def _prepare(self, generation, criteria, vocabulary):
        reader = self.repository.open_reader()
        with self._condition:
            if generation == self._generation:
                self._active_reader = reader
        try:
            result_count = self.repository.count(criteria, reader)
            if not self._is_current(generation):
                return None

            card_types = _catalog_values(vocabulary["card_types"])
            supertypes = _catalog_values(vocabulary["supertypes"])
            subtypes = _catalog_values(vocabulary["subtypes"])
            keywords = _catalog_values(vocabulary["keywords"])
            layouts = _catalog_values(vocabulary["layouts"])
            rarities = _catalog_values(vocabulary["rarities"])
            formats = _catalog_values(vocabulary["formats"])
            set_types = _catalog_values(vocabulary["set_types"])
            set_codes = _catalog_values(vocabulary["sets"])

            output = {
                "card_type_counts": {}, "supertype_counts": {}, "subtype_counts": {},
                "keyword_counts": {}, "color_counts": {}, "produces_counts": {},
                "layout_counts": {}, "rarity_counts": {}, "numeric_ranges": {},
                "numeric_applicability": {}, "release_years": (),
                "release_year_counts": {}, "trait_counts": {},
                "mana_feature_counts": {}, "special_property_counts": {},
                "status_property_counts": {}, "pip_counts": {},
                "content_counts": {}, "game_counts": {}, "set_type_counts": {},
                "set_counts": {}, "format_counts": {}, "english_count": 0,
                "all_language_count": 0,
            }

            for facets, rows in self._rows_by_facet(generation, reader, criteria):
                facets = set(facets)
                if not self._is_current(generation):
                    return None

                type_facets = facets & {"card_types", "supertypes", "subtypes"}
                if type_facets:
                    ct, st, sub = _predict_type_line(
                        rows,
                        card_types=(card_types if "card_types" in type_facets else ()),
                        selected_card_types=criteria.card_types,
                        card_type_mode=criteria.card_type_mode,
                        supertypes=(supertypes if "supertypes" in type_facets else ()),
                        selected_supertypes=criteria.supertypes,
                        supertype_mode=criteria.supertype_mode,
                        subtypes=(subtypes if "subtypes" in type_facets else ()),
                        selected_subtypes=criteria.subtypes,
                        subtype_mode=criteria.subtype_mode)
                    if "card_types" in type_facets:
                        output["card_type_counts"] = ct
                    if "supertypes" in type_facets:
                        output["supertype_counts"] = st
                    if "subtypes" in type_facets:
                        output["subtype_counts"] = sub

                if "keywords" in facets:
                    output["keyword_counts"] = _predict_keywords(
                        rows, keywords, criteria.keywords, criteria.keyword_mode)
                if "colors" in facets:
                    field = "colors" if criteria.color_scope == "colors" else "color_identity"
                    output["color_counts"] = _predict_colors(
                        rows, field, (*COLORS, "C"), criteria.colors,
                        criteria.color_mode, produced=False)
                if "produces" in facets:
                    output["produces_counts"] = _predict_colors(
                        rows, "produced_mana", (*COLORS, "C"), criteria.produces,
                        criteria.produces_mode, produced=True)
                if "traits" in facets:
                    predictor = _PredictiveMembershipCounts(
                        _LEGACY_TRAIT_KEYS, criteria.traits, criteria.trait_mode)
                    for row in rows:
                        predictor.add(_trait_keys(row))
                    output["trait_counts"] = predictor.finish()
                if "mana_features" in facets:
                    predictor = _PredictiveMembershipCounts(
                        _MANA_FEATURE_KEYS, criteria.mana_features,
                        criteria.mana_feature_mode)
                    for row in rows:
                        predictor.add(_trait_keys(row))
                    output["mana_feature_counts"] = predictor.finish()
                if "special_properties" in facets:
                    predictor = _PredictiveMembershipCounts(
                        _SPECIAL_PROPERTY_KEYS, criteria.special_properties,
                        criteria.special_property_mode)
                    for row in rows:
                        predictor.add(_trait_keys(row))
                    output["special_property_counts"] = predictor.finish()
                if "status_properties" in facets:
                    predictor = _PredictiveMembershipCounts(
                        _STATUS_PROPERTY_KEYS, criteria.status_properties,
                        criteria.status_property_mode)
                    for row in rows:
                        predictor.add(_trait_keys(row))
                    output["status_property_counts"] = predictor.finish()
                if "layouts" in facets:
                    output["layout_counts"] = _predict_set_candidates(
                        rows, "layout", layouts, criteria.layouts,
                        criteria.layout_mode)
                if "pips" in facets:
                    output["pip_counts"] = _predict_pips(
                        rows, criteria.pips, criteria.pip_min, criteria.pip_mode)
                for facet in ("cmc", "power", "toughness", "loyalty", "defense"):
                    if facet in facets:
                        bounds, applicable = _numeric_range(rows, facet)
                        output["numeric_ranges"][facet] = bounds
                        output["numeric_applicability"][facet] = applicable
                if "released" in facets:
                    years, counts = _release_context(rows)
                    output["release_years"] = years
                    output["release_year_counts"] = counts
                if "games" in facets:
                    output["game_counts"] = _predict_games(rows, criteria.games)
                if "rarities" in facets:
                    output["rarity_counts"] = _predict_set_candidates(
                        rows, "rarity", rarities, criteria.rarities, "any")
                if "format" in facets:
                    output["format_counts"] = _format_counts(
                        rows, formats, criteria.fmt_status)
                if "set_types" in facets:
                    output["set_type_counts"] = _predict_set_candidates(
                        rows, "set_type", set_types,
                        criteria.set_types or (), "any")
                if "set_codes" in facets:
                    output["set_counts"] = _predict_set_candidates(
                        rows, "set_code", set_codes,
                        criteria.set_codes or (), "any")
                if "lang" in facets:
                    output["all_language_count"] = len(rows)
                    output["english_count"] = sum(
                        1 for row in rows if str(row.get("lang") or "").casefold() == "en")
                if "content" in facets:
                    output["content_counts"] = _predict_content(
                        rows, criteria.content_types)

            suggestions = []
            mode_suggestions = []
            if result_count == 0:
                for label, relaxed in _active_relaxations(criteria):
                    if not self._is_current(generation):
                        return None
                    count = self.repository.count(relaxed, reader)
                    if count:
                        suggestions.append((label, int(count)))
                suggestions.sort(key=lambda item: (-item[1], item[0].casefold()))
                for label, mode, alternate in _mode_relaxations(criteria):
                    if not self._is_current(generation):
                        return None
                    count = self.repository.count(alternate, reader)
                    if count:
                        mode_suggestions.append((label, mode, int(count)))
                mode_suggestions.sort(
                    key=lambda item: (-item[2], item[0].casefold(), item[1]))

            return SearchContextSnapshot(
                result_count=int(result_count),
                suggestions=tuple(suggestions[:5]),
                mode_suggestions=tuple(mode_suggestions[:4]),
                **output)
        finally:
            with self._condition:
                if self._active_reader is reader:
                    self._active_reader = None
            try:
                reader.close()
            except Exception:
                pass

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                generation, criteria, vocabulary, cache_key = self._pending
                self._pending = None
                self._working_generation = generation
            started = time.perf_counter()
            try:
                payload = self._prepare(generation, criteria, vocabulary)
                if payload is None:
                    with self._condition:
                        if self._working_generation == generation:
                            self._working_generation = None
                    continue
                event = SearchContextEvent(
                    generation, criteria.signature(), "done", payload,
                    time.perf_counter() - started)
            except sqlite3.OperationalError as exc:
                # ``Connection.interrupt`` is how a newer draft preempts an old
                # expensive context query.  Interrupted stale work is not an error.
                if not self._is_current(generation) and "interrupt" in str(exc).casefold():
                    with self._condition:
                        if self._working_generation == generation:
                            self._working_generation = None
                    continue
                event = SearchContextEvent(
                    generation, criteria.signature(), "error", str(exc),
                    time.perf_counter() - started)
            except Exception as exc:
                event = SearchContextEvent(
                    generation, criteria.signature(), "error", str(exc),
                    time.perf_counter() - started)
            with self._condition:
                if self._working_generation == generation:
                    self._working_generation = None
                if event.kind == "done" and event.generation == self._generation:
                    self._cache[cache_key] = event.payload
                    self._cache.move_to_end(cache_key)
                    while len(self._cache) > self.CACHE_LIMIT:
                        self._cache.popitem(last=False)
            if self._is_current(generation):
                self.events.put(event)

    def poll_latest(self):
        latest = None
        current = self.generation
        try:
            while True:
                event = self.events.get_nowait()
                if event.generation == current:
                    latest = event
        except queue.Empty:
            return latest

    def shutdown(self, timeout=0.75):
        with self._condition:
            self._closed = True
            self._pending = None
            self._generation += 1
            reader = self._active_reader
            if reader is not None:
                try:
                    reader.interrupt()
                except Exception:
                    pass
            self._condition.notify_all()
        self._thread.join(max(0.0, float(timeout)))
        return not self._thread.is_alive()

    def _clear_events(self):
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass
