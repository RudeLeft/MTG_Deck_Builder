"""Optional Search filter registry and the on-demand filter panel.

The Search form used to render every filter permanently, so each new filter
cost vertical space for every user whether or not they used it -- and that
space comes straight out of the Results table, which shares the column with no
sash between them. This module inverts that: a filter is built when it is
added and destroyed when it is removed, so an unused filter costs nothing.

The registry is deliberately data, not widgets. Each entry names its category,
its label, the tooltip that explains what the filter actually matches, and the
builder that constructs its row. `ui/search.py` owns the controls themselves;
this module owns which of them currently exist.
"""

from __future__ import annotations


CATEGORY_ORDER = ("Mana", "Card", "Printing")

# Tooltip wording rule, applied to every filter including the pinned ones:
# one sentence saying what the filter matches, then at most one more for the
# boundary people get wrong about it. Describe cards, not where the data came
# from -- naming a data source tells the user nothing about their search.
FILTER_DEFINITIONS = (
    {
        "key": "mana_value",
        "category": "Mana",
        "label": "Mana value",
        "tooltip": (
            "The total cost of a card, counting coloured and generic mana "
            "together: {2}{G} is 3. Leave a box empty for no limit on that "
            "side."),
    },
    {
        "key": "produces",
        "category": "Mana",
        "label": "Produces",
        "tooltip": (
            "The mana a card can make, which is not the same as its colour. "
            "Birds of Paradise is green but makes all five colours, and "
            "Command Tower is colourless but makes any of them."),
    },
    {
        "key": "stats",
        "category": "Card",
        "label": "Power / Toughness",
        "tooltip": (
            "Printed power and toughness, compared as numbers. Cards with "
            "variable stats such as */* have no number to compare, so they "
            "are left out."),
    },
    {
        "key": "loyalty",
        "category": "Card",
        "label": "Loyalty (Planeswalker)",
        "tooltip": (
            "The starting loyalty printed on a planeswalker. Only "
            "planeswalkers have loyalty, so this filter always narrows the "
            "search to them."),
    },
    {
        "key": "defense",
        "category": "Card",
        "label": "Defense (Battle)",
        "tooltip": (
            "The defense printed on a battle. Kept separate from Loyalty "
            "because no card has both, so combining the two would always "
            "find nothing."),
    },
    {
        "key": "supertypes",
        "category": "Card",
        "label": "Supertypes",
        "tooltip": (
            "The words in front of the card type, such as Legendary, Basic "
            "or Snow. Most cards have none, so this filter narrows a search "
            "sharply."),
    },
    {
        "key": "subtype",
        "category": "Card",
        "label": "Subtype",
        "tooltip": (
            "The words after the dash on the type line: creature types such "
            "as Goblin, land types such as Island, and Equipment or Aura "
            "subtypes."),
    },
    {
        "key": "mechanics",
        "category": "Card",
        "label": "Mechanics",
        "tooltip": (
            "Named abilities such as Flying, Scry or Landfall. Finds cards "
            "that actually have the ability, not cards that merely mention "
            "its name in their rules text."),
    },
    {
        "key": "rules_text",
        "category": "Card",
        "label": "Rules text",
        "tooltip": (
            "Words in a card's rules text, across every face. Each entry has "
            "to appear, with other words allowed in between; put quotes "
            "around an entry to require that exact phrase."),
    },
    {
        "key": "card_shape",
        "category": "Card",
        "label": "Card shape",
        "tooltip": (
            "How the card is printed: Adventure, Saga, Split, Flip, Meld, "
            "Class, Leveler and the rest. A card has exactly one shape, so "
            "this filter offers Any and None rather than All."),
    },
    {
        "key": "mana_pips",
        "category": "Mana",
        "label": "Colored pips",
        "tooltip": (
            "How many colored mana symbols the cost has, counted per color: "
            "two green finds {G}{G} and {2}{G}{G}. A hybrid symbol counts for "
            "both of its colors, the way devotion reads it."),
    },
    {
        # Not "printings": that key belongs to the pinned Printings filter,
        # which chooses which printings a search may return at all.
        "key": "print_count",
        "category": "Printing",
        "label": "Printed in",
        "tooltip": (
            "How many different sets the card has appeared in. One finds "
            "cards printed only once; two or more finds everything reprinted. "
            "This counts every set, so narrowing the search does not change "
            "it."),
    },
    {
        "key": "traits",
        "category": "Card",
        "label": "Card traits",
        "tooltip": (
            "Yes-or-no facts that no other filter covers: Universes Beyond, "
            "Reserved List, Commander game changers, two-faced cards, hybrid "
            "and Phyrexian costs, and creatures with more power than "
            "toughness."),
    },
    {
        "key": "format",
        "category": "Printing",
        "label": "Format",
        "tooltip": (
            "Cards with the legality you choose in the format you choose: "
            "playable, banned or restricted. Playable covers both legal and "
            "restricted cards."),
    },
    {
        "key": "rarity",
        "category": "Printing",
        "label": "Rarity",
        "tooltip": (
            "The rarity of an individual printing rather than of the card. A "
            "card printed at both common and mythic can be found under "
            "either one."),
    },
    {
        "key": "released",
        "category": "Printing",
        "label": "Released",
        "tooltip": (
            "The year a printing came out, with both years included. An old "
            "card reprinted recently matches the recent year, not the year "
            "it was first printed."),
    },
)

