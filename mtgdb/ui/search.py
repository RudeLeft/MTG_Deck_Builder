"""Search-pane layout, filter state, criteria capture, and UI callbacks."""

from __future__ import annotations

import datetime
import math
import logging
import tkinter as tk
from tkinter import messagebox, ttk

from mtgdb.database.constants import COLORS
from mtgdb.search.models import SearchCriteria
from mtgdb.ui.autocomplete import AutocompleteEntry
from mtgdb.ui.components import (
    AppButton, AppCombobox, AppSpinbox, ClassicCheckbutton,
    TokenBubbleEntry, format_display_name,
)
from mtgdb.ui.search_checklist import open_search_checklist
from mtgdb.ui.search_filters import (
    FILTER_BY_KEY, STANDARD_FILTERS, advanced_filter_keys, advanced_filters,
    filter_tooltip,
)
from mtgdb.ui.search_printings import SearchPrintingFilter
from mtgdb.ui.tables import TABLE_COLUMNS, TABLE_COLUMN_ORDER
from mtgdb.ui.tokens import (
    MANA_NAMES, PALETTE,
)

log = logging.getLogger("mtg")

CARD_TYPE_MIN_COLUMNS = 4
CARD_TYPE_MAX_COLUMNS = 5
SUPERTYPE_COLUMNS = 5
CARD_TYPE_LOADING_SLOTS = CARD_TYPE_MIN_COLUMNS * 3
SUPERTYPE_LOADING_SLOTS = SUPERTYPE_COLUMNS
CHIP_GRID_X_GAP = 4
CHIP_GRID_Y_GAP = 2
SEARCH_ROW_PADY = 2
ADVANCED_ROW_PADY = 1
MODE_ROW_PADY = (1, 0)
MATCH_MODE_LABEL = "Match"
COLOR_SCOPE_LABEL = "Use"
SECONDARY_LABEL_WIDTH = 42
SECONDARY_CONTROL_GAP = 6
SECONDARY_HELPER_GAP = 6
MATCH_MODE_LABEL_WIDTH = SECONDARY_LABEL_WIDTH
MATCH_MODE_CHOICE_GAP = 8
TYPE_LINE_HEADING_PADY = (5, 1)
ADVANCED_HEADER_PADY = (4, 0)
ADVANCED_SECTION_HEADING_PADY = (6, 1)
# Keeps every advanced filter label on the same x-position as the standard
# rows above them, so the control column does not step in and out.
FILTER_LABEL_WIDTH = 144
FILTER_LABEL_GAP = 10
# Style B association rail: a two-pixel center-weighted fade that occupies only
# unused space inside the fixed label column.  It never changes control rails.
ASSOCIATION_RAIL_TEXT_GAP = 8
ASSOCIATION_RAIL_MIN_WIDTH = 10
ASSOCIATION_RAIL_SEGMENTS = 24
ASSOCIATION_RAIL_LINE_WIDTH = 2
ASSOCIATION_RAIL_CANVAS_HEIGHT = 3
ASSOCIATION_RAIL_MAX_BLEND = 1.0
ASSOCIATION_RAIL_FADE_POWER = 0.72
SEARCH_ROW_HOVER_COLOR = PALETTE["search_hover"]
SEARCH_CHIP_HOVER_BORDER = PALETTE["text"]
SEARCH_RESULTS_BOUNDARY_HEIGHT = 2
ASSOCIATION_RAIL_HOVER_MAX_BLEND = 1.0
MODE_CONTROL_GAP = SECONDARY_CONTROL_GAP
MODE_CHOICE_COLUMNS = 3
MANA_CHOICE_GAP = 8
# Every numeric range uses the same fixed mini-grid so Min / to / Max fields
# line up regardless of whether the widgets are spinboxes or year comboboxes.
RANGE_FIELD_WIDTH_PX = 64
RANGE_SEPARATOR_WIDTH_PX = 30
FORMAT_STATUS_CHOICES = (
    ("Playable", "playable"), ("Banned", "banned"),
    ("Restricted", "restricted"),
)
# Search vocabulary is loaded from the local Scryfall-backed snapshot rather
# than guessed from a fixed calendar/taxonomy list.
CONTENT_TRAIT_KEYS = {
    "content_cards": "card",
    "content_tokens": "token",
    "content_emblems": "emblem",
    "content_art_series": "art",
}
DEFAULT_CONTENT_TRAITS = ("content_cards",)
TRAIT_CHOICES = (
    ("content_cards", "Cards"),
    ("content_tokens", "Tokens"),
    ("content_emblems", "Emblems"),
    ("content_art_series", "Art Series"),
    ("not_universes_beyond", "Not Universes Beyond"),
    ("universes_beyond", "Universes Beyond"),
    ("reserved", "Reserved List"),
    ("game_changer", "Commander game changer"),
    ("multi_faced", "Has multiple faces"),
    ("single_faced", "Single-faced card"),
    ("hybrid_mana", "Hybrid mana in cost"),
    ("phyrexian_mana", "Phyrexian mana in cost"),
    ("has_x_cost", "X in mana cost"),
    ("color_indicator", "Has a color indicator"),
    ("top_heavy", "Power greater than toughness"),
    ("variable_stats", "Variable power or toughness (*)"),
)
TRAIT_LABELS = dict(TRAIT_CHOICES)
MANA_COST_FEATURE_KEYS = ("hybrid_mana", "phyrexian_mana", "has_x_cost")
SPECIAL_PROPERTY_KEYS = (
    "top_heavy", "variable_stats", "color_indicator", "multi_faced",
)
STATUS_PROPERTY_KEYS = (
    "not_universes_beyond", "universes_beyond", "reserved", "game_changer",
)
SPECIAL_PROPERTY_PICKER_LABELS = {
    "top_heavy": "Power / Toughness · Power greater than toughness",
    "variable_stats": "Power / Toughness · Variable power or toughness (*)",
    "color_indicator": "Card Characteristics · Has a color indicator",
    "multi_faced": "Card Characteristics · Has multiple faces",
}
PICKER_SUMMARY_PER_LINE = 5


def _chip_grid_padx(column, columns):
    """Keep one exact gap between equal-width chips and no outer gutter."""
    if columns <= 0:
        raise ValueError("columns must be positive")
    half = CHIP_GRID_X_GAP // 2
    remainder = CHIP_GRID_X_GAP - half
    return (0 if column == 0 else half,
            0 if column == columns - 1 else remainder)


def _chip_grid_pady(row, rows):
    """Keep one exact vertical gap between chip rows and no outside padding."""
    if rows <= 0:
        raise ValueError("rows must be positive")
    half = CHIP_GRID_Y_GAP // 2
    remainder = CHIP_GRID_Y_GAP - half
    return (0 if row == 0 else half,
            0 if row == rows - 1 else remainder)


