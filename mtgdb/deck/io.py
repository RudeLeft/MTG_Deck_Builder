"""Portable decklist serialization and exact-printing TXT resolution."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile

from mtgdb.core.atomic_files import (
    TEMP_SUFFIX, sweep_abandoned_writes, temp_prefix,
)



_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")

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


def read_deck_text(path):
    """Return the text of a decklist this application may not have written.

    A decklist arrives from another builder, an exporter or a text editor, so
    its encoding is not ours to assume. Three real cases have to work:

    * A byte-order mark, which Notepad and many exporters add. Left in place it
      is worse than a decoding nuisance: an invisible character in front of
      ``// Name (format)`` stops that line being recognised as the header, so
      the deck silently loses its saved format, falls back to Commander, and a
      60-card Modern deck is then reported as needing 100 cards with every
      playset over the singleton limit.
    * UTF-16, which older Windows editors write when asked for "Unicode".
    * Windows-1252, which older exports still use, and which only fails once a
      name like Lim-Dûl's Vault or Jötun Grunt appears.

    Marks are matched on the raw bytes so an encoding is never guessed from
    content: ``utf-16`` without a mark would happily decode UTF-8 into
    nonsense. cp1252 is the last resort because it decodes any byte, so a
    decklist never reaches the user as a raw codec error.
    """
    data = Path(path).read_bytes()
    if data.startswith(_UTF8_BOM):
        text = data.decode("utf-8-sig")
    elif data.startswith(_UTF16_BOMS):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    # A mark can also survive re-encoding by an intermediate tool; the header
    # must not be hidden behind one however it arrived.
    return text.lstrip("\ufeff")


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
    # Deck files land in folders the user chooses, so only this deck's own
    # abandoned temporaries are swept, and only when writing here anyway.
    sweep_abandoned_writes(target.parent, target.name)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=temp_prefix(target.name), suffix=TEMP_SUFFIX, dir=target.parent)
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
    bypasses optional resolver restrictions. Untagged cards honor any explicit
    scope arguments supplied by the caller; the interactive Open Deck workflow
    deliberately supplies no such restrictions and resolves against the complete
    local card database. Mainboard, sideboard, and ignored section semantics
    match the legacy importer.
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
                lang=None,
                # An explicit [SET]/[SET:COLLECTOR] tag is authoritative and may
                # legitimately name a token/emblem/art printing (this app exports
                # deck tokens as tagged entries), so keep those layouts eligible.
                allow_non_card=True)
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
