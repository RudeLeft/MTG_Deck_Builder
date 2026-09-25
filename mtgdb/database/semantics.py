"""Shared Scryfall text and type-line semantics used by import and queries."""

from functools import lru_cache
import re

from mtgdb.database.constants import (
    ART_LAYOUTS, CONTENT_TYPES, KNOWN_SCRYFALL_LAYOUTS, NON_CARD_LAYOUTS)


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
    """Whitespace-bounded subtype match supporting any present/future phrase.

    The selected catalog value is matched as a complete run of whitespace-
    delimited subtype words on each face.  Every non-space character -- hyphens,
    apostrophes, question marks and the like -- stays part of its word, so
    ``Plant`` does not match ``Power-Plant``, ``Urza`` does not match
    ``Urza's Saga``, and ``Elemental`` does not match the Un-set ``Elemental?``.
    This mirrors the whitespace tokens the contextual count uses, so the picker's
    per-subtype count equals the result of selecting it.
    """
    target = " ".join(_type_key(wanted).split())
    if not target:
        return 0
    _left, subtype_texts = _type_line_search_parts(str(type_line or ""))
    pattern = re.compile(r"(?<!\S)" + re.escape(target) + r"(?!\S)")
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


# Content kind is a function of the layout column alone (see
# ``_card_content_classification``), so a scope filter can be a pure-SQL layout
# predicate instead of the per-row ``CARD_CONTENT_KIND`` Python function -- the
# same result thousands of times faster over the whole card table.  This mapping
# mirrors the classifier's non-card branches; everything else (NULL, known, and
# unknown layouts) is a ``card``.  ``test_search_architecture`` differentially
# proves the two agree for every layout.
_CONTENT_KIND_LAYOUT_MEMBERS = {
    "art": tuple(ART_LAYOUTS),
    "emblem": ("emblem",),
    "token": ("token", "double_faced_token"),
}


def content_scope_layout_sql(content_types, column):
    """Return ``(sql, params)`` equivalent to ``CARD_CONTENT_KIND(...) IN (...)``.

    ``column`` is the (optionally table-qualified) layout column expression.
    Returns ``(None, [])`` when the scope selects nothing.  Raises ``ValueError``
    on an unrecognized content type, matching the previous inline behavior.
    """
    chosen = {str(value).casefold() for value in content_types if str(value)}
    unknown = chosen - set(CONTENT_TYPES)
    if unknown:
        raise ValueError(
            "Unknown card-content type(s): " + ", ".join(sorted(unknown)))
    if not chosen:
        return None, []
    parts = []
    params = []
    for kind in sorted(chosen):
        if kind == "card":
            placeholders = ",".join("?" * len(NON_CARD_LAYOUTS))
            parts.append(f"({column} IS NULL OR {column} NOT IN ({placeholders}))")
            params.extend(NON_CARD_LAYOUTS)
        else:
            members = _CONTENT_KIND_LAYOUT_MEMBERS[kind]
            placeholders = ",".join("?" * len(members))
            parts.append(f"{column} IN ({placeholders})")
            params.extend(members)
    if len(parts) == 1:
        return parts[0], params
    return "(" + " OR ".join(parts) + ")", params



_MANA_COST_SYMBOL = re.compile(r"\{([^}]+)\}")
_MANA_FILTER_COLORS = frozenset(("W", "U", "B", "R", "G", "C"))


def mana_cost_has_hybrid_symbol(mana_cost):
    """True if any brace symbol offers 2+ non-Phyrexian choices.

    A symbol like ``{W/U}`` or ``{2/W}`` is a genuine hybrid choice. ``{W/P}``
    pairs one colour with a life payment and is not a hybrid choice on its
    own, but a "compleated" three-part symbol like ``{W/U/P}`` (March of the
    Machine) still offers a real W-or-U hybrid choice alongside the life
    option, so it counts too -- excluding every symbol that merely contains
    "P" wrongly excluded those.
    """
    for raw_symbol in _MANA_COST_SYMBOL.findall(str(mana_cost or "")):
        parts = [part for part in str(raw_symbol).upper().split("/")
                 if part != "P"]
        if len(parts) >= 2:
            return True
    return False


def mana_cost_has_phyrexian_symbol(mana_cost):
    """True if any brace symbol includes a Phyrexian ("pay life") option."""
    for raw_symbol in _MANA_COST_SYMBOL.findall(str(mana_cost or "")):
        if "P" in str(raw_symbol).upper().split("/"):
            return True
    return False


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


