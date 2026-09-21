"""Shared database/search vocabulary and card-layout categories."""

RARITIES = ("common", "uncommon", "rare", "mythic", "special", "bonus")
COLORS = ("W", "U", "B", "R", "G")

# Search content categories follow Scryfall-backed row/layout semantics.
# Ordinary cards remain one class regardless of card type; Tokens, Emblems, and
# Art Series are explicit opt-in content classes derived from their Scryfall layouts.
CONTENT_TYPES = ("card", "token", "emblem", "art")

PLAYABLE_LEGALITY_STATUSES = frozenset({"legal", "restricted"})
KNOWN_LEGALITY_STATUSES = frozenset({"legal", "restricted", "banned", "not_legal"})

# Layouts are application semantics, not Search taxonomy.  Known current
# Scryfall layouts are enumerated only so unfamiliar future layouts can be
# diagnosed. Unknown layouts remain importable and Search-visible as Cards.
# ``art_series`` and ``front_card`` are non-gameplay collectible objects (art
# cards and Jumpstart "memorabilia" theme front cards); both are hidden from the
# Cards scope and surface only under the Art Series content class.
ART_LAYOUTS = ("art_series", "front_card")
TOKEN_LAYOUTS = ("token", "double_faced_token", "emblem")
KNOWN_CARD_LAYOUTS = (
    "normal", "split", "flip", "transform", "modal_dfc", "meld", "leveler",
    "class", "case", "saga", "adventure", "mutate", "prototype", "battle",
    "planar", "scheme", "vanguard", "augment", "host", "reversible_card",
    # "prepare" is a playable two-part creature/spell layout, like split.
    "prepare",
)
KNOWN_SCRYFALL_LAYOUTS = frozenset((*KNOWN_CARD_LAYOUTS, *ART_LAYOUTS, *TOKEN_LAYOUTS))
NON_CARD_LAYOUTS = ART_LAYOUTS + TOKEN_LAYOUTS
