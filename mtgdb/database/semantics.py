"""Shared Scryfall text and type-line semantics used by import and queries."""

from functools import lru_cache
import re

from mtgdb.database.constants import ART_LAYOUTS, KNOWN_SCRYFALL_LAYOUTS


def _face0(card):
    faces = card.get("card_faces")
    return faces[0] if faces else {}


_RULES_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u0060": "'", "\u00b4": "'",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ",
})


def _normalize_rules_text(value):
    """Normalize user/Scryfall rules text for forgiving literal matching.

    The original oracle_text column remains untouched for display. This search
    copy folds common smart punctuation, line breaks and repeated whitespace so
    ordinary keyboard input matches Scryfall typography consistently.
    """
    text = str(value or "").translate(_RULES_TRANSLATION)
    # Sentence punctuation should not make a visually obvious phrase fail just
    # because Scryfall uses a period/comma/semicolon between words or abilities.
    text = re.sub(r"[.,;:!?]+", " ", text)
    text = " ".join(text.split())
    return text.casefold()


_BFM_NAME_KEY = "b.f.m. (big furry monster)"
_BFM_COMPLETE_TYPE_LINE = (
    "Creature — The-Biggest-Baddest-Nastiest-Scariest-Creature-"
    "You'll-Ever-See"
)


def _raw_type_line(card):
    """Return Scryfall's unmodified parent/front-face type-line fragment."""
    face = _face0(card)
    return card.get("type_line") or face.get("type_line") or ""


def _complete_type_line(card):
    """Return the logical full type line used by discovery and search.

    Most Scryfall objects already contain a complete parent type line.  When a
    future multi-face layout only supplies face lines, join them without losing
    either face.  Unglued's B.F.M. is the historical exception: its two physical
    printings have the same name but each object contains only half of the type
    line.  Scryfall relates them as same-name ``combo_piece`` objects, allowing
    us to reconstruct the one official hyphenated creature subtype safely.
    """
    raw = _raw_type_line(card)
    if _type_key(card.get("name")) == _BFM_NAME_KEY:
        parts = [part for part in (card.get("all_parts") or [])
                 if part.get("component") == "combo_piece"
                 and _type_key(part.get("name")) == _BFM_NAME_KEY]
        numbers = {str(part.get("collector_number") or "") for part in parts}
        # Constrain the repair to the documented Unglued pair.  This avoids
        # guessing about a future same-name combo card with different grammar.
        if ((str(card.get("set") or "").casefold() == "ugl") and
                ({"28", "29"} <= numbers or
                 str(card.get("collector_number") or "") in {"28", "29"})):
            return _BFM_COMPLETE_TYPE_LINE

    face_lines = [str(face.get("type_line") or "").strip()
                  for face in (card.get("card_faces") or [])
                  if str(face.get("type_line") or "").strip()]
    if face_lines and (not raw or (len(face_lines) > 1 and "//" not in raw)):
        return " // ".join(face_lines)
    return raw


def _type_key(value):
    return str(value or "").replace("’", "'").replace("‘", "'").casefold()


_ART_LAYOUT_KEYS = frozenset(str(value).casefold() for value in ART_LAYOUTS)
@lru_cache(maxsize=16384)
def _split_type_line(type_line):
    """Return (type/supertype words, subtype text) for one card face.

    Accept Scryfall's em dash plus common plain-text en-dash/hyphen exports,
    while never splitting a legitimate hyphenated subtype such as Power-Plant.
    """
    line = re.split(r"\s*//\s*", str(type_line or ""), maxsplit=1)[0]
    match = re.search(r"\s+(?:—|–|-)\s+", line)
    if match:
        left, right = line[:match.start()], line[match.end():]
    else:
        left, right = line, ""
    return left.strip(), right.strip()


@lru_cache(maxsize=16384)
def _type_line_faces(type_line):
    """Return parsed ``(left, right)`` pairs for every ``//`` card face."""
    faces = []
    for fragment in re.split(r"\s*//\s*", str(type_line or "")):
        fragment = fragment.strip()
        if fragment:
            faces.append(_split_type_line(fragment))
    return tuple(faces)


def _semantic_type_face_parts(left, right):
    """Return structural left-side words and subtype text for one card face.

    Picker vocabulary is authorized later by cached Scryfall catalogs. This
    parser therefore does not contain a hardcoded Magic taxonomy. The sole
    historical grammar repair is pre-Sixth-Edition ``Summon X`` ->
    ``Creature — X``; arbitrary dashless words may remain explicitly searchable
    by non-UI callers but cannot become interactive Card Type vocabulary unless
    Scryfall's ``card-types`` catalog authorizes them.
    """
    raw_words = [word for word in str(left or "").split() if word]
    if not right and raw_words and _type_key(raw_words[0]) == "summon":
        return ("Creature",), " ".join(raw_words[1:]).strip()
    return tuple(raw_words), str(right or "").strip()