def pip_minimum_threshold(minimum):
    """Total-symbol threshold implied by the Mana Symbols in Cost Minimum.

    Empty, non-numeric, or below-one values all mean one -- colour presence alone
    -- so the Minimum box's untouched default (``1``) is the same query as no
    minimum at all.  Shared by the SQL matcher, the SQLite context worker and
    the bitset index, so all three read Minimum identically.  It never raises:
    infinity (which ``int()`` cannot convert) reads as one, like NaN.  Rejecting
    a non-finite Minimum is the query builder's job (SRCH-015), not this reader's.
    """
    try:
        return max(1, int(float(minimum if minimum is not None else 1)))
    except (TypeError, ValueError, OverflowError):
        return 1


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

    threshold = pip_minimum_threshold(minimum)

    qualifying_symbols = sum(1 for colors in symbols if colors & wanted)
    if normalized == "any":
        presence_matches = bool(wanted & represented)
    else:
        presence_matches = wanted.issubset(represented)
    return int(presence_matches and qualifying_symbols >= threshold)


def _escape_like(value):
    """Escape SQLite LIKE wildcards so user text is treated literally."""
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# -- numeric stat classification (shared by import, SQL, and the facet engine) --
# Moved here from search/context so the import projection, the search-filter SQL,
# and the in-memory predictive counts all classify power/toughness through one
# implementation rather than three copies that could drift.
_NON_NUMERIC = re.compile(r"[^0-9.\-]")
_NUMERIC_PREFIX = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _glob_numeric(value):
    """True when a TEXT stat passes the search filter's GLOB numeric guard.

    Mirrors ``field NOT GLOB '*[^0-9.-]*' AND field <> ''`` so every caller
    classifies power/toughness exactly as ``TRAIT_CLAUSES`` does.
    """
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


# -- precomputed search columns (populated at import) --------------------------
# Colour membership as a bitmask so the search filter can test presence with an
# indexed integer AND instead of a LIKE over the comma-joined column.
COLOR_BITS = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16, "C": 32}

# Per-card boolean traits packed into one integer, mirroring the filter semantics
# in ``search_queries.TRAIT_CLAUSES`` (and ``facet_index._trait_filter_keys``)
# exactly, so a query built on the column reproduces today's results.
TRAIT_MULTI_FACED = 1
TRAIT_HYBRID_MANA = 2
TRAIT_PHYREXIAN_MANA = 4
TRAIT_HAS_X_COST = 8
TRAIT_VARIABLE_STATS = 16
TRAIT_TOP_HEAVY = 32
TRAIT_COLOR_INDICATOR = 64


def color_mask(comma_text):
    """Bitmask of the colours in a stored comma-joined colour string."""
    mask = 0
    for member in str(comma_text or "").split(","):
        mask |= COLOR_BITS.get(member.strip().upper(), 0)
    return mask


def combined_mana_cost(mana_cost, back_mana_cost=""):
    """Every face's mana cost as one string, for reading cost symbols.

    A two-faced card is found by either face's cost (SRCH-052).  Split and
    adventure costs already arrive as one "A // B" string in ``mana_cost``, so
    their ``back_mana_cost`` is empty and this is a no-op for them; for a
    transform or modal card it appends the back face's cost.
    """
    front = str(mana_cost or "")
    back = str(back_mana_cost or "")
    if front and back:
        return f"{front} // {back}"
    return front or back


def derive_trait_flags(mana_cost, power, toughness, card_faces, color_indicator,
                       back_mana_cost="", back_power=None, back_toughness=None):
    """Pack the per-card boolean traits, computed from stored column values.

    ``card_faces`` and ``color_indicator`` are the stored string forms (a JSON
    array and a comma-joined string) so the multi-faced / colour-indicator bits
    match the filter, which reads those same stored strings.

    Cost traits (hybrid, Phyrexian, X) read every face's cost, and stat traits
    (variable, top-heavy) hold if EITHER face has them -- top-heavy per face, a
    face's own power against its own toughness -- so a card is found by
    whichever face carries the trait (SRCH-052).
    """
    flags = 0
    if card_faces is not None and str(card_faces) not in ("", "[]", "null"):
        flags |= TRAIT_MULTI_FACED
    mana = combined_mana_cost(mana_cost, back_mana_cost)
    if mana_cost_has_hybrid_symbol(mana):
        flags |= TRAIT_HYBRID_MANA
    if mana_cost_has_phyrexian_symbol(mana):
        flags |= TRAIT_PHYREXIAN_MANA
    if "{X}" in mana.upper():
        flags |= TRAIT_HAS_X_COST
    for face_power, face_toughness in (
            (power, toughness), (back_power, back_toughness)):
        power_text = str(face_power or "")
        toughness_text = str(face_toughness or "")
        if "*" in power_text or "*" in toughness_text:
            flags |= TRAIT_VARIABLE_STATS
        if (_glob_numeric(power_text) and _glob_numeric(toughness_text)
                and _cast_real(power_text) > _cast_real(toughness_text)):
            flags |= TRAIT_TOP_HEAVY
    if color_indicator is not None and str(color_indicator) != "":
        flags |= TRAIT_COLOR_INDICATOR
    return flags

