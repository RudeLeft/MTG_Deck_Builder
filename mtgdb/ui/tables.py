"""Shared table schema, formatting, sorting, columns, and reordering UI."""

from __future__ import annotations

import logging
import tkinter as tk

from mtgdb.preferences.repository import UIPreferencesRepository
from mtgdb.search.results import table_sort_key, table_value
from mtgdb.ui.components import AppButton, ClassicCheckbutton
from mtgdb.ui.tokens import FONT_HELPER_BOLD, PALETTE


log = logging.getLogger("mtg")

TABLE_VIEWS = ("results", "main", "side")
TABLE_COLUMNS = {
    "cost":      {"label": "Cost",        "width": 105, "views": {"results", "main", "side"}},
    "qty":       {"label": "Qty",         "width": 48,  "views": {"main", "side"}},
    "name":      {"label": "Name",        "width": 240, "views": {"results", "main", "side"}},
    "type":      {"label": "Type",        "width": 230, "views": {"results", "main", "side"}},
    "set":       {"label": "Set",         "width": 180, "views": {"results", "main", "side"}},
    "collector": {"label": "Collector #", "width": 92,  "views": {"results", "main", "side"}},
    "year":      {"label": "Year",        "width": 62,  "views": {"results", "main", "side"}},
    "ability":   {"label": "Ability",     "width": 170, "views": {"results", "main", "side"}},
    "rarity":    {"label": "Rarity",      "width": 78,  "views": {"results", "main", "side"}},
    "cmc":       {"label": "Mana Value",  "width": 92,  "views": {"results", "main", "side"}},
    "power":     {"label": "Power",       "width": 68,  "views": {"results", "main", "side"}},
    "toughness": {"label": "Toughness",   "width": 82,  "views": {"results", "main", "side"}},
    "colors":    {"label": "Colors",      "width": 90,  "views": {"results", "main", "side"}},
    "rules":     {"label": "Rules Text",  "width": 360, "views": {"results", "main", "side"}},
}
TABLE_COLUMN_ORDER = tuple(TABLE_COLUMNS)
TABLE_DEFAULTS = {
    "results": ["cost", "name", "type", "power", "toughness", "rarity"],
    "main":    ["cost", "qty", "name", "type", "power", "toughness", "rarity"],
    "side":    ["cost", "qty", "name", "type", "power", "toughness", "rarity"],
}


def available_columns(view):
    return [key for key in TABLE_COLUMN_ORDER if view in TABLE_COLUMNS[key]["views"]]


def table_column_minwidth(key):
    """Return the canonical minimum width used by setup and one-click reset."""
    if key == "cost":
        return 72
    return {
        "qty": 42, "rarity": 68, "name": 130, "type": 120,
        "year": 50, "power": 56, "toughness": 68, "collector": 76,
    }.get(key, 72)


