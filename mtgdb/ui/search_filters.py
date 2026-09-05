"""Search filter registry: the standard set, and the advanced ones by category.

The Search form and the Results table share one column with no sash between
them, so every permanently-rendered filter takes its height out of Results.
Two designs have answered that. The first rendered everything; the second
built each filter on demand from an Add filter menu, which cost nothing unused
but made a real search several menu trips before it could be run.

This is the third and it comes from using the second: a small standard set
that is always present because nearly every search touches it, and everything
else together behind one Advanced Filter Options button. One click reveals all
of them, grouped by category, instead of one click per filter.

The registry is deliberately data, not widgets. Each entry names its category,
its label and the tooltip that explains what the filter actually matches;
`ui/search.py` owns the controls themselves.
"""

from __future__ import annotations


CATEGORY_ORDER = ("Mana", "Card", "Printing")

# Always on the Search form, in this order. These are the filters a search
# starts from: what the card is called, what it is, what color it is, how big
# it is, and which printings are in scope.
STANDARD_FILTERS = ("name", "card_type", "colors", "stats", "printings")

# Tooltip wording rule, applied to every filter including the standard ones:
# one sentence saying what the filter matches, then at most one more for the
# boundary people get wrong about it. Describe cards, not where the data came
# from -- naming a data source tells the user nothing about their search.
FILTER_DEFINITIONS = (
    {
        "key": "mana_value",
        "category": "Mana",
        "label": "Mana value",
        "tooltip": (
            "The total cost of a card, counting colored and generic mana "
            "together: {2}{G} is 3. Leave a box empty for no limit on that "
            "side."),
    },
    {
        "key": "produces",
        "category": "Mana",
        "label": "Produces",
        "tooltip": (
            "The mana a card can make, which is not the same as its color. "
            "Birds of Paradise is green but makes all five colors, and "
            "Command Tower is colorless but makes any of them."),
    },
    {
        "key": "stats",
        "category": "Card",
        "label": "Power / Toughness",
        "tooltip": (
            "Printed power and toughness, compared as numbers, so only "
            "creatures and other cards that have them can match. A card whose "
            "stats vary, such as */*, has no number to compare and is left "
            "out. Leave a box empty for no limit on that side."),
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
            "sharply. A two-faced card matches when either face carries the "
            "word, so a Legendary back face is found too."),
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
            "Words in a card's rules text, across every face. Words inside "
            "one entry may have other words between them; put quotes around "
            "an entry to require that exact phrase. The row below decides "
            "whether every entry must appear, any one of them, or none of "
            "them."),
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
        "key": "traits",
        "category": "Card",
        "label": "Card traits",
        "tooltip": (
            "Yes-or-no facts that no other filter covers: Universes Beyond, "
            "Reserved List, Commander game changers, two-faced cards, hybrid "
            "and Phyrexian costs, and creatures with more power than "
            "toughness. The Scope choices at the top of the list decide which "
            "kinds of object are searched at all: Cards alone by default, and "
            "untick Cards to search only tokens, emblems or Art Series."),
    },
    {
        "key": "format",
        "category": "Printing",
        "label": "Format",
        "tooltip": (
            "Cards with the legality you choose in the format you choose: "
            "playable, banned or restricted. Playable covers both legal and "
            "restricted cards. Only formats that have cards in the chosen "
            "state are offered, so Restricted lists very few."),
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

# The standard filters with no registry entry: Card Name, Colors, Card type
# and Printings are built by hand, and Power / Toughness is standard but keeps
# its registry entry. All five explain themselves the same way the advanced
# ones do, so the whole panel reads as one voice rather than four exceptions.
STANDARD_FILTER_TOOLTIPS = {
    "name": (
        "Matches any card whose name contains what you type, so bolt finds "
        "Lightning Bolt. Searching from a deck selection looks for those "
        "exact names instead."),
    "colors": (
        "The colors a card is. Look at chooses which meaning: color identity "
        "counts everything the card brings to a deck, including its rules "
        "text and both faces, while Card colors counts only what the card "
        "itself is. The row below that decides whether the colors must match "
        "exactly, be included, or simply not be exceeded."),
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


def filter_tooltip(key):
    """The tooltip for any filter, standard or advanced.

    Four standard filters are built by hand in `ui/search.py` because each has
    a shape no generic row could give it, so their wording lives in the dict
    above rather than in a registry entry. Every caller asks here instead of
    knowing which of the two a filter came from, which is also what stops the
    same filter being described twice in two different voices.
    """
    entry = FILTER_BY_KEY.get(str(key))
    if entry is not None:
        return entry["tooltip"]
    return STANDARD_FILTER_TOOLTIPS.get(str(key), "")


def advanced_filters():
    """Every filter outside the standard set, grouped in category order.

    Order is the registry's, not the order a user happened to open things in,
    so a filter is always in the same place on the panel.
    """
    grouped = []
    for category in CATEGORY_ORDER:
        entries = tuple(
            {
                "key": entry["key"],
                "label": entry["label"],
                "tooltip": entry["tooltip"],
            }
            for entry in FILTER_DEFINITIONS
            if entry["category"] == category
            and entry["key"] not in STANDARD_FILTERS
        )
        if entries:
            grouped.append((category, entries))
    return tuple(grouped)


def advanced_filter_keys():
    """Flat advanced order, for callers that only need the keys."""
    return tuple(
        entry["key"] for _category, entries in advanced_filters()
        for entry in entries)


def is_standard(key):
    """Standard filters are always on the form and never inside Advanced."""
    return str(key) in STANDARD_FILTERS
