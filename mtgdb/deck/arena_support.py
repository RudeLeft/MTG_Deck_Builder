"""Which card names Proxic Arena can play, and which ones a deck carries that it cannot.

Proxic Arena resolves a card by name: it normalizes the name, looks for a card
script of that name, and plays whatever that script says. A name it holds no
script for is not a failed cast but a card it never finds, so a deck exported
from here can lose cards on import with nothing said about it.

This module answers only the name question. The supported names ship as a
generated asset, located by ``ARENA_SUPPORTED_CARDS_FILE`` in ``ui/assets.py``,
because the two applications are separate projects and neither reads the
other's tree at runtime; the asset is regenerated when the bundled Arena card
scripts are updated, per DECK-012.

The normalization mirrors Arena's own lookup: accents are stripped, the name is
lowercased, apostrophes and commas are dropped, and every other run of
non-alphanumeric characters becomes one underscore. Both the curly and the
straight apostrophe are dropped, which Arena's own rule does not do, because
the two spellings of one name must not read as two different cards.
"""

from __future__ import annotations

import json
import unicodedata

# The asset's payload shape. A future generator that changes the meaning of
# `names` raises this, and a reader that does not recognize a version declines
# the asset rather than guessing at its contents.
ASSET_VERSION = 1

FACE_SEPARATOR = " // "


def front_face_name(name):
    """Return the face a card is played from, which is the one Arena looks up.

    A stored name holds every face ("Delver of Secrets // Insectile
    Aberration"); Arena files such a card under its front face alone.
    """
    text = str(name or "")
    head = text.split(FACE_SEPARATOR, 1)[0]
    return head.strip()


def normalize_card_name(name):
    """Return the lookup key Arena would derive from ``name``."""
    decomposed = unicodedata.normalize("NFKD", str(name or ""))
    letters = []
    for character in decomposed:
        if unicodedata.combining(character):
            continue
        lowered = character.lower()
        if lowered in ("'", "\u2019", ","):
            continue
        if not ("a" <= lowered <= "z" or "0" <= lowered <= "9"):
            if letters and letters[-1] == "_":
                continue
            lowered = "_"
        letters.append(lowered)
    return "".join(letters).strip("_")


def load_supported_names(path):
    """Return the normalized names Arena can play, or an empty set when unavailable.

    A missing, unreadable, or unrecognized asset yields no names rather than an
    exception: this runs after a deck has already been written to disk, and a
    broken asset MUST NOT turn a successful save into a failure. An empty
    result means "not known", and callers report nothing.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(payload, dict) or payload.get("version") != ASSET_VERSION:
        return frozenset()
    names = payload.get("names")
    if not isinstance(names, list):
        return frozenset()
    return frozenset(
        name for name in names if isinstance(name, str) and name)


def unsupported_deck_names(deck, supported):
    """Return the deck's distinct card names Arena holds no script for.

    Names are returned as they are stored, so they can be shown to the person
    who recognizes them, and deduplicated by lookup key, so one card listed in
    both boards is reported once. An empty ``supported`` set means the asset
    was unavailable, and nothing is reported.
    """
    if not supported:
        return []
    found = {}
    for board in ("main", "side"):
        for entry in deck.entries(board):
            card = entry.get("card") or {}
            stored = str(card.get("name") or "").strip()
            if not stored:
                continue
            key = normalize_card_name(front_face_name(stored))
            if not key or key in supported:
                continue
            found.setdefault(key, stored)
    return sorted(found.values(), key=str.casefold)
