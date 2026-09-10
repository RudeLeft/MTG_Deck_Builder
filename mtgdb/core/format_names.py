"""Readable names for Scryfall format keys, shared by every surface.

Scryfall identifies a format by a bare key -- "paupercommander", "duel",
"standardbrawl". Those keys are the app's vocabulary and its lookup identity,
but they are not what a player calls the format, and they read badly in prose:
"commander decks have no sideboard".

This module holds the one mapping from key to name so the Format picker, the
card preview's legality list, and the deck legality report cannot disagree
about what a format is called. It lives in ``core`` because the legality engine
is a pure domain module that must not import UI code, and both need this.

The labels are presentation only. They never authorize vocabulary: the formats
a user can choose still come from the observed Scryfall legalities in the card
database, and a format missing from this mapping is titled from its own key
rather than hidden, so a format this build has never heard of stays usable.

Tk-free and SQLite-free by contract.
"""

from __future__ import annotations


# Keyed by real Scryfall legality keys. A key absent here is not unknown to the
# app -- it is simply one whose title-cased key already reads correctly.
FORMAT_WORD_LABELS = {
    "competitivebrawl": "Competitive Brawl",
    "duel": "Duel Commander",
    "future": "Future Standard",
    "tlr": "Tarkir Dragonstorm Limited",
    "penny": "Penny Dreadful",
    "oathbreaker": "Oathbreaker",
    "oldschool": "Old School",
    "paupercommander": "Pauper Commander",
    "predh": "PreDH",
    "premodern": "Premodern",
    "standardbrawl": "Standard Brawl",
}


def format_display_name(value):
    """Return the readable name for one Scryfall format key.

    An unknown key is title-cased rather than hidden: a format this build has
    never heard of must still be selectable and still name itself in a
    legality message.
    """
    key = str(value or "").strip()
    if not key:
        return ""
    known = FORMAT_WORD_LABELS.get(key.casefold())
    if known:
        return known
    return key.replace("_", " ").title()
