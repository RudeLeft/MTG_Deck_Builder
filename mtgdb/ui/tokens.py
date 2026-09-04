"""Shared visual tokens for MTG Deck Builder.

This module contains appearance values only.  Search, filter, deck, comparison,
and database behavior deliberately remain in their feature modules.
"""

FONT_FAMILY = "Segoe UI"

PALETTE = {
    "bg": "#0E0F11",
    "surface": "#17191D",
    "surface2": "#1E2126",
    "surface3": "#252931",
    "accent": "#C39A4A",
    "accent2": "#A77D31",
    # One authoritative foreground for every gold/accent surface.  Keeping it
    # in the palette prevents individual controls from drifting to white text,
    # which does not have sufficient contrast on the MTG gold.
    "on_accent": "#111111",
    "text": "#F1EEE7",
    "muted": "#A6A39C",
    "border": "#343840",
    "stripe": "#1B1E23",
    "select": "#5B4723",
    "input": "#111317",
    "bar": "#C39A4A",
    "bar_land": "#777C86",
    "good": "#74B78A",
    "bad": "#D57474",
    "deck_good": "#2E7D32",
    "deck_bad": "#C0392B",
    # Dimmed partner for deck_bad.  The bounded over-limit comparison flash
    # pulses between the two so the alerting text stays red the whole time
    # instead of dropping to another hue mid-pulse.
    "deck_bad_dim": "#6E2A24",
}

MANA_NAMES = {
    "W": "White", "U": "Blue", "B": "Black", "R": "Red",
    "G": "Green", "C": "Colorless",
}

DECK_TYPE_SEGMENT_COLORS = {
    "Creatures": "#4E79A7", "Instants": "#59A14F",
    "Sorceries": "#F28E2B", "Artifacts": "#9C755F",
    "Enchantments": "#B07AA1", "Planeswalkers": "#EDC948",
    "Battles": "#E15759", "Other": "#BAB0AC",
}
DECK_COLOR_SEGMENT_COLORS = {
    "W": "#EDE3B0", "U": "#2E77B5", "B": "#4B4B4B",
    "R": "#C0392B", "G": "#3C8D40", "Multi": "#C9A94E",
    "Colorless": "#AEA69B",
}

# Typography is role-based so new controls do not invent local font tuples.
FONT_BODY = (FONT_FAMILY, 10)
FONT_BODY_BOLD = (FONT_FAMILY, 10, "bold")
FONT_HELPER = (FONT_FAMILY, 9)
FONT_HELPER_BOLD = (FONT_FAMILY, 9, "bold")
FONT_MICRO = (FONT_FAMILY, 8)
FONT_MICRO_BOLD = (FONT_FAMILY, 8, "bold")
FONT_PANE_TITLE = (FONT_FAMILY, 15, "bold")
FONT_DIALOG_TITLE = (FONT_FAMILY, 15, "bold")
FONT_PROGRESS_TITLE = (FONT_FAMILY, 15, "bold")
FONT_CONTROL_GLYPH = (FONT_FAMILY, 11, "bold")

# Control padding values are logical Tk units and scale with the Tk DPI setting.
PAD_STANDARD = (10, 5)
PAD_PRIMARY = (12, 5)
# Picker buttons represent field values, so their vertical padding matches the
# shared Entry/Combobox/Spinbox field padding below.
PAD_PICKER = (8, 3)
PAD_COMPACT = (7, 4)
PAD_DECK = (10, 6)
PAD_DENSE = (3, 2)
PAD_DENSE_PRIMARY = (4, 2)

FORM_CONTROL_PADDING = (5, 3)
CLASSIC_ENTRY_IPADY = 3

PANEL_PADDING = 14
WINDOW_GUTTER = 10
POPUP_PADDING = (12, 11)
POPUP_FOOTER_PADDING = (11, 5)

# Image display roles.  Source mana assets remain at their native 128x128.
FILTER_PIP_SIZE = 22

# Card-image presentation metrics. Secondary windows clamp these logical sizes to
# the current screen before locking or presenting their viewport.
CARD_PREVIEW_PORTRAIT_SIZE = (340, 480)
# Main-window geometry is deterministic: the center column never competes with
# Search/Deck for width, and the preview region always has room for the complete
# card image plus its Legality/Rotate/Zoom action row.
MAIN_CENTER_COLUMN_WIDTH = 470
MAIN_CARD_PREVIEW_HEIGHT = 558
MAIN_SIDE_MIN_WIDTH = 340
MAIN_SEARCH_WEIGHT = 51
MAIN_DECK_WEIGHT = 49
CARD_ZOOM_WINDOW_SIZE = (900, 900)
# The zoom header carries Close/Rotate/Flip/zoom controls whose combined
# requested width is ~460px; the minimum leaves room for those plus a
# readable slice of a long double-faced card name.
CARD_ZOOM_WINDOW_MIN_SIZE = (720, 520)
CARD_ZOOM_BASE_PORTRAIT_SIZE = (560, 784)
CARD_ZOOM_LEVELS = (50, 75, 100, 125, 150, 175, 200, 225)

COMPARISON_WINDOW_SIZE = (1840, 1120)
COMPARISON_WINDOW_MIN_SIZE = (960, 640)
COMPARISON_MAX_COLUMNS = 4
COMPARISON_IMAGE_MAX_WIDTH = CARD_PREVIEW_PORTRAIT_SIZE[0]
COMPARISON_IMAGE_MAX_HEIGHT = CARD_PREVIEW_PORTRAIT_SIZE[1]
