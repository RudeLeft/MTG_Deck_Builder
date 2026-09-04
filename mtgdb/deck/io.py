"""Portable decklist serialization and exact-printing TXT resolution."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile



_LINE_RE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
_HEADER_RE = re.compile(r"^//\s*(.+?)\s*\(([^()]+)\)\s*$")
_SET_TAG_RE = re.compile(
    r"^(.*?)\s+\[([A-Za-z0-9]{2,12})(?::([^\]]+))?\]\s*$")

MAIN_HEADERS = frozenset({
    "deck", "main", "maindeck", "mainboard", "commander", "companion"})
SIDE_HEADERS = frozenset({"sideboard", "side", "sb"})
SKIP_HEADERS = frozenset({
    "maybeboard", "maybe", "considering", "tokens", "attractions",
    "stickers", "about"})


def _line_for(entry):
    card = entry["card"]
    name = card.get("name") or "(unnamed card)"
    set_code = (card.get("set_code") or card.get("set") or "").strip()
    collector = str(card.get("collector_number") or "").strip()
    if set_code and collector:
        suffix = f" [{set_code.upper()}:{collector}]"
    else:
        suffix = f" [{set_code.upper()}]" if set_code else ""
    return f"{entry['qty']} {name}{suffix}"


def deck_to_text(deck):
    """Serialize a portable TXT decklist preserving exact printings."""
    lines = [f"// {deck.name} ({deck.fmt})", ""]
    lines.extend(_line_for(entry) for entry in deck.entries("main"))
    side = deck.entries("side")
    if side:
        lines.extend(("", "Sideboard"))
        lines.extend(_line_for(entry) for entry in side)
    return "\n".join(lines) + "\n"


def save_deck_text(path, deck):
    """Atomically save a TXT decklist so a failed overwrite preserves the old file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(deck_to_text(deck))
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def deck_from_text(text, resolver, name="Imported Deck", fmt="commander",
                   allowed_set_types=None, allowed_set_codes=None,
                   paper_only=True, lang=None, deck_class=None):
    """Parse a TXT decklist and return ``(deck, unresolved_names)``.

    A trailing ``[SET]`` or ``[SET:COLLECTOR]`` tag is authoritative and
    bypasses picker restrictions. Untagged cards honor the supplied Paper,
    language, Set Type, and Exact Set scope from the shared Printings picker.
    Mainboard, sideboard, and ignored section semantics match the legacy importer.
    """
    if deck_class is None:
        from mtgdb.deck.model import Deck
        deck_class = Deck
    deck = deck_class(name, fmt)
    board = "main"
    not_found = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("//"):
            metadata = _HEADER_RE.match(line)
            if metadata:
                saved_name, saved_format = metadata.groups()
                deck.name = saved_name.strip() or deck.name
                saved_format = saved_format.strip().lower()
                if saved_format:
                    deck.fmt = saved_format
            continue
        if line.startswith("#"):
            continue
        header = line.lower().rstrip(":").strip()
        if header in MAIN_HEADERS:
            board = "main"
            continue
        if header in SIDE_HEADERS:
            board = "side"
            continue
        if header in SKIP_HEADERS:
            board = None
            continue
        if board is None:
            continue
        match = _LINE_RE.match(line)
        if match:
            quantity = int(match.group(1))
            card_spec = match.group(2).strip()
            explicit_quantity = True
        else:
            # Many exports omit the leading "1" for singleton entries, so a bare
            # line is resolved as one copy instead of being dropped. An
            # unresolved bare line is treated as an unrecognized section heading
            # ("Creatures (24)") rather than reported, because headings cannot
            # be enumerated in advance.
            quantity = 1
            card_spec = line
            explicit_quantity = False
        if quantity <= 0:
            # "0 Cardname" explicitly requests no copies. Skip the entry instead
            # of letting Deck.add reject it and abort the whole import.
            continue

        set_match = _SET_TAG_RE.match(card_spec)
        if set_match:
            card_name = set_match.group(1).strip()
            explicit_set = set_match.group(2).strip().lower()
            explicit_collector = (set_match.group(3) or "").strip() or None
            card = (resolver.get_by_name(
                card_name,
                allowed_set_types=None,
                allowed_set_codes={explicit_set},
                allowed_collector_numbers=(
                    {explicit_collector}
                    if explicit_collector is not None else None),
                paper_only=False,
                lang=None)
                    if resolver else None)
        else:
            card_name = card_spec
            explicit_set = None
            explicit_collector = None
            card = (resolver.get_by_name(
                card_name,
                allowed_set_types=allowed_set_types,
                allowed_set_codes=allowed_set_codes,
                paper_only=paper_only,
                lang=lang)
                    if resolver else None)

        if card is None:
            if explicit_quantity:
                not_found.append(
                    (f"{card_name} [{explicit_set.upper()}:{explicit_collector}]"
                     if explicit_set and explicit_collector else
                     f"{card_name} [{explicit_set.upper()}]")
                    if explicit_set else card_name)
            continue
        deck.add(card, board, quantity)
    return deck, not_found
