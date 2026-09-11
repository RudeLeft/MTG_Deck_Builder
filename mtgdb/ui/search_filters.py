"""Presentation registry for Search filters and their user-facing help text."""

from __future__ import annotations


CATEGORY_ORDER = ("Search Scope", "Mana", "Card", "Printing & Status")

# The common Search surface follows a printed Magic type line before moving on
# to mana/color, stats, and the advanced groups.
STANDARD_FILTERS = (
    "name", "supertypes", "card_type", "subtype", "colors", "stats",
)

FILTER_DEFINITIONS = (
    {
        "key": "search_scope", "category": "Search Scope", "label": "Search scope",
        "tooltip": (
            "Choose which kinds of objects can appear in Results: Cards, Tokens, "
            "Emblems, or Art Series. Selecting several object types includes any of them. "
            "Search Scope is independent of the Any, All, or None modes used by other filters."),
    },
    {
        "key": "mana_value", "category": "Mana", "label": "Mana value",
        "tooltip": (
            "Filter by a card's mana value. The Min and Max fields let you search one "
            "exact value or a range. If every remaining result has no meaningful mana cost, "
            "the control becomes unavailable even though rules may assign those objects mana value 0."),
    },
    {
        "key": "produces", "category": "Mana", "label": "Mana produced",
        "tooltip": (
            "Filter by the colors of mana a card can produce. This is separate from "
            "the card's own colors and Color Identity. Select one or more mana colors, "
            "then use Match to choose Within, Contains, or Exactly."),
    },
    {
        "key": "mana_pips", "category": "Mana", "label": "Mana symbols in cost",
        "tooltip": (
            "Filter by colored or colorless mana symbols that appear in a card's mana cost. "
            "Match: All requires every selected color, Any requires at least one selected "
            "color, and None excludes them. Minimum is the total number of qualifying physical "
            "symbols. A hybrid symbol can represent each of its colors for Match but counts only "
            "once toward Minimum. Colorless means literal {C}; generic costs such as {1} or {2} "
            "do not count. This is separate from Mana Color and Mana Produced."),
    },
    {
        "key": "mana_cost_features", "category": "Mana", "label": "Mana cost features",
        "tooltip": (
            "Filter for Hybrid mana, Phyrexian mana, or X in the mana cost. Match: Any "
            "accepts at least one selected feature, All requires every selected feature, "
            "and None excludes cards with any selected feature."),
    },
    {
        "key": "stats", "category": "Card", "label": "Power / Toughness",
        "tooltip": (
            "Filter by numeric power and toughness. Nonnumeric values such as * do not "
            "satisfy a numeric range; use Special Properties to find variable power or "
            "toughness. Power and Toughness are filtered independently."),
    },
    {
        "key": "loyalty", "category": "Card", "label": "Loyalty",
        "tooltip": (
            "Filter by a card's printed numeric loyalty, used by planeswalkers. Cards "
            "without a numeric loyalty value do not match a Loyalty range."),
    },
    {
        "key": "defense", "category": "Card", "label": "Defense",
        "tooltip": (
            "Filter by a card's printed numeric defense, used by battles. Cards without "
            "numeric defense do not match a Defense range. Loyalty and Defense are separate "
            "characteristics."),
    },
    {
        "key": "supertypes", "category": "Card", "label": "Supertype",
        "tooltip": (
            "Filter by supertypes such as Legendary, Basic, Snow, or World. On a "
            "multi-faced card, a matching supertype on either face qualifies. Match: "
            "Any accepts at least one selected supertype, All requires every selected "
            "supertype, and None excludes any selected supertype."),
    },
    {
        "key": "subtype", "category": "Card", "label": "Subtype",
        "tooltip": (
            "Filter by subtypes such as Angel, Equipment, Forest, or Wizard. On a "
            "multi-faced card, a matching subtype on either face qualifies. Match: Any "
            "accepts at least one selected subtype, All requires every selected subtype, "
            "and None excludes any selected subtype."),
    },
    {
        "key": "mechanics", "category": "Card", "label": "Mechanics",
        "tooltip": (
            "Filter by named Keyword Abilities, Keyword Actions, and Ability Words. "
            "Unlike Rules Text, this searches named mechanics rather than arbitrary words "
            "or phrases. Match: Any accepts at least one selected mechanic, All requires "
            "every selected mechanic, and None excludes cards with any selected mechanic."),
    },
    {
        "key": "rules_text", "category": "Card", "label": "Rules text",
        "tooltip": (
            "Filter by words or phrases in a card's rules text across every face. "
            "Unquoted text can appear anywhere; text in double quotes must appear as an "
            "exact phrase. Match: Any accepts at least one entry, All requires every "
            "entry, and None excludes cards matching any entry."),
    },
    {
        "key": "card_form", "category": "Card", "label": "Card form",
        "tooltip": (
            "Filter by a card's structural form, such as split, transforming, modal "
            "double-faced, adventure, or another available form. Match: Any includes "
            "the selected forms; None excludes them."),
    },
    {
        "key": "special_properties", "category": "Card", "label": "Special properties",
        "tooltip": (
            "Filter uncommon card characteristics. Power greater than toughness compares "
            "numeric power and toughness; Variable power or toughness finds a * value; a "
            "color indicator is the printed color marker that defines a card's color; and "
            "Has multiple faces finds cards with more than one card face. Match: Any accepts "
            "at least one selected property, All requires every selected property, and None "
            "excludes cards with any selected property."),
    },
    {
        "key": "printings", "category": "Printing & Status", "label": "Printings",
        "tooltip": (
            "Choose which printings can appear by Printing Type (Paper, Arena, or MTGO), "
            "Set Type, Exact Set, and language. Selecting multiple printing types, set types, "
            "or exact sets includes any selected choice. These settings also limit what can "
            "match Rarity and Released."),
    },
    {
        "key": "format", "category": "Printing & Status", "label": "Format",
        "tooltip": (
            "Filter by a card's legality in one format. Playable includes cards that "
            "are legal or restricted, Banned finds cards explicitly banned, and "
            "Restricted finds cards limited to one copy in that format."),
    },
    {
        "key": "rarity", "category": "Printing & Status", "label": "Rarity",
        "tooltip": (
            "Filter by the rarity of a qualifying printing. Selecting several rarities "
            "includes a printing with any selected rarity. Printings excluded by your other "
            "Printing & Status filters do not qualify."),
    },
    {
        "key": "released", "category": "Printing & Status", "label": "Released",
        "tooltip": (
            "Filter by the release year of a qualifying printing. Min and Max are "
            "inclusive, and either side can be left blank for no limit."),
    },
    {
        "key": "status_properties", "category": "Printing & Status", "label": "Product / status",
        "tooltip": (
            "Filter by Universes Beyond, Reserved List, or Commander Game Changer "
            "status. Match: Any accepts at least one selected status, All requires every "
            "selected status, and None excludes cards with any selected status."),
    },
)

