"""Searchable, viewport-virtualized checklist controls used by filter pickers."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from mtgdb.ui.components import (
    ClassicButton, ClassicCheckbutton, ClassicEntry, ClassicRadiobutton,
)
from mtgdb.ui.tokens import (
    FONT_DIALOG_TITLE, FONT_HELPER, PALETTE, POPUP_PADDING,
)


class VirtualChecklistView(tk.Frame):
    """Fixed Tk-row pool backed by a complete logical selection set.

    Logical item count never changes Tk widget count.  The scrollbar and wheel
    move a logical top index; only the bounded physical row pool is rebound.
    """

    ROW_POOL = 32
    ROW_HEIGHT = 24
    SINGLE_SELECT_VIEWPORT_EXTRA = 5

    def __init__(self, owner, master, *, values=(), selected=(), single_select=False,
                 on_change=None, height=300, columns=1):
        super().__init__(master, bg=PALETTE["input"], highlightthickness=1,
                         highlightbackground=PALETTE["border"], bd=0,
                         height=height)
        self.pack_propagate(False)
        self.owner = owner
        self.single_select = bool(single_select)
        self.columns = max(1, min(4, int(columns or 1)))
        self.on_change = on_change
        self._all = []
        self._visible = []
        self._selected = {str(value) for value in selected}
        self._top = 0
        self._visible_rows = 12
        self._capacity = self._visible_rows * self.columns
        self._last_layout_height = 0
        self._slot_values = [None] * self.ROW_POOL
        self._enabled = True
        self._row_vars = []
        self._row_text = []
        self._rows = []
        self._single_value = tk.StringVar(master=owner, value="")

        self._body = tk.Frame(self, bg=PALETTE["input"])
        self._body.pack(side="left", fill="both", expand=True)
        self._scroll = ttk.Scrollbar(
            self, orient="vertical", command=self.yview,
            style="Dark.Vertical.TScrollbar")
        # Permanent gutter: do not pack_forget during content changes.
        self._scroll.pack(side="right", fill="y")
        self.owner._register_scrollable(self, target=self)
        self.owner._register_scrollable(self._body, target=self)

        for slot in range(self.ROW_POOL):
            var = tk.BooleanVar(master=owner, value=False)
            text = tk.StringVar(master=owner, value="")
            if self.single_select:
                # Themed ttk radio, not a classic Tk one: on Windows the classic
                # indicator is painted by the OS and ignores this dark palette,
                # so selected and unselected rows become indistinguishable and
                # every row reads as selected (UI-002).
                row = ttk.Radiobutton(
                    self._body, textvariable=text, variable=self._single_value,
                    value=f"slot:{slot}", style="ListChoice.TRadiobutton",
                    command=lambda index=slot: self._toggle_slot(index))
            else:
                row = ClassicCheckbutton(
                    self._body, textvariable=text, variable=var, role="list",
                    command=lambda index=slot: self._toggle_slot(index))
            row.grid(
                row=slot // self.columns, column=slot % self.columns,
                sticky="ew", padx=7, pady=1)
            self._row_vars.append(var)
            self._row_text.append(text)
            self._rows.append(row)
        for column in range(self.columns):
            self._body.columnconfigure(column, weight=1, uniform="checklist_column")
        # Tk font scaling can make a radio/check row taller than the nominal
        # 24 px layout token. Capacity must use the real requested row extent
        # or the final label can be clipped at higher DPI/scaling values.
        self._effective_row_height = max(
            self.ROW_HEIGHT,
            max((row.winfo_reqheight() + 2 for row in self._rows), default=self.ROW_HEIGHT),
        )
        self.bind("<Configure>", self._on_configure, add="+")
        self.set_values(values, selected=selected)

    @property
    def effective_row_height(self):
        return self._effective_row_height

    @classmethod
    def preferred_height_for_items(cls, item_count, columns=1, row_height=None):
        """Return a compact list viewport height for a logical catalog size."""
        columns = max(1, int(columns or 1))
        logical_rows = max(1, (max(0, int(item_count)) + columns - 1) // columns)
        visible_rows = max(4, min(16, logical_rows))
        effective_row_height = max(cls.ROW_HEIGHT, int(row_height or cls.ROW_HEIGHT))
        return visible_rows * effective_row_height + 12

    @staticmethod
    def _normalize(values):
        normalized = []
        for item in values:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                key, display = str(item[0]), str(item[1])
            else:
                key = display = str(item)
            normalized.append((key, display))
        return normalized

    def set_values(self, values, *, selected=None, preserve_selection=True):
        self._all = self._normalize(values)
        if selected is not None:
            self._selected = {str(value) for value in selected}
        elif not preserve_selection:
            self._selected.clear()
        valid = {key for key, _display in self._all}
        self._selected.intersection_update(valid)
        if self.single_select and len(self._selected) > 1:
            # A radio-backed picker can never carry multiple logical values.
            # If Any (the empty key) is present it wins; otherwise preserve the
            # first selected value in catalog order for deterministic recovery.
            chosen = "" if "" in self._selected else next(
                key for key, _display in self._all if key in self._selected)
            self._selected = {chosen}
        self._visible = list(self._all)
        self._top = 0
        self._refresh()

    def filter(self, query=""):
        needle = str(query or "").strip().casefold()
        self._visible = [
            (key, display) for key, display in self._all
            if not needle or needle in key.casefold() or needle in display.casefold()
        ]
        self._top = 0
        self._refresh()

    @property
    def visible_keys(self):
        return tuple(key for key, _display in self._visible)

    def selected_values(self):
        return set(self._selected)

    def select_visible(self, value):
        keys = {key for key, _display in self._visible}
        if value:
            if self.single_select and keys:
                first = next(iter(keys))
                self._selected = {first}
            else:
                self._selected.update(keys)
        else:
            self._selected.difference_update(keys)
        self._refresh()
        self._changed()

    def select_all(self, value=True):
        keys = {key for key, _display in self._all}
        if value and self.single_select and self._all:
            self._selected = {self._all[0][0]}
        else:
            self._selected = keys if value else set()
        self._refresh()
        self._changed()

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        state = "normal" if self._enabled else "disabled"
        for widget in self._rows:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        try:
            if self._enabled and len(self._visible) > self._capacity:
                self._scroll.state(["!disabled"])
            else:
                self._scroll.state(["disabled"])
        except tk.TclError:
            pass

    def _changed(self):
        if self.on_change is not None:
            self.on_change()

    def _toggle_slot(self, slot):
        key = self._slot_values[slot]
        if key is None:
            return
        if self.single_select:
            self._selected = {key}
        elif bool(self._row_vars[slot].get()):
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._refresh()
        self._changed()

    def _on_configure(self, event=None):
        height = int(getattr(event, "height", 0) or self.winfo_height() or 1)
        max_rows = max(1, self.ROW_POOL // self.columns)
        visible_rows = max(
            4, min(max_rows, height // self._effective_row_height))
        capacity = visible_rows * self.columns
        layout_changed = height != self._last_layout_height
        self._last_layout_height = height
        if capacity != self._capacity:
            self._visible_rows = visible_rows
            self._capacity = capacity
            self._clamp_top()
            self._refresh()
        elif layout_changed:
            # Capacity can remain unchanged while the viewport gains a few
            # pixels. Rebind row minimums so that extra space is actually used.
            self._refresh()

    def _clamp_top(self):
        overflow = max(0, len(self._visible) - self._capacity)
        max_top = ((overflow + self.columns - 1) // self.columns) * self.columns
        self._top = max(0, min(self._top, max_top))
        self._top -= self._top % self.columns

    def _refresh(self):
        self._clamp_top()
        end = min(len(self._visible), self._top + self._capacity)
        rows = self._visible[self._top:end]
        # Short catalogs stay compact and top-aligned. For an actually scrollable
        # catalog, distribute only the normal row-height slack so the final
        # visible checkbox/radio reaches almost to the bottom of the viewport
        # without turning a short catalog (for example Rarity) into giant rows.
        pool_rows = max(1, self.ROW_POOL // self.columns)
        active_grid_rows = min(
            self._visible_rows,
            max(1, (len(rows) + self.columns - 1) // self.columns),
        )
        scrolling = len(self._visible) > self._capacity
        measured_body = int(self._body.winfo_height() or 1)
        configured_body = max(1, self._last_layout_height - 2)
        body_height = max(measured_body, configured_body)
        fill_target = max(0, body_height - 2) if scrolling else 0
        fill_base = (fill_target // active_grid_rows) if scrolling else 0
        fill_extra = (fill_target % active_grid_rows) if scrolling else 0
        for grid_row in range(pool_rows):
            minsize = 0
            if scrolling and grid_row < active_grid_rows:
                minsize = max(
                    self._effective_row_height,
                    fill_base + (1 if grid_row < fill_extra else 0),
                )
            self._body.rowconfigure(grid_row, weight=0, minsize=minsize)
        selected_single = next(iter(self._selected), "") if self.single_select else ""
        for slot, widget in enumerate(self._rows):
            if slot < len(rows) and slot < self._capacity:
                key, display = rows[slot]
                self._slot_values[slot] = key
                self._row_text[slot].set(display or "(blank)")
                if self.single_select:
                    widget.configure(value=key)
                else:
                    self._row_vars[slot].set(key in self._selected)
                widget.grid()
            else:
                self._slot_values[slot] = None
                widget.grid_remove()
        if self.single_select:
            self._single_value.set(selected_single)
        total = len(self._visible)
        if not total or total <= self._capacity:
            first, last = 0.0, 1.0
        else:
            first = self._top / total
            last = min(1.0, (self._top + self._capacity) / total)
        try:
            self._scroll.set(first, last)
            state = "disabled" if (not self._enabled or total <= self._capacity) else "normal"
            self._scroll.state([state] if state == "disabled" else ["!disabled"])
        except tk.TclError:
            pass

    def yview(self, *args):
        if not args:
            total = len(self._visible)
            if not total:
                return (0.0, 1.0)
            return (self._top / total,
                    min(1.0, (self._top + self._capacity) / total))
        if args[0] == "moveto" and len(args) >= 2:
            total = len(self._visible)
            try:
                fraction = max(0.0, min(1.0, float(args[1])))
            except (TypeError, ValueError):
                return
            self._top = round(fraction * max(0, total - self._capacity))
        elif args[0] == "scroll" and len(args) >= 3:
            try:
                amount = int(args[1])
            except (TypeError, ValueError):
                return
            if str(args[2]) == "pages":
                amount *= max(1, self._visible_rows - 1)
            amount *= self.columns
            self._top += amount
        self._refresh()

    def yview_scroll(self, number, what):
        self.yview("scroll", number, what)


class SearchChecklistDialog:
    """Hidden-first searchable picker backed by a fixed VirtualChecklistView."""

    ROW_POOL = VirtualChecklistView.ROW_POOL

    MODE_CHOICES = (("Any", "any"), ("All", "all"), ("None", "none"))

    def __init__(self, owner, *, title, values, selected, apply_callback,
                 mode_var=None, mode_default="any", mode_label="Selected values:",
                 mode_choices=None,
                 help_text="Type to narrow the list.", single_select=False):
        self.owner = owner
        self.title = title
        self.apply_callback = apply_callback
        self.mode_var = mode_var
        self.single_select = single_select
        p = PALETTE

        self.popup = owner._create_hidden_popup(title, transient=owner, resizable=False)
        self.popup.bind("<Destroy>", self._on_popup_destroy, add="+")

        outer = tk.Frame(
            self.popup, bg=p["surface2"], padx=POPUP_PADDING[0], pady=POPUP_PADDING[1],
            highlightbackground=p["border"], highlightthickness=1)
        outer.pack(fill="both", expand=True)
        self._title_label = tk.Label(
            outer, text=title.upper(), bg=p["surface2"], fg=p["accent"],
            font=FONT_DIALOG_TITLE)
        self._title_label.pack(anchor="w")
        self._help_label = tk.Label(
            outer, text=help_text, bg=p["surface2"], fg=p["muted"],
            font=FONT_HELPER, justify="left", wraplength=470)
        self._help_label.pack(anchor="w", pady=(2, 8))

        self.popup_mode = tk.StringVar(
            master=owner, value=(mode_var.get() if mode_var is not None else mode_default))
        if mode_var is not None:
            mode = tk.Frame(outer, bg=p["surface2"])
            mode.pack(fill="x", pady=(0, 7))
            self._mode_label = tk.Label(
                mode, text=mode_label, bg=p["surface2"], fg=p["text"],
                font=FONT_HELPER)
            self._mode_label.pack(side="left")
            scope = mode_label.rstrip(":").lower()
            meanings = {
                "any": f"Any: a card only needs to match one of the selected {scope}.",
                "all": f"All: a card must match every selected {scope}.",
                "none": f"None: exclude every card matching any of the selected {scope}.",
                "playable": "Playable: legal or restricted in the chosen format.",
                "banned": "Banned: explicitly banned in the chosen format.",
                "restricted": "Restricted: limited to one copy in the chosen format.",
            }
            for label, value in (mode_choices or self.MODE_CHOICES):
                radio = ttk.Radiobutton(
                    mode, text=label, variable=self.popup_mode, value=value,
                    style="DialogChoice.TRadiobutton")
                radio.pack(side="left", padx=(7, 0))
                meaning = meanings.get(value)
                if meaning:
                    owner._add_tooltip(radio, meaning)

        self.find_var = tk.StringVar(master=owner)
        self.find = ClassicEntry(outer, textvariable=self.find_var)
        self.find.pack(fill="x", pady=(0, 7))
        owner._bind_editable_focus_behavior(self.find)

        actionrow = tk.Frame(outer, bg=p["surface2"])
        actionrow.pack(fill="x", pady=(0, 6))
        if not single_select:
            ClassicButton(actionrow, text="Select All", role="compact",
                          command=lambda: self.list_view.select_visible(True)).pack(side="left")
            ClassicButton(actionrow, text="Clear Selected", role="compact",
                          command=lambda: self.list_view.select_visible(False)).pack(
                              side="left", padx=(6, 0))

        # Reserve the footer before packing the expanding list. On a constrained
        # Windows work area this guarantees Done/Cancel remain visible while the
        # long Mechanics/Subtype list yields the remaining vertical cavity.
        foot = tk.Frame(outer, bg=p["surface2"])
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        ClassicButton(foot, text="Done", role="compact_primary",
                      command=self._apply_and_close).pack(side="right")
        ClassicButton(foot, text="Cancel", role="compact",
                      command=self.close).pack(side="right", padx=(0, 7))

        self.list_view = VirtualChecklistView(
            owner, outer, values=values, selected=selected,
            single_select=single_select, height=390)
        self.list_view.pack(fill="both", expand=True)

        owner._trace_write_debounced(
            self.find_var, lambda *_args: self.list_view.filter(self.find_var.get()),
            lifecycle_widget=self.popup)
        self.popup.bind("<Escape>", lambda _event: self.close())
        self.show(
            title=title, values=values, selected=selected,
            apply_callback=apply_callback, mode_var=mode_var,
            mode_default=mode_default, mode_label=mode_label, help_text=help_text)

    def show(self, *, title, values, selected, apply_callback, mode_var=None,
             mode_default="any", mode_label="Selected values:", help_text=""):
        self.title = title
        self.apply_callback = apply_callback
        self.mode_var = mode_var
        self.popup.title(title)
        self._title_label.configure(text=title.upper())
        self._help_label.configure(text=help_text)
        if hasattr(self, "_mode_label"):
            self._mode_label.configure(text=mode_label)
        self.popup_mode.set(mode_var.get() if mode_var is not None else mode_default)
        self.find_var.set("")
        self.list_view.set_values(values, selected=selected)
        # Four-value catalogs such as Rarity should look like a compact picker,
        # while long catalogs such as Mechanics retain a full scrolling viewport.
        list_height = self.list_view.preferred_height_for_items(
            len(values), self.list_view.columns,
            row_height=self.list_view.effective_row_height)
        if self.single_select:
            list_height += self.list_view.SINGLE_SELECT_VIEWPORT_EXTRA
        self.list_view.configure(height=list_height)
        try:
            # Cached dialogs may have been locked to a previous catalog size.
            self.popup.minsize(1, 1)
            self.popup.maxsize(100000, 100000)
        except tk.TclError:
            pass
        self.popup.update_idletasks()
        preferred_height = max(330, min(600, self.popup.winfo_reqheight()))
        self.owner._present_hidden_popup(
            self.popup, preferred_width=560, preferred_height=preferred_height,
            min_width=560, min_height=330, lock_size=True, focus=self.find, grab=True)

    def close(self):
        popup = self.popup
        if popup is None:
            return
        try:
            if popup.winfo_exists():
                try:
                    popup.grab_release()
                except tk.TclError:
                    pass
                popup.withdraw()
                if getattr(self.owner, "_active_search_checklist", None) is self:
                    self.owner._active_search_checklist = None
        except tk.TclError:
            pass

    def _on_popup_destroy(self, event):
        if event.widget is self.popup:
            popup = self.popup
            self.popup = None
            if getattr(self.owner, "_active_search_checklist", None) is self:
                self.owner._active_search_checklist = None
            cache = getattr(self.owner, "_search_checklist_cache", None)
            if isinstance(cache, dict):
                for key, dialog in tuple(cache.items()):
                    if dialog is self:
                        cache.pop(key, None)
            if popup is not None:
                recorder = getattr(self.owner, "_record_popup_destroyed", None)
                if callable(recorder):
                    recorder(popup)

    def _apply_and_close(self):
        chosen = self.list_view.selected_values()
        if self.single_select and len(chosen) > 1:
            chosen = {next(iter(chosen))}
        if self.mode_var is not None:
            self.mode_var.set(self.popup_mode.get())
        self.apply_callback(chosen)
        self.owner._update_search_filter_summary()
        self.close()


def open_search_checklist(owner, **options):
    cache = getattr(owner, "_search_checklist_cache", None)
    if cache is None:
        cache = owner._search_checklist_cache = {}
    key = (bool(options.get("mode_var") is not None), bool(options.get("single_select", False)))
    dialog = cache.get(key)
    if dialog is None or dialog.popup is None:
        dialog = SearchChecklistDialog(owner, **options)
        cache[key] = dialog
    else:
        dialog.show(
            title=options.get("title", "Choose"),
            values=options.get("values", ()),
            selected=options.get("selected", ()),
            apply_callback=options.get("apply_callback"),
            mode_var=options.get("mode_var"),
            mode_default=options.get("mode_default", "any"),
            mode_label=options.get("mode_label", "Selected values:"),
            help_text=options.get("help_text", "Type to narrow the list."),
        )
    owner._active_search_checklist = dialog
    return dialog