def _blend_hex(start, end, amount):
    """Blend two palette hex colors without introducing a local color literal."""
    amount = max(0.0, min(1.0, float(amount)))
    left = tuple(int(start[index:index + 2], 16) for index in (1, 3, 5))
    right = tuple(int(end[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(
        round(a + ((b - a) * amount)) for a, b in zip(left, right))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _association_rail_color(position, active=False):
    """Palette-derived Style B fade with a bright champagne-gold hover center."""
    position = max(0.0, min(1.0, float(position)))
    curve = math.sin(math.pi * position) ** ASSOCIATION_RAIL_FADE_POWER
    if active:
        return _blend_hex(
            SEARCH_ROW_HOVER_COLOR, PALETTE["search_hover_glow"],
            curve * ASSOCIATION_RAIL_HOVER_MAX_BLEND)
    return _blend_hex(
        PALETTE["surface"], PALETTE["border"],
        curve * ASSOCIATION_RAIL_MAX_BLEND)


def _pack_mana_choice(widget):
    """Keep W/U/B/R/G/C choices compact and aligned across Search mana rows."""
    widget.pack(side="left", padx=(0, MANA_CHOICE_GAP))


def _chip_layout_widget(widget):
    """Return the geometry owner for a trusted Type Line chip."""
    return getattr(widget, "_ui_chip_border_shell", None) or widget


def _row_major_grid_required_width(widgets, columns):
    """Return width needed when every chip column is intentionally equal."""
    if columns <= 0:
        raise ValueError("columns must be positive")
    if not widgets:
        return 0
    widest = max(
        int(_chip_layout_widget(widget).winfo_reqwidth()) for widget in widgets)
    return (widest * columns) + (CHIP_GRID_X_GAP * (columns - 1))


class SearchFeatureMixin:
    """Own the interactive Search feature while the root wires other features."""

    @staticmethod
    def _draw_association_rail(canvas, active=False):
        """Paint the Style B fade without affecting layout geometry."""
        try:
            width = int(canvas.winfo_width())
            canvas.delete("association_rail")
        except tk.TclError:
            return
        if width < ASSOCIATION_RAIL_MIN_WIDTH:
            return
        segments = max(1, min(ASSOCIATION_RAIL_SEGMENTS, width))
        y = ASSOCIATION_RAIL_CANVAS_HEIGHT / 2
        for index in range(segments):
            x0 = round(index * width / segments)
            x1 = round((index + 1) * width / segments)
            position = (index + 0.5) / segments
            canvas.create_line(
                x0, y, x1, y,
                fill=_association_rail_color(position, active=active),
                width=ASSOCIATION_RAIL_LINE_WIDTH,
                tags=("association_rail",),
            )

    def _position_search_row_hover_region(self, region):
        """Place one zero-geometry hover band behind an existing grid row."""
        parent = region["parent"]
        band = region["band"]
        try:
            if not parent.winfo_exists():
                return
            x, y, width, height = parent.grid_bbox(
                0, region["row"], region["last_column"], region["row"])
            if height <= 0 or width <= 0:
                band.place_forget()
                return
            band.place(x=x, y=y, width=max(width, parent.winfo_width() - x), height=height)
            band.lower()
        except tk.TclError:
            return

    @staticmethod
    def _search_hover_style_for(widget, active):
        """Swap only surface-bearing Search widgets to hover-safe styles."""
        try:
            base = getattr(widget, "_mtg_search_hover_base_style", None)
            if base is None:
                base = str(widget.cget("style") or "")
                widget._mtg_search_hover_base_style = base
            if not active:
                widget.configure(style=base)
                return
            mapping = {
                "": {"TFrame": "SearchHover.TFrame", "TLabel": "SearchHover.TLabel"},
                "TFrame": {"TFrame": "SearchHover.TFrame"},
                "TLabel": {"TLabel": "SearchHover.TLabel"},
                "Muted.TLabel": {"TLabel": "SearchHoverMuted.TLabel"},
                "FormChoice.TRadiobutton": {
                    "TRadiobutton": "SearchHover.FormChoice.TRadiobutton"},
                "Color.TCheckbutton": {
                    "TCheckbutton": "SearchHover.Color.TCheckbutton"},
            }
            replacement = mapping.get(base, {}).get(widget.winfo_class())
            if replacement:
                widget.configure(style=replacement)
        except (tk.TclError, AttributeError):
            return

    def _set_search_row_hover_surface(self, widget, active):
        """Tint neutral surfaces and brighten that row's association rail."""
        self._search_hover_style_for(widget, active)
        if (isinstance(widget, ClassicCheckbutton)
                and getattr(widget, "_ui_check_role", None) == "chip"):
            # The white cue belongs to the individual unselected chip, not to
            # the logical row.  Store the owning-row hover state on the chip
            # so selecting it can immediately restore its normal selected
            # outline without waiting for the pointer to leave/re-enter.
            widget._ui_search_row_hover_active = bool(active)
            widget._ui_search_chip_hover_border = SEARCH_CHIP_HOVER_BORDER
            widget._sync_chip_contrast()
        rail = getattr(widget, "_mtg_search_association_rail", None)
        if rail is not None:
            try:
                rail.configure(
                    background=(SEARCH_ROW_HOVER_COLOR if active else PALETTE["surface"]))
                self._draw_association_rail(rail, active=active)
            except tk.TclError:
                pass
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            self._set_search_row_hover_surface(child, active)

    def _register_search_row_hover(self, parent, row, *, last_column):
        """Register a non-layout hover band for one logical Search filter row."""
        band = tk.Frame(
            parent, bg=PALETTE["surface"], bd=0, highlightthickness=0,
            takefocus=0)
        region = {
            "parent": parent, "row": int(row), "last_column": int(last_column),
            "band": band, "active": False,
        }
        self._search_row_hover_regions.append(region)
        parent.bind(
            "<Configure>",
            lambda _event, item=region: self._position_search_row_hover_region(item),
            add="+")
        self.after_idle(lambda item=region: self._position_search_row_hover_region(item))

    def _sync_search_row_hover(self, _event=None):
        """Highlight whichever Search row currently contains the pointer."""
        try:
            pointer_x, pointer_y = self.winfo_pointerxy()
        except tk.TclError:
            return
        for region in getattr(self, "_search_row_hover_regions", ()):
            parent = region["parent"]
            band = region["band"]
            active = False
            try:
                if parent.winfo_ismapped():
                    self._position_search_row_hover_region(region)
                    left = band.winfo_rootx()
                    top = band.winfo_rooty()
                    right = left + band.winfo_width()
                    bottom = top + band.winfo_height()
                    active = left <= pointer_x < right and top <= pointer_y < bottom
            except tk.TclError:
                active = False
            if active == region["active"]:
                continue
            region["active"] = active
            try:
                band.configure(
                    bg=(SEARCH_ROW_HOVER_COLOR if active else PALETTE["surface"]),
                )
                for widget in parent.grid_slaves(row=region["row"]):
                    self._set_search_row_hover_surface(widget, active)
                band.lower()
            except tk.TclError:
                continue

    def _bind_search_row_hover_tracking(self):
        """Track pointer movement through the toplevel bindtag, without row wrappers."""
        if getattr(self, "_search_row_hover_tracking_bound", False):
            return
        self._search_row_hover_tracking_bound = True
        self.bind("<Motion>", self._sync_search_row_hover, add="+")
        self.bind(
            "<Leave>",
            lambda _event: self.after_idle(self._sync_search_row_hover),
            add="+")

    def _build_search_row_label(
            self, parent, text, *, row, pady, tooltip_key=None, tooltip_text=None):
        """Build a primary label and overlay a zero-geometry Style B fade rail.

        The label is gridded exactly as it was before association rails existed.
        The rail is placed relative to the label instead of packed or gridded, so
        it cannot contribute a requested width or move the shared control column.
        """
        label = ttk.Label(parent, text=text)
        label.grid(
            row=row, column=0, sticky="nw",
            padx=(0, FILTER_LABEL_GAP), pady=pady)

        label.update_idletasks()
        available = FILTER_LABEL_WIDTH - FILTER_LABEL_GAP
        rail_width = (available - int(label.winfo_reqwidth())
                      - ASSOCIATION_RAIL_TEXT_GAP)
        if rail_width >= ASSOCIATION_RAIL_MIN_WIDTH:
            rail = tk.Canvas(
                parent, width=rail_width, height=ASSOCIATION_RAIL_CANVAS_HEIGHT,
                background=PALETTE["surface"], highlightthickness=0,
                borderwidth=0, relief="flat", takefocus=0)
            rail.place(
                in_=label, relx=1.0, rely=0.5,
                x=ASSOCIATION_RAIL_TEXT_GAP, anchor="w")
            label._mtg_search_association_rail = rail
            rail.bind(
                "<Configure>",
                lambda _event, canvas=rail: self._draw_association_rail(canvas),
                add="+")

        if tooltip_key:
            self._add_standard_filter_tooltip(label, tooltip_key)
        elif tooltip_text:
            self._add_tooltip(label, tooltip_text, wraplength=380)
        return label

    def _initialize_search_filter_state(self):
        """Create every filter's state before any of its widgets exist.

        Optional filters are destroyed when removed, so their state cannot live
        in the widget. Variables and selection sets are made once here and the
        builders bind to them, which lets a row be removed and re-added without
        losing what the user chose. Text-backed controls have no variable, so
        they shadow their value through _capture_advanced_filter_values.
        """
        self.card_type_vars = {}
        warm_catalogs = {"card_types": (), "supertypes": ()}
        preferences = getattr(self, "_ui_preferences_repository", None)
        if preferences is not None:
            try:
                warm_catalogs = preferences.load_search_type_line_catalogs()
            except (OSError, ValueError, TypeError, AttributeError):
                log.debug("Could not load Type Line warm-start catalogs", exc_info=True)
        self._card_type_catalog = list(warm_catalogs.get("card_types") or ())
        self._card_type_chip_widgets = ()
        self._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
        self.q_card_type_mode = tk.StringVar(value="any")

        self.property_vars = {}
        self._property_catalog = list(warm_catalogs.get("supertypes") or ())
        self._search_type_line_cold_start = True
        self._card_type_authority_available = False
        self._supertype_authority_available = False
        # Any, like every other multi-select. Only 17 cards in the whole
        # paper pool carry two supertypes, so an "all" default silently
        # emptied any two-value selection.
        self.q_supertype_mode = tk.StringVar(value="any")

        self.color_vars = {}
        self.q_color_mode = tk.StringVar(value="within")
        self.q_color_scope = tk.StringVar(value="identity")
        self._colorless_check = None
        self.produces_vars = {}
        self.q_produces_mode = tk.StringVar(value="includes")

        self._selected_keywords = set()
        self._keyword_catalog = []
        self.q_keyword_mode = tk.StringVar(value="any")
        self._selected_subtypes = set()
        self._subtype_catalog = []
        self._subtype_category_by_value = {}
        self.q_subtype_mode = tk.StringVar(value="any")
        self._selected_rarities = set()
        self._rarity_catalog = []
        self._release_year_catalog = []
        self.q_format = tk.StringVar(value="")
        # Playable is legal-or-restricted. Banned and restricted are the
        # states a deck check asks about and nothing could previously reach.
        self.q_format_status = tk.StringVar(value="playable")
        self.q_rules_mode = tk.StringVar(value="all")
        # Search Scope keeps its compact content-trait storage. Boolean card
        # properties live in independent facets so each picker owns Match.
        self._selected_traits = set(DEFAULT_CONTENT_TRAITS)
        self._selected_mana_features = set()
        self.q_mana_feature_mode = tk.StringVar(value="any")
        self._selected_special_properties = set()
        self.q_special_property_mode = tk.StringVar(value="any")
        self._selected_status_properties = set()
        self.q_status_property_mode = tk.StringVar(value="any")
        self._selected_layouts = set()
        self.q_layout_mode = tk.StringVar(value="any")
        self.q_pip_mode = tk.StringVar(value="all")
        self._layout_catalog = []
        self._card_form_btn = None
        self._layout_duplicates = set()
        self._context_snapshot = None
        self._context_poll_after = None
        self._context_debounce_after = None
        self._context_requested_criteria = None
        self._context_applied_criteria = None
        self._active_search_criteria = None
        self._color_checks = {}
        self._produces_checks = {}
        self._pip_checks = {}
        self.pip_vars = {}
        self._pip_checks = {}
        self.q_pip_min = None
        self._rules_text_shadow = []
        self._rules_pending_shadow = ""
        # Widget handles for advanced rows. They must exist as None from the
        # start: a guard that reads self._rarity_btn raises AttributeError just
        # as readily as the call it was written to protect.
        self.q_rules = None
        self._format_btn = None
        self._rarity_btn = None
        self._subtype_btn = None
        self._keyword_btn = None
        self._search_scope_btn = None
        self._mana_cost_features_btn = None
        self._special_properties_btn = None
        self._status_properties_btn = None
        self._color_indicator_var = None
        self._color_indicator_check = None
        self._property_chip_frame = None
        for name in (
                "q_cmc_min", "q_cmc_max", "q_power_min", "q_power_max",
                "q_toughness_min", "q_toughness_max",
                "q_loyalty_min", "q_loyalty_max",
                "q_defense_min", "q_defense_max",
                "q_released_min", "q_released_max"):
            setattr(self, name, None)

    def _build_search_pane(self, parent):
        self._search_row_hover_regions = []
        form = ttk.Frame(parent)
        form.pack(fill="x")
        form.columnconfigure(0, minsize=FILTER_LABEL_WIDTH)
        form.columnconfigure(1, weight=1, uniform="search_control")
        form.columnconfigure(2, minsize=76)
        form.columnconfigure(3, weight=1, uniform="search_control")
        self._initialize_search_filter_state()
        # Keep the familiar vertical Search form, but make the printed type-line
        # relationship explicit: Supertype -> Card Type -> Subtype.
        self._build_name_filter(form)
        self._build_standard_type_line_filters(form)
        self._build_color_filters(form, row=5)
        self._build_standard_stats_filter(form, row=6)
        for row in (0, 2, 3, 4, 5, 6, 7):
            self._register_search_row_hover(form, row, last_column=3)
        self._build_advanced_filter_zone(parent)
        self._bind_search_row_hover_tracking()
        self._bind_search_outside_click_selection_cleanup()
        self._build_search_actions(parent)
        self._build_results_table(parent)


    def _filter_chip(self, parent, text, variable, command=None, **options):
        return ClassicCheckbutton(
            parent, text=text, variable=variable, indicatoron=False,
            role="chip",
            command=(command or self._update_search_filter_summary),
            **options,
        )

    def _build_name_filter(self, form):
        # Primary card identity starts with a full-width name field. Language is
        # still one Search criterion but its control lives with Printings.
        self._build_search_row_label(
            form, "Card Name", row=0, pady=SEARCH_ROW_PADY, tooltip_key="name")
        self._search_name_batch = ()
        self._search_name_batch_display = ""
        self.q_name = AutocompleteEntry(form)
        self.q_name.set_suggest_source(self.search_repository.name_suggestions)
        self.q_name.set("")
        self.q_name.grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(self.q_name, "name")
        self.q_name.bind("<Return>", lambda e, c=self.q_name: self._autocomplete_return(c))
        self.q_name.bind("<<ComboboxSelected>>", self._on_name_autocomplete_selected)
        self.q_name.bind("<KeyRelease>", self._on_name_filter_edited, add="+")
        self._bind_editable_focus_behavior(self.q_name)
        self.english_only = tk.BooleanVar(value=True)
        # Tk buttons do not consistently take keyboard focus on Windows, so a
        # click elsewhere may leave this entry focused and its non-focus-taking
        # suggestion popup visible. Dismiss it explicitly on outside clicks.
        self.bind_all(
            "<ButtonPress-1>", self._dismiss_name_autocomplete_on_outside_click,
            add="+")

    SEARCH_BLUR_FIELDS = (
        "q_cmc_min", "q_cmc_max", "q_power_min", "q_power_max",
        "q_toughness_min", "q_toughness_max", "q_loyalty_min", "q_loyalty_max",
        "q_defense_min", "q_defense_max", "q_released_min", "q_released_max",
    )

    def _bind_search_outside_click_selection_cleanup(self):
        """Clear Search text highlights when a click lands outside that editor."""
        self._refresh_search_blur_widgets()
        self.bind_all(
            "<ButtonPress-1>", self._clear_search_selections_on_outside_click,
            add="+")

    def _refresh_search_blur_widgets(self):
        """Recollect the editors that blur on an outside click.

        Optional filters come and go, so this is recomputed whenever a row is
        added or removed rather than captured once at startup.
        """
        widgets = []
        rules = getattr(self, "q_rules", None)
        if rules is not None:
            widgets.append(rules.entry)
        for name in self.SEARCH_BLUR_FIELDS:
            widget = getattr(self, name, None)
            if widget is not None:
                widgets.append(widget)
        self._search_blur_clear_widgets = tuple(widgets)

    def _clear_search_selections_on_outside_click(self, event):
        """Give Search editors the same outside-click blur behavior as Card Name."""
        target = getattr(event, "widget", None)
        target_path = str(target or "")
        for widget in getattr(self, "_search_blur_clear_widgets", ()):
            try:
                if not widget.winfo_exists() or target_path == str(widget):
                    continue
            except tk.TclError:
                continue
            self._clear_editable_selection(widget)

            def finish_blur(w=widget, clicked=target):
                try:
                    if str(self.tk.call("focus") or "") == str(w):
                        focus_target = getattr(clicked, "focus_set", None)
                        if callable(focus_target):
                            focus_target()
                        else:
                            self.focus_set()
                except (tk.TclError, AttributeError):
                    try:
                        self.focus_set()
                    except (tk.TclError, AttributeError):
                        pass
                self._clear_editable_selection(w)

            self.after_idle(finish_blur)

    def _build_standard_type_line_filters(self, form):
        heading = ttk.Label(form, text="TYPE LINE", style="Section.TLabel")
        heading.grid(row=1, column=0, columnspan=4, sticky="w", pady=TYPE_LINE_HEADING_PADY)

        self._build_search_row_label(
            form, "Supertype", row=2, pady=SEARCH_ROW_PADY,
            tooltip_key="supertypes")
        super_box = ttk.Frame(form)
        super_box.grid(
            row=2, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._property_chip_frame = ttk.Frame(super_box)
        self._property_chip_frame.pack(fill="x")
        self._render_supertype_chips()
        self._build_mode_row(
            super_box, self.q_supertype_mode, "supertypes")

        self._build_card_type_filters(form, row=3)

        self._build_search_row_label(
            form, "Subtype", row=4, pady=SEARCH_ROW_PADY,
            tooltip_key="subtype")
        self._subtype_btn = AppButton(
            form, text=self._picker_button_text(
                self._selected_subtypes, "Any", "subtypes",
                max_visible=10, single_line=True),
            role="search_picker", command=self._choose_subtypes)
        self._subtype_btn.grid(
            row=4, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(self._subtype_btn, "subtype")

    def _build_card_type_filters(self, form, *, row=1):
        self._build_search_row_label(
            form, "Card Type", row=row, pady=SEARCH_ROW_PADY,
            tooltip_key="card_type")
        typebox = ttk.Frame(form)
        typebox.grid(row=row, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._card_type_chip_frame = ttk.Frame(typebox)
        self._card_type_chip_frame.pack(fill="x")
        self._card_type_chip_frame.bind(
            "<Configure>", self._layout_card_type_chips, add="+")
        self._card_type_chip_widgets = self._render_trusted_chips(
            self._card_type_chip_frame, self._card_type_catalog, self.card_type_vars,
            columns=CARD_TYPE_MIN_COLUMNS, tooltip_key="card_type",
            loading_placeholders=CARD_TYPE_LOADING_SLOTS,
            empty_label="No Card Types in this scope")
        self._layout_card_type_chips()
        self._build_mode_row(
            typebox, self.q_card_type_mode, "card types",
            meanings={
                "any": ("Any: a card needs at least one selected Card Type. Selecting "
                        "additional types can broaden this filter."),
                "all": ("All: a card must have every selected Card Type across its "
                        "type line, including its faces. Additional types narrow the filter."),
                "none": "None: exclude cards that have any selected Card Type.",
            })


    def _build_color_filters(self, form, *, row=3):
        self._build_search_row_label(
            form, "Mana Color", row=row, pady=SEARCH_ROW_PADY,
            tooltip_key="colors")
        colorwrap = ttk.Frame(form)
        colorwrap.grid(row=row, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        colorbox = ttk.Frame(colorwrap)
        colorbox.pack(fill="x")
        for c in (*COLORS, "C"):
            v = tk.BooleanVar(value=False)
            self.color_vars[c] = v
            kw = {"text": " " + MANA_NAMES[c], "variable": v,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            if c != "C":
                # Colorless is not a colour a card can also be, so ticking a
                # colour releases it. Silently dropping it from the query made
                # White plus Colorless return exactly the mono-white result.
                kw["command"] = self._sync_colorless_availability
            pip_image = self._filter_pip_image(c)
            if pip_image:
                kw["image"] = pip_image
                kw["compound"] = "left"
            check = ttk.Checkbutton(colorbox, **kw)
            self._color_checks[c] = check
            _pack_mana_choice(check)
            if c != "C":
                self._add_standard_filter_tooltip(check, "colors")
            if c == "C":
                self._colorless_check = check
                self._add_tooltip(
                    check,
                    "Colorless means the card has no colors in the selected Use field. "
                    "With Color Identity, it means no color identity; with Card Colors, "
                    "it means the card itself is colorless. Colorless cannot be combined "
                    "with White, Blue, Black, Red, or Green in this filter. This is not "
                    "the same as producing colorless mana; use Mana Produced for that.",
                    wraplength=410)
        scope = ttk.Frame(colorwrap)
        scope.pack(fill="x", pady=MODE_ROW_PADY)
        scope.columnconfigure(0, minsize=MATCH_MODE_LABEL_WIDTH)
        ttk.Label(scope, text=COLOR_SCOPE_LABEL, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        scope_help = {
            "identity": (
                "Color Identity uses the color set that matters for Commander deck "
                "construction. It includes colors from colored mana symbols in the mana "
                "cost or rules text, color indicators, and color-defining abilities, including "
                "information on both faces of a double-faced card. Reminder text does not add "
                "colors. A card can be colorless itself but still have a colored identity."),
            "colors": (
                "Card Colors uses only the colors the card actually is. A colored mana "
                "symbol appearing only in rules text does not by itself make the card that "
                "color. Lands and cards with Devoid can be colorless here even when their "
                "Color Identity contains one or more colors."),
        }
        for column, (label, value) in enumerate(
                (("Color Identity", "identity"), ("Card Colors", "colors")),
                start=1):
            radio = ttk.Radiobutton(
                scope, text=label, variable=self.q_color_scope, value=value,
                style="FormChoice.TRadiobutton",
                command=self._on_color_scope_changed)
            radio.grid(
                row=0, column=column, sticky="w",
                padx=(MODE_CONTROL_GAP if column == 1 else MATCH_MODE_CHOICE_GAP, 0))
            self._add_tooltip(radio, scope_help[value], wraplength=390)
        colormode = ttk.Frame(colorwrap)
        colormode.pack(fill="x", pady=MODE_ROW_PADY)
        colormode.columnconfigure(0, minsize=MATCH_MODE_LABEL_WIDTH)
        ttk.Label(
            colormode, text=MATCH_MODE_LABEL, style="Muted.TLabel").grid(
                row=0, column=0, sticky="w")
        color_mode_help = {
            "within": (
                "Within: a card may use any subset of the selected colors, but no "
                "colors outside them. Selecting White + Blue allows colorless, white, "
                "blue, and white-blue cards, but excludes cards containing another color."),
            "includes": (
                "Contains: a card must include every selected color, but it may include "
                "additional colors. Selecting White + Blue includes white-blue and "
                "three-, four-, or five-color cards containing both colors."),
            "exact": (
                "Exactly: a card must have exactly the selected colors and no others. "
                "Selecting White + Blue excludes mono-colored cards and cards containing "
                "a third color."),
        }
        for column, (label, value) in enumerate(
                (("Within", "within"), ("Contains", "includes"),
                 ("Exactly", "exact")), start=1):
            radio = ttk.Radiobutton(
                colormode, text=label, variable=self.q_color_mode, value=value,
                style="FormChoice.TRadiobutton",
                command=self._update_search_filter_summary)
            radio.grid(
                row=0, column=column, sticky="w",
                padx=(MODE_CONTROL_GAP if column == 1 else MATCH_MODE_CHOICE_GAP, 0))
            self._add_tooltip(radio, color_mode_help[value], wraplength=390)
        self._sync_colorless_availability()

    def _on_color_scope_changed(self):
        """Refresh Search state when the selected color field changes."""
        self._update_search_filter_summary()

    def _sync_colorless_availability(self, *, update_summary=True):
        """Release the Colorless pip while any colour is selected.

        Colorless is the absence of colour in both columns this filter can
        read, so it cannot be combined with one. The query layer has always
        ignored it in that case; leaving the box ticked was what made the
        result look wrong.
        """
        colored = any(
            variable.get() for key, variable in self.color_vars.items()
            if key in COLORS)
        colorless = self.color_vars.get("C")
        if colored and colorless is not None and colorless.get():
            colorless.set(False)
        check = getattr(self, "_colorless_check", None)
        if check is not None:
            try:
                context_available = bool(
                    getattr(check, "_mtg_context_available", True))
                selected = bool(colorless is not None and colorless.get())
                enabled = (not colored) and (context_available or selected)
                check.state(["!disabled"] if enabled else ["disabled"])
            except tk.TclError:
                pass
        if update_summary:
            self._update_search_filter_summary()

    def _build_produces_filter(self, parent, row):
        """Mana a card can actually produce, distinct from its colour identity.

        Deliberately a twin of the Mana Color control: the same pip checkboxes and
        the same within/contains/exactly modes, because the two filters read
        the same comma-joined WUBRG(+C) encoding. Only the default mode differs
        -- "contains" answers the mana-base question people actually ask.
        """
        wrap = ttk.Frame(parent)
        wrap.grid(row=row, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)
        box = ttk.Frame(wrap)
        box.pack(fill="x")
        for color in (*COLORS, "C"):
            variable = tk.BooleanVar(value=False)
            self.produces_vars[color] = variable
            kw = {"text": " " + MANA_NAMES[color], "variable": variable,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            pip_image = self._filter_pip_image(color)
            if pip_image:
                kw["image"] = pip_image
                kw["compound"] = "left"
            produced = ttk.Checkbutton(box, **kw)
            self._produces_checks[color] = produced
            _pack_mana_choice(produced)
            produced_help = (
                f"{MANA_NAMES[color]}: filter for cards that can produce "
                f"{MANA_NAMES[color].lower()} mana. "
                + (
                    "Here Colorless means actual colorless mana (C), not a card that "
                    "happens to be colorless. "
                    if color == "C" else ""
                )
                + "Mana Produced is independent of the card's own colors and Color Identity. "
                  "The current Match rule determines how this choice combines with other "
                  "selected mana colors."
            )
            self._add_tooltip(produced, produced_help, wraplength=410)
        mode = ttk.Frame(wrap)
        mode.pack(fill="x", pady=MODE_ROW_PADY)
        mode.columnconfigure(0, minsize=MATCH_MODE_LABEL_WIDTH)
        ttk.Label(mode, text=MATCH_MODE_LABEL, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        help_text = {
            "within": (
                "Within: a card must produce at least one selected mana color and cannot "
                "produce mana colors outside the selected set. White + Blue allows "
                "white-only, blue-only, and white-blue producers, but not a white-blue-black producer."),
            "includes": (
                "Contains: a card must be able to produce every selected mana color, but "
                "it may also produce additional colors. This is useful for finding mana "
                "sources that cover all colors you need."),
            "exact": (
                "Exactly: a card must produce exactly the selected mana colors and no "
                "others. A source that can produce an additional color is excluded."),
        }
        for column, (label, value) in enumerate(
                (("Within", "within"), ("Contains", "includes"),
                 ("Exactly", "exact")), start=1):
            radio = ttk.Radiobutton(
                mode, text=label, variable=self.q_produces_mode, value=value,
                style="FormChoice.TRadiobutton",
                command=self._update_search_filter_summary)
            radio.grid(
                row=0, column=column, sticky="w",
                padx=(MODE_CONTROL_GAP if column == 1 else MATCH_MODE_CHOICE_GAP, 0))
            self._add_tooltip(radio, help_text[value], wraplength=390)

    def _configure_zero_start_spinbox(self, spin):
        def mouse(event):
            if spin.get().strip():
                return None
            try:
                element = spin.identify(event.x, event.y)
            except tk.TclError:
                element = ""
            if "arrow" in str(element):
                spin.set("0")
                return "break"
            return None

        def key(_event):
            if not spin.get().strip():
                spin.set("0")
                return "break"
            return None

        spin.bind("<Button-1>", mouse, add="+")
        spin.bind("<Up>", key, add="+")
        spin.bind("<Down>", key, add="+")

    def _build_rules_text_filter(self, form, *, row, advanced=False):
        rules_box = ttk.Frame(form)
        rules_box.grid(
            row=row, column=1, sticky="ew",
            pady=(ADVANCED_ROW_PADY if advanced else SEARCH_ROW_PADY))
        rules_box.columnconfigure(0, weight=1)
        self.q_rules = TokenBubbleEntry(
            rules_box, search_command=self._do_search,
            change_command=self._update_search_filter_summary)
        self.q_rules.grid(row=0, column=0, sticky="ew")
        self._bind_editable_focus_behavior(self.q_rules.entry)
        self._add_tooltip(
            self.q_rules.entry,
            ("Unquoted words may appear anywhere in the card's rules text across its "
             "faces; use double quotes for an exact phrase. Press Enter to add another "
             "Rules Text entry."),
            wraplength=420)
        mode_box = ttk.Frame(rules_box)
        mode_box.grid(row=1, column=0, sticky="ew", pady=(0, 0))
        self._build_mode_row(
            mode_box, self.q_rules_mode, "chips",
            meanings={
                "any": "Any: at least one Rules Text chip must match the card.",
                "all": "All: every Rules Text chip must match the card.",
                "none": ("None: exclude every card matching any Rules Text "
                         "chip, which finds cards that never mention a word."),
            })


    # ------------------------------------------------------------------
    # advanced filter controls
    # ------------------------------------------------------------------

    RANGE_BOUNDS_HELP = (
        "Min and Max are inclusive. Leave Min blank for no lower limit or Max blank "
        "for no upper limit. Enter the same number in both fields to require one exact value.")

    @staticmethod
    def _layout_numeric_range(box, low, high):
        """Place one Min / to / Max control on the shared range geometry.

        Pixel minimums, rather than each widget's requested character width,
        keep spinbox ranges and release-year combobox ranges on the same rails.
        """
        box.columnconfigure(0, minsize=RANGE_FIELD_WIDTH_PX)
        box.columnconfigure(1, minsize=RANGE_SEPARATOR_WIDTH_PX)
        box.columnconfigure(2, minsize=RANGE_FIELD_WIDTH_PX)
        low.grid(row=0, column=0, sticky="ew")
        ttk.Label(box, text="to", style="Muted.TLabel", anchor="center").grid(
            row=0, column=1, sticky="ew")
        high.grid(row=0, column=2, sticky="ew")

    def _numeric_pair(self, parent, attribute_prefix, tooltip_key):
        """Two spinboxes using the shared fixed Min / to / Max mini-grid."""
        box = ttk.Frame(parent)
        # A tiny character width prevents the widget's requested size from
        # overriding the pixel rails; sticky=ew expands it to the shared field.
        low = AppSpinbox(box, from_=0, to=999, width=1)
        high = AppSpinbox(box, from_=0, to=999, width=1)
        self._layout_numeric_range(box, low, high)
        for widget in (low, high):
            widget.delete(0, "end")
            self._configure_zero_start_spinbox(widget)
            filter_help = filter_tooltip(tooltip_key)
            self._add_tooltip(
                widget, f"{filter_help}\n\n{self.RANGE_BOUNDS_HELP}", wraplength=410)
            widget.bind(
                "<KeyRelease>",
                lambda _event: self._update_search_filter_summary(), add="+")
            widget.bind(
                "<ButtonRelease-1>",
                lambda _event: self._update_search_filter_summary(), add="+")
            widget.bind(
                "<FocusOut>",
                lambda _event: self._update_search_filter_summary(), add="+")
        setattr(self, f"{attribute_prefix}_min", low)
        setattr(self, f"{attribute_prefix}_max", high)
        return box

    def _trait_subset_selected(self, keys, selected_attr):
        selected = set(getattr(self, selected_attr, set()) or ())
        return {key for key in keys if key in selected}

    def _context_trait_label(self, key, count_attr, picker_labels=None):
        label = (picker_labels or TRAIT_LABELS).get(key, TRAIT_LABELS.get(key, key))
        snapshot = getattr(self, "_context_snapshot", None)
        if snapshot is None:
            return label
        counts = getattr(snapshot, count_attr, None) or {}
        count = int(counts.get(key, 0))
        return f"{label} · {count:,}"

    def _choose_trait_subset(self, title, keys, button_attr, noun, *,
                             selected_attr, mode_var, count_attr,
                             picker_labels=None, help_text=None):
        keys = tuple(keys)
        selected = self._trait_subset_selected(keys, selected_attr)

        def apply(chosen):
            chosen = {str(value) for value in chosen if str(value) in keys}
            setattr(self, selected_attr, chosen)
            button = getattr(self, button_attr, None)
            self._set_picker_text(button, self._picker_button_text(
                {TRAIT_LABELS[key] for key in chosen if key in TRAIT_LABELS},
                "Any", noun, max_visible=8, single_line=True))
            self._update_search_filter_summary()

        snapshot = getattr(self, "_context_snapshot", None)
        counts = getattr(snapshot, count_attr, None) if snapshot is not None else None
        self._open_search_multi_picker(
            title,
            [
                (key, self._context_trait_label(key, count_attr, picker_labels),
                 {"zero_count": (counts is not None and int(counts.get(key, 0)) <= 0)})
                for key in keys
            ],
            selected, apply, mode_var=mode_var, mode_label=MATCH_MODE_LABEL,
            help_text=(help_text or
                       "Choose one or more properties. Each count shows how many cards match "
                       "that choice with the other current filters."))

    def _build_trait_subset_button(self, parent, keys, button_attr, title, noun, *,
                                   selected_attr, mode_var, count_attr,
                                   picker_labels=None, help_text=None):
        selected = self._trait_subset_selected(keys, selected_attr)
        button = AppButton(
            parent,
            text=self._picker_button_text(
                {TRAIT_LABELS[key] for key in selected}, "Any", noun,
                max_visible=8, single_line=True),
            role="search_picker",
            command=lambda: self._choose_trait_subset(
                title, keys, button_attr, noun, selected_attr=selected_attr,
                mode_var=mode_var, count_attr=count_attr,
                picker_labels=picker_labels, help_text=help_text))
        button.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)
        setattr(self, button_attr, button)

    def _reset_trait_subset(self, keys, button_attr, selected_attr, mode_var):
        setattr(self, selected_attr, set())
        mode_var.set("any")
        setattr(self, button_attr, None)

    def _refresh_split_trait_button_texts(self):
        groups = (
            (MANA_COST_FEATURE_KEYS, "_selected_mana_features",
             "_mana_cost_features_btn", "features"),
            (SPECIAL_PROPERTY_KEYS, "_selected_special_properties",
             "_special_properties_btn", "properties"),
            (STATUS_PROPERTY_KEYS, "_selected_status_properties",
             "_status_properties_btn", "status properties"),
        )
        for keys, selected_attr, button_attr, noun in groups:
            button = getattr(self, button_attr, None)
            selected = self._trait_subset_selected(keys, selected_attr)
            self._set_picker_text(button, self._picker_button_text(
                {TRAIT_LABELS[key] for key in selected}, "Any", noun,
                max_visible=8, single_line=True))
        scope = {key for key in self._selected_traits if key in CONTENT_TRAIT_KEYS}
        self._set_picker_text(self._search_scope_btn, self._picker_button_text(
            {TRAIT_LABELS[key] for key in scope}, "Cards", "objects",
            max_visible=4, single_line=True))

    def _build_filter_search_scope(self, parent):
        self._search_scope_btn = AppButton(
            parent, text=self._picker_button_text(
                {TRAIT_LABELS[key] for key in self._selected_traits
                 if key in CONTENT_TRAIT_KEYS},
                "Cards", "objects", max_visible=4, single_line=True),
            role="search_picker", command=self._choose_search_scope)
        self._search_scope_btn.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)

    def _reset_filter_search_scope(self):
        self._selected_traits = set(DEFAULT_CONTENT_TRAITS)
        self._search_scope_btn = None

    def _choose_search_scope(self):
        selected = {key for key in self._selected_traits if key in CONTENT_TRAIT_KEYS}

        def apply(chosen):
            chosen = {str(value) for value in chosen if str(value) in CONTENT_TRAIT_KEYS}
            self._selected_traits = chosen or set(DEFAULT_CONTENT_TRAITS)
            self._set_picker_text(self._search_scope_btn, self._picker_button_text(
                {TRAIT_LABELS[key] for key in self._selected_traits},
                "Cards", "objects", max_visible=4, single_line=True))
            self._content_types_from_traits()
            self._on_content_filter_change()
            self._update_search_filter_summary()

        snapshot = getattr(self, "_context_snapshot", None)
        content_counts = getattr(snapshot, "content_counts", None) or {}
        values = []
        for key, kind in CONTENT_TRAIT_KEYS.items():
            label = TRAIT_LABELS[key]
            count = int(content_counts.get(kind, 0))
            shown = f"{label} · {count:,}" if snapshot is not None else label
            values.append((
                key, shown,
                {"zero_count": (snapshot is not None and count <= 0)},
            ))
        self._open_search_multi_picker(
            "Search Scope", values, selected, apply,
            help_text=(
                "Choose which object types Search includes. Each count shows how many objects "
                "of that type match the other current filters."))

    def _build_filter_mana_cost_features(self, parent):
        self._build_trait_subset_button(
            parent, MANA_COST_FEATURE_KEYS, "_mana_cost_features_btn",
            "Mana Cost Features", "features",
            selected_attr="_selected_mana_features",
            mode_var=self.q_mana_feature_mode, count_attr="mana_feature_counts")

    def _reset_filter_mana_cost_features(self):
        self._reset_trait_subset(
            MANA_COST_FEATURE_KEYS, "_mana_cost_features_btn",
            "_selected_mana_features", self.q_mana_feature_mode)

    def _build_filter_special_properties(self, parent):
        self._build_trait_subset_button(
            parent, SPECIAL_PROPERTY_KEYS, "_special_properties_btn",
            "Special Properties", "properties",
            selected_attr="_selected_special_properties",
            mode_var=self.q_special_property_mode, count_attr="special_property_counts",
            picker_labels=SPECIAL_PROPERTY_PICKER_LABELS,
            help_text=(
                "Choose rare or unusual card characteristics. Counts show how many cards "
                "match each property with the other current filters."))

    def _reset_filter_special_properties(self):
        self._reset_trait_subset(
            SPECIAL_PROPERTY_KEYS, "_special_properties_btn",
            "_selected_special_properties", self.q_special_property_mode)

    def _build_filter_status_properties(self, parent):
        self._build_trait_subset_button(
            parent, STATUS_PROPERTY_KEYS, "_status_properties_btn",
            "Product / Status", "status properties",
            selected_attr="_selected_status_properties",
            mode_var=self.q_status_property_mode, count_attr="status_property_counts")

    def _reset_filter_status_properties(self):
        self._reset_trait_subset(
            STATUS_PROPERTY_KEYS, "_status_properties_btn",
            "_selected_status_properties", self.q_status_property_mode)

    def _build_filter_card_form(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)
        self._card_form_btn = AppButton(
            box, text=self._picker_button_text(
                {self._layout_display_name(value) for value in self._selected_layouts},
                "Any", "forms", max_visible=10, single_line=True),
            role="search_picker", command=self._choose_card_forms)
        self._card_form_btn.pack(fill="x")
        self._build_mode_row(
            box, self.q_layout_mode, "forms",
            meanings={
                "any": "Any: the card uses one of the selected card forms.",
                "none": "None: exclude every card using any selected card form.",
            }, choices=self.ANY_NONE_CHOICES)

    def _reset_filter_card_form(self):
        self._selected_layouts = set()
        self.q_layout_mode.set("any")
        self._card_form_btn = None

    def _contextual_layout_catalog(self):
        snapshot = getattr(self, "_context_snapshot", None)
        counts = (snapshot.layout_counts if snapshot is not None else {}) or {}
        catalog = list(getattr(self, "_layout_catalog", ()) or ())
        selected = set(getattr(self, "_selected_layouts", set()) or ())
        duplicates = set(getattr(self, "_layout_duplicates", set()) or ())
        # A hidden duplicate from a legacy workspace stays visible until the user
        # clears it, so restoring old state can never silently change its query.
        catalog = [item for item in catalog if item[0] not in duplicates or item[0] in selected]
        return sorted(
            catalog,
            key=lambda item: (
                0 if item[0] in selected else 1,
                0 if int(counts.get(item[0], 0)) > 0 else 1,
                -int(counts.get(item[0], 0)),
                self._layout_display_name(item[0]).casefold(),
            ))

    def _choose_card_forms(self):
        valid = {key for key, _count in self._layout_catalog}
        snapshot = getattr(self, "_context_snapshot", None)
        counts = (snapshot.layout_counts if snapshot is not None else {}) or {}

        def apply(chosen):
            self._selected_layouts = {str(value) for value in chosen if str(value) in valid}
            self._set_picker_text(
                self._card_form_btn,
                self._picker_button_text(
                    {self._layout_display_name(value) for value in self._selected_layouts},
                    "Any", "forms", max_visible=10, single_line=True))
            self._update_search_filter_summary()

        self._open_search_multi_picker(
            "Choose Card Forms",
            [
                (key, f"{self._layout_display_name(key)} · {int(counts.get(key, count)):,}",
                 {"zero_count": (snapshot is not None and
                                  int(counts.get(key, count)) <= 0)})
                for key, count in self._contextual_layout_catalog()
            ],
            set(self._selected_layouts), apply,
            mode_var=self.q_layout_mode,
            mode_label=MATCH_MODE_LABEL,
            help_text=(
                "Choose one or more card forms. Current matches appear first; "
                "zero-count values remain visible but unavailable."),
            mode_choices=self.ANY_NONE_CHOICES)

    LAYOUT_LABELS = {
        "modal_dfc": "Modal double-faced",
        "double_faced_token": "Double-faced token",
        "art_series": "Art series",
        "reversible_card": "Reversible",
        "transform": "Transforming",
    }

    def _layout_display_name(self, value):
        """Readable name for one Scryfall layout key.

        An unmapped key is title-cased rather than hidden: a layout this build
        has never seen must still be selectable.
        """
        key = str(value or "").strip()
        return self.LAYOUT_LABELS.get(
            key.casefold(), key.replace("_", " ").capitalize())

    PIP_SELECTION_HELP = (
        "Mana Symbols in Cost filters the actual mana symbols printed in a card's cost. "
        "Match: All requires every selected color to be represented, Any requires at "
        "least one, and None excludes all selected colors. Minimum is the total number "
        "of qualifying physical mana symbols, not a separate minimum for each color. "
        "A hybrid symbol such as {W/B} represents both White and Black for matching but "
        "counts as one symbol toward Minimum. Colorless means the literal {C} symbol; "
        "generic symbols such as {1}, {2}, or {X} do not count. This is separate from "
        "Mana Color and Mana Produced."
    )

    def _build_filter_mana_pips(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)
        pips = ttk.Frame(box)
        pips.pack(fill="x")
        for color in (*COLORS, "C"):
            variable = tk.BooleanVar(value=False)
            self.pip_vars[color] = variable
            kw = {"text": " " + MANA_NAMES[color], "variable": variable,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            pip_image = self._filter_pip_image(color)
            if pip_image:
                kw["image"] = pip_image
                kw["compound"] = "left"
            check = ttk.Checkbutton(pips, **kw)
            self._pip_checks[color] = check
            _pack_mana_choice(check)
            self._add_tooltip(check, self.PIP_SELECTION_HELP, wraplength=380)
        self._build_mode_row(
            box, self.q_pip_mode, "mana-symbol colors",
            meanings={
                "any": ("Any: at least one selected color must be represented. "
                        "Minimum still counts all qualifying selected-color symbols."),
                "all": ("All: every selected color must be represented. A hybrid can "
                        "represent more than one selected color."),
                "none": ("None: exclude costs containing any selected color. Minimum "
                         "does not affect None."),
            })
        row = ttk.Frame(box)
        row.pack(fill="x", pady=MODE_ROW_PADY)
        row.columnconfigure(0, minsize=SECONDARY_LABEL_WIDTH)
        ttk.Label(row, text="Minimum", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        self.q_pip_min = AppSpinbox(row, from_=1, to=9, width=3)
        self.q_pip_min.grid(
            row=0, column=1, sticky="w", padx=(SECONDARY_CONTROL_GAP, 0))
        self._add_tooltip(self.q_pip_min, self.PIP_SELECTION_HELP, wraplength=380)
        self.q_pip_min.delete(0, "end")
        self.q_pip_min.insert(0, "1")
        self.q_pip_min.bind(
            "<KeyRelease>",
            lambda _event: self._update_search_filter_summary(), add="+")
        self.q_pip_min.bind(
            "<ButtonRelease-1>",
            lambda _event: self._update_search_filter_summary(), add="+")
        self.q_pip_min.bind(
            "<FocusOut>",
            lambda _event: self._update_search_filter_summary(), add="+")
        ttk.Label(
            row, text="total selected symbols", style="Muted.TLabel").grid(
                row=0, column=2, sticky="w", padx=(SECONDARY_HELPER_GAP, 0))

    def _reset_filter_mana_pips(self):
        for variable in self.pip_vars.values():
            variable.set(False)
        self.pip_vars = {}
        self.q_pip_mode.set("all")
        self.q_pip_min = None

    def _build_filter_loyalty(self, parent):
        self._numeric_pair(parent, "q_loyalty", "loyalty").grid(
            row=0, column=1, sticky="w", pady=ADVANCED_ROW_PADY)

    def _reset_filter_loyalty(self):
        self.q_loyalty_min = None
        self.q_loyalty_max = None

    def _build_filter_defense(self, parent):
        self._numeric_pair(parent, "q_defense", "defense").grid(
            row=0, column=1, sticky="w", pady=ADVANCED_ROW_PADY)

    def _reset_filter_defense(self):
        self.q_defense_min = None
        self.q_defense_max = None

    def _build_filter_released(self, parent):
        """Year pickers rather than spinners.

        A release year is chosen from a known list, not dialled to; a spinner
        invites holding an arrow through thirty years of Magic.
        """
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="w", pady=ADVANCED_ROW_PADY)
        years = [""] + list(getattr(self, "_release_year_catalog", ()) or ())
        self.q_released_min = AppCombobox(
            box, values=years, width=1, state="readonly")
        self.q_released_min.set("")
        self.q_released_max = AppCombobox(
            box, values=years, width=1, state="readonly")
        self.q_released_max.set("")
        self._layout_numeric_range(
            box, self.q_released_min, self.q_released_max)
        for widget in (self.q_released_min, self.q_released_max):
            widget.bind(
                "<<ComboboxSelected>>",
                lambda _event: self._update_search_filter_summary(), add="+")

    def _reset_filter_released(self):
        self.q_released_min = None
        self.q_released_max = None

    @staticmethod
    def _set_picker_text(button, text):
        """Update a picker's caption, or do nothing when absent.

        The trusted-catalog refresh runs on every startup and does not know
        which rows have been rebuilt, so it must be able to record vocabulary
        without a button to show it on.
        """
        if button is not None:
            button.configure(text=text)

    def _rules_text_values(self, commit_pending=True):
        """Rules-text chips, or nothing when the filter is not present.

        A removed filter contributes nothing to the query (SRCH-034), so this
        reports empty rather than falling back to the shadow. The shadow exists
        only so a workspace restore can refill the row it is about to rebuild.
        """
        rules = getattr(self, "q_rules", None)
        if rules is None:
            return []
        return list(rules.values(commit_pending=commit_pending))

    def _rules_pending_text(self):
        """Uncommitted Rules text, or nothing when the filter is absent."""
        rules = getattr(self, "q_rules", None)
        if rules is None:
            return ""
        return rules.entry.get()

    def _numeric_field_value(self, widget):
        """Spinbox value as a number, or None when absent or blank."""
        if widget is None:
            return None
        try:
            text = str(widget.get()).strip()
        except tk.TclError:
            return None
        if not text:
            return None
        return self._parse_search_number(text, "filter value")

    ADVANCED_TEXT_FIELDS = (
        "q_loyalty_min", "q_loyalty_max", "q_defense_min", "q_defense_max",
        "q_released_min", "q_released_max", "q_pip_min",
    )

    def _capture_advanced_filter_values(self):
        """Text currently held by advanced filter controls, by attribute name."""
        values = {}
        for name in self.ADVANCED_TEXT_FIELDS:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                values[name] = str(widget.get()).strip()
            except tk.TclError:
                continue
        return {name: value for name, value in values.items() if value}

    def _restore_advanced_filter_values(self, values):
        """Refill advanced controls after their rows have been rebuilt.

        A readonly combobox rejects delete/insert, so anything offering set()
        is restored through it and only entry-style widgets are edited.
        """
        for name, value in dict(values or {}).items():
            widget = getattr(self, name, None)
            if widget is None or name not in self.ADVANCED_TEXT_FIELDS:
                continue
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.set(str(value))
                else:
                    widget.delete(0, "end")
                    widget.insert(0, str(value))
            except tk.TclError:
                continue

    # ------------------------------------------------------------------
    # advanced filters, built once and revealed together
    # ------------------------------------------------------------------

    # The widgets a filter row is operated through. Chips and mode radios
    # are left out: they carry their own wording, and a row of fifteen chips
    # repeating one paragraph is noise rather than help.
    CONTROL_TOOLTIP_CLASSES = (
        "TButton", "TSpinbox", "TCombobox", "TEntry", "Entry", "Spinbox")

    def _tooltip_row_controls(self, frame, text):
        """Give a row's controls the same explanation as its label.

        A user hovers the control they are about to use, not the word beside
        it, so a tooltip only on the label is one most people never see.
        """
        pending = list(frame.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_class() not in self.CONTROL_TOOLTIP_CLASSES:
                continue
            if getattr(widget, "_mtg_tooltip", None) is not None:
                continue
            self._add_tooltip(widget, text, wraplength=380)

    def _add_standard_filter_tooltip(self, widget, key):
        """Explain a standard filter exactly like an advanced one."""
        text = filter_tooltip(key)
        if text:
            self._add_tooltip(widget, text, wraplength=380)

    def _build_advanced_filter_zone(self, parent):
        """One button that reveals every remaining filter, grouped by category.

        The previous design built each filter on demand from an Add filter
        menu. It cost nothing unused, but a real search became several menu
        trips before it could be run, and the filters a user reached for most
        had to be re-added every session. Everything here is built once and
        kept; the panel starts collapsed, so an unused Advanced section still
        costs the Results table nothing but a single row.
        """
        self._advanced_filter_rows = {}
        self._advanced_expanded = False

        header = ttk.Frame(parent)
        header.pack(fill="x", pady=ADVANCED_HEADER_PADY)
        self._advanced_btn = AppButton(
            header, text=self.ADVANCED_COLLAPSED_TEXT, role="search_section",
            command=self._toggle_advanced_filters)
        self._advanced_btn.pack(fill="x")

        self._advanced_host = ttk.Frame(parent)
        self._build_advanced_filter_rows()
        # Built now, shown on request: building on first expand would make the
        # first click the slowest one in the panel.
        self._advanced_host.pack_forget()

    ADVANCED_COLLAPSED_TEXT = "▸  Advanced Filter Options"
    ADVANCED_EXPANDED_TEXT = "▾  Advanced Filter Options"

    def _build_advanced_filter_rows(self):
        """Build every advanced filter once, under its category heading."""
        for category, entries in advanced_filters():
            heading = ttk.Label(
                self._advanced_host, text=category.upper(),
                style="Section.TLabel")
            heading.pack(fill="x", anchor="w", pady=ADVANCED_SECTION_HEADING_PADY)
            for entry in entries:
                key = entry["key"]
                builder = getattr(self, f"_build_filter_{key}", None)
                if builder is None:
                    continue
                frame = ttk.Frame(self._advanced_host)
                frame.pack(fill="x")
                frame.columnconfigure(0, minsize=FILTER_LABEL_WIDTH)
                frame.columnconfigure(1, weight=1)
                self._build_search_row_label(
                    frame, entry["label"], row=0, pady=ADVANCED_ROW_PADY,
                    tooltip_text=entry["tooltip"])
                builder(frame)
                self._tooltip_row_controls(frame, entry["tooltip"])
                self._register_search_row_hover(frame, 0, last_column=1)
                self._advanced_filter_rows[key] = frame
        self._refresh_search_blur_widgets()

    def _toggle_advanced_filters(self, expand=None):
        """Show or hide the advanced panel without rebuilding it."""
        expanded = (not self._advanced_expanded) if expand is None else bool(expand)
        if expanded == self._advanced_expanded and expand is not None:
            return
        self._advanced_expanded = expanded
        if expanded:
            # Advanced is built before the actions row, so the anchor it packs
            # above may not exist yet. A missing anchor must not be the kind of
            # AttributeError that only a real window reveals.
            anchor = getattr(self, "_search_results_boundary", None)
            if anchor is None or not anchor.winfo_exists():
                anchor = getattr(self, "_search_actions_frame", None)
            if anchor is not None and anchor.winfo_exists():
                self._advanced_host.pack(fill="x", before=anchor)
            else:
                self._advanced_host.pack(fill="x")
        else:
            self._advanced_host.pack_forget()
        if self._advanced_btn is not None:
            self._advanced_btn.configure(
                text=(self.ADVANCED_EXPANDED_TEXT if expanded
                      else self.ADVANCED_COLLAPSED_TEXT))
        # Collapsing shortens the form, so the Results viewport has to follow
        # it back up rather than stay scrolled to where the taller panel was.
        self._reset_results_viewport()

    def _reset_advanced_filter_values(self):
        """Empty every advanced filter without taking any of them away."""
        for key in advanced_filter_keys():
            reset = getattr(self, f"_reset_filter_{key}", None)
            frame = self._advanced_filter_rows.get(key)
            if reset is None or frame is None:
                continue
            reset()
            # Printings owns a persistent shared controller/popup adapter.
            # Clearing its state must not destroy/rebuild that controller merely
            # because the row now lives inside Advanced.
            if key == "printings":
                continue
            for child in frame.winfo_children():
                if child.winfo_manager() != "grid":
                    continue
                try:
                    column = int(child.grid_info().get("column", -1))
                except (TypeError, ValueError):
                    continue
                # Column 0 is the label the row keeps; column 1 is the control
                # the builder is about to make again.
                if column == 1:
                    child.destroy()
            builder = getattr(self, f"_build_filter_{key}", None)
            if builder is not None:
                builder(frame)
                self._tooltip_row_controls(frame, filter_tooltip(key))
        self._refresh_search_blur_widgets()

    def _build_filter_produces(self, parent):
        self._build_produces_filter(parent, row=0)

    def _reset_filter_produces(self):
        for variable in self.produces_vars.values():
            variable.set(False)
        self.q_produces_mode.set("includes")

    def _build_filter_rules_text(self, parent):
        self._build_rules_text_filter(parent, row=0, advanced=True)
        for value in getattr(self, "_rules_text_shadow", []) or []:
            self.q_rules.add(value)
        pending = getattr(self, "_rules_pending_shadow", "")
        if pending:
            self.q_rules.entry.insert(0, pending)
        # The shadow is consumed by the rebuild it was recorded for.
        self._rules_text_shadow = []
        self._rules_pending_shadow = ""

    def _reset_filter_rules_text(self):
        # Removing a filter clears what it contributed, exactly like the
        # spinbox rows whose widgets simply cease to exist.
        self._rules_text_shadow = []
        self._rules_pending_shadow = ""
        self.q_rules = None
        self.q_rules_mode.set("all")

    def _build_filter_format(self, parent):
        self._format_btn = AppButton(
            parent, text=self.q_format.get() or "Any", role="search_picker",
            command=self._choose_format)
        self._format_btn.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)

    def _reset_filter_format(self):
        self.q_format.set("")
        self.q_format_status.set("playable")
        self._format_btn = None

    def _build_filter_rarity(self, parent):
        self._rarity_btn = AppButton(
            parent, text=self._picker_button_text(
                self._selected_rarities, "Any", "rarities"),
            role="search_picker", command=self._choose_rarities)
        self._rarity_btn.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)

    def _reset_filter_rarity(self):
        self._selected_rarities = set()
        self._rarity_btn = None

    MODE_ROW_CHOICES = (("Any", "any"), ("All", "all"), ("None", "none"))
    # A card has exactly one shape, so All could only ever find nothing.
    # Offering a mode its values cannot satisfy is worse than offering fewer.
    ANY_NONE_CHOICES = (("Any", "any"), ("None", "none"))

    def _build_mode_row(self, parent, variable, noun, meanings=None,
                        choices=None):
        """Build one compact, consistently titled matching-mode row.

        The primary filter row already establishes what is being matched.
        Keeping a single muted ``Match`` title and clustering Any / All / None
        prevents the secondary control from reading like three unrelated
        options spread across the full Search pane.
        """
        mode = ttk.Frame(parent)
        mode.pack(fill="x", pady=MODE_ROW_PADY)
        resolved_choices = tuple(choices or self.MODE_ROW_CHOICES)
        mode.columnconfigure(0, minsize=MATCH_MODE_LABEL_WIDTH)
        title = ttk.Label(mode, text=MATCH_MODE_LABEL, style="Muted.TLabel")
        title.grid(row=0, column=0, sticky="w")
        meanings = dict(meanings or {}) or {
            "any": (f"Any: a card only needs to match one selected {noun}. "
                    "Selecting additional choices can broaden this filter."),
            "all": (f"All: a card must match every selected {noun}. "
                    "Selecting additional choices narrows this filter."),
            "none": f"None: exclude cards that match any selected {noun}.",
        }
        for offset, (text, value) in enumerate(resolved_choices, start=1):
            radio = ttk.Radiobutton(
                mode, text=text, variable=variable, value=value,
                style="FormChoice.TRadiobutton",
                command=self._update_search_filter_summary)
            radio.grid(
                row=0, column=offset, sticky="w",
                padx=(MODE_CONTROL_GAP if offset == 1 else MATCH_MODE_CHOICE_GAP, 0))
            self._add_tooltip(radio, meanings[value], wraplength=390)
        return mode

    def _render_supertype_chips(self, *, force_placeholders=False):
        """Draw supertype chips when the filter is present.

        The trusted-catalog refresh runs whether or not the Supertypes filter
        has been added, so it records the vocabulary and defers drawing to
        whichever build owns the frame.
        """
        frame = getattr(self, "_property_chip_frame", None)
        if frame is None or not frame.winfo_exists():
            return
        self._property_chip_widgets = self._render_trusted_chips(
            frame, self._property_catalog, self.property_vars,
            columns=SUPERTYPE_COLUMNS,
            tooltip_key="supertypes",
            loading_placeholders=SUPERTYPE_LOADING_SLOTS,
            force_placeholders=force_placeholders,
            empty_label="No Supertypes in this scope")

    def _build_filter_supertypes(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)
        self._property_chip_frame = ttk.Frame(box)
        self._property_chip_frame.pack(fill="x")
        self._render_supertype_chips()
        self._build_mode_row(
            box, self.q_supertype_mode, "supertypes")

    def _reset_filter_supertypes(self):
        for variable in self.property_vars.values():
            variable.set(False)
        self.q_supertype_mode.set("any")
        self._property_chip_frame = None

    def _build_filter_mechanics(self, parent):
        self._keyword_btn = AppButton(
            parent, text=self._picker_button_text(
                self._selected_keywords, "Any", "mechanics",
                max_visible=10, single_line=True),
            role="search_picker", command=self._choose_keywords)
        self._keyword_btn.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)

    def _reset_filter_mechanics(self):
        self._selected_keywords = set()
        self.q_keyword_mode.set("any")
        self._keyword_btn = None

    def _build_filter_subtype(self, parent):
        self._subtype_btn = AppButton(
            parent, text=self._picker_button_text(
                self._selected_subtypes, "Any", "subtypes",
                max_visible=10, single_line=True),
            role="search_picker", command=self._choose_subtypes)
        self._subtype_btn.grid(row=0, column=1, sticky="ew", pady=ADVANCED_ROW_PADY)

    def _reset_filter_subtype(self):
        self._selected_subtypes = set()
        self.q_subtype_mode.set("any")
        self._subtype_btn = None

    def _build_filter_mana_value(self, parent):
        pair = self._numeric_pair(parent, "q_cmc", "mana_value")
        pair.grid(row=0, column=1, sticky="w", pady=ADVANCED_ROW_PADY)

    def _reset_filter_mana_value(self):
        self.q_cmc_min = None
        self.q_cmc_max = None

    def _build_filter_stats(self, parent, *, row=0):
        """Build Power and Toughness as two aligned primary-range rows."""
        for offset, (label_text, prefix) in enumerate((
                ("Power", "q_power"), ("Toughness", "q_toughness"))):
            self._build_search_row_label(
                parent, label_text, row=row + offset, pady=SEARCH_ROW_PADY,
                tooltip_key="stats")
            pair = self._numeric_pair(parent, prefix, "stats")
            pair.grid(
                row=row + offset, column=1, columnspan=3, sticky="w",
                pady=SEARCH_ROW_PADY)
            self._add_standard_filter_tooltip(pair, "stats")

    def _reset_filter_stats(self):
        # Power/Toughness is a standard row, so its widgets outlive a Clear:
        # empty them rather than dropping the handles the form still holds.
        for name in ("q_power_min", "q_power_max",
                     "q_toughness_min", "q_toughness_max"):
            self._set_search_entry_text(getattr(self, name, None), "")


    def _build_standard_stats_filter(self, form, *, row):
        """Power and Toughness share the same global numeric range rails."""
        self._build_filter_stats(form, row=row)

    def _build_printing_filter(self, parent, *, row=0, show_label=True):
        self._search_printings = SearchPrintingFilter(
            self, parent, row=row, show_label=show_label)

    def _build_filter_printings(self, parent):
        self._build_printing_filter(parent, row=0, show_label=False)

    def _reset_filter_printings(self):
        self._search_printings.clear()


    def _content_types_from_traits(self):
        """Which kinds of object the Search Scope covers.

        All four are independent, so turning Cards off and Tokens on searches
        tokens alone. Tokens, Emblems and Art Series stay off until asked for,
        which is what DATA-008 requires of Art Series in particular. Turning
        every one of them off would search nothing at all, so the last one
        standing falls back to Cards rather than emptying the Results table
        with no way to tell why.
        """
        selected = set(getattr(self, "_selected_traits", set()) or ())
        kinds = {
            kind for key, kind in CONTENT_TRAIT_KEYS.items() if key in selected}
        return kinds or {"card"}

    def _selected_content_types(self):
        return self._content_types_from_traits()

    def _render_trusted_chips(
            self, frame, values, variables, *, columns=3,
            tooltip_key=None, loading_placeholders=0, force_placeholders=False,
            empty_label=None):
        selected = {key for key, variable in variables.items() if bool(variable.get())}
        for child in frame.winfo_children():
            child.destroy()
        variables.clear()
        widgets = []
        for index, value in enumerate(values):
            variable = tk.BooleanVar(master=self, value=value in selected)
            variables[value] = variable
            # A real one-pixel shell is used as the visible chip border.  Tk's
            # Checkbutton highlight ring is not reliably painted on Windows.
            shell = tk.Frame(
                frame, bg=PALETTE["border"], bd=0, highlightthickness=0,
                takefocus=0)
            chip = self._filter_chip(shell, value, variable, anchor="center")
            chip._ui_chip_border_shell = shell
            chip.pack(fill="both", expand=True, padx=1, pady=1)
            chip._sync_chip_contrast()
            if tooltip_key:
                self._add_standard_filter_tooltip(chip, tooltip_key)
            if getattr(self, "_search_type_line_cold_start", False):
                chip.configure(state="disabled")
            widgets.append(chip)
            chip_row = index // columns
            chip_column = index % columns
            chip_rows = max(1, math.ceil(len(values) / columns))
            shell.grid(
                row=chip_row, column=chip_column, sticky="ew",
                padx=_chip_grid_padx(chip_column, columns),
                pady=_chip_grid_pady(chip_row, chip_rows))
            frame.columnconfigure(
                chip_column, weight=1, uniform="search-trusted-chip")
        if (not values and loading_placeholders and
                (getattr(self, "_search_type_line_cold_start", False) or
                 force_placeholders)):
            placeholder_rows = max(1, math.ceil(loading_placeholders / columns))
            for index in range(loading_placeholders):
                shell = tk.Frame(
                    frame, bg=PALETTE["border"], bd=0, highlightthickness=0,
                    takefocus=0)
                variable = tk.BooleanVar(master=self, value=False)
                chip = self._filter_chip(shell, " ", variable, anchor="center")
                chip._ui_chip_border_shell = shell
                chip._mtg_loading_placeholder = True
                chip.configure(state="disabled")
                chip.pack(fill="both", expand=True, padx=1, pady=1)
                chip_row = index // columns
                chip_column = index % columns
                shell.grid(
                    row=chip_row, column=chip_column, sticky="ew",
                    padx=_chip_grid_padx(chip_column, columns),
                    pady=_chip_grid_pady(chip_row, placeholder_rows))
                frame.columnconfigure(
                    chip_column, weight=1, uniform="search-trusted-chip")
                widgets.append(chip)
        elif (not values and empty_label
                and not getattr(self, "_search_type_line_cold_start", False)
                and not force_placeholders):
            # The taxonomy is authoritative and this scope genuinely has no such
            # values (e.g. Emblems have no supertype/card type, Art Series has
            # neither). Rather than leave an empty gap, show one disabled chip
            # carrying the same red ✕ unavailable convention used elsewhere, so
            # the row reads as intentionally-empty instead of broken.
            shell = tk.Frame(
                frame, bg=PALETTE["border"], bd=0, highlightthickness=0,
                takefocus=0)
            placeholder_var = tk.BooleanVar(master=self, value=False)
            chip = self._filter_chip(
                shell, f"✕ {empty_label}", placeholder_var, anchor="center")
            chip._ui_chip_border_shell = shell
            chip._mtg_empty_scope_placeholder = True
            chip.pack(fill="both", expand=True, padx=1, pady=1)
            try:
                chip.configure(
                    state="disabled", fg=PALETTE["bad"],
                    disabledforeground=PALETTE["bad"])
            except tk.TclError:
                pass
            shell.grid(
                row=0, column=0, columnspan=max(1, columns), sticky="ew",
                padx=_chip_grid_padx(0, 1), pady=_chip_grid_pady(0, 1))
            frame.columnconfigure(0, weight=1)
            widgets.append(chip)
        return tuple(widgets)

    def _layout_card_type_chips(self, _event=None):
        """Use responsive columns only after layout motion settles."""
        if getattr(self, "_window_in_motion", False):
            return
        frame = getattr(self, "_card_type_chip_frame", None)
        widgets = tuple(getattr(self, "_card_type_chip_widgets", ()))
        if frame is None or not widgets:
            return
        try:
            available_width = int(frame.winfo_width())
            if available_width <= 1:
                return
            desired = CARD_TYPE_MIN_COLUMNS
            required = _row_major_grid_required_width(widgets, CARD_TYPE_MAX_COLUMNS)
            current = getattr(self, "_card_type_chip_columns", CARD_TYPE_MIN_COLUMNS)
            margin = 8
            if current == CARD_TYPE_MAX_COLUMNS:
                if required - margin <= available_width:
                    desired = CARD_TYPE_MAX_COLUMNS
            elif required + margin <= available_width:
                desired = CARD_TYPE_MAX_COLUMNS
            if desired == getattr(self, "_card_type_chip_columns", None):
                return
            for index, widget in enumerate(widgets):
                if not widget.winfo_exists():
                    return
                layout_widget = _chip_layout_widget(widget)
                if not layout_widget.winfo_exists():
                    return
                # The single "No Card Types in this scope" chip spans the row so
                # it reads as an intentional placeholder, not one narrow chip.
                if getattr(widget, "_mtg_empty_scope_placeholder", False):
                    layout_widget.grid_configure(
                        row=0, column=0, columnspan=max(1, desired), sticky="ew",
                        padx=_chip_grid_padx(0, 1), pady=_chip_grid_pady(0, 1))
                    continue
                chip_row = index // desired
                chip_column = index % desired
                chip_rows = max(1, math.ceil(len(widgets) / desired))
                layout_widget.grid_configure(
                    row=chip_row, column=chip_column, sticky="ew", columnspan=1,
                    padx=_chip_grid_padx(chip_column, desired),
                    pady=_chip_grid_pady(chip_row, chip_rows))
            for column in range(CARD_TYPE_MAX_COLUMNS):
                frame.columnconfigure(
                    column, weight=(1 if column < desired else 0),
                    uniform=("search-card-type-chip" if column < desired else ""))
            self._card_type_chip_columns = desired
        except tk.TclError:
            return

    def _build_search_actions(self, parent):
        # A visual-only boundary separates filter construction above from the
        # result/action area below. It is deliberately just a separator in the
        # same pane: no new container, sash, or geometry lock is introduced.
        self._search_results_boundary = tk.Frame(
            parent, bg=PALETTE["accent2"], height=SEARCH_RESULTS_BOUNDARY_HEIGHT,
            bd=0, highlightthickness=0, takefocus=0)
        self._search_results_boundary.pack(fill="x", pady=(8, 6))

        # Search controls stay on the left; deck actions sit as a visually separate
        # group on the right so adding a selected result is always close at hand.
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(4, 5))
        # The advanced panel packs itself in above this frame, so it must be
        # reachable by name rather than by whatever happens to be last.
        self._search_actions_frame = btns

        # Pack the right-side actions first so Tk reserves their full natural
        # width before allocating the left group.  Their position is unchanged,
        # but Add Mainboard and Add Sideboard can no longer be the first controls
        # clipped when a pane is narrow or Windows DPI scaling is increased.
        deck_actions = ttk.Frame(btns)
        deck_actions.pack(side="right")
        AppButton(
            deck_actions, text="Add Mainboard", role="primary",
            command=lambda: self._add_to_deck("main")).pack(side="left")
        AppButton(
            deck_actions, text="Add Sideboard", role="standard",
            command=lambda: self._add_to_deck("side")).pack(
                side="left", padx=(3, 0))

        search_actions = ttk.Frame(btns)
        search_actions.pack(side="left")
        self._search_btn = AppButton(
            search_actions, text="Search", role="primary", width=6,
            command=self._do_search)
        self._search_btn.pack(side="left")
        clear_btn = AppButton(
            search_actions, text="Clear", role="standard", width=5,
            command=self._clear_search)
        clear_btn.pack(side="left", padx=(4, 0))


    def _build_results_table(self, parent):
        # Results are the main purpose of this pane, so they receive all of the
        # remaining vertical space. Columns are user-configurable per table.
        result_head = ttk.Frame(parent)
        result_head.pack(fill="x", pady=(0, 3))
        self.results_count_lbl = ttk.Label(
            result_head, text="RESULTS | 0 CARDS", style="Section.TLabel")
        self.results_count_lbl.pack(side="left")
        results_columns_btn = AppButton(
            result_head, text="Edit Columns", role="compact",
            command=lambda: self._toggle_column_popup("results", results_columns_btn))
        results_columns_btn.pack(side="right")
        AppButton(
            result_head, text="Clear Filters", role="compact",
            command=lambda: self._clear_table_filter("results")
        ).pack(side="right", padx=(0, 6))

        self._search_context_notice = ttk.Label(
            parent, text="", style="Muted.TLabel", justify="left", wraplength=620)
        self._search_context_notice.pack(fill="x", pady=(0, 3))
        self._search_context_notice.pack_forget()

        table = ttk.Frame(parent)
        self._results_table_frame = table
        table.pack(fill="both", expand=True, pady=(0, 8))
        ordinary = tuple(c for c in TABLE_COLUMN_ORDER
                         if c != "cost" and "results" in TABLE_COLUMNS[c]["views"])
        self.results_tv = ttk.Treeview(table, columns=ordinary,
                                       show="tree headings", height=16,
                                       selectmode="extended")
        self._setup_table_columns(self.results_tv, "results")
        self.results_tv.tag_configure("odd", background=PALETTE["stripe"])
        self.results_tv.tag_configure("even", background=PALETTE["surface"])
        vsb = ttk.Scrollbar(table, orient="vertical", command=self._result_yview,
                            style="Dark.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(table, orient="horizontal", command=self.results_tv.xview,
                            style="Dark.Horizontal.TScrollbar")
        self.results_tv.configure(xscrollcommand=hsb.set)
        self._install_result_tree_virtualization(vsb)
        self.results_tv.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.results_tv.bind("<<TreeviewSelect>>", self._on_result_select)
        self.results_tv.bind("<Double-1>", lambda e: self._add_to_deck("main"))
        self.results_tv.bind("<Button-3>", self._show_result_context_menu)
        self._bind_column_drag(self.results_tv, "results")


    def _on_name_autocomplete_selected(self, _event=None):
        self._search_name_batch = ()
        self._search_name_batch_display = ""
        self._do_search()

    def _on_name_filter_edited(self, _event=None):
        if (self._search_name_batch and
                self.q_name.get().strip() != self._search_name_batch_display):
            self._search_name_batch = ()
            self._search_name_batch_display = ""
        self._update_search_filter_summary()

    def _set_exact_name_batch(self, names):
        clean = []
        seen = set()
        for value in names or ():
            value = str(value or "").strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                clean.append(value)
        self._search_name_batch = tuple(clean)
        self._search_name_batch_display = " | ".join(clean)
        self.q_name.set(self._search_name_batch_display)

    def _effective_name_filters(self):
        value = self.q_name.get().strip()
        if (self._search_name_batch and
                value == self._search_name_batch_display):
            return "", tuple(self._search_name_batch)
        if self._search_name_batch:
            self._search_name_batch = ()
            self._search_name_batch_display = ""
        return value, ()

    def _clear_search(self):
        # Search Clear resets criteria and releases all highlighted source rows.
        # It deliberately leaves the comparison collection itself intact.
        # A Search clicked while trusted catalogs were loading is stale once the
        # user clears the form, so do not run it when that load later completes.
        self._pending_search_request = False
        clear_highlights = getattr(self, "_clear_source_highlights", None)
        if callable(clear_highlights):
            clear_highlights()
        # Search Clear is a complete reset of the Search workspace. Results
        # column filters are easy to forget because their editors live in the
        # table headings, so clear them together with the visible Search form.
        self._clear_table_filter("results")
        self._search_name_batch = ()
        self._search_name_batch_display = ""
        self.q_name.set("")
        # Every filter's own reset runs, so the state each owns is cleared
        # without naming it twice here. The rows themselves stay, and so does
        # whether Advanced is open: clearing a search is not the same as
        # abandoning the set of questions it was asking.
        self._reset_advanced_filter_values()
        # Standard rows are never rebuilt, so each empties itself in place.
        self._reset_filter_stats()
        self._rules_text_shadow = []
        self.q_rules_mode.set("all")
        self.q_card_type_mode.set("any")
        self.q_supertype_mode.set("any")
        self.q_subtype_mode.set("any")
        for variable in self.property_vars.values():
            variable.set(False)
        self._selected_subtypes = set()
        self._set_picker_text(self._subtype_btn, "Any")
        self.q_color_mode.set("within")
        self.q_color_scope.set("identity")
        self.q_produces_mode.set("includes")
        self.q_mana_feature_mode.set("any")
        self.q_special_property_mode.set("any")
        self.q_status_property_mode.set("any")
        self.q_layout_mode.set("any")
        for variable in self.card_type_vars.values():
            variable.set(False)
        for variable in self.color_vars.values():
            variable.set(False)
        for variable in self.produces_vars.values():
            variable.set(False)
        self.english_only.set(True)
        self._pending_catalog_filter_state = {
            "card_types": set(), "supertypes": set(), "format": "",
            "rarities": set(), "keywords": set(), "subtypes": set(),
        }
        # Printings is now an Advanced row and was already cleared by its
        # owning reset above.
        # Clearing can shorten what the rows display, so the Results viewport
        # would otherwise stay scrolled to wherever the taller panel had left
        # it. Return it to the first row along with the criteria.
        self._reset_results_viewport()
        self._clear_search_context_presentation()
        self._update_search_filter_summary()


    def _apply_cards_search_preset(self, cards):
        """Configure Search for every distinct selected deck-card name."""
        names = [
            card.get("name") for card in (cards or ())
            if isinstance(card, dict) and card.get("name")
        ]
        if not names:
            return False
        self._clear_search()
        self._set_exact_name_batch(names)
        self._search_printings.select_all_present_types_and_sets()
        self._update_search_filter_summary()
        return True

    def _on_content_filter_change(self):
        """Search Scope changed; rebuild the scoped vocabulary."""
        self._refresh_search_catalogs()
        self._update_search_filter_summary()


    def _picker_button_text(
            self, selected, empty_text, noun, *, max_visible=None, single_line=False):
        vals = sorted((str(value) for value in selected), key=str.casefold)
        if not vals:
            return empty_text
        visible = vals if max_visible is None else vals[:max_visible]
        if single_line:
            text = " · ".join(visible)
        else:
            lines = [
                " · ".join(visible[index:index + PICKER_SUMMARY_PER_LINE])
                for index in range(0, len(visible), PICKER_SUMMARY_PER_LINE)
            ]
            text = "\n".join(lines)
        if max_visible is not None and len(vals) > max_visible:
            text += f" · +{len(vals) - max_visible}"
        return text

    def _open_search_multi_picker(self, title, values, selected, apply_callback,
                                  mode_var=None, mode_default="any",
                                  mode_label=MATCH_MODE_LABEL,
                                  help_text="Type to narrow the list.",
                                  single_select=False, mode_choices=None,
                                  mode_command=None):
        """Open the reusable hidden-first, batch-rendered search picker."""
        return open_search_checklist(
            self, title=title, values=values, selected=selected,
            apply_callback=apply_callback, mode_var=mode_var,
            mode_default=mode_default, mode_label=mode_label,
            help_text=help_text, single_select=single_select,
            mode_choices=mode_choices, mode_command=mode_command)


    def _choose_subtypes(self):
        def apply(chosen):
            self._selected_subtypes = set(chosen)
            if self._subtype_btn is not None:
                self._subtype_btn.configure(text=self._picker_button_text(
                self._selected_subtypes, "Any", "subtypes",
            max_visible=10, single_line=True))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Subtypes",
            self._contextual_subtype_values(),
            self._selected_subtypes, apply,
            mode_var=self.q_subtype_mode,
            mode_label=MATCH_MODE_LABEL,
            help_text=("Choose one or more subtypes. Counts show matches under the other "
                       "current filters; available matches appear first."))


    def _choose_keywords(self):
        def apply(chosen):
            self._selected_keywords = set(chosen)
            if self._keyword_btn is not None:
                self._keyword_btn.configure(text=self._picker_button_text(
                    self._selected_keywords, "Any", "mechanics",
                    max_visible=10, single_line=True))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Mechanics",
            self._contextual_picker_values(
                self._keyword_catalog, "keyword_counts", self._selected_keywords),
            self._selected_keywords, apply,
            mode_var=self.q_keyword_mode,
            mode_label=MATCH_MODE_LABEL,
            help_text=("Choose one or more mechanics. Counts show matches under the other "
                       "current filters; available matches appear first."))


    def _contextual_picker_values(self, catalog, count_attribute, selected=()):
        """Selected first, then current matches, while retaining the full catalog."""
        selected = {str(value) for value in (selected or ())}
        snapshot = getattr(self, "_context_snapshot", None)
        counts = (getattr(snapshot, count_attribute, None) if snapshot is not None else None)
        normalized = []
        for item in catalog or ():
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                key, display = str(item[0]), str(item[1])
            else:
                key = display = str(item)
            count = int((counts or {}).get(key, 0))
            contextual_display = f"{display} · {count:,}" if counts is not None else display
            normalized.append((key, contextual_display, display, count))
        if counts is None:
            return [(key, display) for key, _shown, display, _count in normalized]
        normalized.sort(key=lambda item: (
            0 if item[0] in selected else 1,
            0 if item[3] > 0 else 1,
            -item[3], item[2].casefold()))
        return [
            (key, shown, {"zero_count": count <= 0})
            for key, shown, _display, count in normalized
        ]

    def _contextual_subtype_values(self):
        values = self._contextual_picker_values(
            self._subtype_catalog, "subtype_counts", self._selected_subtypes)
        selected_types = {
            str(value).casefold() for value, variable in self.card_type_vars.items()
            if variable.get()}
        if not selected_types:
            return values
        positions = {item[0]: index for index, item in enumerate(values)}
        return sorted(values, key=lambda item: (
            0 if item[0] in self._selected_subtypes else 1,
            0 if str(self._subtype_category_by_value.get(item[0], "")).casefold()
                 in selected_types else 1,
            positions[item[0]],
        ))

    def _clear_search_context_presentation(self):
        pending = getattr(self, "_context_debounce_after", None)
        self._context_debounce_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        self._context_snapshot = None
        self._context_requested_criteria = None
        self._context_applied_criteria = None
        controller = getattr(self, "search_context_controller", None)
        if controller is not None:
            controller.invalidate()
        notice = getattr(self, "_search_context_notice", None)
        if notice is not None:
            try:
                notice.pack_forget()
                notice.configure(text="")
            except tk.TclError:
                pass

    def _context_set_tooltip(self, widget, suffix=""):
        """Append live context to an existing tooltip without changing geometry."""
        if widget is None:
            return
        tip = getattr(widget, "_mtg_tooltip", None)
        if tip is None:
            return
        base = getattr(widget, "_context_tooltip_base", None)
        if base is None:
            base = str(tip.text or "")
            try:
                widget._context_tooltip_base = base
            except AttributeError:
                pass
        tip.text = base if not suffix else f"{base}\n\n{suffix}"

    @staticmethod
    def _context_choice_text(count, selected, noun="choice"):
        """Describe one predictive choice without overstating union-mode totals."""
        count = max(0, int(count or 0))
        if count > 0:
            card_word = "card" if count == 1 else "cards"
            return f"{count:,} {card_word} match this {noun} with the other current filters."
        if selected:
            return (
                f"No cards matching the other current filters also match this selected {noun}. "
                "It stays available so you can deselect it.")
        return (
            f"Not available with the current filters. No cards match this {noun}.")

    def _apply_context_chip_state(self, widgets, catalog, counts, variables, *, noun="choice"):
        """Mark zero-result trusted chips unavailable without trapping selection."""
        for value, widget in zip(catalog or (), widgets or ()):
            try:
                count = int((counts or {}).get(value, 0))
                available = count > 0
                selected = bool(variables.get(value) and variables[value].get())
                availability = getattr(widget, "set_context_availability", None)
                if callable(availability):
                    availability(available, allow_selected_clear=True)
                else:
                    widget.configure(
                        state=("normal" if available or selected else "disabled"),
                        fg=(PALETTE["text"] if available else PALETTE["bad"]),
                        disabledforeground=PALETTE["bad"],
                    )
                if getattr(widget, "_mtg_tooltip", None) is None:
                    self._add_tooltip(
                        widget,
                        self._context_choice_text(count, selected, noun),
                        wraplength=320)
                    try:
                        widget._context_tooltip_base = ""
                    except AttributeError:
                        pass
                else:
                    self._context_set_tooltip(
                        widget,
                        self._context_choice_text(count, selected, noun))
            except (tk.TclError, AttributeError):
                pass

    @staticmethod
    def _context_range_text(bounds, applicable, noun):
        if not applicable or not bounds:
            return (
                f"Not available with the current filters. No matching cards have numeric {noun}.")
        low, high = bounds
        def shown(value):
            number = float(value)
            return str(int(number)) if number.is_integer() else f"{number:g}"
        card_word = "card" if int(applicable) == 1 else "cards"
        return (
            f"With the other current filters, {int(applicable):,} {card_word} have numeric {noun}, "
            f"ranging from {shown(low)} to {shown(high)}.")

    @staticmethod
    def _context_widget_has_value(widget):
        if widget is None:
            return False
        try:
            return bool(str(widget.get()).strip())
        except (tk.TclError, AttributeError):
            return False

    def _set_context_field_pair_availability(self, widgets, available):
        """Disable an empty inapplicable range while keeping conflicts clearable."""
        widgets = tuple(widget for widget in widgets if widget is not None)
        selected = any(self._context_widget_has_value(widget) for widget in widgets)
        enabled = bool(available or selected)
        for widget in widgets:
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except (tk.TclError, AttributeError):
                pass
        return enabled

    @staticmethod
    def _set_context_check_availability(widget, variable, available):
        """Disable one zero-result choice unless it is selected and must be clearable."""
        if widget is None:
            return False
        selected = bool(variable is not None and variable.get())
        enabled = bool(available or selected)
        try:
            widget._mtg_context_available = bool(available)
            widget.state(["!disabled"] if enabled else ["disabled"])
        except (tk.TclError, AttributeError):
            pass
        return enabled

    def _apply_live_context_presentation(self):
        snapshot = getattr(self, "_context_snapshot", None)
        criteria = getattr(self, "_context_applied_criteria", None)
        if snapshot is None or criteria is None:
            return

        self._refresh_split_trait_button_texts()
        self._apply_context_chip_state(
            getattr(self, "_card_type_chip_widgets", ()),
            getattr(self, "_card_type_catalog", ()), snapshot.card_type_counts,
            self.card_type_vars, noun="card type")
        self._apply_context_chip_state(
            getattr(self, "_property_chip_widgets", ()),
            getattr(self, "_property_catalog", ()), snapshot.supertype_counts,
            self.property_vars, noun="supertype")

        for key, widget in getattr(self, "_color_checks", {}).items():
            count = int((snapshot.color_counts or {}).get(key, 0))
            self._set_context_check_availability(
                widget, self.color_vars.get(key), count > 0)
            selected = bool(self.color_vars.get(key) and self.color_vars[key].get())
            self._context_set_tooltip(
                widget, self._context_choice_text(count, selected, "Mana Color choice"))
        for key, widget in getattr(self, "_produces_checks", {}).items():
            count = int((snapshot.produces_counts or {}).get(key, 0))
            self._set_context_check_availability(
                widget, self.produces_vars.get(key), count > 0)
            selected = bool(self.produces_vars.get(key) and self.produces_vars[key].get())
            self._context_set_tooltip(
                widget, self._context_choice_text(count, selected, "Mana Produced choice"))
        for key, widget in getattr(self, "_pip_checks", {}).items():
            count = int((snapshot.pip_counts or {}).get(key, 0))
            self._set_context_check_availability(
                widget, self.pip_vars.get(key), count > 0)
            selected = bool(self.pip_vars.get(key) and self.pip_vars[key].get())
            self._context_set_tooltip(
                widget, self._context_choice_text(count, selected, "mana-symbol requirement"))
        # Colorless has an additional semantic exclusion: it cannot coexist
        # with an actual card color. Reapply that rule after contextual state
        # so a positive predictive count cannot re-enable an impossible choice.
        self._sync_colorless_availability(update_summary=False)

        numeric_widgets = {
            "cmc": (getattr(self, "q_cmc_min", None), getattr(self, "q_cmc_max", None), "mana value"),
            "power": (getattr(self, "q_power_min", None), getattr(self, "q_power_max", None), "power"),
            "toughness": (getattr(self, "q_toughness_min", None), getattr(self, "q_toughness_max", None), "toughness"),
            "loyalty": (getattr(self, "q_loyalty_min", None), getattr(self, "q_loyalty_max", None), "loyalty"),
            "defense": (getattr(self, "q_defense_min", None), getattr(self, "q_defense_max", None), "defense"),
        }
        for key, (low_widget, high_widget, noun) in numeric_widgets.items():
            applicable = int((snapshot.numeric_applicability or {}).get(key, 0))
            suffix = self._context_range_text(
                (snapshot.numeric_ranges or {}).get(key),
                applicable, noun)
            self._context_set_tooltip(low_widget, suffix)
            self._context_set_tooltip(high_widget, suffix)
            if key in {"cmc", "power", "toughness", "loyalty", "defense"}:
                self._set_context_field_pair_availability(
                    (low_widget, high_widget), applicable > 0)

        release_suffix = (
            f"With the other current filters, qualifying release years range from "
            f"{snapshot.release_years[0]} to {snapshot.release_years[-1]}."
            if snapshot.release_years else
            "Not available with the current filters. No matching printings have a release year.")
        self._context_set_tooltip(getattr(self, "q_released_min", None), release_suffix)
        self._context_set_tooltip(getattr(self, "q_released_max", None), release_suffix)
        self._set_context_field_pair_availability(
            (getattr(self, "q_released_min", None),
             getattr(self, "q_released_max", None)),
            bool(snapshot.release_years))

        printings = getattr(self, "_search_printings", None)
        if printings is not None and hasattr(printings, "apply_context_snapshot"):
            printings.apply_context_snapshot(snapshot)

        notice = getattr(self, "_search_context_notice", None)
        if notice is None:
            return
        text = ""
        if snapshot.result_count == 0:
            parts = []
            for label, count in tuple(snapshot.suggestions or ())[:2]:
                parts.append(f"remove {label} -> {count:,}")
            for label, mode, count in tuple(snapshot.mode_suggestions or ())[:1]:
                parts.append(f"{label} {mode} -> {count:,}")
            text = "Current filters match 0 cards."
            if parts:
                text += " Try: " + " · ".join(parts) + "."
        elif (getattr(self, "_active_search_signature", None)
              != criteria.signature()):
            text = f"Current filters match {snapshot.result_count:,} cards. Search to update Results."

        if text:
            notice.configure(text=text)
            table = getattr(self, "_results_table_frame", None)
            if table is not None and table.winfo_exists():
                notice.pack(fill="x", pady=(0, 3), before=table)
            else:
                notice.pack(fill="x", pady=(0, 3))
        else:
            notice.pack_forget()

    def _request_search_context(self, criteria):
        controller = getattr(self, "search_context_controller", None)
        if controller is None:
            return
        self._context_requested_criteria = criteria
        sets = tuple(getattr(getattr(self, "_search_printings", None), "_eligible_sets", ()) or ())
        set_types = tuple(sorted(
            getattr(getattr(self, "_search_printings", None), "_present_set_types", ()) or (),
            key=str.casefold))
        controller.request(
            criteria, card_types=self._card_type_catalog,
            supertypes=self._property_catalog, subtypes=self._subtype_catalog,
            keywords=self._keyword_catalog, layouts=self._layout_catalog,
            rarities=self._rarity_catalog, formats=self._format_catalog_for_status(),
            set_types=set_types, sets=sets)
        if self._context_poll_after is None:
            self._context_poll_after = self.after(40, self._poll_search_context)

    def _poll_search_context(self):
        self._context_poll_after = None
        controller = getattr(self, "search_context_controller", None)
        if controller is None:
            return
        event = controller.poll_latest()
        if event is None:
            if controller.running:
                self._context_poll_after = self.after(40, self._poll_search_context)
            return
        requested = getattr(self, "_context_requested_criteria", None)
        if requested is None or event.signature != requested.signature():
            if controller.running:
                self._context_poll_after = self.after(40, self._poll_search_context)
            return
        if event.kind == "error":
            log.warning("Search context analysis failed: %s", event.payload)
            return
        self._context_snapshot = event.payload
        self._context_applied_criteria = requested
        self._apply_live_context_presentation()
        log.debug(
            "Live Search context prepared for %s rows in %.3f seconds",
            event.payload.result_count, float(event.elapsed or 0.0))
        if controller.running:
            self._context_poll_after = self.after(40, self._poll_search_context)

    def _format_catalog_for_status(self, status=None):
        """Formats that can actually be legal, banned or restricted somewhere.

        Only a handful of formats restrict anything, so offering all of them
        under Restricted was offering a guaranteed-empty search. Falls back to
        the playable list whenever the scoped snapshot has not arrived yet.
        """
        status = str(status or self.q_format_status.get() or "playable").casefold()
        grouped = getattr(self, "_format_catalog_by_status", None) or {}
        return list(grouped.get(status) or self._format_catalog)

    def _rescope_format_choices(self, status):
        """Supply the picker a new value list when its Legality changes."""
        values = self._format_catalog_for_status(status)
        current = self.q_format.get().strip()
        snapshot = getattr(self, "_context_snapshot", None)
        applied = getattr(self, "_context_applied_criteria", None)
        if applied is None or str(applied.fmt_status) != str(status):
            snapshot = None
        counts = (snapshot.format_counts if snapshot is not None else {}) or {}
        shown = []
        for fmt in values:
            label = format_display_name(fmt)
            if snapshot is not None:
                label = f"{label} · {int(counts.get(fmt, 0)):,}"
            shown.append((
                fmt, label,
                {"zero_count": (snapshot is not None and
                                 int(counts.get(fmt, 0)) <= 0)},
            ))
        return ([("", "Any")] + shown,
                {current} if current in values else {""})

    def _set_format_filter(self, value):
        value = str(value or "").strip()
        if value not in self._format_catalog:
            value = ""
        self.q_format.set(value)
        self._set_picker_text(
            self._format_btn, format_display_name(value) or "Any")


    def _choose_format(self):
        """Single-select format picker using the same filter-window language."""
        current = self.q_format.get().strip()
        # The empty value is the explicit Any radio choice. Keep it selected so
        # reopening the picker shows the true mutually-exclusive Format state.
        selected = {current}

        def apply(chosen):
            # The explicit Any radio (empty key) is mutually exclusive with every
            # specific format. Defensive normalization keeps that invariant even
            # if a stale/restored caller ever supplies more than one value.
            normalized = {str(value or "").strip() for value in (chosen or ())}
            value = "" if "" in normalized else next(iter(normalized), "")
            # A format the chosen legality cannot produce is no longer offered,
            # so a selection left over from another legality is dropped rather
            # than kept as a silently empty search.
            if value and value not in self._format_catalog_for_status():
                value = ""
            self._set_format_filter(value)
            self._update_search_filter_summary()

        snapshot = getattr(self, "_context_snapshot", None)
        counts = (snapshot.format_counts if snapshot is not None else {}) or {}
        format_values = [("", "Any")] + [
            (
                fmt,
                (f"{format_display_name(fmt)} · {int(counts.get(fmt, 0)):,}"
                 if snapshot is not None else format_display_name(fmt)),
                {"zero_count": (snapshot is not None and
                                 int(counts.get(fmt, 0)) <= 0)},
            )
            for fmt in self._format_catalog_for_status()
        ]
        self._open_search_multi_picker(
            "Choose Format",
            format_values,
            selected,
            apply,
            help_text=(
                "Choose a format and the legality it must have. "
                "Only formats available for that legality are shown."),
            single_select=True,
            mode_var=self.q_format_status,
            mode_label="Legality",
            mode_choices=FORMAT_STATUS_CHOICES,
            mode_command=self._rescope_format_choices)

    def _choose_rarities(self):
        def apply(chosen):
            self._selected_rarities = set(chosen)
            labels = {r.capitalize() for r in chosen}
            if self._rarity_btn is not None:
                self._rarity_btn.configure(text=self._picker_button_text(
                labels, "Any", "rarities"))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Rarities",
            self._contextual_picker_values(
                [(r, r.replace("_", " ").capitalize()) for r in self._rarity_catalog],
                "rarity_counts", self._selected_rarities),
            self._selected_rarities, apply,
            help_text=("Choose one or more rarities. Counts show qualifying matches under the "
                       "other current filters; available matches appear first."))

    def _capture_catalog_filter_state(self):
        return {
            "card_types": {
                value for value, variable in self.card_type_vars.items() if variable.get()},
            "supertypes": {
                value for value, variable in self.property_vars.items() if variable.get()},
            "format": self.q_format.get().strip(),
            "rarities": set(self._selected_rarities),
            "keywords": set(self._selected_keywords),
            "subtypes": set(self._selected_subtypes),
        }

    def _set_search_catalog_controls_enabled(self, enabled):
        """Keep trusted-filter geometry stable while a cold scope loads."""
        card_type_state = (
            "normal" if enabled and getattr(
                self, "_card_type_authority_available", False) else "disabled")
        supertype_state = (
            "normal" if enabled and getattr(
                self, "_supertype_authority_available", False) else "disabled")
        for widget in tuple(getattr(self, "_card_type_chip_widgets", ())):
            try:
                if getattr(widget, "_mtg_empty_scope_placeholder", False):
                    continue
                if enabled and getattr(widget, "_mtg_loading_placeholder", False):
                    continue
                widget.configure(state=card_type_state)
            except (tk.TclError, AttributeError):
                pass
        for widget in tuple(getattr(self, "_property_chip_widgets", ())):
            try:
                if getattr(widget, "_mtg_empty_scope_placeholder", False):
                    continue
                if enabled and getattr(widget, "_mtg_loading_placeholder", False):
                    continue
                widget.configure(state=supertype_state)
            except (tk.TclError, AttributeError):
                pass
        for name in ("_format_btn", "_rarity_btn", "_keyword_btn", "_subtype_btn"):
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except (tk.TclError, AttributeError):
                try:
                    widget.configure(state=state)
                except Exception:
                    pass

    def _mark_search_catalogs_loading(self, *, full_scope):
        self._search_catalog_loading = True
        if self._pending_catalog_filter_state is None:
            self._pending_catalog_filter_state = self._capture_catalog_filter_state()
        if not full_scope:
            self._search_printings.begin_sets_loading()
            return
        # Preserve the last complete trusted controls while the next scope is
        # prepared. Destroying/recreating the controls into a visible "Loading"
        # state caused a layout collapse/flash even though discovery was async.
        self._set_search_catalog_controls_enabled(False)
        self._search_printings.begin_scope_loading()

    def _refresh_search_catalogs(self, selected_set_types=None):
        """Request trusted vocabulary for the current scope without blocking Tk."""
        content = tuple(sorted(self._selected_content_types()))
        printing_filter = getattr(self, "_search_printings", None)
        paper_only = bool(printing_filter.paper_only.get()) if printing_filter else True
        if selected_set_types is None:
            selected_set_types = (
                printing_filter.selected_set_types() if printing_filter else ())
        # Platform is part of the scope, not a detail of it. Requesting without
        # it meant the snapshot that arrived after a Paper/Arena/MTGO toggle
        # described the wrong platform and overwrote the set list the toggle
        # had just produced.
        games = printing_filter.selected_games() if printing_filter else ()
        base_scope = (content, paper_only, tuple(games))
        start = self.search_catalog_controller.request(
            content, paper_only, selected_set_types, games)
        if start.kind == "cached":
            self._apply_search_catalog_snapshot(start.payload)
            return
        self._mark_search_catalogs_loading(
            full_scope=(base_scope != self._search_catalog_scope))
        if self._search_catalog_poll_after is not None:
            try:
                self.after_cancel(self._search_catalog_poll_after)
            except tk.TclError:
                pass
        self._search_catalog_poll_after = self.after(
            18, self._poll_search_catalogs)

    def _poll_search_catalogs(self):
        self._search_catalog_poll_after = None
        event = self.search_catalog_controller.poll_latest()
        if event is None:
            self._search_catalog_poll_after = self.after(
                18, self._poll_search_catalogs)
            return
        if event.kind == "error":
            log.error("Trusted Search taxonomy failed: %s", event.payload)
            self._search_catalog_loading = False
            self._pending_catalog_filter_state = None
            self._pending_search_request = False
            self._set_search_catalog_controls_enabled(True)
            printing_filter = getattr(self, "_search_printings", None)
            if printing_filter is not None:
                printing_filter.cancel_loading()
            self._update_search_filter_summary()
            # Never leave the Results header stuck in a transient loading state.
            # Preserve the last completed logical-result count when one exists.
            set_count = getattr(self, "_set_result_count", None)
            if callable(set_count):
                set_count()
            else:
                self.results_count_lbl.configure(text="RESULTS | Trusted filters unavailable")
            self._status("Trusted Search filters could not be loaded for this scope.")
            return
        self._apply_search_catalog_snapshot(event.payload)

    def _apply_search_catalog_snapshot(self, snapshot):
        """Atomically publish one generation-checked trusted catalog snapshot on Tk."""
        pending = self._pending_catalog_filter_state or self._capture_catalog_filter_state()
        card_type_status = snapshot.card_type_status
        card_type_authority_available = bool(
            isinstance(card_type_status, (tuple, list))
            and card_type_status and card_type_status[0])
        supertype_status = snapshot.supertype_status
        supertype_authority_available = bool(
            isinstance(supertype_status, (tuple, list))
            and supertype_status and supertype_status[0])

        # Inline Type Line prose is intentionally forbidden. During database
        # startup an accepted snapshot can temporarily report a taxonomy as
        # unavailable; preserve the last trusted labels and keep them disabled
        # instead of replacing the chip row with a status sentence. A true
        # cold start uses disabled blank chip shells until authority arrives.
        if card_type_authority_available:
            self._card_type_catalog = list(snapshot.card_types)
        if supertype_authority_available:
            self._property_catalog = list(snapshot.supertypes)
        self._card_type_authority_available = card_type_authority_available
        self._supertype_authority_available = supertype_authority_available
        self._search_type_line_cold_start = False
        preferences = getattr(self, "_ui_preferences_repository", None)
        if preferences is not None and (
                card_type_authority_available or supertype_authority_available):
            try:
                preferences.save_search_type_line_catalogs(
                    self._card_type_catalog, self._property_catalog)
            except OSError:
                log.debug("Could not save Type Line warm-start catalogs", exc_info=True)

        self._card_type_chip_widgets = ()
        self._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
        self._card_type_chip_widgets = self._render_trusted_chips(
            self._card_type_chip_frame, self._card_type_catalog, self.card_type_vars,
            columns=CARD_TYPE_MIN_COLUMNS, tooltip_key="card_type",
            loading_placeholders=CARD_TYPE_LOADING_SLOTS,
            force_placeholders=not card_type_authority_available,
            empty_label="No Card Types in this scope")
        for value, variable in self.card_type_vars.items():
            variable.set(value in pending["card_types"])
        self._layout_card_type_chips()

        self._render_supertype_chips(
            force_placeholders=not supertype_authority_available)
        for value, variable in self.property_vars.items():
            variable.set(value in pending["supertypes"])

        self._format_catalog_by_status = {
            state: list(values) for state, values
            in (snapshot.formats_by_status or {}).items()
        }
        self._format_catalog = list(
            self._format_catalog_by_status.get("playable") or snapshot.formats)
        self._set_format_filter(
            pending["format"] if pending["format"] in self._format_catalog else "")
        self._layout_catalog = [
            (str(value), int(count)) for value, count in snapshot.layouts]
        self._layout_duplicates = set(snapshot.equivalent_layouts or ())
        self._release_year_catalog = [str(value) for value in snapshot.release_years]
        for widget in (getattr(self, "q_released_min", None),
                       getattr(self, "q_released_max", None)):
            if widget is not None:
                current = widget.get().strip()
                widget.configure(values=[""] + self._release_year_catalog)
                if current and current in self._release_year_catalog:
                    widget.set(current)
        valid_layouts = {value for value, _count in self._layout_catalog}
        self._selected_layouts = {
            value for value in getattr(self, "_selected_layouts", set()) or ()
            if value in valid_layouts}
        self._set_picker_text(
            getattr(self, "_card_form_btn", None),
            self._picker_button_text(
                {self._layout_display_name(value)
                 for value in self._selected_layouts},
                "Any", "forms", max_visible=10, single_line=True))
        self._rarity_catalog = list(snapshot.rarities)
        self._selected_rarities = set(pending["rarities"]).intersection(
            self._rarity_catalog)
        self._set_picker_text(self._rarity_btn, self._picker_button_text(
            {value.capitalize() for value in self._selected_rarities},
            "Any", "rarities"))

        self._keyword_catalog = [
            (value, f"{category} · {value}") for value, category in snapshot.keywords
        ]
        valid_keywords = {value for value, _display in self._keyword_catalog}
        self._selected_keywords = set(pending["keywords"]).intersection(valid_keywords)
        self._set_picker_text(self._keyword_btn, self._picker_button_text(
            self._selected_keywords, "Any", "mechanics",
            max_visible=10, single_line=True))

        self._subtype_category_by_value = {
            str(value): str(category) for value, category in snapshot.subtypes}
        self._subtype_catalog = [
            (value, f"{category} · {value}") for value, category in snapshot.subtypes
        ]
        valid_subtypes = {value for value, _display in self._subtype_catalog}
        self._selected_subtypes = set(pending["subtypes"]).intersection(valid_subtypes)
        self._set_picker_text(self._subtype_btn, self._picker_button_text(
            self._selected_subtypes, "Any", "subtypes",
            max_visible=10, single_line=True))

        actual_set_types = self._search_printings.apply_snapshot(snapshot)
        self._search_catalog_scope = (
            snapshot.content_types, snapshot.paper_only, tuple(snapshot.games))
        if set(actual_set_types) != set(snapshot.selected_set_types):
            # Invalid restored/old set types were pruned; resolve Exact Sets for
            # the now-authoritative selection without blocking the UI.
            self._refresh_search_catalogs(selected_set_types=actual_set_types)
            return

        self._pending_catalog_filter_state = None
        self._search_catalog_loading = False
        self._set_search_catalog_controls_enabled(True)
        self._update_search_filter_summary()
        unavailable = []
        if not card_type_authority_available:
            unavailable.append(
                "Scryfall Card Type taxonomy is unavailable; use Database > Update Database to retry.")
        if not supertype_authority_available:
            unavailable.append(
                "Official Wizards Supertype taxonomy is unavailable; use Database > Update Database to retry.")
        if unavailable:
            self._status(" ".join(unavailable))
        self._resume_pending_search_request()

    def _resume_pending_search_request(self):
        """Run one queued Search once trusted catalogs and any prior query are idle."""
        if not self._pending_search_request:
            return False
        if self._search_catalog_loading or self.search_controller.running:
            return False
        self._pending_search_request = False
        # The queued request may resolve as "unchanged" and avoid a new DB query.
        # Restore the last completed count before dispatch so that optimization can
        # never leave the transient trusted-filter message in the Results header.
        set_count = getattr(self, "_set_result_count", None)
        if callable(set_count):
            set_count()
        self._do_search()
        return True

    def _update_search_filter_summary(self):
        """Debounce a live draft-facet refresh after any existing filter changes.

        Results remain manual: this schedules only Tk-free context analysis. A
        later filter change invalidates the visible snapshot immediately, and
        the latest-wins context worker cancels stale SQLite work.
        """
        self._context_snapshot = None
        self._context_applied_criteria = None
        pending = getattr(self, "_context_debounce_after", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        try:
            self._context_debounce_after = self.after(200, self._prepare_live_search_context)
        except tk.TclError:
            self._context_debounce_after = None
        return None

    def _prepare_live_search_context(self):
        self._context_debounce_after = None
        if getattr(self, "_search_catalog_loading", False):
            return
        repository = getattr(self, "search_repository", None)
        if repository is None or not repository.has_cards():
            return
        try:
            criteria = self._capture_search_criteria(commit_rules=False)
        except (ValueError, tk.TclError):
            # Partial numeric edits such as '-' are allowed while typing. They
            # simply have no context snapshot until the field becomes valid.
            return
        self._request_search_context(criteria)


    @staticmethod
    def _search_entry_text(widget):
        try:
            return widget.get().strip()
        except (tk.TclError, AttributeError):
            return ""

    def _capture_search_workspace_state(self):
        """Return complete serializable Search state, including cold catalogs."""
        selected_result_id = None
        try:
            card = self._selected_result()
            if card:
                selected_result_id = card.get("id")
        except Exception:
            pass

        # A cold async taxonomy request intentionally clears unavailable chip
        # widgets. Preserve the staged authoritative selections instead of
        # serializing those temporary empty controls during autosave/close.
        pending = (self._pending_catalog_filter_state
                   if self._search_catalog_loading else None) or {}
        card_types = sorted(pending.get("card_types", {
            key for key, variable in self.card_type_vars.items() if variable.get()}))
        supertypes = sorted(pending.get("supertypes", {
            key for key, variable in self.property_vars.items() if variable.get()}))
        subtypes = sorted(pending.get("subtypes", self._selected_subtypes))
        keywords = sorted(pending.get("keywords", self._selected_keywords))
        rarities = sorted(pending.get("rarities", self._selected_rarities))
        format_value = str(pending.get("format", self.q_format.get()) or "")

        pending_types = getattr(self._search_printings, "_pending_restore_types", None)
        pending_codes = getattr(self._search_printings, "_pending_restore_codes", None)
        set_types = sorted(
            pending_types if pending_types is not None
            else self._search_printings.selected_set_types())
        set_codes = sorted(
            pending_codes if pending_codes is not None
            else self._search_printings.selected_set_codes())

        return {
            "name": self.q_name.get().strip(),
            "exact_names": list(self._search_name_batch),
            "rules": self._rules_text_values(commit_pending=False),
            "rules_pending": self._rules_pending_text(),
            "rules_mode": self.q_rules_mode.get(),
            "card_type_mode": self.q_card_type_mode.get(),
            "supertype_mode": self.q_supertype_mode.get(),
            "subtype_mode": self.q_subtype_mode.get(),
            "keyword_mode": self.q_keyword_mode.get(),
            "color_mode": self.q_color_mode.get(),
            "color_scope": self.q_color_scope.get(),
            "produces_mode": self.q_produces_mode.get(),
            "pip_mode": self.q_pip_mode.get(),
            "mana_feature_mode": self.q_mana_feature_mode.get(),
            "special_property_mode": self.q_special_property_mode.get(),
            "status_property_mode": self.q_status_property_mode.get(),
            "format_status": self.q_format_status.get(),
            "advanced_expanded": bool(getattr(self, "_advanced_expanded", False)),
            "advanced_values": self._capture_advanced_filter_values(),
            # Keep the legacy key content-only so older builds do not mistake
            # new independent property groups for one global OR/AND bucket.
            "traits": sorted(
                getattr(self, "_selected_traits", set()) or ()),
            "mana_features": sorted(self._selected_mana_features),
            "special_properties": sorted(self._selected_special_properties),
            "status_properties": sorted(self._selected_status_properties),
            "layouts": sorted(getattr(self, "_selected_layouts", set()) or ()),
            "layout_mode": self.q_layout_mode.get(),
            "pips": sorted(
                value for value, variable in self.pip_vars.items()
                if variable.get()),
            "card_types": card_types,
            "supertypes": supertypes,
            "colors": [key for key, variable in self.color_vars.items() if variable.get()],
            "produces": [key for key, variable in self.produces_vars.items()
                         if variable.get()],
            "subtypes": subtypes,
            "keywords": keywords,
            "rarities": rarities,
            "cmc_min": self._search_entry_text(self.q_cmc_min),
            "cmc_max": self._search_entry_text(self.q_cmc_max),
            "power_min": self._search_entry_text(self.q_power_min),
            "power_max": self._search_entry_text(self.q_power_max),
            "toughness_min": self._search_entry_text(self.q_toughness_min),
            "toughness_max": self._search_entry_text(self.q_toughness_max),
            "format": format_value,
            "english_only": bool(self.english_only.get()),
            "content": sorted(self._selected_content_types()),
            # Paper/Arena/MTGO is the scope the paper flag is derived from, so
            # saving only the flag reopened an Arena session on paper.
            "games": list(self._search_printings.selected_games()),
            "paper_only": bool(self._search_printings.paper_only.get()),
            "set_types": set_types,
            "set_codes": set_codes,
            "result_sort": [self._sort_col, bool(self._sort_desc)],
            "result_filters": self._table_filters.get("results", {}),
            "had_results": bool(self._result_store.logical_count),
            "selected_result_id": selected_result_id,
        }


    @staticmethod
    def _set_search_entry_text(widget, value):
        # An advanced row is rebuilt on Clear, so a restore may name a widget that
        # does not currently exist; the row rebuild refills it instead.
        if widget is None:
            return
        try:
            widget.delete(0, "end")
            if value not in (None, ""):
                widget.insert(0, str(value))
        except tk.TclError:
            pass

    def _restore_search_workspace_state(self, state):
        """Restore only currently valid Search vocabulary from saved UI state."""
        if not isinstance(state, dict):
            self._pending_result_restore_id = None
            return False
        self._pending_result_restore_id = str(
            state.get("selected_result_id") or "") or None
        restored_names = [str(value) for value in state.get("exact_names", []) if str(value)]
        self._search_name_batch = tuple(restored_names)
        self._search_name_batch_display = " | ".join(restored_names)
        self.q_name.set(self._search_name_batch_display if restored_names
                        else str(state.get("name") or ""))
        # The Rules text row may not exist yet -- advanced rows are rebuilt
        # further down -- so restore through the shadow the builder reads.
        self._rules_text_shadow = [
            str(phrase) for phrase in state.get("rules", []) if str(phrase)]
        self._rules_pending_shadow = str(state.get("rules_pending") or "")
        rules = getattr(self, "q_rules", None)
        if rules is not None:
            rules.clear()
            for phrase in self._rules_text_shadow:
                rules.add(phrase)
            if self._rules_pending_shadow:
                rules.entry.insert(0, self._rules_pending_shadow)

        saved_content = [
            "card" if str(value) == "deck" else str(value)
            for value in state.get("content", ["card"])
        ]
        # Search Scope uses the existing content-trait storage, so a saved content list is
        # restored by selecting the traits that produce it.
        content = {
            value for value in saved_content
            if value in ("card", "token", "emblem", "art")
        } or {"card"}
        # The saved content list is authoritative for scope, and a workspace
        # written before Cards became a choice carries no trait for it, so the
        # scope traits are derived here and applied after the saved trait list
        # rather than before it.
        restored_content_traits = {
            trait_key for trait_key, kind in CONTENT_TRAIT_KEYS.items()
            if kind in content}

        self._pending_catalog_filter_state = {
            "card_types": {str(value) for value in state.get("card_types", [])},
            "supertypes": {
                str(value) for value in state.get(
                    "supertypes", state.get("characteristics", []))},
            "format": str(state.get("format") or ""),
            "rarities": {str(value) for value in state.get("rarities", [])},
            "keywords": {str(value) for value in state.get("keywords", [])},
            "subtypes": {str(value) for value in state.get("subtypes", [])},
        }
        saved_games = state.get("games")
        self._search_printings.restore_selection(
            state.get("set_types", []),
            state.get("set_codes", []),
            paper_only=bool(state.get("paper_only", True)),
            games=(list(saved_games) if isinstance(saved_games, (list, tuple))
                   else None))

        safe_modes = (
            (self.q_rules_mode, state.get("rules_mode"),
             {"all", "any", "none"}, "all"),
            (self.q_card_type_mode, state.get("card_type_mode"),
             {"all", "any", "none"}, "any"),
            (self.q_supertype_mode, state.get("supertype_mode", state.get("characteristic_mode")),
             {"all", "any", "none"}, "any"),
            (self.q_subtype_mode, state.get("subtype_mode"),
             {"all", "any", "none"}, "any"),
            (self.q_keyword_mode, state.get("keyword_mode"),
             {"all", "any", "none"}, "any"),
            (self.q_color_mode, state.get("color_mode"), {"within", "includes", "exact"}, "within"),
            (self.q_color_scope, state.get("color_scope"),
             {"identity", "colors"}, "identity"),
            (self.q_produces_mode, state.get("produces_mode"),
             {"within", "includes", "exact"}, "includes"),
            (self.q_pip_mode, state.get("pip_mode"),
             {"any", "all", "none"}, "all"),
            (self.q_mana_feature_mode,
             state.get("mana_feature_mode", state.get("trait_mode")),
             {"any", "all", "none"}, "any"),
            (self.q_special_property_mode,
             state.get("special_property_mode", state.get("trait_mode")),
             {"any", "all", "none"}, "any"),
            (self.q_status_property_mode,
             state.get("status_property_mode", state.get("trait_mode")),
             {"any", "all", "none"}, "any"),
            (self.q_layout_mode, state.get("layout_mode"),
             {"any", "none"}, "any"),
            (self.q_format_status, state.get("format_status"),
             {"playable", "banned", "restricted"}, "playable"),
        )
        for variable, value, allowed, default in safe_modes:
            variable.set(value if value in allowed else default)

        wanted_colors = {str(value) for value in state.get("colors", [])}
        for key, variable in self.color_vars.items():
            variable.set(key in wanted_colors)

        # Advanced rows always exist, so only its open/closed state is
        # restored; a session that was working in Advanced reopens there.
        self._toggle_advanced_filters(bool(state.get("advanced_expanded", False)))
        legacy_properties = {
            str(value) for value in state.get("traits", []) or ()
            if str(value) in TRAIT_LABELS and str(value) not in CONTENT_TRAIT_KEYS
        }
        self._selected_traits = set(restored_content_traits)
        self._selected_mana_features = {
            str(value) for value in state.get(
                "mana_features", legacy_properties & set(MANA_COST_FEATURE_KEYS)) or ()
            if str(value) in MANA_COST_FEATURE_KEYS}
        self._selected_special_properties = {
            str(value) for value in state.get(
                "special_properties", legacy_properties & set(SPECIAL_PROPERTY_KEYS)) or ()
            if str(value) in SPECIAL_PROPERTY_KEYS}
        self._selected_status_properties = {
            str(value) for value in state.get(
                "status_properties", legacy_properties & set(STATUS_PROPERTY_KEYS)) or ()
            if str(value) in STATUS_PROPERTY_KEYS}
        # Legacy Single-faced is the complement of the new Has multiple faces
        # property.  Preserve the common one-property workspace case by
        # translating it to Match None rather than re-exposing Single-faced.
        if ("special_properties" not in state and
                legacy_properties == {"single_faced"}):
            self._selected_special_properties = {"multi_faced"}
            self.q_special_property_mode.set(
                "any" if state.get("trait_mode") == "none" else "none")
        self._refresh_split_trait_button_texts()
        # Workspaces written before the standard/advanced split named this
        # key "optional_values"; the values inside it never changed.
        self._restore_advanced_filter_values(
            state.get("advanced_values", state.get("optional_values", {})))
        # Produces is restored here rather than with Mana Color: its checkboxes
        # belong to an advanced row, so anything set before the rows are
        # rebuilt is discarded along with the widgets that held it.
        wanted_produces = {str(value) for value in state.get("produces", [])}
        for key, variable in self.produces_vars.items():
            variable.set(key in wanted_produces)
        # Card Form and mana-symbol controls own row-built state for the same reason.
        self._selected_layouts = {
            str(value) for value in state.get("layouts", []) or ()}
        self._set_picker_text(
            getattr(self, "_card_form_btn", None),
            self._picker_button_text(
                {self._layout_display_name(value)
                 for value in self._selected_layouts},
                "Any", "forms", max_visible=10, single_line=True))
        wanted_pips = {str(value) for value in state.get("pips", []) or ()}
        for key, variable in self.pip_vars.items():
            variable.set(key in wanted_pips)

        for widget, key in (
            (self.q_cmc_min, "cmc_min"), (self.q_cmc_max, "cmc_max"),
            (self.q_power_min, "power_min"), (self.q_power_max, "power_max"),
            (self.q_toughness_min, "toughness_min"),
            (self.q_toughness_max, "toughness_max"),
        ):
            self._set_search_entry_text(widget, state.get(key))
        self.english_only.set(bool(state.get("english_only", True)))


        sort = state.get("result_sort", [None, False])
        if isinstance(sort, list) and len(sort) >= 2:
            self._sort_col = sort[0] if sort[0] in TABLE_COLUMNS else None
            self._sort_desc = bool(sort[1])
        filters = state.get("result_filters", {})
        self._table_filters["results"] = dict(filters) if isinstance(filters, dict) else {}
        self._update_search_filter_summary()
        return bool(state.get("had_results"))


    def _restore_pending_result_selection(self):
        """Apply a workspace-restored exact printing selection to the virtual view."""
        card_id = getattr(self, "_pending_result_restore_id", None)
        if not card_id:
            return
        if self._result_store.view_position_for_id(card_id) is None:
            return
        self._pending_result_restore_id = None
        if not self._result_select_card_id(card_id, ensure_visible=True):
            return
        full = self._selected_result()
        if full:
            self._show_card(full)

    def _invalidate_search_cache(self):
        was_running = self.search_controller.running
        self.search_controller.invalidate()
        self._active_search_signature = None
        if was_running:
            try:
                self._search_btn.state(["!disabled"])
            except tk.TclError:
                pass

    @staticmethod
    def _parse_search_number(value, label):
        """Parse one numeric Search field with a user-facing error.

        A field whose row has been rebuilt reads as no restriction rather than
        raising, which is what lets Clear rebuild a row mid-session.
        """
        text = str(value or "").strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"{label} must be a finite number.") from exc
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a finite number.")
        return number

    @staticmethod
    def _validate_search_range(label, minimum, maximum):
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(
                f"{label} minimum cannot be greater than its maximum.")

    def _capture_search_criteria(self, *, commit_rules=False):
        """Capture one validated semantic snapshot for Search and live facets.

        This is the only Tk-to-SearchCriteria adapter. Manual Results searches
        and draft context therefore cannot drift in how a filter is interpreted.
        """
        numeric = {
            "cmc_min": self._numeric_field_value(getattr(self, "q_cmc_min", None)),
            "cmc_max": self._numeric_field_value(getattr(self, "q_cmc_max", None)),
            "power_min": self._numeric_field_value(getattr(self, "q_power_min", None)),
            "power_max": self._numeric_field_value(getattr(self, "q_power_max", None)),
            "toughness_min": self._numeric_field_value(getattr(self, "q_toughness_min", None)),
            "toughness_max": self._numeric_field_value(getattr(self, "q_toughness_max", None)),
        }
        optional_ranges = {
            "Loyalty": ("q_loyalty_min", "q_loyalty_max"),
            "Defense": ("q_defense_min", "q_defense_max"),
            "Released": ("q_released_min", "q_released_max"),
        }
        for _label, (low, high) in optional_ranges.items():
            numeric[low] = self._numeric_field_value(getattr(self, low, None))
            numeric[high] = self._numeric_field_value(getattr(self, high, None))
        self._validate_search_range("Mana value", numeric["cmc_min"], numeric["cmc_max"])
        self._validate_search_range("Power", numeric["power_min"], numeric["power_max"])
        self._validate_search_range(
            "Toughness", numeric["toughness_min"], numeric["toughness_max"])
        for label, (low, high) in optional_ranges.items():
            self._validate_search_range(label, numeric[low], numeric[high])
        numeric["q_pip_min"] = self._numeric_field_value(getattr(self, "q_pip_min", None))

        name_filter, exact_names = self._effective_name_filters()
        rules = self._rules_text_values(commit_pending=commit_rules)
        if not commit_rules:
            pending = " ".join(self._rules_pending_text().strip().split())
            if pending and not any(value.casefold() == pending.casefold() for value in rules):
                rules.append(pending)

        return SearchCriteria.from_mapping(dict(
            name=name_filter, names=exact_names,
            text=rules, text_mode=self.q_rules_mode.get(),
            card_types=[value for value, variable in self.card_type_vars.items() if variable.get()],
            card_type_mode=self.q_card_type_mode.get(),
            supertypes=[value for value, variable in self.property_vars.items() if variable.get()],
            supertype_mode=self.q_supertype_mode.get(),
            subtypes=sorted(self._selected_subtypes), subtype_mode=self.q_subtype_mode.get(),
            keywords=sorted(self._selected_keywords), keyword_mode=self.q_keyword_mode.get(),
            colors=[value for value, variable in self.color_vars.items() if variable.get()],
            color_mode=self.q_color_mode.get(), color_scope=self.q_color_scope.get(),
            produces=[value for value, variable in self.produces_vars.items() if variable.get()],
            produces_mode=self.q_produces_mode.get(),
            # Legacy global traits stay empty in the interactive UI. Each
            # property family owns its own Match mode.
            traits=(), trait_mode="any",
            mana_features=sorted(self._selected_mana_features),
            mana_feature_mode=self.q_mana_feature_mode.get(),
            special_properties=sorted(self._selected_special_properties),
            special_property_mode=self.q_special_property_mode.get(),
            status_properties=sorted(self._selected_status_properties),
            status_property_mode=self.q_status_property_mode.get(),
            layouts=sorted(self._selected_layouts), layout_mode=self.q_layout_mode.get(),
            pips=[value for value, variable in self.pip_vars.items() if variable.get()],
            pip_mode=self.q_pip_mode.get(), pip_min=numeric["q_pip_min"],
            loyalty_min=numeric["q_loyalty_min"], loyalty_max=numeric["q_loyalty_max"],
            defense_min=numeric["q_defense_min"], defense_max=numeric["q_defense_max"],
            released_from=numeric["q_released_min"], released_to=numeric["q_released_max"],
            cmc_min=numeric["cmc_min"], cmc_max=numeric["cmc_max"],
            power_min=numeric["power_min"], power_max=numeric["power_max"],
            toughness_min=numeric["toughness_min"], toughness_max=numeric["toughness_max"],
            rarities=sorted(self._selected_rarities),
            fmt=self.q_format.get().strip(), fmt_status=self.q_format_status.get(),
            set_codes=sorted(self._search_printings.selected_set_codes()) or None,
            set_types=sorted(self._search_printings.selected_set_types()) or None,
            lang=("en" if self.english_only.get() else ""),
            paper_only=bool(self._search_printings.paper_only.get()),
            games=self._search_printings.selected_games(),
            content_types=sorted(self._selected_content_types()),
        ))

    def _do_search(self):
        """Run SQLite search off Tk's main thread and deliver only results to Tk."""
        self._update_search_filter_summary()
        if self._search_catalog_loading:
            # Preserve one user Search intent across asynchronous trusted-catalog
            # preparation. The accepted latest snapshot resumes it exactly once.
            self._pending_search_request = True
            self.results_count_lbl.configure(text="RESULTS | Trusted filters are loading…")
            self._status(
                "Trusted Search filters are loading; Search will run automatically when ready.")
            return
        if self.search_controller.running:
            # Keep one latest intent rather than dropping a Search click while an
            # older query is still finishing. Its criteria are captured fresh when
            # the active query reaches a terminal event.
            self._pending_search_request = True
            return
        if not self.search_repository.has_cards():
            messagebox.showinfo(
                "No cards yet",
                "The card database is empty.\n\nUse Database -> Update Database first.")
            return
        try:
            criteria = self._capture_search_criteria(commit_rules=True)
        except ValueError as exc:
            self.results_count_lbl.configure(text="RESULTS | Invalid search filter")
            self._status(str(exc))
            messagebox.showerror("Invalid Search Filter", str(exc))
            return

        self._active_search_criteria = criteria
        start = self.search_controller.start(criteria)
        if start.kind == "busy":
            self._pending_search_request = True
            return
        if start.kind == "unchanged":
            self._set_result_count()
            self._request_search_context(criteria)
            return
        if start.kind == "cached":
            self._set_result_store(start.results, start.signature)
            self._render_results()
            self._request_search_context(criteria)
            return
        self.results_count_lbl.configure(text="RESULTS | Searching…")
        try:
            self._search_btn.state(["disabled"])
        except tk.TclError:
            pass
        self._start_search_event_pump()


    def _start_search_event_pump(self):
        if self._search_poll_after is not None:
            try:
                self.after_cancel(self._search_poll_after)
            except tk.TclError:
                pass
        self._search_poll_after = self.after(25, self._poll_search_events)

    def _poll_search_events(self):
        self._search_poll_after = None
        event = self.search_controller.poll_latest()

        if event is None:
            if self.search_controller.running:
                self._search_poll_after = self.after(
                    25, self._poll_search_events)
            return

        if not self.search_controller.accept(event):
            if self.search_controller.running:
                self._search_poll_after = self.after(
                    25, self._poll_search_events)
            return

        try:
            self._search_btn.state(["!disabled"])
        except tk.TclError:
            pass

        if event.kind == "error":
            log.error("Search failed: %s", event.payload)
            self.results_count_lbl.configure(text="RESULTS | Search failed")
            messagebox.showerror("Search error", event.payload)
            self._resume_pending_search_request()
            return

        log.info(
            "Card search returned %s rows in %.3f seconds",
            event.payload.logical_count, float(event.elapsed or 0.0))
        self._set_result_store(event.payload, event.signature)
        self._render_results()
        if (self._active_search_criteria is not None
                and self._active_search_criteria.signature() == event.signature):
            self._request_search_context(self._active_search_criteria)
        self._resume_pending_search_request()
