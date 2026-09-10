"""Presentation registry for the existing Search capabilities.

The registry deliberately names UI groups, not a second Magic taxonomy. Values
still come from Scryfall/observed database fields and the query model remains
unchanged; this module only decides where an existing capability is presented.
"""

from __future__ import annotations


CATEGORY_ORDER = ("Search Scope", "Mana", "Card", "Printing & Status")

# The common Search surface follows a printed Magic type line before moving on
# to colour, stats, and printing scope.  No query field was added for this UI
# change: Supertype/Card Type/Subtype are the existing criteria moved together.
STANDARD_FILTERS = (
    "name", "supertypes", "card_type", "subtype", "colors", "stats", "printings",
)

FILTER_DEFINITIONS = (
    {
        "key": "search_scope", "category": "Search Scope", "label": "Search scope",
        "tooltip": (
            "Choose whether Search covers Cards, Tokens, Emblems, or Art Series. "
            "These choices set the search universe and are not affected by the "
            "Any, All, or None mode used for card properties."),
    },
    {
        "key": "mana_value", "category": "Mana", "label": "Mana value",
        "tooltip": (
            "The total cost of a card, counting colored and generic mana together. "
            "Leave either side empty for no limit."),
    },
    {
        "key": "produces", "category": "Mana", "label": "Mana produced",
        "tooltip": (
            "The mana a card can make, which is not the same as its color or color "
            "identity. The existing Within, Contains and Exactly modes are preserved."),
    },
    {
        "key": "mana_pips", "category": "Mana", "label": "Mana symbols in cost",
        "tooltip": (
            "How many colored mana symbols the cost has, counted per selected color. "
            "The threshold applies to every selected color exactly as before."),
    },
    {
        "key": "mana_cost_features", "category": "Mana", "label": "Mana cost features",
        "tooltip": (
            "Find cards whose mana cost contains Hybrid mana, Phyrexian mana, or X. "
            "These are the existing mana-cost properties, grouped here so cost-related "
            "questions stay together."),
    },
    {
        "key": "stats", "category": "Card", "label": "Power / Toughness",
        "tooltip": (
            "Printed numeric power and toughness. Variable values such as */* are not "
            "numeric range matches; their existing property filter remains available."),
    },
    {
        "key": "loyalty", "category": "Card", "label": "Loyalty",
        "tooltip": (
            "The printed numeric loyalty value used by planeswalker cards. Leave either "
            "side empty for no limit; cards without numeric loyalty do not satisfy a "
            "loyalty range."),
    },
    {
        "key": "defense", "category": "Card", "label": "Defense",
        "tooltip": (
            "The printed numeric defense value used by battle cards. Leave either side "
            "empty for no limit; loyalty and defense are separate values and no card "
            "has both as one shared statistic."),
    },
    {
        "key": "supertypes", "category": "Card", "label": "Supertype",
        "tooltip": (
            "The words before Card Type on the type line, such as Legendary, Basic or "
            "Snow. Matching checks the complete type line across either face while the "
            "trusted Supertype vocabulary remains authoritative."),
    },
    {
        "key": "subtype", "category": "Card", "label": "Subtype",
        "tooltip": (
            "The words after the dash on the type line, such as Angel, Equipment, or "
            "Forest. Current results may change the ordering and counts, but they never "
            "create subtype names that are not already trusted vocabulary."),
    },
    {
        "key": "mechanics", "category": "Card", "label": "Mechanics",
        "tooltip": (
            "Named Keyword Abilities, Keyword Actions, and Ability Words, separate from "
            "free-form rules text. Current results prioritize relevant mechanics without "
            "creating new mechanic names."),
    },
    {
        "key": "rules_text", "category": "Card", "label": "Rules text",
        "tooltip": (
            "Words in a card's rules text across every face. Quotes still require an "
            "exact phrase; Any can match one term, All requires every term, and None "
            "requires none of them to match."),
    },
    {
        "key": "card_form", "category": "Card", "label": "Card form",
        "tooltip": (
            "The card's existing layout value, such as a split or double-faced form. "
            "Current results prioritize forms that can match, while selected and other "
            "observed forms remain available."),
    },
    {
        "key": "faces", "category": "Card", "label": "Faces",
        "tooltip": (
            "Choose the existing Single-faced or Multi-faced card property. This uses the "
            "card's actual face data and does not infer face count from a maintained list "
            "of layouts."),
    },
    {
        "key": "color_indicator", "category": "Card", "label": "Color indicator",
        "tooltip": (
            "Require a card to have a printed color indicator. This is the existing "
            "yes/no property only; it does not add a separate filter for the indicator's "
            "color."),
    },
    {
        "key": "pt_properties", "category": "Card", "label": "P/T properties",
        "tooltip": (
            "The existing Power greater than toughness and Variable power/toughness "
            "predicates, grouped beside the card characteristics they describe."),
    },
    {
        "key": "property_match", "category": "Card", "label": "Property matching",
        "tooltip": (
            "How the selected yes/no property filters above and below combine: Any, All "
            "or None. This is the existing property matching mode, not a new filter."),
    },
    {
        "key": "format", "category": "Printing & Status", "label": "Format",
        "tooltip": (
            "Cards with the selected legality in the selected format. Playable, Banned "
            "and Restricted retain their existing search semantics."),
    },
    {
        "key": "rarity", "category": "Printing & Status", "label": "Rarity",
        "tooltip": (
            "The rarity of an individual printing rather than a card name in general. "
            "Current matching printings determine which existing rarity values are most "
            "relevant."),
    },
    {
        "key": "released", "category": "Printing & Status", "label": "Released",
        "tooltip": (
            "The inclusive release-year range for an individual printing. Leave either "
            "side empty for no limit; the available year range follows the current "
            "printing scope."),
    },
    {
        "key": "status_properties", "category": "Printing & Status", "label": "Product / status",
        "tooltip": (
            "The existing Universes Beyond, Reserved List, and Commander Game Changer "
            "yes/no properties. They remain the same search predicates and are grouped "
            "here only for presentation."),
    },
)

STANDARD_FILTER_TOOLTIPS = {
    "name": (
        "Matches any card whose name contains what you type. Searching from a deck "
        "selection continues to use the existing exact-name batch behavior."),
    "colors": (
        "Use Look at to search either color identity or the card's printed colors, "
        "then apply the existing Within, Contains, or Exactly set comparison."),
    "card_type": (
        "The main Card Type on the type line, such as Creature, Instant or Land. "
        "Multi-type cards continue to match according to Any/All/None."),
    "printings": (
        "Which existing printings may match: Printing Type, Set Type, Exact Set and "
        "English-only scope. These choices also scope trusted filter vocabulary."),
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
