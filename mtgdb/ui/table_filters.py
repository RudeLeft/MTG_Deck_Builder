"""Shared filtering behavior for Results, Mainboard, and Sideboard tables."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from mtgdb.ui.components import AppCombobox, ClassicButton, ClassicEntry
from mtgdb.ui.search_checklist import VirtualChecklistView
from mtgdb.ui.tokens import FILTER_PIP_SIZE, FONT_HELPER, FONT_HELPER_BOLD, PALETTE
from mtgdb.search.results import (
    COLOR_FILTER_GROUP_LABELS, COST_SYMBOL_GROUP_LABELS,
    row_passes_filters, table_value)
from mtgdb.ui.tables import TABLE_COLUMNS

try:
    from PIL import ImageTk
except Exception:
    ImageTk = None

# Cost groups that map to a single, recognizable mana symbol get a pip; the
# abstract classes (Generic/Hybrid/Phyrexian/No cost) read as plain text.
_COST_PIP_TOKENS = {
    "W": "W", "U": "U", "B": "B", "R": "R", "G": "G", "C": "C",
    "x": "X", "snow": "S",
}

# One short line under each column filter's header explaining its purpose.
# Collector # has no heading popup, so it needs no entry here.
COLUMN_FILTER_HELP = {
    "cost": "Filter by the mana symbols in a card's cost.",
    "qty": "Filter by how many copies are in the deck.",
    "name": "Filter by card name.",
    "type": "Filter by type line, like Creature or Instant.",
    "set": "Filter by the set a card comes from.",
    "year": "Filter by the year a card was released.",
    "ability": "Filter by keyword abilities, like Flying or Trample.",
    "rarity": "Filter by rarity — common, uncommon, rare, or mythic.",
    "cmc": "Filter by total mana value.",
    "power": "Filter by a creature's power.",
    "toughness": "Filter by a creature's toughness.",
    "colors": "Filter by a card's colors.",
    "rules": "Filter by words in the rules text.",
}


def _finite_bound(text):
    """Parse one numeric filter bound, rejecting non-finite values.

    Search already refuses these through math.isfinite, and accepting them here
    is actively misleading: a NaN bound compares False against every row, so the
    column shows an active filter that filters nothing at all. Infinities are
    refused for the same reason a user cannot mean them.
    """
    text = str(text or "").strip()
    if not text:
        return None
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{text} is not a finite number")
    return number


class TableFilterMixin:
    """Own shared Results/Mainboard/Sideboard table-filter behavior."""

    def _filter_kind(self, key):
        """Choose the most useful filter editor for a table column."""
        if key == "cost":
            # Mana cost is symbol-encoded, so free text ("what do I type?") is
            # useless. Offer a pip checkbox picker over the symbol groups.
            return "cost"
        if key == "colors":
            # Card colours are best picked as pips with Any/All/None rather than
            # ticking every comma-joined colour combination.
            return "colors"
        # Collector # has no heading popup at all (see _update_table_headings),
        # so no filter kind is needed for it.
        if key in ("qty", "cmc", "power", "toughness", "year"):
            return "numeric"
        if key in ("rarity", "set", "ability"):
            return "values"
        return "text"

    def _filter_display_value(self, card, key, qty=None):
        """Stable display value used by checklist/text filters.

        One lookup, shared with the matching in row_passes_filters. Holding a
        special case for Cost here while the matcher used table_value is what
        let the popup list costs the filter could never match.
        """
        return table_value(card, key, qty=qty)

    def _row_passes_table_filters(self, view, card, qty=None, skip_col=None):
        """True when a row satisfies all active filters for one table."""
        return row_passes_filters(
            card, self._table_filters.get(view, {}), qty=qty, skip_col=skip_col)

    def _table_source_rows(self, view, skip_col=None):
        """Rows available to a filter popup, respecting other active filters."""
        if view == "results":
            for card in self._result_store.rows:
                if self._row_passes_table_filters(
                        view, card, skip_col=skip_col):
                    yield card, None
            return
        board = "main" if view == "main" else "side"
        for entry in self.deck.entries(board):
            card, qty = entry["card"], entry["qty"]
            if self._row_passes_table_filters(
                    view, card, qty=qty, skip_col=skip_col):
                yield card, qty

    def _refresh_table_after_filter(self, view):
        if view == "results":
            self._render_results()
        else:
            self._refresh_single_deck_view(view)

    def _clear_table_filter(self, view, key=None):
        filters = self._table_filters[view]
        changed = bool(filters) if key is None else key in filters
        if key is None:
            filters.clear()
        else:
            filters.pop(key, None)
        if changed:
            self._refresh_table_after_filter(view)
        self._hide_filter_popup()

    def _hide_filter_popup(self):
        pop = self._filter_popup
        if pop is not None:
            try:
                if pop.winfo_exists():
                    pop.withdraw()
            except tk.TclError:
                self._filter_popup = None
        self._filter_popup_view = None
        self._filter_popup_col = None

    def _sort_from_filter_popup(self, view, key, descending):
        if view == "results":
            self._sort_col, self._sort_desc = key, descending
            self._render_results()
        else:
            self._deck_sorts[view] = [key, descending]
            self._refresh_single_deck_view(view)
        self._hide_filter_popup()

    def _show_table_filter(self, view, key):
        """Open the Excel-style smart filter for a clicked column heading."""
        pop, outer = self._create_table_filter_popup(view, key)
        self._build_filter_sort_controls(outer, view, key)

        kind = self._filter_kind(key)
        tk.Frame(outer, bg=PALETTE["border"], height=1).pack(
            fill="x", pady=(0, 7))

        current = self._table_filters[view].get(key, {})
        editor = tk.Frame(outer, bg=PALETTE["surface2"])
        editor.pack(fill="both", expand=True)
        if kind == "numeric":
            apply_filter = self._build_numeric_filter_editor(
                editor, view, key, current)
        elif kind == "cost":
            apply_filter = self._build_cost_filter_editor(
                editor, view, key, current)
        elif kind == "colors":
            apply_filter = self._build_colors_filter_editor(
                editor, view, key, current)
        elif kind == "text":
            apply_filter = self._build_text_filter_editor(
                editor, view, key, current)
        elif view == "results":
            # Keep the popup withdrawn until the complete vocabulary and fixed
            # physical row pool are ready. No visible "Preparing…" replacement.
            self._build_async_result_values_filter(
                outer, editor, pop, view, key, current)
            return
        else:
            apply_filter = self._build_values_filter_editor(
                editor, pop, view, key, current)

        self._build_filter_actions(outer, view, key, apply_filter)
        self._position_table_filter_popup(pop)

    def _create_table_filter_popup(self, view, key):
        self._hide_filter_popup()
        p = PALETTE
        pop = self._filter_popup
        try:
            exists = pop is not None and pop.winfo_exists()
        except tk.TclError:
            exists = False
        if not exists:
            pop = tk.Toplevel(self)
            pop.withdraw()
            pop.overrideredirect(True)
            pop.configure(bg=p["border"])
            pop.bind("<Escape>", lambda _event: self._hide_filter_popup())
            self._filter_popup = pop
        else:
            pop.withdraw()
            for child in pop.winfo_children():
                child.destroy()
        self._filter_popup_view = view
        self._filter_popup_col = key
        outer = tk.Frame(pop, bg=p["surface2"], padx=10, pady=9)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            outer, text=TABLE_COLUMNS[key]["label"].upper(),
            bg=p["surface2"], fg=p["accent"],
            font=FONT_HELPER_BOLD).pack(anchor="w")
        help_text = COLUMN_FILTER_HELP.get(key)
        if help_text:
            tk.Label(
                outer, text=help_text, bg=p["surface2"], fg=p["muted"],
                font=FONT_HELPER, justify="left", wraplength=320).pack(
                    anchor="w", pady=(1, 4))
        return pop, outer

    def _build_filter_sort_controls(self, outer, view, key):
        p = PALETTE
        sortrow = tk.Frame(outer, bg=p["surface2"])
        sortrow.pack(fill="x", pady=(6, 6))
        numeric = key in (
            "qty", "cmc", "power", "toughness", "year", "cost", "collector")
        asc = "Smallest → Largest" if numeric else "A → Z"
        desc = "Largest → Smallest" if numeric else "Z → A"
        ClassicButton(
            sortrow, text=asc, role="compact",
            command=lambda: self._sort_from_filter_popup(view, key, False)
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ClassicButton(
            sortrow, text=desc, role="compact",
            command=lambda: self._sort_from_filter_popup(view, key, True)
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

    def _build_numeric_filter_editor(self, editor, view, key, current):
        p = PALETTE
        tk.Label(editor, text="Minimum", bg=p["surface2"], fg=p["muted"],
                 font=FONT_HELPER).grid(row=0, column=0, sticky="w")
        tk.Label(editor, text="Maximum", bg=p["surface2"], fg=p["muted"],
                 font=FONT_HELPER).grid(
                     row=0, column=1, sticky="w", padx=(8, 0))
        min_var = tk.StringVar(
            value="" if current.get("min") is None else str(current["min"]))
        max_var = tk.StringVar(
            value="" if current.get("max") is None else str(current["max"]))
        min_entry = ClassicEntry(editor, textvariable=min_var, width=12)
        max_entry = ClassicEntry(editor, textvariable=max_var, width=12)
        min_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        max_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(2, 0))
        self._bind_editable_focus_behavior(min_entry)
        self._bind_editable_focus_behavior(max_entry)
        editor.columnconfigure(0, weight=1)
        editor.columnconfigure(1, weight=1)
        # A reserved line, so a message never resizes the popup after it is
        # mapped.  The popup stays open on a refusal with the input visible.
        message = tk.Label(
            editor, text=" ", bg=p["surface2"], fg=p["deck_bad"],
            font=FONT_HELPER, anchor="w", justify="left", wraplength=280)
        message.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        for var in (min_var, max_var):
            var.trace_add("write", lambda *_a: message.configure(text=" "))

        def apply_filter():
            try:
                low = _finite_bound(min_var.get())
            except ValueError:
                message.configure(text="Minimum must be a finite number.")
                return
            try:
                high = _finite_bound(max_var.get())
            except ValueError:
                message.configure(text="Maximum must be a finite number.")
                return
            if low is not None and high is not None and low > high:
                # Applied as-is this silently emptied the table.
                message.configure(text="Minimum cannot be greater than Maximum.")
                return
            if low is None and high is None:
                self._table_filters[view].pop(key, None)
            else:
                self._table_filters[view][key] = {
                    "kind": "numeric", "min": low, "max": high}
            self._refresh_table_after_filter(view)
            self._hide_filter_popup()

        return apply_filter

    def _cost_group_pip_image(self, group):
        """Return a mana-pip image for one symbol group, or None for text-only.

        Colours and Colorless reuse the loaded filter pips; X and Snow are
        composited from the symbol sheet.  Abstract classes (Generic, Hybrid,
        Phyrexian, No mana cost) have no single glyph and stay text-only.
        """
        token = _COST_PIP_TOKENS.get(group)
        if token is None:
            return None
        if group in ("W", "U", "B", "R", "G", "C"):
            getter = getattr(self, "_filter_pip_image", None)
            if callable(getter):
                spec = getter(group)
                if spec is not None:
                    return spec
        symbol_pil = getattr(self, "_symbol_pil", None)
        if ImageTk is None or not callable(symbol_pil):
            return None
        try:
            return ImageTk.PhotoImage(symbol_pil(token, FILTER_PIP_SIZE))
        except Exception:
            return None

    def _build_cost_filter_editor(self, editor, view, key, current):
        return self._build_symbol_group_filter_editor(
            editor, view, key, current, kind="cost",
            group_labels=COST_SYMBOL_GROUP_LABELS,
            image_for=self._cost_group_pip_image)

    def _build_colors_filter_editor(self, editor, view, key, current):
        return self._build_symbol_group_filter_editor(
            editor, view, key, current, kind="colors",
            group_labels=COLOR_FILTER_GROUP_LABELS,
            image_for=self._color_group_pip_image)

    def _color_group_pip_image(self, group):
        getter = getattr(self, "_filter_pip_image", None)
        return getter(group) if callable(getter) else None

    def _build_symbol_group_filter_editor(
            self, editor, view, key, current, *, kind, group_labels, image_for):
        """Shared pip checkbox picker (Any/All/None) for Cost and Colors."""
        p = PALETTE
        mode_var = tk.StringVar(value=current.get("mode", "Any"))
        modes = tk.Frame(editor, bg=p["surface2"])
        modes.pack(fill="x", pady=(0, 6))
        tk.Label(modes, text="Match", bg=p["surface2"], fg=p["muted"],
                 font=FONT_HELPER).pack(side="left", padx=(0, 6))
        AppCombobox(
            modes, textvariable=mode_var, values=("Any", "All", "None"),
            state="readonly", width=8).pack(side="left")

        selected_prev = current.get("groups")
        grid = tk.Frame(editor, bg=p["surface2"])
        grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        # Hold image references so Tk does not garbage-collect the pips.
        self._symbol_filter_pip_refs = []
        group_vars = {}
        for index, (group, label) in enumerate(group_labels):
            variable = tk.BooleanVar(
                value=bool(selected_prev) and group in selected_prev)
            group_vars[group] = variable
            image = image_for(group)
            kw = {"text": " " + label, "variable": variable,
                  "style": "Filter.Color.TCheckbutton"}
            if image is not None:
                kw["image"] = image
                kw["compound"] = "left"
                self._symbol_filter_pip_refs.append(image)
            check = ttk.Checkbutton(grid, **kw)
            check.grid(row=index // 2, column=index % 2, sticky="w",
                       padx=4, pady=2)

        def apply_filter():
            chosen = {group for group, variable in group_vars.items()
                      if variable.get()}
            if not chosen:
                self._table_filters[view].pop(key, None)
            else:
                self._table_filters[view][key] = {
                    "kind": kind, "mode": mode_var.get(), "groups": chosen}
            self._refresh_table_after_filter(view)
            self._hide_filter_popup()

        return apply_filter

    def _build_text_filter_editor(self, editor, view, key, current):
        modes = ("Contains", "Does not contain", "Starts with", "Equals")
        mode_var = tk.StringVar(value=current.get("mode", "Contains"))
        value_var = tk.StringVar(value=current.get("value", ""))
        mode = AppCombobox(
            editor, textvariable=mode_var, values=modes,
            state="readonly", width=18)
        mode.pack(fill="x")
        entry = ClassicEntry(editor, textvariable=value_var)
        entry.pack(fill="x", pady=(6, 0))
        self._bind_editable_focus_behavior(entry)

        def apply_filter():
            value = value_var.get().strip()
            if not value:
                self._table_filters[view].pop(key, None)
            else:
                self._table_filters[view][key] = {
                    "kind": "text", "mode": mode_var.get(), "value": value}
            self._refresh_table_after_filter(view)
            self._hide_filter_popup()

        entry.bind("<Return>", lambda _event: apply_filter())
        return apply_filter

    def _build_values_filter_editor(
            self, editor, pop, view, key, current, raw_values=None):
        if raw_values is None:
            raw_values = list(dict.fromkeys(
                self._filter_display_value(card, key, qty=qty)
                for card, qty in self._table_source_rows(view, skip_col=key)))
            raw_values.sort(key=lambda value: str(value).casefold())
        else:
            raw_values = list(raw_values)

        normalized = [(str(value), str(value) if str(value) else "(blank)")
                      for value in raw_values]
        previous = current.get("values")
        selected = ({str(value) for value in raw_values}
                    if previous is None else {str(value) for value in previous})

        search_var = tk.StringVar(master=self)
        search_entry = ClassicEntry(editor, textvariable=search_var)
        search_entry.pack(fill="x", pady=(0, 5))
        self._bind_editable_focus_behavior(search_entry)

        checklist = VirtualChecklistView(
            self, editor, values=normalized, selected=selected, height=210)
        checklist.pack(fill="both", expand=True)
        self._trace_write_debounced(
            search_var, lambda *_args: checklist.filter(search_var.get()),
            lifecycle_widget=pop)

        picks = tk.Frame(editor, bg=PALETTE["surface2"])
        picks.pack(fill="x", pady=(5, 0))
        ClassicButton(
            picks, text="Select All", role="compact",
            command=lambda: checklist.select_all(True)).pack(side="left")
        ClassicButton(
            picks, text="Clear", role="compact",
            command=lambda: checklist.select_all(False)).pack(side="left", padx=(5, 0))

        by_text = {str(value): value for value in raw_values}

        def apply_filter():
            chosen_keys = checklist.selected_values()
            chosen = {by_text[value] for value in chosen_keys if value in by_text}
            if len(chosen) == len(raw_values):
                self._table_filters[view].pop(key, None)
            else:
                self._table_filters[view][key] = {
                    "kind": "values", "values": chosen}
            self._refresh_table_after_filter(view)
            self._hide_filter_popup()

        return apply_filter

    def _build_async_result_values_filter(
            self, outer, editor, pop, view, key, current):
        """Prepare Results vocabulary while the popup remains completely hidden."""
        def ready(raw_values):
            try:
                if (self._filter_popup is not pop or not pop.winfo_exists()
                        or self._filter_popup_view != view
                        or self._filter_popup_col != key):
                    return
                apply_filter = self._build_values_filter_editor(
                    editor, pop, view, key, current, raw_values=raw_values)
                self._build_filter_actions(outer, view, key, apply_filter)
                self._position_table_filter_popup(pop)
            except tk.TclError:
                return

        self._request_result_vocabulary(key, ready)

    def _build_filter_actions(self, outer, view, key, apply_filter):
        action = tk.Frame(outer, bg=PALETTE["surface2"])
        action.pack(fill="x", pady=(8, 0))
        ClassicButton(
            action, text="Clear This", role="compact",
            command=lambda: self._clear_table_filter(view, key)).pack(side="left")
        ClassicButton(
            action, text="Apply", role="compact_primary",
            command=apply_filter).pack(side="right")

    def _position_table_filter_popup(self, pop):
        # Hidden layout flush: all rows/actions already exist before first map.
        pop.update_idletasks()
        pointer_x, pointer_y = self.winfo_pointerx(), self.winfo_pointery()
        work_x, work_y, work_w, work_h = self._work_area_for_widget(self)
        width, height = pop.winfo_reqwidth(), pop.winfo_reqheight()
        x = min(max(work_x + 4, pointer_x - 10),
                max(work_x + 4, work_x + work_w - width - 8))
        y = min(max(work_y + 4, pointer_y + 8),
                max(work_y + 4, work_y + work_h - height - 8))
        pop.geometry(f"+{x}+{y}")
        pop.deiconify()
        pop.lift()
        pop.focus_set()

