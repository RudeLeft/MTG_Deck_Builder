"""Pure basic deck-construction and Scryfall legality checks."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re

from mtgdb.core.scryfall_json import card_faces as scryfall_card_faces


@dataclass(frozen=True)
class _FormatRule:
    """Basic construction profile for a specifically verified format."""

    exact_main: int | None = None
    min_main: int | None = None
    max_sideboard: int | None = None
    copy_limit: int = 4
    size_note: str = ""


# These profiles are legality-engine rules, not Search-format vocabulary.
# Search continues to discover formats only from observed Scryfall legalities.
# Unsupported/dynamically new formats deliberately fail closed for construction
# checks instead of inheriting a guessed 60/4/15 profile.
_FORMAT_RULES = {
    # Wizards Commander / Commander 1v1.
    "commander": _FormatRule(
        exact_main=100, max_sideboard=0, copy_limit=1,
        size_note="including your commander",
    ),
    "duel": _FormatRule(
        exact_main=100, max_sideboard=0, copy_limit=1,
        size_note="including your commander",
    ),
    # Wizards Oathbreaker: 1 Oathbreaker + 1 Signature Spell + 58 cards.
    "oathbreaker": _FormatRule(
        exact_main=60, max_sideboard=0, copy_limit=1,
        size_note="including your Oathbreaker and Signature Spell",
    ),
    # Wizards Brawl and Competitive Brawl are current 100-card singleton decks.
    "brawl": _FormatRule(
        exact_main=100, max_sideboard=0, copy_limit=1,
        size_note="including your commander",
    ),
    "competitivebrawl": _FormatRule(
        exact_main=100, max_sideboard=0, copy_limit=1,
        size_note="including your commander",
    ),
    # Current ordinary Constructed formats whose basic construction profile is
    # covered by Wizards format pages / Comprehensive Rules 100.2a and 100.4a.
    "standard": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "pioneer": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "modern": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "legacy": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "vintage": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "pauper": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "historic": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "timeless": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
    "alchemy": _FormatRule(min_main=60, max_sideboard=15, copy_limit=4),
}
_FORMAT_KEY_RE = re.compile(r"[^a-z0-9]+")
_ANY_NUMBER_RE_TEMPLATE = (
    r"\b(?:a|your) deck can have any number of cards named {name}(?:\.|$)"
)
_UP_TO_RE_TEMPLATE = (
    r"\b(?:a|your) deck can have up to (?P<count>[a-z-]+|\d+) "
    r"cards named {name}(?:\.|$)"
)
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _format_key(value):
    """Normalize a display/Scryfall format spelling for rule lookup only."""
    return _FORMAT_KEY_RE.sub("", str(value or "").casefold())


def normalize_legalities(raw):
    """Return a normalized Scryfall legality mapping from JSON text or mapping.

    Search/database rows normally carry JSON text while hydrated/direct card
    objects can already carry a decoded mapping. Callers must accept both.
    Malformed/non-object values fail closed to an empty mapping.
    """
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key, status in value.items():
        if key is None or status is None:
            continue
        result[str(key).casefold()] = str(status).casefold()
    return result


def _status_for_format(legalities, format_key):
    """Return the status for one format, tolerating harmless key punctuation."""
    if format_key in legalities:
        return legalities[format_key]
    for key, status in legalities.items():
        if _format_key(key) == format_key:
            return status
    return None


def _oracle_text(card):
    """Return every face's rules text for one card row or API object.

    ``card_faces`` arrives as JSON text from the database and as a list from a
    raw Scryfall object; the shared parser normalizes both. Iterating the raw
    column would walk characters and silently drop all face text.
    """
    texts = []
    text = card.get("oracle_text")
    if text:
        texts.append(str(text))
    for face in scryfall_card_faces(card):
        if face.get("oracle_text"):
            texts.append(str(face["oracle_text"]))
    return "\n".join(texts)


def _is_basic_land(card):
    """Match the actual Basic + Land type words, not a substring anywhere."""
    type_line = str(card.get("type_line") or "")
    for face in re.split(r"\s*//\s*", type_line):
        left = re.split(r"\s+(?:—|–|-)\s+", face, maxsplit=1)[0]
        words = {word.casefold() for word in left.split()}
        if "basic" in words and "land" in words:
            return True
    return False


def _parse_small_number(value):
    text = str(value or "").strip().casefold()
    if text.isdigit():
        number = int(text)
        return number if number >= 0 else None
    if text in _ONES:
        return _ONES[text]
    if text in _TENS:
        return _TENS[text]
    parts = text.split("-")
    if len(parts) == 2 and parts[0] in _TENS and parts[1] in _ONES:
        return _TENS[parts[0]] + _ONES[parts[1]]
    return None


def _card_copy_limit(card, default_limit):
    """Return a card-specific copy limit, or None for unlimited copies."""
    if _is_basic_land(card):
        return None

    name = str(card.get("name") or "").strip()
    text = _oracle_text(card)
    if not name or not text:
        return default_limit

    escaped_name = re.escape(name)
    if re.search(
            _ANY_NUMBER_RE_TEMPLATE.format(name=escaped_name),
            text, flags=re.IGNORECASE):
        return None

    match = re.search(
        _UP_TO_RE_TEMPLATE.format(name=escaped_name),
        text, flags=re.IGNORECASE,
    )
    if match:
        parsed = _parse_small_number(match.group("count"))
        if parsed is not None:
            return parsed
    return default_limit


def _format_display(deck):
    return str(deck.fmt or "Selected Format").strip() or "Selected Format"


def legality_problems(deck):
    """Return problems found by the app's available basic selected-format checks.

    Per-card banned/restricted/not-legal status still comes from each card's
    Scryfall legalities. Deck-construction checks run only for specifically
    verified format profiles; unknown future formats are reported as unverified
    rather than silently inheriting generic Constructed assumptions.
    """
    format_display = _format_display(deck)
    deck_format = _format_key(format_display)
    problems = []
    main_total = deck.total("main")
    side_total = deck.total("side")
    rule = _FORMAT_RULES.get(deck_format)

    if rule is None:
        problems.append(
            f"Deck-construction rules are not verified for {format_display}; "
            "size, sideboard, and copy-limit checks were skipped."
        )
    else:
        if rule.exact_main is not None and main_total != rule.exact_main:
            note = f" ({rule.size_note})" if rule.size_note else ""
            problems.append(
                f"Mainboard is {main_total} cards; {format_display} requires "
                f"exactly {rule.exact_main}{note}."
            )
        elif rule.min_main is not None and main_total < rule.min_main:
            problems.append(
                f"Mainboard is {main_total} cards; minimum is {rule.min_main}."
            )

        if rule.max_sideboard == 0 and side_total:
            problems.append(
                f"{format_display} decks have no sideboard "
                f"({side_total} cards in yours)."
            )
        elif (rule.max_sideboard is not None
              and side_total > rule.max_sideboard):
            problems.append(
                f"Sideboard is {side_total} cards; "
                f"maximum is {rule.max_sideboard}."
            )

    groups = {}
    for entry in deck.entries():
        card = entry["card"]
        key = card.get("oracle_id") or (card.get("name") or "").casefold()
        bucket = groups.setdefault(key, {
            "name": card.get("name") or "(unnamed card)",
            "qty": 0,
            "cards": [],
        })
        bucket["qty"] += int(entry.get("qty", 0))
        bucket["cards"].append(card)

    missing_status_count = 0
    for bucket in groups.values():
        name = bucket["name"]
        total_quantity = bucket["qty"]

        if rule is not None:
            limits = [
                _card_copy_limit(card, rule.copy_limit)
                for card in bucket["cards"]
            ]
            finite_limits = [limit for limit in limits if limit is not None]
            limit = None if len(finite_limits) != len(limits) else max(finite_limits)
            if limit is not None and total_quantity > limit:
                problems.append(
                    f"{name}: {total_quantity} copies "
                    f"(limit {limit} in {format_display})."
                )

        statuses = set()
        for card in bucket["cards"]:
            legalities = normalize_legalities(card.get("legalities"))
            status = _status_for_format(legalities, deck_format)
            if status:
                statuses.add(status)

        if not statuses:
            missing_status_count += 1
        elif "banned" in statuses:
            problems.append(f"{name} is BANNED in {format_display}.")
        elif "not_legal" in statuses:
            problems.append(f"{name} is not legal in {format_display}.")
        elif "restricted" in statuses and total_quantity > 1:
            problems.append(
                f"{name} is restricted in {format_display} "
                f"(max 1 copy; you have {total_quantity})."
            )

    if missing_status_count:
        noun = "card" if missing_status_count == 1 else "cards"
        problems.append(
            f"Scryfall legality status is unavailable for {missing_status_count} "
            f"unique {noun} in {format_display}; card legality could not be "
            "fully verified."
        )

    return problems