def normalized_visible_columns(preferences):
    """Migrate and validate saved table layouts against the current schema."""
    result = {view: list(columns) for view, columns in TABLE_DEFAULTS.items()}
    saved = preferences.get("table_columns") if isinstance(preferences, dict) else None
    if not isinstance(saved, dict):
        return result

    version = preferences.get("table_columns_version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 1

    for view in TABLE_VIEWS:
        value = saved.get(view)
        if not isinstance(value, list):
            continue
        allowed = set(available_columns(view))
        cleaned = []
        for key in value:
            if key in allowed and key not in cleaned:
                cleaned.append(key)
        if cleaned and version < 2:
            insert_at = cleaned.index("rarity") if "rarity" in cleaned else len(cleaned)
            for key in ("power", "toughness"):
                if key not in cleaned:
                    cleaned.insert(insert_at, key)
                    insert_at += 1
        if cleaned:
            if "cost" in cleaned:
                cleaned.remove("cost")
                cleaned.insert(0, "cost")
            result[view] = cleaned
    return result




def column_popup_position(
        *, anchor_x, anchor_y, anchor_height, popup_width, popup_height,
        work_area, margin=8):
    """Clamp an Edit Columns popup to the anchor monitor's visible work area."""
    work_x, work_y, work_width, work_height = work_area
    right = int(work_x) + max(1, int(work_width))
    bottom = int(work_y) + max(1, int(work_height))
    width = max(1, int(popup_width))
    height = max(1, int(popup_height))
    margin = max(0, int(margin))
    x = max(int(work_x) + margin,
            min(int(anchor_x), right - width - margin))
    y = int(anchor_y) + max(1, int(anchor_height)) + 2
    if y + height > bottom - margin:
        y = int(anchor_y) - height - 2
    y = max(int(work_y) + margin, min(y, bottom - height - margin))
    return int(x), int(y)


class TableInfrastructureMixin:
    """Own shared table presentation while the app supplies feature callbacks."""

    def _initialize_table_infrastructure(self, preferences_path):
        self._visible_columns = {
            view: list(columns) for view, columns in TABLE_DEFAULTS.items()
        }
        self._column_popups = {}
        self._column_vars = {}
        self._column_drag = None
        self._column_drop_marker = None
        self._ui_preferences_repository = UIPreferencesRepository(preferences_path)
        self._load_ui_preferences()

    def _load_ui_preferences(self):
        self._visible_columns = normalized_visible_columns(
            self._ui_preferences_repository.load())

    def _save_ui_preferences(self):
        try:
            self._ui_preferences_repository.save_table_columns({
                view: self._visible_columns.get(view, TABLE_DEFAULTS[view])
                for view in TABLE_VIEWS
            })
        except OSError:
            log.debug("Could not save UI preferences", exc_info=True)

    def _table_value(self, card, key, qty=None):
        return table_value(card, key, qty=qty)

    def _table_sort_key(self, card, key, qty=None):
        return table_sort_key(card, key, qty=qty)

    def _table_widget(self, view):
        return {
            "results": getattr(self, "results_tv", None),
            "main": getattr(self, "main_tv", None),
            "side": getattr(self, "side_tv", None),
        }.get(view)

    def _update_table_headings(self, view, tv=None):
        """Apply labels, sort arrows, and filter actions to every column."""
        tv = tv or self._table_widget(view)
        if tv is None:
            return
        if view == "results":
            sort_column, descending = self._sort_col, self._sort_desc
        else:
            sort_column, descending = self._deck_sorts[view]
        active_filters = self._table_filters.get(view, {})
        for key in available_columns(view):
            column_id = "#0" if key == "cost" else key
            label = TABLE_COLUMNS[key]["label"]
            if key in active_filters:
                label += "  ●"
            if key == sort_column:
                label += "  ▼" if descending else "  ▲"
            tv.heading(
                column_id, text=label, anchor="w",
                command=lambda table_view=view, column=key:
                    self._show_table_filter(table_view, column))

    def _setup_table_columns(self, tv, view):
        tv.heading("#0", text="Cost", anchor="w")
        tv.column(
            "#0", width=TABLE_COLUMNS["cost"]["width"],
            minwidth=table_column_minwidth("cost"), anchor="w", stretch=True)
        for key in available_columns(view):
            if key == "cost":
                continue
            metadata = TABLE_COLUMNS[key]
            tv.heading(key, text=metadata["label"], anchor="w")
            tv.column(
                key, width=metadata["width"],
                minwidth=table_column_minwidth(key), anchor="w", stretch=True)
        self._apply_table_columns(view, tv=tv)
        self._update_table_headings(view, tv)

    def _apply_table_columns(self, view, tv=None):
        tv = tv or self._table_widget(view)
        if tv is None:
            return
        visible = [
            key for key in self._visible_columns.get(view, [])
            if key in available_columns(view)]
        cost_visible = "cost" in visible
        tv.configure(
            show="tree headings" if cost_visible else "headings",
            displaycolumns=tuple(key for key in visible if key != "cost"))
        tv.column(
            "#0", width=TABLE_COLUMNS["cost"]["width"] if cost_visible else 0,
            minwidth=(table_column_minwidth("cost") if cost_visible else 0), anchor="w",
            stretch=cost_visible)
        for key in available_columns(view):
            if key != "cost":
                tv.column(key, stretch=(key in visible))
        self._update_table_headings(view, tv)

    def _toggle_column_popup(self, view, anchor):
        popup = self._column_popups.get(view)
        if popup is not None and popup.winfo_exists() and popup.winfo_viewable():
            self._hide_column_popup(view)
            return
        self._show_column_popup(view, anchor)

    def _show_column_popup(self, view, anchor):
        popup = self._column_popups.get(view)
        if popup is None or not popup.winfo_exists():
            popup = self._create_column_popup(view)
        # The popup is still withdrawn, so its own geometry may settle without
        # re-entering the root layout during an interactive resize/sash motion.
        popup.update_idletasks()
        width = max(popup.winfo_reqwidth(), 1)
        height = max(popup.winfo_reqheight(), 1)
        x, y = column_popup_position(
            anchor_x=anchor.winfo_rootx(),
            anchor_y=anchor.winfo_rooty(),
            anchor_height=anchor.winfo_height(),
            popup_width=width, popup_height=height,
            work_area=self._work_area_for_widget(anchor), margin=8)
        popup.geometry(f"+{int(x)}+{int(y)}")
        popup.deiconify()
        popup.lift()
        popup.focus_set()

    def _hide_column_popup(self, view):
        popup = self._column_popups.get(view)
        if popup is not None and popup.winfo_exists():
            popup.withdraw()

    def _create_column_popup(self, view):
        palette = PALETTE
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=palette["border"])
        popup.bind("<Escape>", lambda _event, table_view=view:
                   self._hide_column_popup(table_view))
        self._column_popups[view] = popup
        outer = tk.Frame(popup, bg=palette["surface2"], padx=10, pady=9)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        title = {
            "results": "RESULT COLUMNS", "main": "MAINBOARD COLUMNS",
            "side": "SIDEBOARD COLUMNS",
        }[view]
        tk.Label(
            outer, text=title, bg=palette["surface2"], fg=palette["accent"],
            font=FONT_HELPER_BOLD).grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        variables = {}
        self._column_vars[view] = variables
        columns = available_columns(view)
        for index, key in enumerate(columns):
            variable = tk.BooleanVar(value=key in self._visible_columns.get(view, []))
            variables[key] = variable
            ClassicCheckbutton(
                outer, text=TABLE_COLUMNS[key]["label"], variable=variable,
                role="option", command=lambda table_view=view:
                    self._columns_changed(table_view)).grid(
                        row=1 + index // 2, column=index % 2, sticky="w",
                        padx=(0, 16), pady=1)
        action_row = 1 + (len(columns) + 1) // 2
        tk.Frame(outer, bg=palette["border"], height=1).grid(
            row=action_row, column=0, columnspan=2, sticky="ew", pady=(7, 6))
        AppButton(
            outer, text="Reset", role="compact",
            command=lambda table_view=view: self._reset_columns(table_view)
        ).grid(row=action_row + 1, column=0, sticky="w")
        AppButton(
            outer, text="Done", role="compact_primary",
            command=lambda table_view=view: self._hide_column_popup(table_view)
        ).grid(row=action_row + 1, column=1, sticky="e")
        return popup

    def _columns_changed(self, view):
        variables = self._column_vars.get(view, {})
        enabled = {
            key for key in available_columns(view)
            if variables.get(key) and variables[key].get()}
        current = [
            key for key in self._visible_columns.get(view, []) if key in enabled]
        current.extend(
            key for key in available_columns(view)
            if key in enabled and key not in current)
        self._visible_columns[view] = current
        self._apply_table_columns(view)
        self._save_ui_preferences()

    def _reset_columns(self, view):
        """Restore default visibility, order, widths, and stretch in one action."""
        defaults = list(TABLE_DEFAULTS[view])
        self._column_drag = None
        self._clear_column_drop_marker()
        self._visible_columns[view] = defaults
        for key, variable in self._column_vars.get(view, {}).items():
            variable.set(key in defaults)
        tv = self._table_widget(view)
        if tv is not None:
            # Restore every physical column, including hidden columns, so a later
            # re-enable starts from its canonical geometry rather than stale drag
            # or resize state. _apply_table_columns restores display order.
            tv.column(
                "#0", width=TABLE_COLUMNS["cost"]["width"],
                minwidth=table_column_minwidth("cost"), anchor="w",
                stretch=("cost" in defaults))
            for key in available_columns(view):
                if key == "cost":
                    continue
                tv.column(
                    key, width=TABLE_COLUMNS[key]["width"],
                    minwidth=table_column_minwidth(key), anchor="w",
                    stretch=(key in defaults))
        self._apply_table_columns(view, tv=tv)
        self._save_ui_preferences()

    def _display_column_at(self, tv, view, x):
        identifier = tv.identify_column(x)
        if not identifier:
            return None
        try:
            display_index = int(identifier[1:])
        except (TypeError, ValueError):
            return None
        visible = [
            key for key in self._visible_columns.get(view, [])
            if key in available_columns(view)]
        if "cost" in visible and display_index == 0:
            return "cost"
        ordinary = [key for key in visible if key != "cost"]
        index = display_index - 1
        return ordinary[index] if 0 <= index < len(ordinary) else None

    def _clear_column_drop_marker(self):
        marker = getattr(self, "_column_drop_marker", None)
        if marker is not None:
            try:
                marker.destroy()
            except tk.TclError:
                pass
        self._column_drop_marker = None

    def _column_boundaries(self, tv, view):
        visible = [
            key for key in self._visible_columns.get(view, [])
            if key in available_columns(view)]
        display = (["cost"] if "cost" in visible else []) + [
            key for key in visible if key != "cost"]
        result = []
        x = 0
        for column in display:
            width = int(tv.column("#0" if column == "cost" else column, "width"))
            result.append((column, x, x + width))
            x += width
        return result

    def _drop_index_for_x(self, tv, view, x):
        movable = [boundary for boundary in self._column_boundaries(tv, view)
                   if boundary[0] != "cost"]
        if not movable:
            return 0, None
        for index, (_column, left, right) in enumerate(movable):
            if x < (left + right) / 2:
                return index, left
        return len(movable), movable[-1][2]

    def _show_column_drop_marker(self, tv, marker_x):
        self._clear_column_drop_marker()
        if marker_x is None:
            return
        marker = tk.Frame(
            tv, width=3, bg=PALETTE["accent"], highlightthickness=0, bd=0)
        marker.place(x=max(0, marker_x - 1), y=0, relheight=1.0)
        marker.lift()
        self._column_drop_marker = marker

    def _column_drag_press(self, event, tv, view):
        if tv.identify_region(event.x, event.y) != "heading":
            self._column_drag = None
            return
        column = self._display_column_at(tv, view, event.x)
        if not column or column == "cost":
            self._column_drag = None
            return
        self._column_drag = {
            "tv": tv, "view": view, "column": column,
            "start_x": event.x_root, "moved": False, "drop_index": None,
        }

    def _column_drag_motion(self, event, tv, view):
        state = getattr(self, "_column_drag", None)
        if not state or state["tv"] is not tv or state["view"] != view:
            return
        if abs(event.x_root - state["start_x"]) < 5 and not state["moved"]:
            return
        state["moved"] = True
        try:
            tv.configure(cursor="sb_h_double_arrow")
        except tk.TclError:
            pass
        drop_index, marker_x = self._drop_index_for_x(tv, view, event.x)
        state["drop_index"] = drop_index
        self._show_column_drop_marker(tv, marker_x)

    def _column_drag_release(self, _event, tv, view):
        state = getattr(self, "_column_drag", None)
        self._column_drag = None
        self._clear_column_drop_marker()
        try:
            tv.configure(cursor="")
        except tk.TclError:
            pass
        if not state or state["tv"] is not tv or state["view"] != view:
            return
        if not state["moved"] or state["drop_index"] is None:
            return
        source = state["column"]
        visible = list(self._visible_columns.get(view, []))
        ordinary = [key for key in visible if key != "cost"]
        if source not in ordinary:
            return "break"
        original_index = ordinary.index(source)
        ordinary.remove(source)
        # The drop index addresses a slot in the list BEFORE the drag column was
        # removed, so translate it first and clamp afterwards. Clamping first
        # collapsed the past-the-last-column slot onto the final index and then
        # decremented it again, which made dragging a column to the last
        # position land it second to last instead.
        index = state["drop_index"]
        if index > original_index:
            index -= 1
        index = max(0, min(index, len(ordinary)))
        ordinary.insert(index, source)
        self._visible_columns[view] = (
            (["cost"] if "cost" in visible else []) + ordinary)
        self._apply_table_columns(view)
        self._save_ui_preferences()
        return "break"

    def _bind_column_drag(self, tv, view):
        tv.bind(
            "<ButtonPress-1>",
            lambda event, tree=tv, table_view=view:
                self._column_drag_press(event, tree, table_view), add="+")
        tv.bind(
            "<B1-Motion>",
            lambda event, tree=tv, table_view=view:
                self._column_drag_motion(event, tree, table_view), add="+")
        tv.bind(
            "<ButtonRelease-1>",
            lambda event, tree=tv, table_view=view:
                self._column_drag_release(event, tree, table_view), add="+")
        tv.bind(
            "<Leave>", lambda _event: self._clear_column_drop_marker(), add="+")
