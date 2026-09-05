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

# Tooltip wording rule: say what the filter matches against, not what the
# control is. A user who reads only the tooltip should know whether the filter
# answers their question, and where its answer differs from a neighbouring one.
FILTER_DEFINITIONS = (
    {
        "key": "mana_value",
        "category": "Mana",
        "label": "Mana value",
        "tooltip": (
            "Total converted mana cost, counting generic and coloured mana "
            "together. A card costing {2}{G} has mana value 3. Leave a bound "
            "empty for no limit on that side."),
    },
    {
        "key": "produces",
        "category": "Mana",
        "label": "Produces",
        "tooltip": (
            "Mana the card can actually make, from Scryfall's produced-mana "
            "data. This is not the card's colour: Birds of Paradise is green "
            "but produces all five, and Command Tower has no colour at all. "
            "Includes cards whose text only says “any colour”."),
    },
    {
        "key": "stats",
        "category": "Card",
        "label": "Power / toughness",
        "tooltip": (
            "Printed power and toughness as numbers. Cards with variable "
            "stats such as */* are excluded, because there is no number to "
            "compare. Use the Properties filter for the top-heavy check."),
    },
    {
        "key": "loyalty",
        "category": "Card",
        "label": "Loyalty",
        "tooltip": (
            "Starting loyalty printed on a planeswalker. Cards without loyalty "
            "are excluded rather than counted as zero, so this filter always "
            "narrows to planeswalkers."),
    },
    {
        "key": "defense",
        "category": "Card",
        "label": "Defense",
        "tooltip": (
            "Defense printed on a battle. Separate from Loyalty because no "
            "card has both, so combining them would always find nothing."),
    },
    {
        "key": "supertypes",
        "category": "Card",
        "label": "Supertypes",
        "tooltip": (
            "Words before the dash on the type line, such as Legendary, "
            "Basic or Snow. Vocabulary comes from the current Comprehensive "
            "Rules, so it never contains invented words."),
    },
    {
        "key": "subtype",
        "category": "Card",
        "label": "Subtype",
        "tooltip": (
            "Words after the dash on the type line — creature types like "
            "Goblin, land types like Island, and equipment or aura subtypes. "
            "Only values that appear on a card in the current scope are "
            "offered."),
    },
    {
        "key": "mechanics",
        "category": "Card",
        "label": "Mechanics",
        "tooltip": (
            "Named keyword abilities, keyword actions and ability words such "
            "as Flying, Scry or Landfall. Matches Scryfall's keyword data "
            "rather than searching rules text, so it will not match a card "
            "that merely mentions the word."),
    },
    {
        "key": "rules_text",
        "category": "Card",
        "label": "Rules text",
        "tooltip": (
            "Searches the full Oracle text of every face. Each chip must "
            "appear, with other words allowed between its words; wrap a chip "
            "in double quotes to require that exact phrase. Reminder text is "
            "included."),
    },
    {
        "key": "traits",
        "category": "Card",
        "label": "Card traits",
        "tooltip": (
            "Yes-or-no facts about a card that no other filter covers: "
            "Universes Beyond, Reserved List, Commander game changers, "
            "double-faced and other multi-face layouts, hybrid and Phyrexian "
            "costs, and creatures whose power exceeds their toughness. Pick "
            "the negative form to exclude instead of include."),
    },
    {
        "key": "content",
        "category": "Card",
        "label": "Content",
        "tooltip": (
            "Which kinds of object the search may return. Cards is the "
            "default; Tokens, Emblems and Art Series are separate printed "
            "objects that are excluded unless you ask for them."),
    },
    {
        "key": "format",
        "category": "Printing",
        "label": "Format",
        "tooltip": (
            "Cards legal or restricted in the chosen format, using the "
            "legality data on each card. Banned and not-legal cards are "
            "excluded. Format membership comes from the local snapshot, so a "
            "brand-new format appears after the next database update."),
    },
    {
        "key": "rarity",
        "category": "Printing",
        "label": "Rarity",
        "tooltip": (
            "Rarity of the specific printing, not the card. A card printed at "
            "both common and mythic matches either, through its separate "
            "printings."),
    },
    {
        "key": "released",
        "category": "Printing",
        "label": "Released",
        "tooltip": (
            "Year the printing was released. Bounds are inclusive, so 2015 to "
            "2020 covers both. This is the printing's own date — an old card "
            "in a recent set matches the recent year."),
    },
)

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