STANDARD_FILTER_TOOLTIPS = {
    "name": (
        "Filter by card name. Typing part of a name matches names containing that text. "
        "When Search is opened from selected deck cards, those selected card names are "
        "matched exactly."),
    "colors": (
        "Filter by Mana Color using either Color Identity or Card Colors. Use chooses "
        "which color definition is searched. Match controls the relationship to the "
        "selected colors: Within allows only selected colors, Contains requires every "
        "selected color and allows additional colors, and Exactly requires the selected "
        "color set and no others."),
    "card_type": (
        "Filter by card types such as Creature, Instant, Land, or Dungeon. On a "
        "multi-faced card, a matching type on either face qualifies. Match: Any accepts "
        "at least one selected type, All requires every selected type, and None excludes "
        "cards with any selected type."),
}

FILTER_BY_KEY = {entry["key"]: entry for entry in FILTER_DEFINITIONS}


def filter_tooltip(key):
    entry = FILTER_BY_KEY.get(str(key))
    if entry is not None:
        return entry["tooltip"]
    return STANDARD_FILTER_TOOLTIPS.get(str(key), "")


def advanced_filters():
    grouped = []
    for category in CATEGORY_ORDER:
        entries = tuple(
            {"key": entry["key"], "label": entry["label"], "tooltip": entry["tooltip"]}
            for entry in FILTER_DEFINITIONS
            if entry["category"] == category and entry["key"] not in STANDARD_FILTERS
        )
        if entries:
            grouped.append((category, entries))
    return tuple(grouped)


def advanced_filter_keys():
    return tuple(entry["key"] for _category, entries in advanced_filters() for entry in entries)


def is_standard(key):
    return str(key) in STANDARD_FILTERS