@lru_cache(maxsize=16384)
def _type_line_search_parts(type_line):
    """Cached left-side word sequences and full subtype strings for all faces.

    Storing word *sequences* rather than a flat word set keeps current one-word
    Card Type/Supertype matching fast while allowing an authoritative future
    value such as ``Quantum Being`` to remain atomic and searchable.
    """
    left_sequences = []
    subtype_texts = []
    for left, right in _type_line_faces(type_line):
        type_words, subtype_text = _semantic_type_face_parts(left, right)
        keys = tuple(_type_key(word) for word in type_words if _type_key(word))
        if keys:
            left_sequences.append(keys)
        normalized = " ".join(_type_key(subtype_text).split())
        if normalized:
            subtype_texts.append(normalized)
    return tuple(left_sequences), tuple(subtype_texts)


def _card_has_type(type_line, wanted):
    """Boundary-safe phrase match on the left side of every type-line face.

    Picker vocabulary still comes only from authoritative upstream sources.
    This matcher merely avoids assuming those future authoritative values must
    always be a single word.
    """
    target = tuple(_type_key(wanted).split())
    if not target:
        return 0
    width = len(target)
    left_sequences, _subtypes = _type_line_search_parts(str(type_line or ""))
    for sequence in left_sequences:
        for index in range(0, len(sequence) - width + 1):
            if sequence[index:index + width] == target:
                return 1
    return 0


def _card_has_subtype(type_line, wanted):
    """Boundary-safe subtype match supporting any present/future phrase.

    Multi-word subtypes are catalog data, not source-code exceptions.  The
    selected catalog value is therefore matched as a complete contiguous phrase
    on each face.  Hyphens remain word characters for boundary purposes so
    ``Plant`` cannot accidentally match ``Power-Plant``.
    """
    target = " ".join(_type_key(wanted).split())
    if not target:
        return 0
    _left, subtype_texts = _type_line_search_parts(str(type_line or ""))
    pattern = re.compile(r"(?<![\w-])" + re.escape(target) + r"(?![\w-])")
    return 1 if any(pattern.search(text) for text in subtype_texts) else 0


def _card_content_classification(layout, type_line):
    """Return the internal content interpretation for one Scryfall row.

    ``unknown`` is diagnostic-only: it marks a future layout whose semantics are
    not yet understood. Missing layouts stay ``card`` for sparse/legacy rows.
    """
    layout_key = str(layout or "").casefold().strip()
    if layout_key in _ART_LAYOUT_KEYS:
        return "art"
    if layout_key == "emblem":
        return "emblem"
    if layout_key in {"token", "double_faced_token"}:
        return "token"
    if not layout_key or layout_key in KNOWN_SCRYFALL_LAYOUTS:
        return "card"
    return "unknown"


def _card_content_kind(layout, type_line):
    """Return the stable user-facing Cards/Tokens/Emblems/Art-Series Search class.

    Unknown future layouts deliberately remain visible as ``card`` instead of
    disappearing from Search. Their provisional status is retained separately
    by ``_card_content_classification`` and compatibility diagnostics.
    """
    classification = _card_content_classification(layout, type_line)
    return "card" if classification == "unknown" else classification



_MANA_COST_SYMBOL = re.compile(r"\{([^}]+)\}")
_MANA_FILTER_COLORS = frozenset(("W", "U", "B", "R", "G", "C"))


@lru_cache(maxsize=65536)
def _mana_cost_symbol_colors(mana_cost):
    """Return represented filter colors for each physical mana symbol.

    Each tuple entry is one brace-delimited mana symbol. A hybrid such as
    ``{W/B}`` therefore yields one entry representing both W and B rather than
    two pips. Generic, variable, snow, and other non-WUBRGC symbols yield an
    empty set and do not count toward Mana Symbols in Cost Minimum.
    """
    output = []
    for raw_symbol in _MANA_COST_SYMBOL.findall(str(mana_cost or "")):
        represented = frozenset(
            part for part in str(raw_symbol).upper().split("/")
            if part in _MANA_FILTER_COLORS
        )
        output.append(represented)
    return tuple(output)


def _mana_cost_symbol_match(mana_cost, selected, mode="all", minimum=1):
    """Match the interactive Mana Symbols in Cost semantics.

    ``All`` requires every selected color to be represented; ``Any`` requires
    at least one; ``None`` excludes every selected color. For All/Any, Minimum
    is the total number of *physical* symbols that represent at least one
    selected color. One hybrid symbol can satisfy multiple color-presence
    requirements, but it contributes only one to that total.
    """
    if isinstance(selected, str):
        raw_values = selected.split(",")
    else:
        raw_values = selected or ()
    wanted = {
        str(value).strip().upper() for value in raw_values
        if str(value).strip().upper() in _MANA_FILTER_COLORS
    }
    if not wanted:
        return 1

    symbols = _mana_cost_symbol_colors(str(mana_cost or ""))
    represented = set().union(*symbols) if symbols else set()
    normalized = str(mode or "all").casefold()

    if normalized == "none":
        return int(not bool(wanted & represented))

    try:
        threshold = max(1, int(float(minimum if minimum is not None else 1)))
    except (TypeError, ValueError):
        threshold = 1

    qualifying_symbols = sum(1 for colors in symbols if colors & wanted)
    if normalized == "any":
        presence_matches = bool(wanted & represented)
    else:
        presence_matches = wanted.issubset(represented)
    return int(presence_matches and qualifying_symbols >= threshold)


def _escape_like(value):
    """Escape SQLite LIKE wildcards so user text is treated literally."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

