"""Search-pane layout, filter state, criteria capture, and UI callbacks."""

from __future__ import annotations

import math
import logging
import tkinter as tk
from tkinter import messagebox, ttk

from mtgdb.database.constants import COLORS
from mtgdb.search.models import SearchCriteria
from mtgdb.ui.autocomplete import AutocompleteEntry
from mtgdb.ui.components import (
    AppButton, AppEntry, AppMenubutton, AppSpinbox, ClassicCheckbutton,
    TokenBubbleEntry,
)
from mtgdb.ui.search_checklist import open_search_checklist
from mtgdb.ui.search_filters import (
    FILTER_BY_KEY, filter_catalog, ordered_active_filters,
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
CHIP_GRID_X_GAP = 4
SEARCH_ROW_PADY = 3
# Keeps every optional filter label on the same x-position as the fixed
# Advanced rows above them, so the control column does not step in and out.
OPTIONAL_FILTER_LABEL_WIDTH = 76
TRAIT_CHOICES = (
    ("not_universes_beyond", "Not Universes Beyond"),
    ("universes_beyond", "Universes Beyond"),
    ("reserved", "Reserved List"),
    ("game_changer", "Commander game changer"),
    ("multi_faced", "Multi-faced card"),
    ("single_faced", "Single-faced card"),
    ("hybrid_mana", "Hybrid mana in cost"),
    ("phyrexian_mana", "Phyrexian mana in cost"),
    ("has_x_cost", "X in mana cost"),
    ("color_indicator", "Has a color indicator"),
    ("top_heavy", "Power greater than toughness"),
)
TRAIT_LABELS = dict(TRAIT_CHOICES)
PICKER_SUMMARY_PER_LINE = 5


def _row_major_grid_required_width(widgets, columns):
    """Return the natural width needed by a row-major grid of chip widgets."""
    if columns <= 0:
        raise ValueError("columns must be positive")
    column_widths = [0] * columns
    for index, widget in enumerate(widgets):
        column = index % columns
        column_widths[column] = max(
            column_widths[column], int(widget.winfo_reqwidth()) + CHIP_GRID_X_GAP)
    return sum(column_widths)


class SearchFeatureMixin:
    """Own the interactive Search feature while the root wires other features."""

    def _build_search_pane(self, parent):
        form = ttk.Frame(parent)
        form.pack(fill="x")
        form.columnconfigure(0, minsize=76)
        form.columnconfigure(1, weight=1, uniform="search_control")
        form.columnconfigure(2, minsize=76)
        form.columnconfigure(3, weight=1, uniform="search_control")
        self._build_name_filter(form)
        self._build_card_type_filters(form)
        self._build_color_filters(form)
        self._build_numeric_filters(form)
        self._build_taxonomy_filters(form)
        self._build_advanced_filters(parent)
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
        ttk.Label(form, text="Card Name").grid(row=0, column=0, sticky="w",
                                               padx=(0, 8), pady=SEARCH_ROW_PADY)
        self._search_name_batch = ()
        self._search_name_batch_display = ""
        self.q_name = AutocompleteEntry(form)
        self.q_name.set_suggest_source(self.search_repository.name_suggestions)
        self.q_name.set("")
        self.q_name.grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
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

    def _bind_search_outside_click_selection_cleanup(self):
        """Clear Search text highlights when a click lands outside that editor."""
        self._search_blur_clear_widgets = (
            self.q_rules.entry,
            self.q_cmc_min, self.q_cmc_max,
            self.q_power_min, self.q_power_max,
            self.q_toughness_min, self.q_toughness_max,
        )
        self.bind_all(
            "<ButtonPress-1>", self._clear_search_selections_on_outside_click,
            add="+")

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

    def _build_card_type_filters(self, form):
        ttk.Label(form, text="Card type").grid(
            row=1, column=0, sticky="nw", padx=(0, 8), pady=SEARCH_ROW_PADY)
        typebox = ttk.Frame(form)
        typebox.grid(row=1, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self.card_type_vars = {}
        self._card_type_catalog = []
        self._card_type_chip_frame = ttk.Frame(typebox)
        self._card_type_chip_frame.pack(fill="x")
        self._card_type_chip_widgets = ()
        self._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
        self._card_type_chip_frame.bind(
            "<Configure>", self._layout_card_type_chips, add="+")
        mode = ttk.Frame(typebox)
        mode.pack(fill="x", pady=(2, 0))
        ttk.Label(mode, text="Selected types:", style="Muted.TLabel").pack(side="left")
        self.q_card_type_mode = tk.StringVar(value="any")
        type_mode_help = {
            "any": "Any: the card must have at least one selected Card Type.",
            "all": ("All: the card must have every selected Card Type across "
                    "its full type line, including multiple faces."),
        }
        for label, value in (("Any", "any"), ("All", "all")):
            radio = ttk.Radiobutton(
                mode, text=label, variable=self.q_card_type_mode, value=value,
                command=self._update_search_filter_summary)
            radio.pack(side="left", padx=(3, 0))
            self._add_tooltip(radio, type_mode_help[value], wraplength=390)

        ttk.Label(form, text="Supertypes").grid(
            row=2, column=0, sticky="nw", padx=(0, 8), pady=SEARCH_ROW_PADY)
        self.property_vars = {}
        self._property_catalog = []
        self._property_chip_frame = ttk.Frame(form)
        self._property_chip_frame.grid(
            row=2, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self.q_supertype_mode = tk.StringVar(value="all")


    def _build_color_filters(self, form):
        ttk.Label(form, text="Colors").grid(
            row=3, column=0, sticky="nw", padx=(0, 8), pady=SEARCH_ROW_PADY)
        colorwrap = ttk.Frame(form)
        colorwrap.grid(row=3, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        colorbox = ttk.Frame(colorwrap)
        colorbox.pack(fill="x")
        self.color_vars = {}
        for c in (*COLORS, "C"):
            v = tk.BooleanVar(value=False)
            self.color_vars[c] = v
            kw = {"text": " " + MANA_NAMES[c], "variable": v,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            if self.pips.get(c):
                kw["image"] = self.pips[c]
                kw["compound"] = "left"
            ttk.Checkbutton(colorbox, **kw).pack(side="left", padx=(0, 8))
        colormode = ttk.Frame(colorwrap)
        colormode.pack(fill="x", pady=(2, 0))
        ttk.Label(colormode, text="Color identity:", style="Muted.TLabel").pack(side="left")
        self.q_color_mode = tk.StringVar(value="within")
        color_mode_help = {
            "within": "Within: the card's entire color identity must fit inside the selected colors.",
            "includes": "Contains: the card must contain every selected color but may contain others.",
            "exact": "Exactly: the card's color identity must exactly equal the selected colors.",
        }
        for label, value in (("Within", "within"), ("Contains", "includes"),
                             ("Exactly", "exact")):
            radio = ttk.Radiobutton(
                colormode, text=label, variable=self.q_color_mode, value=value,
                command=self._update_search_filter_summary)
            radio.pack(side="left", padx=(3, 0))
            self._add_tooltip(radio, color_mode_help[value], wraplength=390)


    def _build_produces_filter(self, parent, row):
        """Mana a card can actually produce, distinct from its colour identity.

        Deliberately a twin of the Colors control: the same pip checkboxes and
        the same within/contains/exactly modes, because the two filters read
        the same comma-joined WUBRG(+C) encoding. Only the default mode differs
        -- "contains" answers the mana-base question people actually ask.
        """
        ttk.Label(parent, text="Produces").grid(
            row=row, column=0, sticky="nw", padx=(0, 8), pady=2)
        wrap = ttk.Frame(parent)
        wrap.grid(row=row, column=1, sticky="ew", pady=2)
        box = ttk.Frame(wrap)
        box.pack(fill="x")
        self.produces_vars = {}
        for color in (*COLORS, "C"):
            variable = tk.BooleanVar(value=False)
            self.produces_vars[color] = variable
            kw = {"text": " " + MANA_NAMES[color], "variable": variable,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            if self.pips.get(color):
                kw["image"] = self.pips[color]
                kw["compound"] = "left"
            ttk.Checkbutton(box, **kw).pack(side="left", padx=(0, 8))
        mode = ttk.Frame(wrap)
        mode.pack(fill="x", pady=(2, 0))
        ttk.Label(mode, text="Produces mana:", style="Muted.TLabel").pack(side="left")
        self.q_produces_mode = tk.StringVar(value="includes")
        help_text = {
            "within": "Within: everything the card produces must fit inside the selected colors.",
            "includes": "Contains: the card must produce every selected color and may produce others.",
            "exact": "Exactly: the card must produce exactly the selected colors.",
        }
        for label, value in (("Within", "within"), ("Contains", "includes"),
                             ("Exactly", "exact")):
            radio = ttk.Radiobutton(
                mode, text=label, variable=self.q_produces_mode, value=value,
                command=self._update_search_filter_summary)
            radio.pack(side="left", padx=(3, 0))
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

    def _build_numeric_filters(self, form):
        ranges = ttk.Frame(form)
        ranges.grid(row=4, column=0, columnspan=4, sticky="ew", pady=SEARCH_ROW_PADY)
        ranges.columnconfigure(1, weight=1)
        ranges.columnconfigure(3, weight=1)
        ranges.columnconfigure(5, weight=1)
        ttk.Label(ranges, text="Mana value").grid(row=0, column=0, sticky="w", padx=(0, 7))
        cmcbar = ttk.Frame(ranges); cmcbar.grid(row=0, column=1, sticky="w", padx=(0, 14))
        self.q_cmc_min = AppSpinbox(cmcbar, from_=0, to=30, width=5); self._bind_editable_focus_behavior(self.q_cmc_min); self.q_cmc_min.pack(side="left")
        ttk.Label(cmcbar, text="to", style="Muted.TLabel").pack(side="left", padx=5)
        self.q_cmc_max = AppSpinbox(cmcbar, from_=0, to=30, width=5); self._bind_editable_focus_behavior(self.q_cmc_max); self.q_cmc_max.pack(side="left")
        ttk.Label(ranges, text="Power").grid(row=0, column=2, sticky="w", padx=(0, 7))
        powerbar = ttk.Frame(ranges); powerbar.grid(row=0, column=3, sticky="w", padx=(0, 14))
        self.q_power_min = AppSpinbox(powerbar, from_=-20, to=30, width=5); self._bind_editable_focus_behavior(self.q_power_min); self.q_power_min.pack(side="left")
        ttk.Label(powerbar, text="to", style="Muted.TLabel").pack(side="left", padx=5)
        self.q_power_max = AppSpinbox(powerbar, from_=-20, to=30, width=5); self._bind_editable_focus_behavior(self.q_power_max); self.q_power_max.pack(side="left")
        ttk.Label(ranges, text="Toughness").grid(row=0, column=4, sticky="w", padx=(0, 7))
        toughbar = ttk.Frame(ranges); toughbar.grid(row=0, column=5, sticky="w")
        self.q_toughness_min = AppSpinbox(toughbar, from_=-20, to=30, width=5); self._bind_editable_focus_behavior(self.q_toughness_min); self.q_toughness_min.pack(side="left")
        ttk.Label(toughbar, text="to", style="Muted.TLabel").pack(side="left", padx=5)
        self.q_toughness_max = AppSpinbox(toughbar, from_=-20, to=30, width=5); self._bind_editable_focus_behavior(self.q_toughness_max); self.q_toughness_max.pack(side="left")
        for spin in (self.q_cmc_min, self.q_cmc_max, self.q_power_min,
                     self.q_power_max, self.q_toughness_min, self.q_toughness_max):
            self._configure_zero_start_spinbox(spin)


    def _build_taxonomy_filters(self, form):
        ttk.Label(form, text="Mechanics").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=SEARCH_ROW_PADY)
        self._selected_keywords = set()
        self._keyword_catalog = []
        self.q_keyword_mode = tk.StringVar(value="any")
        mechanicbox = ttk.Frame(form)
        mechanicbox.grid(row=5, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._keyword_btn = AppButton(
            mechanicbox, text="Any", role="picker",
            command=self._choose_keywords)
        self._keyword_btn.pack(side="left", fill="x", expand=True)


    def _build_rules_text_filter(self, form, *, row, advanced=False):
        label_options = {}
        ttk.Label(form, text="Rules text", **label_options).grid(
            row=row, column=0, sticky="nw", padx=(0, 8),
            pady=(2 if advanced else SEARCH_ROW_PADY))
        rules_box = ttk.Frame(form)
        rules_box.grid(
            row=row, column=1, sticky="ew",
            pady=(2 if advanced else SEARCH_ROW_PADY))
        rules_box.columnconfigure(0, weight=1)
        self.q_rules = TokenBubbleEntry(rules_box, search_command=self._do_search)
        self.q_rules.grid(row=0, column=0, sticky="ew")
        self._bind_editable_focus_behavior(self.q_rules.entry)
        self._add_tooltip(
            self.q_rules.entry,
            ("Unquoted words may appear anywhere in the card's complete Oracle "
             "text; use double quotes for an exact phrase. Press Enter to add "
             "another Rules Text chip."),
            wraplength=420)
        mode_box = ttk.Frame(rules_box); mode_box.grid(row=1, column=0, sticky="w", pady=(1, 0))
        ttk.Label(mode_box, text="Rules text:").pack(side="left", padx=(0, 4))
        self.q_rules_mode = tk.StringVar(value="all")
        rules_all = ttk.Radiobutton(mode_box, text="All", variable=self.q_rules_mode, value="all")
        rules_all.pack(side="left")
        rules_any = ttk.Radiobutton(mode_box, text="Any", variable=self.q_rules_mode, value="any")
        rules_any.pack(side="left", padx=(4, 0))
        self._add_tooltip(
            rules_all, "All: every Rules Text chip must match the card.")
        self._add_tooltip(
            rules_any, "Any: at least one Rules Text chip must match the card.")


    def _build_format_rarity_filters(self, parent, *, format_row, rarity_row):
        ttk.Label(parent, text="Format").grid(
            row=format_row, column=0, sticky="w", padx=(0, 8), pady=2)
        self.q_format = tk.StringVar(value="")
        self._format_btn = AppButton(
            parent, text="Any", role="picker", command=self._choose_format)
        self._format_btn.grid(
            row=format_row, column=1, sticky="ew", pady=2)

        ttk.Label(parent, text="Rarity").grid(
            row=rarity_row, column=0, sticky="w", padx=(0, 8), pady=2)
        self._selected_rarities = set()
        self._rarity_btn = AppButton(
            parent, text="Any", role="picker", command=self._choose_rarities)
        self._rarity_btn.grid(
            row=rarity_row, column=1, sticky="ew", pady=2)


    def _build_content_filter(self, parent):
        ttk.Label(parent, text="Content").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        contentbox = ttk.Frame(parent)
        contentbox.grid(row=0, column=1, sticky="w", pady=2)
        self.content_vars = {
            "card": tk.BooleanVar(value=True),
            "token": tk.BooleanVar(value=False),
            "emblem": tk.BooleanVar(value=False),
            "art": tk.BooleanVar(value=False),
        }
        labels = (
            ("card", "Cards"), ("token", "Tokens"),
            ("emblem", "Emblems"), ("art", "Art Series"),
        )
        # Keep the choices compact in the shared Advanced control column so
        # Cards aligns with Subtype/Format/Rarity/Printings instead of spreading
        # the four choices across the Search width.
        for index, (key, label) in enumerate(labels):
            column = index * 2
            self._filter_chip(
                contentbox, label, self.content_vars[key],
                command=self._on_content_filter_change, padx=1).grid(
                    row=0, column=column, sticky="w", padx=0, pady=0)
            if index < len(labels) - 1:
                ttk.Label(
                    contentbox, text="|", style="Muted.TLabel").grid(
                        row=0, column=column + 1, sticky="ns", padx=(1, 1))


    def _build_printing_filter(self, parent, *, row=0):
        self._search_printings = SearchPrintingFilter(self, parent, row=row)


    def _build_advanced_filters(self, parent):
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(4, 4))
        self._advanced_filters_visible = False
        self._advanced_btn = AppButton(
            parent, text="Advanced Filters ▾", role="picker",
            command=self._toggle_advanced_filters)
        self._advanced_btn.pack(fill="x")
        self._advanced_filters_frame = ttk.Frame(parent)
        self._advanced_filters_frame.columnconfigure(1, weight=1)

        self._build_content_filter(self._advanced_filters_frame)
        self._build_produces_filter(self._advanced_filters_frame, row=1)
        self._build_rules_text_filter(
            self._advanced_filters_frame, row=2, advanced=True)
        ttk.Label(self._advanced_filters_frame, text="Subtype").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=2)
        self._selected_subtypes = set()
        self._subtype_catalog = []
        self.q_subtype_mode = tk.StringVar(value="any")
        subtypebox = ttk.Frame(self._advanced_filters_frame)
        subtypebox.grid(row=3, column=1, sticky="ew", pady=2)
        self._subtype_btn = AppButton(
            subtypebox, text="Any", role="picker",
            command=self._choose_subtypes)
        self._subtype_btn.pack(fill="x", expand=True)

        self._build_format_rarity_filters(
            self._advanced_filters_frame, format_row=4, rarity_row=5)

        # Keep Printings on the same outer label/control grid as Rules Text
        # and Subtype. A nested two-column frame gives its label a different
        # natural width and visibly pushes the picker to the right.
        self._build_printing_filter(self._advanced_filters_frame, row=6)

        self._build_optional_filter_zone(self._advanced_filters_frame, row=7)

        # Each picker summarizes itself; there is no duplicate aggregate
        # Active Filters line above the Search actions.
        self._active_filter_label = None

    # ------------------------------------------------------------------
    # optional filter controls
    # ------------------------------------------------------------------

    def _numeric_pair(self, parent, attribute_prefix, width=5):
        """Two spinboxes as one unplaced frame; the caller positions it.

        Returned rather than placed so the same helper serves grid rows and
        packed sub-frames without fighting the geometry manager.
        """
        box = ttk.Frame(parent)
        low = AppSpinbox(box, from_=0, to=999, width=width)
        low.pack(side="left")
        ttk.Label(box, text="to", style="Muted.TLabel").pack(side="left", padx=5)
        high = AppSpinbox(box, from_=0, to=999, width=width)
        high.pack(side="left")
        for widget in (low, high):
            widget.delete(0, "end")
            self._configure_zero_start_spinbox(widget)
        setattr(self, f"{attribute_prefix}_min", low)
        setattr(self, f"{attribute_prefix}_max", high)
        return box

    def _build_filter_traits(self, parent):
        self._traits_btn = AppButton(
            parent, text="Any", role="picker",
            command=self._choose_traits)
        self._traits_btn.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_traits(self):
        self._selected_traits = set()
        self._traits_btn = None

    def _choose_traits(self):
        """Pick boolean card properties through the shared checklist dialog."""
        by_label = {label: key for key, label in TRAIT_CHOICES}

        def apply(chosen):
            self._selected_traits = {
                by_label[label] for label in chosen if label in by_label}
            if self._traits_btn is not None:
                self._traits_btn.configure(text=self._picker_button_text(
                    {TRAIT_LABELS[key]
                     for key in self._selected_traits},
                    "Any", "traits", max_visible=10, single_line=True))
            self._update_search_filter_summary()

        selected = {
            TRAIT_LABELS[key]
            for key in getattr(self, "_selected_traits", set()) or ()
            if key in TRAIT_LABELS}
        self._open_search_multi_picker(
            "Card Traits", [label for _key, label in TRAIT_CHOICES],
            selected, apply,
            mode_label="Selected traits:",
            help_text="Choose one or several card traits.")

    def _build_filter_loyalty(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Loyalty", style="Muted.TLabel").pack(
            side="left", padx=(0, 5))
        self._numeric_pair(box, "q_loyalty").pack(side="left")
        ttk.Label(box, text="Defense", style="Muted.TLabel").pack(
            side="left", padx=(14, 5))
        self._numeric_pair(box, "q_defense").pack(side="left")

    def _reset_filter_loyalty(self):
        for name in ("q_loyalty_min", "q_loyalty_max",
                     "q_defense_min", "q_defense_max"):
            setattr(self, name, None)

    def _build_filter_released(self, parent):
        pair = self._numeric_pair(parent, "q_released", width=6)
        pair.grid(row=0, column=1, sticky="w", pady=2)

    def _reset_filter_released(self):
        self.q_released_min = None
        self.q_released_max = None

    def _build_filter_artist(self, parent):
        self.q_artist = AppEntry(parent)
        self.q_artist.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_artist(self):
        self.q_artist = None

    def _build_filter_mana_cost(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="w", pady=2)
        self.cost_feature_vars = {}
        for key, label in (("hybrid_mana", "Hybrid"),
                           ("phyrexian_mana", "Phyrexian"),
                           ("has_x_cost", "Has X")):
            variable = tk.BooleanVar(value=False)
            self.cost_feature_vars[key] = variable
            ttk.Checkbutton(
                box, text=" " + label, variable=variable,
                command=self._update_search_filter_summary).pack(
                    side="left", padx=(0, 10))

    def _reset_filter_mana_cost(self):
        self.cost_feature_vars = {}

    def _optional_numeric(self, widget):
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

    OPTIONAL_TEXT_FIELDS = (
        "q_loyalty_min", "q_loyalty_max", "q_defense_min", "q_defense_max",
        "q_released_min", "q_released_max", "q_artist",
    )

    def _capture_optional_filter_values(self):
        """Text currently held by optional filter controls, by attribute name."""
        values = {}
        for name in self.OPTIONAL_TEXT_FIELDS:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                values[name] = str(widget.get()).strip()
            except tk.TclError:
                continue
        return {name: value for name, value in values.items() if value}

    def _restore_optional_filter_values(self, values):
        """Refill optional controls after their rows have been rebuilt."""
        for name, value in dict(values or {}).items():
            widget = getattr(self, name, None)
            if widget is None or name not in self.OPTIONAL_TEXT_FIELDS:
                continue
            try:
                widget.delete(0, "end")
                widget.insert(0, str(value))
            except tk.TclError:
                continue

    def _selected_trait_keys(self):
        keys = set(getattr(self, "_selected_traits", set()) or ())
        for key, variable in (getattr(self, "cost_feature_vars", {}) or {}).items():
            try:
                if variable.get():
                    keys.add(key)
            except tk.TclError:
                continue
        return tuple(sorted(keys))

    # ------------------------------------------------------------------
    # optional filters, built on demand
    # ------------------------------------------------------------------

    def _build_optional_filter_zone(self, parent, *, row):
        """Host for filters that exist only while they are in use.

        The Search form and the Results table share one column with no sash
        between them, so a permanently-rendered filter takes its height out of
        Results for every user. Optional filters are built when added and
        destroyed when removed, so an unused one costs nothing.
        """
        self._optional_filter_rows = {}
        host = ttk.Frame(parent)
        host.grid(row=row, column=0, columnspan=2, sticky="ew")
        host.columnconfigure(0, weight=1)
        self._optional_filter_host = host

        self._add_filter_btn = AppMenubutton(
            parent, text="+ Add filter", role="menu")
        self._add_filter_btn.grid(
            row=row + 1, column=0, columnspan=2, sticky="w", pady=(6, 2))
        self._add_filter_menu = self._dark_menu(self._add_filter_btn)
        self._add_filter_btn.configure(menu=self._add_filter_menu)
        self._add_tooltip(
            self._add_filter_btn,
            "Add a filter to this search. Filters you have not added take no "
            "space, so the Results list stays as tall as possible.",
            wraplength=360)
        self._refresh_add_filter_menu()

    def _refresh_add_filter_menu(self):
        """Rebuild the catalogue, grouping by category and marking what is on."""
        menu = getattr(self, "_add_filter_menu", None)
        if menu is None:
            return
        menu.delete(0, "end")
        active = set(getattr(self, "_optional_filter_rows", {}))
        first = True
        for category, entries in filter_catalog(active):
            if not first:
                menu.add_separator()
            first = False
            menu.add_command(label=category, state="disabled")
            for entry in entries:
                menu.add_command(
                    label=("   " + entry["label"]
                           + ("  (added)" if entry["active"] else "")),
                    state="disabled" if entry["active"] else "normal",
                    command=(None if entry["active"]
                             else lambda key=entry["key"]:
                                 self._add_optional_filter(key)))

    def _add_optional_filter(self, key, *, notify=True):
        """Build one optional filter row, or focus it when already present."""
        if key in getattr(self, "_optional_filter_rows", {}):
            return
        definition = FILTER_BY_KEY.get(key)
        builder = getattr(self, f"_build_filter_{key}", None)
        if definition is None or builder is None:
            return

        frame = ttk.Frame(self._optional_filter_host)
        frame.pack(fill="x")
        frame.columnconfigure(0, minsize=OPTIONAL_FILTER_LABEL_WIDTH)
        frame.columnconfigure(1, weight=1)

        label = ttk.Label(frame, text=definition["label"])
        label.grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=2)
        self._add_tooltip(label, definition["tooltip"], wraplength=380)

        builder(frame)

        remove = AppButton(
            frame, text="×", role="compact",
            command=lambda: self._remove_optional_filter(key))
        remove.grid(row=0, column=2, sticky="ne", padx=(6, 0), pady=2)
        self._add_tooltip(
            remove, f"Remove the {definition['label']} filter from this search.",
            wraplength=300)

        self._optional_filter_rows[key] = frame
        self._refresh_add_filter_menu()
        if notify:
            self._update_search_filter_summary()

    def _remove_optional_filter(self, key, *, notify=True):
        """Destroy one optional filter row and reset the state it owned."""
        frame = getattr(self, "_optional_filter_rows", {}).pop(key, None)
        if frame is None:
            return
        reset = getattr(self, f"_reset_filter_{key}", None)
        if reset is not None:
            reset()
        frame.destroy()
        self._refresh_add_filter_menu()
        if notify:
            self._update_search_filter_summary()

    def _active_optional_filters(self):
        return ordered_active_filters(getattr(self, "_optional_filter_rows", {}))

    def _clear_optional_filters(self):
        for key in list(getattr(self, "_optional_filter_rows", {})):
            self._remove_optional_filter(key, notify=False)

    def _toggle_advanced_filters(self):
        self._advanced_filters_visible = not self._advanced_filters_visible
        if self._advanced_filters_visible:
            self._advanced_filters_frame.pack(fill="x", pady=(3, 0), after=self._advanced_btn)
            self._advanced_btn.configure(text="Advanced Filters ▴")
        else:
            self._advanced_filters_frame.pack_forget()
            self._advanced_btn.configure(text="Advanced Filters ▾")

    def _selected_content_types(self):
        selected = {
            key for key, variable in getattr(self, "content_vars", {}).items()
            if bool(variable.get())
        }
        return selected or {"card"}

    def _render_trusted_chips(
            self, frame, values, variables, *, columns=3, empty_text=None):
        selected = {key for key, variable in variables.items() if bool(variable.get())}
        for child in frame.winfo_children():
            child.destroy()
        variables.clear()
        widgets = []
        for index, value in enumerate(values):
            variable = tk.BooleanVar(master=self, value=value in selected)
            variables[value] = variable
            chip = self._filter_chip(frame, value, variable)
            widgets.append(chip)
            chip.grid(
                row=index // columns, column=index % columns, sticky="ew",
                padx=(0, CHIP_GRID_X_GAP), pady=2)
            frame.columnconfigure(index % columns, weight=1)
        if not values:
            ttk.Label(
                frame,
                text=(empty_text or
                      "No authoritative values available for this content."),
                style="Muted.TLabel", justify="left", wraplength=500
            ).grid(row=0, column=0, sticky="w", columnspan=max(1, columns))
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
                widget.grid_configure(
                    row=index // desired, column=index % desired, sticky="ew",
                    padx=(0, CHIP_GRID_X_GAP), pady=2)
            for column in range(CARD_TYPE_MAX_COLUMNS):
                frame.columnconfigure(
                    column, weight=(1 if column < desired else 0))
            self._card_type_chip_columns = desired
        except tk.TclError:
            return

    def _build_search_actions(self, parent):
        # Search controls stay on the left; deck actions sit as a visually separate
        # group on the right so adding a selected result is always close at hand.
        btns = ttk.Frame(parent)
        btns.pack(fill="x", pady=(4, 5))

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
        AppButton(
            search_actions, text="Clear", role="standard", width=5,
            command=self._clear_search).pack(side="left", padx=(4, 0))


    def _build_results_table(self, parent):
        # Results are the main purpose of this pane, so they receive all of the
        # remaining vertical space. Columns are user-configurable per table.
        result_head = ttk.Frame(parent)
        result_head.pack(fill="x", pady=(0, 3))
        self.results_count_lbl = ttk.Label(
            result_head, text="RESULTS | 0 Cards", style="Section.TLabel")
        self.results_count_lbl.pack(side="left")
        results_columns_btn = AppButton(
            result_head, text="Edit Columns", role="compact",
            command=lambda: self._toggle_column_popup("results", results_columns_btn))
        results_columns_btn.pack(side="right")
        AppButton(
            result_head, text="Clear Filters", role="compact",
            command=lambda: self._clear_table_filter("results")
        ).pack(side="right", padx=(0, 6))

        table = ttk.Frame(parent)
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
        self.q_rules.clear()
        self.q_rules_mode.set("all")
        self.q_card_type_mode.set("any")
        self.q_supertype_mode.set("all")
        self.q_subtype_mode.set("any")
        self.q_keyword_mode.set("any")
        self.q_color_mode.set("within")
        self.q_produces_mode.set("includes")
        self._clear_optional_filters()
        for variable in self.card_type_vars.values():
            variable.set(False)
        for variable in self.property_vars.values():
            variable.set(False)
        self._selected_subtypes.clear()
        self._selected_keywords.clear()
        self._selected_rarities.clear()
        self._subtype_btn.configure(text="Any")
        self._keyword_btn.configure(text="Any")
        self._rarity_btn.configure(text="Any")
        for widget in (self.q_cmc_min, self.q_cmc_max, self.q_power_min,
                       self.q_power_max, self.q_toughness_min, self.q_toughness_max):
            widget.delete(0, "end")
        for variable in self.color_vars.values():
            variable.set(False)
        for variable in self.produces_vars.values():
            variable.set(False)
        self.q_format.set("")
        self._format_btn.configure(text="Any")
        for key, variable in self.content_vars.items():
            variable.set(key == "card")
        self.english_only.set(True)
        self._pending_catalog_filter_state = {
            "card_types": set(), "supertypes": set(), "format": "",
            "rarities": set(), "keywords": set(), "subtypes": set(),
        }
        self._search_printings.clear()
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
        if not any(variable.get() for variable in self.content_vars.values()):
            self.content_vars["card"].set(True)
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
                                  mode_label="Selected values:",
                                  help_text="Type to narrow the list.",
                                  single_select=False):
        """Open the reusable hidden-first, batch-rendered search picker."""
        return open_search_checklist(
            self, title=title, values=values, selected=selected,
            apply_callback=apply_callback, mode_var=mode_var,
            mode_default=mode_default, mode_label=mode_label,
            help_text=help_text, single_select=single_select)


    def _choose_subtypes(self):
        def apply(chosen):
            self._selected_subtypes = set(chosen)
            self._subtype_btn.configure(text=self._picker_button_text(
                self._selected_subtypes, "Any", "subtypes",
            max_visible=10, single_line=True))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Subtypes", self._subtype_catalog, self._selected_subtypes, apply,
            mode_var=self.q_subtype_mode,
            mode_label="Selected subtypes:",
            help_text="Choose one or several card subtypes.")


    def _choose_keywords(self):
        def apply(chosen):
            self._selected_keywords = set(chosen)
            self._keyword_btn.configure(text=self._picker_button_text(
                self._selected_keywords, "Any", "mechanics", max_visible=10))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Mechanics", self._keyword_catalog, self._selected_keywords, apply,
            mode_var=self.q_keyword_mode,
            mode_label="Selected mechanics:",
            help_text="Choose one or several card mechanics.")


    def _set_format_filter(self, value):
        value = str(value or "").strip()
        if value not in self._format_catalog:
            value = ""
        self.q_format.set(value)
        if hasattr(self, "_format_btn"):
            self._format_btn.configure(text=(value.replace("_", " ").capitalize() if value else "Any"))


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
            self._set_format_filter(value)

        self._open_search_multi_picker(
            "Choose Format",
            [("", "Any")] + [
                (fmt, fmt.replace("_", " ").capitalize())
                for fmt in self._format_catalog],
            selected,
            apply,
            help_text="Choose the format cards must be legal in.",
            single_select=True)

    def _choose_rarities(self):
        def apply(chosen):
            self._selected_rarities = set(chosen)
            labels = {r.capitalize() for r in chosen}
            self._rarity_btn.configure(text=self._picker_button_text(
                labels, "Any", "rarities"))
        self._open_search_multi_picker(
            "Choose Rarities", [(r, r.replace("_", " ").capitalize())
                                for r in self._rarity_catalog],
            self._selected_rarities, apply,
            help_text="Choose one or several rarities.")

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
        state = "normal" if enabled else "disabled"
        for widget in tuple(getattr(self, "_card_type_chip_widgets", ())) + tuple(
                getattr(self, "_property_chip_widgets", ())):
            try:
                widget.configure(state=state)
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
        base_scope = (content, paper_only)
        start = self.search_catalog_controller.request(
            content, paper_only, selected_set_types)
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

        self._card_type_catalog = list(snapshot.card_types)
        self._property_catalog = list(snapshot.supertypes)
        card_type_empty_text = None
        if not card_type_authority_available:
            card_type_empty_text = (
                "Scryfall Card Type taxonomy is unavailable. "
                "Use Database > Update Database to retry.")
            if len(card_type_status) > 1 and card_type_status[1]:
                card_type_empty_text += (
                    "\nLast error: "
                    + " ".join(str(card_type_status[1]).split())[:220])
        self._card_type_chip_widgets = ()
        self._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
        self._card_type_chip_widgets = self._render_trusted_chips(
            self._card_type_chip_frame, self._card_type_catalog, self.card_type_vars,
            columns=CARD_TYPE_MIN_COLUMNS, empty_text=card_type_empty_text)
        for value, variable in self.card_type_vars.items():
            variable.set(value in pending["card_types"])
        self._layout_card_type_chips()

        supertype_empty_text = None
        if not supertype_authority_available:
            supertype_empty_text = (
                "Official Wizards Supertype taxonomy is unavailable. "
                "Use Database > Update Database to retry.")
            if len(supertype_status) > 1 and supertype_status[1]:
                supertype_empty_text += (
                    "\nLast error: "
                    + " ".join(str(supertype_status[1]).split())[:220])
        self._property_chip_widgets = self._render_trusted_chips(
            self._property_chip_frame, self._property_catalog, self.property_vars,
            columns=SUPERTYPE_COLUMNS, empty_text=supertype_empty_text)
        for value, variable in self.property_vars.items():
            variable.set(value in pending["supertypes"])

        self._format_catalog = list(snapshot.formats)
        self._set_format_filter(
            pending["format"] if pending["format"] in self._format_catalog else "")
        self._rarity_catalog = list(snapshot.rarities)
        self._selected_rarities = set(pending["rarities"]).intersection(
            self._rarity_catalog)
        self._rarity_btn.configure(text=self._picker_button_text(
            {value.capitalize() for value in self._selected_rarities},
            "Any", "rarities"))

        self._keyword_catalog = [
            (value, f"{category} · {value}") for value, category in snapshot.keywords
        ]
        valid_keywords = {value for value, _display in self._keyword_catalog}
        self._selected_keywords = set(pending["keywords"]).intersection(valid_keywords)
        self._keyword_btn.configure(text=self._picker_button_text(
            self._selected_keywords, "Any", "mechanics", max_visible=10))

        self._subtype_catalog = [
            (value, f"{category} · {value}") for value, category in snapshot.subtypes
        ]
        valid_subtypes = {value for value, _display in self._subtype_catalog}
        self._selected_subtypes = set(pending["subtypes"]).intersection(valid_subtypes)
        self._subtype_btn.configure(text=self._picker_button_text(
            self._selected_subtypes, "Any", "subtypes",
            max_visible=10, single_line=True))

        actual_set_types = self._search_printings.apply_snapshot(snapshot)
        self._search_catalog_scope = (snapshot.content_types, snapshot.paper_only)
        if set(actual_set_types) != set(snapshot.selected_set_types):
            # Invalid restored/old set types were pruned; resolve Exact Sets for
            # the now-authoritative selection without blocking the UI.
            self._refresh_search_catalogs(selected_set_types=actual_set_types)
            return

        self._pending_catalog_filter_state = None
        self._search_catalog_loading = False
        self._set_search_catalog_controls_enabled(True)
        self._update_search_filter_summary()
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

    def search_catalog_performance_info(self):
        return self.search_catalog_controller.cache_info()


    def _update_search_filter_summary(self):
        label = getattr(self, "_active_filter_label", None)
        if label is None:
            return
        parts = []
        name = self.q_name.get().strip() if hasattr(self, "q_name") else ""
        if getattr(self, "_search_name_batch", ()) and name == getattr(
                self, "_search_name_batch_display", ""):
            parts.append("Names: " + ", ".join(self._search_name_batch))
        elif name:
            parts.append(f"Name: {name}")
        selected_types = [
            value for value, variable in self.card_type_vars.items() if variable.get()]
        if selected_types:
            parts.append("Type: " + "/".join(selected_types))
        properties = [
            value for value, variable in self.property_vars.items() if variable.get()]
        if properties:
            parts.append("Supertypes: " + "/".join(properties))
        colors = [value for value, variable in self.color_vars.items() if variable.get()]
        if colors:
            parts.append(f"Colors ({self.q_color_mode.get()}): " + "".join(colors))
        produces = [value for value, variable in self.produces_vars.items()
                    if variable.get()]
        if produces:
            parts.append(
                f"Produces ({self.q_produces_mode.get()}): " + "".join(produces))
        properties = self._selected_trait_keys()
        if properties:
            parts.append("Card traits: " + ", ".join(
                TRAIT_LABELS.get(key, key) for key in properties))
        for label, low, high in (
                ("Loyalty", "q_loyalty_min", "q_loyalty_max"),
                ("Defense", "q_defense_min", "q_defense_max"),
                ("Released", "q_released_min", "q_released_max")):
            bounds = [str(getattr(self, name).get()).strip()
                      for name in (low, high)
                      if getattr(self, name, None) is not None]
            bounds = [value for value in bounds if value]
            if bounds:
                parts.append(f"{label}: " + "-".join(bounds))
        artist = (str(self.q_artist.get()).strip()
                  if getattr(self, "q_artist", None) is not None else "")
        if artist:
            parts.append(f"Artist: {artist}")
        if self._selected_keywords:
            parts.append("Mechanics: " + ", ".join(sorted(self._selected_keywords)))
        rules = self.q_rules.values(commit_pending=False) if hasattr(self, "q_rules") else []
        if rules:
            parts.append("Text: " + ", ".join(rules))
        if self.q_format.get():
            parts.append("Format: " + self.q_format.get())
        if self._selected_rarities:
            parts.append("Rarity: " + ", ".join(sorted(self._selected_rarities)))
        for label_text, lo_widget, hi_widget in (
            ("MV", self.q_cmc_min, self.q_cmc_max),
            ("Power", self.q_power_min, self.q_power_max),
            ("Toughness", self.q_toughness_min, self.q_toughness_max),
        ):
            lo, hi = lo_widget.get().strip(), hi_widget.get().strip()
            if lo or hi:
                parts.append(f"{label_text}: {lo or '…'}–{hi or '…'}")
        content = self._selected_content_types()
        if content != {"card"}:
            pretty = {
                "card": "Cards", "token": "Tokens",
                "emblem": "Emblems", "art": "Art Series",
            }
            parts.append("Content: " + ", ".join(
                pretty[key] for key in self.content_vars if key in content))
        if self._selected_subtypes:
            parts.append("Subtype: " + ", ".join(sorted(self._selected_subtypes)))
        try:
            if not self._search_printings.is_default_selection():
                parts.append("Printings: " + self._search_printings.button.cget("text"))
        except Exception:
            pass
        label.configure(text="Active filters: " + ("  •  ".join(parts) if parts else "None"))


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
            "rules": self.q_rules.values(commit_pending=False),
            "rules_pending": self.q_rules.entry.get(),
            "rules_mode": self.q_rules_mode.get(),
            "card_type_mode": self.q_card_type_mode.get(),
            "supertype_mode": self.q_supertype_mode.get(),
            "subtype_mode": self.q_subtype_mode.get(),
            "keyword_mode": self.q_keyword_mode.get(),
            "color_mode": self.q_color_mode.get(),
            "produces_mode": self.q_produces_mode.get(),
            "optional_filters": list(self._active_optional_filters()),
            "optional_values": self._capture_optional_filter_values(),
            "cost_features": sorted(
                key for key, variable
                in (getattr(self, "cost_feature_vars", {}) or {}).items()
                if variable.get()),
            "traits": sorted(
                getattr(self, "_selected_traits", set()) or ()),
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
            "paper_only": bool(self._search_printings.paper_only.get()),
            "set_types": set_types,
            "set_codes": set_codes,
            "advanced_visible": bool(self._advanced_filters_visible),
            "result_sort": [self._sort_col, bool(self._sort_desc)],
            "result_filters": self._table_filters.get("results", {}),
            "had_results": bool(self._result_store.logical_count),
            "selected_result_id": selected_result_id,
        }


    @staticmethod
    def _set_search_entry_text(widget, value):
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
        self.q_rules.clear()
        for phrase in state.get("rules", []):
            self.q_rules.add(phrase)
        pending = str(state.get("rules_pending") or "")
        if pending:
            self.q_rules.entry.insert(0, pending)

        saved_content = [
            "card" if str(value) == "deck" else str(value)
            for value in state.get("content", ["card"])
        ]
        content = {
            value for value in saved_content if value in self.content_vars
        } or {"card"}
        for key, variable in self.content_vars.items():
            variable.set(key in content)

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
        self._search_printings.restore_selection(
            state.get("set_types", []),
            state.get("set_codes", []),
            paper_only=bool(state.get("paper_only", True)))

        safe_modes = (
            (self.q_rules_mode, state.get("rules_mode"), {"all", "any"}, "all"),
            (self.q_card_type_mode, state.get("card_type_mode"), {"all", "any"}, "any"),
            (self.q_supertype_mode, state.get("supertype_mode", state.get("characteristic_mode")),
             {"all", "any"}, "all"),
            (self.q_subtype_mode, state.get("subtype_mode"), {"all", "any"}, "any"),
            (self.q_keyword_mode, state.get("keyword_mode"), {"all", "any"}, "any"),
            (self.q_color_mode, state.get("color_mode"), {"within", "includes", "exact"}, "within"),
            (self.q_produces_mode, state.get("produces_mode"),
             {"within", "includes", "exact"}, "includes"),
        )
        for variable, value, allowed, default in safe_modes:
            variable.set(value if value in allowed else default)

        wanted_colors = {str(value) for value in state.get("colors", [])}
        for key, variable in self.color_vars.items():
            variable.set(key in wanted_colors)

        wanted_produces = {str(value) for value in state.get("produces", [])}
        for key, variable in self.produces_vars.items():
            variable.set(key in wanted_produces)

        # Rebuild the optional rows before restoring their values, so a
        # restored session shows the same panel it was saved with.
        self._clear_optional_filters()
        for key in ordered_active_filters(
                state.get("optional_filters", []) or ()):
            self._add_optional_filter(key, notify=False)
        self._selected_traits = {
            str(value) for value in state.get("traits", []) or ()
            if str(value) in TRAIT_LABELS}
        if getattr(self, "_traits_btn", None) is not None:
            self._traits_btn.configure(text=self._picker_button_text(
                {TRAIT_LABELS[key] for key in self._selected_traits},
                "Any", "traits", max_visible=10, single_line=True))
        self._restore_optional_filter_values(state.get("optional_values", {}))
        wanted_costs = {str(value) for value in state.get("cost_features", []) or ()}
        for key, variable in (getattr(self, "cost_feature_vars", {}) or {}).items():
            variable.set(key in wanted_costs)

        for widget, key in (
            (self.q_cmc_min, "cmc_min"), (self.q_cmc_max, "cmc_max"),
            (self.q_power_min, "power_min"), (self.q_power_max, "power_max"),
            (self.q_toughness_min, "toughness_min"),
            (self.q_toughness_max, "toughness_max"),
        ):
            self._set_search_entry_text(widget, state.get(key))
        self.english_only.set(bool(state.get("english_only", True)))

        wants_advanced = bool(state.get("advanced_visible", False))
        if wants_advanced != self._advanced_filters_visible:
            self._toggle_advanced_filters()

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
        """Parse one optional numeric Search field with a user-facing error."""
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
            numeric = {
                "cmc_min": self._parse_search_number(self.q_cmc_min.get(), "Mana value minimum"),
                "cmc_max": self._parse_search_number(self.q_cmc_max.get(), "Mana value maximum"),
                "power_min": self._parse_search_number(self.q_power_min.get(), "Power minimum"),
                "power_max": self._parse_search_number(self.q_power_max.get(), "Power maximum"),
                "toughness_min": self._parse_search_number(self.q_toughness_min.get(), "Toughness minimum"),
                "toughness_max": self._parse_search_number(self.q_toughness_max.get(), "Toughness maximum"),
            }
            self._validate_search_range("Mana value", numeric["cmc_min"], numeric["cmc_max"])
            self._validate_search_range("Power", numeric["power_min"], numeric["power_max"])
            self._validate_search_range("Toughness", numeric["toughness_min"], numeric["toughness_max"])
        except ValueError as exc:
            self.results_count_lbl.configure(text="RESULTS | Invalid search filter")
            self._status(str(exc))
            messagebox.showerror("Invalid Search Filter", str(exc))
            return

        name_filter, exact_names = self._effective_name_filters()
        search_args = dict(
            name=name_filter, names=exact_names,
            text=list(self.q_rules.values()), text_mode=self.q_rules_mode.get(),
            card_types=[value for value, variable in self.card_type_vars.items() if variable.get()],
            card_type_mode=self.q_card_type_mode.get(),
            supertypes=[value for value, variable in self.property_vars.items() if variable.get()],
            supertype_mode=self.q_supertype_mode.get(),
            subtypes=sorted(self._selected_subtypes), subtype_mode=self.q_subtype_mode.get(),
            keywords=sorted(self._selected_keywords), keyword_mode=self.q_keyword_mode.get(),
            colors=[value for value, variable in self.color_vars.items() if variable.get()],
            color_mode=self.q_color_mode.get(),
            produces=[value for value, variable in self.produces_vars.items()
                      if variable.get()],
            produces_mode=self.q_produces_mode.get(),
            traits=self._selected_trait_keys(),
            loyalty_min=self._optional_numeric(getattr(self, "q_loyalty_min", None)),
            loyalty_max=self._optional_numeric(getattr(self, "q_loyalty_max", None)),
            defense_min=self._optional_numeric(getattr(self, "q_defense_min", None)),
            defense_max=self._optional_numeric(getattr(self, "q_defense_max", None)),
            released_from=self._optional_numeric(getattr(self, "q_released_min", None)),
            released_to=self._optional_numeric(getattr(self, "q_released_max", None)),
            artist=(str(self.q_artist.get()).strip()
                    if getattr(self, "q_artist", None) is not None else ""),
            cmc_min=numeric["cmc_min"], cmc_max=numeric["cmc_max"],
            power_min=numeric["power_min"], power_max=numeric["power_max"],
            toughness_min=numeric["toughness_min"], toughness_max=numeric["toughness_max"],
            rarities=sorted(self._selected_rarities),
            fmt=self.q_format.get().strip(),
            set_codes=sorted(self._search_printings.selected_set_codes()) or None,
            set_types=sorted(self._search_printings.selected_set_types()) or None,
            lang=("en" if self.english_only.get() else ""),
            paper_only=bool(self._search_printings.paper_only.get()),
            content_types=sorted(self._selected_content_types()),
        )
        criteria = SearchCriteria.from_mapping(search_args)
        start = self.search_controller.start(criteria)
        if start.kind == "busy":
            self._pending_search_request = True
            return
        if start.kind == "unchanged":
            self._set_result_count()
            return
        if start.kind == "cached":
            self._set_result_store(start.results, start.signature)
            self._render_results()
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
        self._resume_pending_search_request()