# The four filters that are always present explain themselves the same way the
# optional ones do. Their controls are built by ui/search.py, but the wording
# belongs with every other filter's wording so the whole panel reads as one
# voice rather than four exceptions.
PINNED_FILTER_TOOLTIPS = {
    "name": (
        "Matches any card whose name contains what you type, so bolt finds "
        "Lightning Bolt. Searching from a deck selection looks for those "
        "exact names instead."),
    "colors": (
        "The colours of a card, taken from its mana cost, its rules text and "
        "both of its faces. The row underneath decides whether those colours "
        "must match exactly, be included, or simply not be exceeded."),
    "card_type": (
        "The main type on the type line, such as Creature, Instant or Land. "
        "A card with two of them, like an Artifact Creature, matches either "
        "one."),
    "printings": (
        "Which printings a search may return: platform, set type, individual "
        "sets and language. It also decides what the other filters have to "
        "offer, so narrowing it here narrows them too."),
}

FILTER_BY_KEY = {entry["key"]: entry for entry in FILTER_DEFINITIONS}

# Present in most searches, so they are never removable and never appear in the
# catalogue. Printings is pinned for a second reason: it carries the Paper and
# English scope every search depends on, and it composes the shared
# PrintingFilter that Open Deck also builds, so its widget lifecycle is not the
# Search panel's to shorten.
PINNED_FILTERS = ("name", "colors", "card_type", "printings")


def filter_catalog(active_keys):
    """Return the Add-filter menu contents grouped in category order.

    Each entry reports whether it is already active, so the menu can show it
    as added rather than silently doing nothing when chosen twice.
    """
    active = {str(key) for key in (active_keys or ())}
    grouped = []
    for category in CATEGORY_ORDER:
        entries = [
            {
                "key": entry["key"],
                "label": entry["label"],
                "tooltip": entry["tooltip"],
                "active": entry["key"] in active,
            }
            for entry in FILTER_DEFINITIONS
            if entry["category"] == category
        ]
        if entries:
            grouped.append((category, tuple(entries)))
    return tuple(grouped)


def ordered_active_filters(active_keys):
    """Order active filters by category so added rows never shuffle.

    Insertion order would let the panel rearrange itself as filters are added
    and removed, which makes a row hard to find again. Registry order is
    stable for the life of the build.
    """
    active = {str(key) for key in (active_keys or ())}
    return tuple(
        entry["key"] for entry in FILTER_DEFINITIONS if entry["key"] in active)


def is_removable(key):
    """Pinned filters have no remove control; everything else does."""
    return str(key) not in PINNED_FILTERS and str(key) in FILTER_BY_KEY
