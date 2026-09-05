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
CHIP_GRID_X_GAP = 4
SEARCH_ROW_PADY = 3
# Keeps every advanced filter label on the same x-position as the standard
# rows above them, so the control column does not step in and out.
FILTER_LABEL_WIDTH = 76
FORMAT_STATUS_CHOICES = (
    ("Playable", "playable"), ("Banned", "banned"),
    ("Restricted", "restricted"),
)
# Magic's first set through a little beyond the current printing horizon.
RELEASE_YEAR_FIRST = 1993
RELEASE_YEAR_LAST = datetime.date.today().year + 2
# Content kinds live here now: they are yes/no facts about what an object is,
# and a separate Content row for four checkboxes was a filter of its own.
CONTENT_TRAIT_KEYS = {
    "include_tokens": "token",
    "include_emblems": "emblem",
    "include_art_series": "art",
}
TRAIT_CHOICES = (
    ("include_tokens", "Include Tokens"),
    ("include_emblems", "Include Emblems"),
    ("include_art_series", "Include Art Series"),
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
    ("variable_stats", "Variable power or toughness (*)"),
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

    def _initialize_search_filter_state(self):
        """Create every filter's state before any of its widgets exist.

        Optional filters are destroyed when removed, so their state cannot live
        in the widget. Variables and selection sets are made once here and the
        builders bind to them, which lets a row be removed and re-added without
        losing what the user chose. Text-backed controls have no variable, so
        they shadow their value through _capture_advanced_filter_values.
        """
        self.card_type_vars = {}
        self._card_type_catalog = []
        self._card_type_chip_widgets = ()
        self._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
        self.q_card_type_mode = tk.StringVar(value="any")

        self.property_vars = {}
        self._property_catalog = []
        # Any, like every other multi-select. Only 17 cards in the whole
        # paper pool carry two supertypes, so an "all" default silently
        # emptied any two-value selection.
        self.q_supertype_mode = tk.StringVar(value="any")

        self.color_vars = {}
        self.q_color_mode = tk.StringVar(value="within")
        self.q_color_scope = tk.StringVar(value="identity")
        self._color_mode_label = None
        self._colorless_check = None
        self.produces_vars = {}
        self.q_produces_mode = tk.StringVar(value="includes")

        self._selected_keywords = set()
        self._keyword_catalog = []
        self.q_keyword_mode = tk.StringVar(value="any")
        self._selected_subtypes = set()
        self._subtype_catalog = []
        self.q_subtype_mode = tk.StringVar(value="any")
        self._selected_rarities = set()
        self.q_format = tk.StringVar(value="")
        # Playable is legal-or-restricted. Banned and restricted are the
        # states a deck check asks about and nothing could previously reach.
        self.q_format_status = tk.StringVar(value="playable")
        self.q_rules_mode = tk.StringVar(value="all")
        self._selected_traits = set()
        # Traits combine with Any by default, like Subtype and Mechanics.
        # Requiring all of them made two selections return nothing.
        self.q_trait_mode = tk.StringVar(value="any")
        self._selected_layouts = set()
        self.q_layout_mode = tk.StringVar(value="any")
        self._layout_catalog = []
        self._card_shape_btn = None
        self.pip_vars = {}
        self.q_pip_min = None
        self.q_print_min = None
        self.q_print_max = None
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
        self._traits_btn = None
        self._property_chip_frame = None
        self._supertype_empty_text = ""
        for name in (
                "q_cmc_min", "q_cmc_max", "q_power_min", "q_power_max",
                "q_toughness_min", "q_toughness_max",
                "q_loyalty_min", "q_loyalty_max",
                "q_defense_min", "q_defense_max",
                "q_released_min", "q_released_max"):
            setattr(self, name, None)

    def _build_search_pane(self, parent):
        form = ttk.Frame(parent)
        form.pack(fill="x")
        form.columnconfigure(0, minsize=76)
        form.columnconfigure(1, weight=1, uniform="search_control")
        form.columnconfigure(2, minsize=76)
        form.columnconfigure(3, weight=1, uniform="search_control")
        self._initialize_search_filter_state()
        # The standard set, in the order a search is usually built: what the
        # card is called, what it is, what colour it is, how big it is, and
        # which printings are in scope.
        self._build_name_filter(form)
        self._build_card_type_filters(form)
        self._build_color_filters(form)
        self._build_standard_stats_filter(form, row=4)
        self._build_printing_filter(form, row=6)
        self._build_advanced_filter_zone(parent)
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
        name_label = ttk.Label(form, text="Card Name")
        name_label.grid(row=0, column=0, sticky="w",
                        padx=(0, 8), pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(name_label, "name")
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

    def _build_card_type_filters(self, form):
        type_label = ttk.Label(form, text="Card type")
        type_label.grid(
            row=1, column=0, sticky="nw", padx=(0, 8), pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(type_label, "card_type")
        typebox = ttk.Frame(form)
        typebox.grid(row=1, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
        self._card_type_chip_frame = ttk.Frame(typebox)
        self._card_type_chip_frame.pack(fill="x")
        self._card_type_chip_frame.bind(
            "<Configure>", self._layout_card_type_chips, add="+")
        self._build_mode_row(
            typebox, "Selected types:", self.q_card_type_mode, "card types",
            meanings={
                "any": "Any: the card must have at least one selected Card Type.",
                "all": ("All: the card must have every selected Card Type across "
                        "its full type line, including multiple faces."),
                "none": ("None: exclude every card having any selected Card Type, "
                         "which is how to ask for a green non-creature."),
            })


    def _build_color_filters(self, form):
        color_label = ttk.Label(form, text="Colors")
        color_label.grid(
            row=3, column=0, sticky="nw", padx=(0, 8), pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(color_label, "colors")
        colorwrap = ttk.Frame(form)
        colorwrap.grid(row=3, column=1, columnspan=3, sticky="ew", pady=SEARCH_ROW_PADY)
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
            if self.pips.get(c):
                kw["image"] = self.pips[c]
                kw["compound"] = "left"
            check = ttk.Checkbutton(colorbox, **kw)
            check.pack(side="left", padx=(0, 8))
            if c == "C":
                self._colorless_check = check
                self._add_tooltip(
                    check,
                    "Colorless: cards with no colors at all. A card cannot be "
                    "colorless and also a color, so this clears itself when "
                    "you pick one. To find what makes colorless mana, use the "
                    "Produces filter instead.",
                    wraplength=380)
        scope = ttk.Frame(colorwrap)
        scope.pack(fill="x", pady=(2, 0))
        ttk.Label(scope, text="Look at:", style="Muted.TLabel").pack(side="left")
        scope_help = {
            "identity": (
                "Color identity: every color the card brings to a deck, from "
                "its cost, its rules text and both faces. This is the one "
                "Commander uses."),
            "colors": (
                "Card colors: the colors the card itself is, from its cost "
                "and any color indicator. Devoid cards and lands are "
                "colorless here even when their identity is not."),
        }
        for label, value in (("Color identity", "identity"),
                             ("Card colors", "colors")):
            radio = ttk.Radiobutton(
                scope, text=label, variable=self.q_color_scope, value=value,
                style="FormChoice.TRadiobutton",
                command=self._on_color_scope_changed)
            radio.pack(side="left", padx=(3, 0))
            self._add_tooltip(radio, scope_help[value], wraplength=390)
        colormode = ttk.Frame(colorwrap)
        colormode.pack(fill="x", pady=(2, 0))
        self._color_mode_label = ttk.Label(
            colormode, text="Color identity:", style="Muted.TLabel")
        self._color_mode_label.pack(side="left")
        color_mode_help = {
            "within": "Within: the card's entire color identity must fit inside the selected colors.",
            "includes": "Contains: the card must contain every selected color but may contain others.",
            "exact": "Exactly: the card's color identity must exactly equal the selected colors.",
        }
        for label, value in (("Within", "within"), ("Contains", "includes"),
                             ("Exactly", "exact")):
            radio = ttk.Radiobutton(
                colormode, text=label, variable=self.q_color_mode, value=value,
                style="FormChoice.TRadiobutton",
                command=self._update_search_filter_summary)
            radio.pack(side="left", padx=(3, 0))
            self._add_tooltip(radio, color_mode_help[value], wraplength=390)
        self._sync_colorless_availability()


    COLOR_SCOPE_LABELS = {
        "identity": "Color identity:", "colors": "Card colors:"}

    def _on_color_scope_changed(self):
        """Keep the mode row naming whichever column is being compared."""
        label = getattr(self, "_color_mode_label", None)
        if label is not None:
            label.configure(text=self.COLOR_SCOPE_LABELS.get(
                self.q_color_scope.get(), "Color identity:"))
        self._update_search_filter_summary()

    def _sync_colorless_availability(self):
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
                check.state(["disabled"] if colored else ["!disabled"])
            except tk.TclError:
                pass
        self._update_search_filter_summary()

    def _build_produces_filter(self, parent, row):
        """Mana a card can actually produce, distinct from its colour identity.

        Deliberately a twin of the Colors control: the same pip checkboxes and
        the same within/contains/exactly modes, because the two filters read
        the same comma-joined WUBRG(+C) encoding. Only the default mode differs
        -- "contains" answers the mana-base question people actually ask.
        """
        wrap = ttk.Frame(parent)
        wrap.grid(row=row, column=1, sticky="ew", pady=2)
        box = ttk.Frame(wrap)
        box.pack(fill="x")
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
        help_text = {
            "within": "Within: everything the card produces must fit inside the selected colors.",
            "includes": "Contains: the card must produce every selected color and may produce others.",
            "exact": "Exactly: the card must produce exactly the selected colors.",
        }
        for label, value in (("Within", "within"), ("Contains", "includes"),
                             ("Exactly", "exact")):
            radio = ttk.Radiobutton(
                mode, text=label, variable=self.q_produces_mode, value=value,
                style="FormChoice.TRadiobutton",
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

    def _build_rules_text_filter(self, form, *, row, advanced=False):
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
        mode_box = ttk.Frame(rules_box)
        mode_box.grid(row=1, column=0, sticky="w", pady=(1, 0))
        self._build_mode_row(
            mode_box, "Rules text:", self.q_rules_mode, "chips",
            meanings={
                "any": "Any: at least one Rules Text chip must match the card.",
                "all": "All: every Rules Text chip must match the card.",
                "none": ("None: exclude every card matching any Rules Text "
                         "chip, which finds cards that never mention a word."),
            })


    # ------------------------------------------------------------------
    # advanced filter controls
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

        def apply(chosen):
            previous_content = self._content_types_from_traits()
            self._selected_traits = {
                str(key) for key in chosen if key in TRAIT_LABELS}
            if self._traits_btn is not None:
                self._traits_btn.configure(text=self._picker_button_text(
                    {TRAIT_LABELS[key]
                     for key in self._selected_traits},
                    "Any", "traits", max_visible=10, single_line=True))
            # Tokens, Emblems and Art Series widen which objects the search
            # covers, so the vocabulary every other picker offers has to be
            # rebuilt for the new scope. Without this the Subtype, Card type
            # and Set lists kept describing cards only.
            if self._content_types_from_traits() != previous_content:
                self._on_content_filter_change()
                return
            self._update_search_filter_summary()

        selected = {
            key for key in getattr(self, "_selected_traits", set()) or ()
            if key in TRAIT_LABELS}
        self._open_search_multi_picker(
            "Card Traits",
            # The first three choose what the search covers rather than adding
            # a condition, so the Any/All/None row below cannot apply to them.
            # Grouping them says so, in the same "group - value" form the
            # Mechanics and Subtype pickers already use.
            [(key, ("Scope · " if key in CONTENT_TRAIT_KEYS else "Trait · ") + label)
             for key, label in TRAIT_CHOICES],
            selected, apply,
            mode_var=self.q_trait_mode,
            mode_label="Selected traits:",
            help_text=(
                "Choose one or several card traits. Scope choices add whole "
                "kinds of object to the search and ignore the Any/All/None "
                "row; every Trait below them is a condition it governs."))

    LAYOUT_LABELS = {
        "modal_dfc": "Modal double-faced",
        "double_faced_token": "Double-faced token",
        "art_series": "Art series",
        "reversible_card": "Reversible",
        "transform": "Transforming",
    }

    def _layout_display_name(self, value):
        """Readable name for one Scryfall layout key.

        An unmapped key is title-cased rather than hidden: a shape this build
        has never seen must still be selectable.
        """
        key = str(value or "").strip()
        return self.LAYOUT_LABELS.get(
            key.casefold(), key.replace("_", " ").capitalize())

    def _build_filter_card_shape(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=2)
        self._card_shape_btn = AppButton(
            box, text=self._picker_button_text(
                {self._layout_display_name(value)
                 for value in self._selected_layouts},
                "Any", "shapes", max_visible=10, single_line=True),
            role="picker", command=self._choose_card_shapes)
        self._card_shape_btn.pack(fill="x")
        self._build_mode_row(
            box, "Selected shapes:", self.q_layout_mode, "shapes",
            meanings={
                "any": "Any: the card is printed in one of the selected shapes.",
                "none": ("None: exclude every card printed in a selected "
                         "shape, which is how to search ordinary cards only."),
            },
            choices=self.ANY_NONE_CHOICES)

    def _reset_filter_card_shape(self):
        self._selected_layouts = set()
        self.q_layout_mode.set("any")
        self._card_shape_btn = None

    def _choose_card_shapes(self):
        def apply(chosen):
            self._selected_layouts = {
                str(value) for value in chosen
                if str(value) in {key for key, _count in self._layout_catalog}}
            self._set_picker_text(
                self._card_shape_btn,
                self._picker_button_text(
                    {self._layout_display_name(value)
                     for value in self._selected_layouts},
                    "Any", "shapes", max_visible=10, single_line=True))
            self._update_search_filter_summary()

        self._open_search_multi_picker(
            "Choose Card Shapes",
            [(key, f"{self._layout_display_name(key)} · {count:,}")
             for key, count in self._layout_catalog],
            set(self._selected_layouts), apply,
            mode_var=self.q_layout_mode,
            mode_label="Selected shapes:",
            mode_choices=self.ANY_NONE_CHOICES,
            help_text=(
                "Choose one or several printed shapes. The count beside each "
                "one is how many printings currently have it."))

    def _build_filter_mana_pips(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=2)
        pips = ttk.Frame(box)
        pips.pack(fill="x")
        for color in (*COLORS, "C"):
            variable = tk.BooleanVar(value=False)
            self.pip_vars[color] = variable
            kw = {"text": " " + MANA_NAMES[color], "variable": variable,
                  "style": "Color.TCheckbutton",
                  "command": self._update_search_filter_summary}
            if self.pips.get(color):
                kw["image"] = self.pips[color]
                kw["compound"] = "left"
            ttk.Checkbutton(pips, **kw).pack(side="left", padx=(0, 8))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(2, 0))
        ttk.Label(row, text="At least:", style="Muted.TLabel").pack(side="left")
        self.q_pip_min = AppSpinbox(row, from_=1, to=9, width=3)
        self.q_pip_min.pack(side="left", padx=(5, 0))
        self.q_pip_min.delete(0, "end")
        self.q_pip_min.insert(0, "1")
        ttk.Label(
            row, text="of each selected color", style="Muted.TLabel").pack(
                side="left", padx=(5, 0))

    def _reset_filter_mana_pips(self):
        for variable in self.pip_vars.values():
            variable.set(False)
        self.pip_vars = {}
        self.q_pip_min = None

    def _build_filter_print_count(self, parent):
        box = self._numeric_pair(parent, "q_print", width=4)
        box.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(box, text="sets", style="Muted.TLabel").pack(
            side="left", padx=(5, 0))

    def _reset_filter_print_count(self):
        self.q_print_min = None
        self.q_print_max = None

    def _build_filter_loyalty(self, parent):
        self._numeric_pair(parent, "q_loyalty").grid(
            row=0, column=1, sticky="w", pady=2)

    def _reset_filter_loyalty(self):
        self.q_loyalty_min = None
        self.q_loyalty_max = None

    def _build_filter_defense(self, parent):
        self._numeric_pair(parent, "q_defense").grid(
            row=0, column=1, sticky="w", pady=2)

    def _reset_filter_defense(self):
        self.q_defense_min = None
        self.q_defense_max = None

    def _build_filter_released(self, parent):
        """Year pickers rather than spinners.

        A release year is chosen from a known list, not dialled to; a spinner
        invites holding an arrow through thirty years of Magic.
        """
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="w", pady=2)
        years = [""] + [str(year) for year in
                        range(RELEASE_YEAR_LAST, RELEASE_YEAR_FIRST - 1, -1)]
        self.q_released_min = AppCombobox(
            box, values=years, width=7, state="readonly")
        self.q_released_min.set("")
        self.q_released_min.pack(side="left")
        ttk.Label(box, text="to", style="Muted.TLabel").pack(
            side="left", padx=5)
        self.q_released_max = AppCombobox(
            box, values=years, width=7, state="readonly")
        self.q_released_max.set("")
        self.q_released_max.pack(side="left")
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
        "q_released_min", "q_released_max",
        "q_print_min", "q_print_max", "q_pip_min",
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

    def _selected_trait_keys(self):
        """Card traits that contribute a clause, excluding content kinds.

        Tokens, Emblems and Art Series choose which objects the search covers
        through content_types; treating them as clauses as well would filter
        the very rows they just admitted.
        """
        keys = set(getattr(self, "_selected_traits", set()) or ())
        return tuple(sorted(keys - set(CONTENT_TRAIT_KEYS)))

    # ------------------------------------------------------------------
    # advanced filters, built once and revealed together
    # ------------------------------------------------------------------

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
        header.pack(fill="x", pady=(6, 0))
        self._advanced_btn = AppButton(
            header, text=self.ADVANCED_COLLAPSED_TEXT, role="dense",
            command=self._toggle_advanced_filters)
        self._advanced_btn.pack(side="left")
        self._add_tooltip(
            self._advanced_btn,
            "Every filter beyond the standard set, grouped by what it asks "
            "about. They stay where you leave them, so a filter you use often "
            "is one click away rather than one search away.",
            wraplength=360)

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
            heading.pack(fill="x", anchor="w", pady=(8, 2))
            for entry in entries:
                key = entry["key"]
                builder = getattr(self, f"_build_filter_{key}", None)
                if builder is None:
                    continue
                frame = ttk.Frame(self._advanced_host)
                frame.pack(fill="x")
                frame.columnconfigure(0, minsize=FILTER_LABEL_WIDTH)
                frame.columnconfigure(1, weight=1)
                label = ttk.Label(frame, text=entry["label"])
                label.grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=2)
                self._add_tooltip(label, entry["tooltip"], wraplength=380)
                builder(frame)
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
            parent, text=self.q_format.get() or "Any", role="picker",
            command=self._choose_format)
        self._format_btn.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_format(self):
        self.q_format.set("")
        self.q_format_status.set("playable")
        self._format_btn = None

    def _build_filter_rarity(self, parent):
        self._rarity_btn = AppButton(
            parent, text=self._picker_button_text(
                self._selected_rarities, "Any", "rarities"),
            role="picker", command=self._choose_rarities)
        self._rarity_btn.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_rarity(self):
        self._selected_rarities = set()
        self._rarity_btn = None

    MODE_ROW_CHOICES = (("Any", "any"), ("All", "all"), ("None", "none"))
    # A card has exactly one shape, so All could only ever find nothing.
    # Offering a mode its values cannot satisfy is worse than offering fewer.
    ANY_NONE_CHOICES = (("Any", "any"), ("None", "none"))

    def _build_mode_row(self, parent, label, variable, noun, meanings=None,
                        choices=None):
        """One Any/All/None row, worded for the values it governs.

        The single construction point for these rows. Card Type built its own
        for a while and silently kept only Any and All when None was added
        everywhere else, which is the failure this helper exists to prevent.
        Callers may override the wording where they can say something more
        precise than the generic phrasing.
        """
        mode = ttk.Frame(parent)
        mode.pack(fill="x", pady=(2, 0))
        ttk.Label(mode, text=label, style="Muted.TLabel").pack(side="left")
        meanings = dict(meanings or {}) or {
            "any": f"Any: the card only needs one of the selected {noun}.",
            "all": f"All: the card must have every selected {noun}.",
            "none": f"None: exclude every card having any selected {noun}.",
        }
        for text, value in (choices or self.MODE_ROW_CHOICES):
            radio = ttk.Radiobutton(
                mode, text=text, variable=variable, value=value,
                style="FormChoice.TRadiobutton",
                command=self._update_search_filter_summary)
            radio.pack(side="left", padx=(3, 0))
            self._add_tooltip(radio, meanings[value], wraplength=390)
        return mode

    def _render_supertype_chips(self):
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
            empty_text=getattr(self, "_supertype_empty_text", ""))

    def _build_filter_supertypes(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="ew", pady=2)
        self._property_chip_frame = ttk.Frame(box)
        self._property_chip_frame.pack(fill="x")
        self._render_supertype_chips()
        self._build_mode_row(
            box, "Selected supertypes:", self.q_supertype_mode, "supertypes")

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
            role="picker", command=self._choose_keywords)
        self._keyword_btn.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_mechanics(self):
        self._selected_keywords = set()
        self.q_keyword_mode.set("any")
        self._keyword_btn = None

    def _build_filter_subtype(self, parent):
        self._subtype_btn = AppButton(
            parent, text=self._picker_button_text(
                self._selected_subtypes, "Any", "subtypes",
                max_visible=10, single_line=True),
            role="picker", command=self._choose_subtypes)
        self._subtype_btn.grid(row=0, column=1, sticky="ew", pady=2)

    def _reset_filter_subtype(self):
        self._selected_subtypes = set()
        self.q_subtype_mode.set("any")
        self._subtype_btn = None

    def _build_filter_mana_value(self, parent):
        pair = self._numeric_pair(parent, "q_cmc", width=5)
        pair.grid(row=0, column=1, sticky="w", pady=2)

    def _reset_filter_mana_value(self):
        self.q_cmc_min = None
        self.q_cmc_max = None

    def _build_filter_stats(self, parent):
        box = ttk.Frame(parent)
        box.grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(box, text="Power", style="Muted.TLabel").pack(
            side="left", padx=(0, 5))
        self._numeric_pair(box, "q_power").pack(side="left")
        ttk.Label(box, text="Toughness", style="Muted.TLabel").pack(
            side="left", padx=(14, 5))
        self._numeric_pair(box, "q_toughness").pack(side="left")

    def _reset_filter_stats(self):
        # Power/Toughness is a standard row, so its widgets outlive a Clear:
        # empty them rather than dropping the handles the form still holds.
        for name in ("q_power_min", "q_power_max",
                     "q_toughness_min", "q_toughness_max"):
            self._set_search_entry_text(getattr(self, name, None), "")


    def _build_standard_stats_filter(self, form, *, row):
        """Power / Toughness on the main form, styled like its neighbours."""
        label = ttk.Label(form, text="Power / Toughness")
        label.grid(row=row, column=0, sticky="w", padx=(0, 8),
                   pady=SEARCH_ROW_PADY)
        self._add_standard_filter_tooltip(label, "stats")
        holder = ttk.Frame(form)
        holder.grid(row=row, column=1, columnspan=3, sticky="ew",
                    pady=SEARCH_ROW_PADY)
        holder.columnconfigure(1, weight=1)
        self._build_filter_stats(holder)

    def _build_printing_filter(self, parent, *, row=0):
        self._search_printings = SearchPrintingFilter(self, parent, row=row)


    def _content_types_from_traits(self):
        """Content kinds requested through Card traits.

        Cards are always searched; Tokens, Emblems and Art Series are separate
        printed objects that stay out until asked for, which is what DATA-008
        requires of Art Series in particular.
        """
        selected = set(getattr(self, "_selected_traits", set()) or ())
        kinds = {"card"}
        for key, kind in CONTENT_TRAIT_KEYS.items():
            if key in selected:
                kinds.add(kind)
        return kinds

    def _selected_content_types(self):
        return self._content_types_from_traits()

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
        self.q_color_mode.set("within")
        self.q_color_scope.set("identity")
        self.q_produces_mode.set("includes")
        self.q_trait_mode.set("any")
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
        self._search_printings.clear()
        # Clearing can shorten what the rows display, so the Results viewport
        # would otherwise stay scrolled to wherever the taller panel had left
        # it. Return it to the first row along with the criteria.
        self._reset_results_viewport()
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
        """Content changed through Card traits; rebuild the scoped vocabulary."""
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
            "Choose Subtypes", self._subtype_catalog, self._selected_subtypes, apply,
            mode_var=self.q_subtype_mode,
            mode_label="Selected subtypes:",
            help_text="Choose one or several card subtypes.")


    def _choose_keywords(self):
        def apply(chosen):
            self._selected_keywords = set(chosen)
            if self._keyword_btn is not None:
                self._keyword_btn.configure(text=self._picker_button_text(
                    self._selected_keywords, "Any", "mechanics",
                    max_visible=10, single_line=True))
            self._update_search_filter_summary()
        self._open_search_multi_picker(
            "Choose Mechanics", self._keyword_catalog, self._selected_keywords, apply,
            mode_var=self.q_keyword_mode,
            mode_label="Selected mechanics:",
            help_text="Choose one or several card mechanics.")


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
        return (
            [("", "Any")] + [(fmt, format_display_name(fmt)) for fmt in values],
            {current} if current in values else {""},
        )

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

        self._open_search_multi_picker(
            "Choose Format",
            [("", "Any")] + [
                (fmt, format_display_name(fmt))
                for fmt in self._format_catalog_for_status()],
            selected,
            apply,
            help_text=(
                "Choose a format, then the legality it must have in it. "
                "Only formats that have cards in the chosen state are listed."),
            single_select=True,
            mode_var=self.q_format_status,
            mode_label="Legality:",
            mode_choices=FORMAT_STATUS_CHOICES,
            mode_command=self._rescope_format_choices)

    def _choose_rarities(self):
        def apply(chosen):
            self._selected_rarities = set(chosen)
            labels = {r.capitalize() for r in chosen}
            if self._rarity_btn is not None:
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
        self._supertype_empty_text = supertype_empty_text
        self._render_supertype_chips()
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
        valid_layouts = {value for value, _count in self._layout_catalog}
        self._selected_layouts = {
            value for value in getattr(self, "_selected_layouts", set()) or ()
            if value in valid_layouts}
        self._set_picker_text(
            getattr(self, "_card_shape_btn", None),
            self._picker_button_text(
                {self._layout_display_name(value)
                 for value in self._selected_layouts},
                "Any", "shapes", max_visible=10, single_line=True))
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
        """The seam every filter control calls when its value changes.

        There is deliberately no aggregate "Active filters" line: each picker
        summarizes itself on its own button, which is where the user is
        looking. This method kept building that line for a long time after the
        label it wrote to stopped existing -- ninety lines that ran on every
        checkbox click and could not change anything a user saw. The seam is
        worth keeping and the body is not.
        """
        return None


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
            "trait_mode": self.q_trait_mode.get(),
            "format_status": self.q_format_status.get(),
            "advanced_expanded": bool(getattr(self, "_advanced_expanded", False)),
            "advanced_values": self._capture_advanced_filter_values(),
            "traits": sorted(
                getattr(self, "_selected_traits", set()) or ()),
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
        # Content is expressed as Card traits now, so a saved content list is
        # restored by selecting the traits that produce it.
        content = {
            value for value in saved_content
            if value in ("card", "token", "emblem", "art")
        } or {"card"}
        traits = set(getattr(self, "_selected_traits", set()) or ())
        for trait_key, kind in CONTENT_TRAIT_KEYS.items():
            traits.discard(trait_key)
            if kind in content:
                traits.add(trait_key)
        self._selected_traits = traits

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
            (self.q_trait_mode, state.get("trait_mode"),
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
        self._selected_traits = {
            str(value) for value in state.get("traits", []) or ()
            if str(value) in TRAIT_LABELS}
        if getattr(self, "_traits_btn", None) is not None:
            self._traits_btn.configure(text=self._picker_button_text(
                {TRAIT_LABELS[key] for key in self._selected_traits},
                "Any", "traits", max_visible=10, single_line=True))
        # Workspaces written before the standard/advanced split named this
        # key "optional_values"; the values inside it never changed.
        self._restore_advanced_filter_values(
            state.get("advanced_values", state.get("optional_values", {})))
        # Produces is restored here rather than with Colors: its checkboxes
        # belong to an advanced row, so anything set before the rows are
        # rebuilt is discarded along with the widgets that held it.
        wanted_produces = {str(value) for value in state.get("produces", [])}
        for key, variable in self.produces_vars.items():
            variable.set(key in wanted_produces)
        # Card shape and Colored pips own row-built state for the same reason.
        self._selected_layouts = {
            str(value) for value in state.get("layouts", []) or ()}
        self._set_picker_text(
            getattr(self, "_card_shape_btn", None),
            self._picker_button_text(
                {self._layout_display_name(value)
                 for value in self._selected_layouts},
                "Any", "shapes", max_visible=10, single_line=True))
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
            # An empty field means no restriction, so a spinbox that has
            # just been rebuilt reads as None rather than raising.
            numeric = {
                "cmc_min": self._numeric_field_value(getattr(self, "q_cmc_min", None)),
                "cmc_max": self._numeric_field_value(getattr(self, "q_cmc_max", None)),
                "power_min": self._numeric_field_value(getattr(self, "q_power_min", None)),
                "power_max": self._numeric_field_value(getattr(self, "q_power_max", None)),
                "toughness_min": self._numeric_field_value(
                    getattr(self, "q_toughness_min", None)),
                "toughness_max": self._numeric_field_value(
                    getattr(self, "q_toughness_max", None)),
            }
            optional_ranges = {
                "Loyalty": ("q_loyalty_min", "q_loyalty_max"),
                "Defense": ("q_defense_min", "q_defense_max"),
                "Released": ("q_released_min", "q_released_max"),
                "Printed in": ("q_print_min", "q_print_max"),
            }
            for label, (low, high) in optional_ranges.items():
                numeric[low] = self._numeric_field_value(getattr(self, low, None))
                numeric[high] = self._numeric_field_value(getattr(self, high, None))
            # Every pair of bounds is checked. Leaving these three out meant a
            # backwards range ran and returned nothing, with no way to tell
            # that apart from a search that genuinely matches no card -- and
            # Released is two dropdowns, so reversing it takes one click.
            self._validate_search_range("Mana value", numeric["cmc_min"], numeric["cmc_max"])
            self._validate_search_range("Power", numeric["power_min"], numeric["power_max"])
            self._validate_search_range("Toughness", numeric["toughness_min"], numeric["toughness_max"])
            for label, (low, high) in optional_ranges.items():
                self._validate_search_range(label, numeric[low], numeric[high])
            numeric["q_pip_min"] = self._numeric_field_value(
                getattr(self, "q_pip_min", None))
        except ValueError as exc:
            self.results_count_lbl.configure(text="RESULTS | Invalid search filter")
            self._status(str(exc))
            messagebox.showerror("Invalid Search Filter", str(exc))
            return

        name_filter, exact_names = self._effective_name_filters()
        search_args = dict(
            name=name_filter, names=exact_names,
            text=self._rules_text_values(), text_mode=self.q_rules_mode.get(),
            card_types=[value for value, variable in self.card_type_vars.items() if variable.get()],
            card_type_mode=self.q_card_type_mode.get(),
            supertypes=[value for value, variable in self.property_vars.items() if variable.get()],
            supertype_mode=self.q_supertype_mode.get(),
            subtypes=sorted(self._selected_subtypes), subtype_mode=self.q_subtype_mode.get(),
            keywords=sorted(self._selected_keywords), keyword_mode=self.q_keyword_mode.get(),
            colors=[value for value, variable in self.color_vars.items() if variable.get()],
            color_mode=self.q_color_mode.get(),
            color_scope=self.q_color_scope.get(),
            produces=[value for value, variable in self.produces_vars.items()
                      if variable.get()],
            produces_mode=self.q_produces_mode.get(),
            traits=self._selected_trait_keys(),
            trait_mode=self.q_trait_mode.get(),
            layouts=sorted(self._selected_layouts),
            layout_mode=self.q_layout_mode.get(),
            pips=[value for value, variable in self.pip_vars.items()
                  if variable.get()],
            pip_min=numeric["q_pip_min"],
            print_min=numeric["q_print_min"], print_max=numeric["q_print_max"],
            loyalty_min=numeric["q_loyalty_min"], loyalty_max=numeric["q_loyalty_max"],
            defense_min=numeric["q_defense_min"], defense_max=numeric["q_defense_max"],
            released_from=numeric["q_released_min"],
            released_to=numeric["q_released_max"],
            cmc_min=numeric["cmc_min"], cmc_max=numeric["cmc_max"],
            power_min=numeric["power_min"], power_max=numeric["power_max"],
            toughness_min=numeric["toughness_min"], toughness_max=numeric["toughness_max"],
            rarities=sorted(self._selected_rarities),
            fmt=self.q_format.get().strip(),
            fmt_status=self.q_format_status.get(),
            set_codes=sorted(self._search_printings.selected_set_codes()) or None,
            set_types=sorted(self._search_printings.selected_set_types()) or None,
            lang=("en" if self.english_only.get() else ""),
            paper_only=bool(self._search_printings.paper_only.get()),
            games=self._search_printings.selected_games(),
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
