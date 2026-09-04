"""Shared authoritative printing/set filter primitives for UI workflows."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from mtgdb.ui.components import ClassicButton, ClassicCheckbutton, ClassicEntry
from mtgdb.ui.search_checklist import VirtualChecklistView
from mtgdb.ui.tokens import (
    FONT_DIALOG_TITLE, FONT_HELPER, FONT_HELPER_BOLD, PALETTE,
    POPUP_FOOTER_PADDING, POPUP_PADDING,
)

log = logging.getLogger("mtg")


class _LightBoolVar:
    """Tk-free BooleanVar-compatible state for large exact-set catalogs."""

    __slots__ = ("_value",)

    def __init__(self, value=False):
        self._value = bool(value)

    def get(self):
        return self._value

    def set(self, value):
        self._value = bool(value)


def set_type_label(value):
    """Humanize one observed Scryfall ``set_type`` without reclassifying it."""
    return str(value or "").replace("_", " ").title()


class PrintingFilter:
    """Reusable Paper/Set Type/Exact Set picker backed by observed Scryfall data.

    Search and deck-import workflows share this controller and popup so changes to
    printing-filter behavior/layout cannot drift between the two entry points.
    Empty Set Type or Exact Set selections mean Any within the current content and
    Paper scope.
    """

    def __init__(
            self, owner, *, repository=None, content_types_getter=None,
            english_variable=None, english_change_callback=None,
            change_callback=None, scope_change_callback=None,
            popup_title="Printings", header_text="PRINTINGS", intro_text=None):
        self.owner = owner
        self.repository = repository or owner.search_repository
        self._content_types_getter = content_types_getter
        self._english_variable = english_variable
        self._english_change_callback = english_change_callback
        self._change_callback = change_callback
        self._scope_change_callback = scope_change_callback
        self._popup_title = str(popup_title or "Printings")
        self._header_text = str(header_text or "PRINTINGS")
        self._intro_text = str(
            ("Optional printing filters. Leaving Set Type and Set empty means "
             "any observed Scryfall set in the current content scope.")
            if intro_text is None else intro_text)

        self.paper_only = tk.BooleanVar(master=owner, value=True)
        self.set_type_vars = {}
        self._present_set_types = set()
        self._set_vars = {}
        self._eligible_sets = []
        self._popup = None
        self._set_type_frame = None
        self._status_label = None
        self._set_search_var = None
        self._visible_set_codes = []
        self._set_checklist = None
        self._set_checklist_catalog = ()
        self._modal = False
        self._modal_result = False
        self._done_text = "Done"

    def _content_types(self):
        if self._content_types_getter is None:
            return ("card",)
        values = tuple(self._content_types_getter() or ())
        return values or ("card",)

    @property
    def present_set_types(self):
        return frozenset(self._present_set_types)

    @property
    def eligible_set_codes(self):
        return {code for code, _name in self._eligible_sets}

    def _notify_change(self):
        callback = self._change_callback
        if callback is not None:
            callback()

    def _clear_popup_widget_state(self, popup=None):
        """Forget every widget/cache owned by one destroyed popup instance."""
        if popup is not None and self._popup is not popup:
            return
        self._popup = None
        self._set_type_frame = None
        self._status_label = None
        self._set_search_var = None
        self._visible_set_codes = []

    def _destroy_popup(self):
        popup = self._popup
        self._clear_popup_widget_state(popup)
        if popup is not None:
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except tk.TclError:
                pass

    def _on_popup_destroy(self, event, popup):
        if event.widget is popup:
            self._clear_popup_widget_state(popup)

    def _set_catalog_controls_enabled(self, enabled):
        """Enable or disable the currently materialized printing-catalog controls."""
        state = "normal" if enabled else "disabled"
        frame = self._set_type_frame
        if frame is not None:
            try:
                for child in frame.winfo_children():
                    try:
                        child.configure(state=state)
                    except (tk.TclError, AttributeError):
                        pass
            except (tk.TclError, AttributeError):
                pass
        checklist = self._set_checklist
        if checklist is not None:
            try:
                checklist.set_enabled(enabled)
            except (tk.TclError, AttributeError):
                pass

    def refresh_catalog(self):
        """Refresh trusted printing vocabulary in place for the current scope."""
        content = self._content_types()
        paper_only = bool(self.paper_only.get())
        selected_types = self.selected_set_types()
        selected_codes = self.selected_set_codes() if self._set_vars else set()
        try:
            present = [
                value for value, _count
                in self.repository.set_types(content, paper_only)
            ]
        except Exception:
            log.exception("Could not load observed set-type catalog")
            present = []
        try:
            sets = self.repository.sets(
                sorted(selected_types) or None,
                content_types=content, paper_only=paper_only)
        except Exception:
            log.exception("Could not load observed exact-set catalog")
            sets = []
        self.apply_catalog_data(
            present, sets, selected_types=selected_types,
            selected_codes=selected_codes)

    def apply_catalog_data(
            self, present_set_types, eligible_sets, *,
            selected_types=None, selected_codes=None):
        """Apply already-authorized printing vocabulary without database access."""
        selected_types = (self.selected_set_types() if selected_types is None
                          else set(selected_types))
        selected_codes = (self.selected_set_codes() if selected_codes is None
                          else set(selected_codes))
        present = [
            value[0] if isinstance(value, (tuple, list)) else value
            for value in (present_set_types or ())
        ]
        old_type_vars = self.set_type_vars
        self._present_set_types = set(present)
        self.set_type_vars = {}
        for set_type in present:
            variable = old_type_vars.get(set_type)
            if variable is None:
                variable = tk.BooleanVar(master=self.owner)
            variable.set(set_type in selected_types)
            self.set_type_vars[set_type] = variable
        self._apply_exact_set_catalog(eligible_sets, selected_codes)
        self._refresh_set_type_controls()
        self._render_individual_set_checks()
        self._update_summary()

    def _refresh_exact_set_catalog(self, selected_codes=None):
        """Cascade Exact Set vocabulary from Content/Paper/selected Set Types."""
        content = self._content_types()
        paper_only = bool(self.paper_only.get())
        allowed_types = sorted(self.selected_set_types()) or None
        if selected_codes is None:
            selected_codes = self.selected_set_codes() if self._set_vars else set()
        try:
            sets = self.repository.sets(
                allowed_types, content_types=content, paper_only=paper_only)
        except Exception:
            log.exception("Could not load observed exact-set catalog")
            sets = []
        self._apply_exact_set_catalog(sets, selected_codes)

    def _apply_exact_set_catalog(self, sets, selected_codes=None):
        """Replace Exact Set logical state without allocating one Tcl variable per set."""
        if selected_codes is None:
            selected_codes = self.selected_set_codes() if self._set_vars else set()
        selected_codes = {str(code) for code in selected_codes}
        eligible_sets = [(str(code or ""), str(name or "")) for code, name in (sets or ())]
        eligible_codes = {code for code, _name in eligible_sets}
        old_vars = self._set_vars
        self._eligible_sets = eligible_sets
        self._set_vars = {}
        for code, _name in eligible_sets:
            variable = old_vars.get(code)
            if not isinstance(variable, _LightBoolVar):
                variable = _LightBoolVar()
            variable.set(code in selected_codes and code in eligible_codes)
            self._set_vars[code] = variable
        checklist = getattr(self, "_set_checklist", None)
        if checklist is not None:
            try:
                if checklist.winfo_exists():
                    checklist.set_values(
                        [(code, f"{name} ({code.upper()})") for code, name in eligible_sets],
                        selected={code for code in selected_codes if code in eligible_codes})
                    self._set_checklist_catalog = tuple(code for code, _name in eligible_sets)
            except tk.TclError:
                self._set_checklist = None
                self._set_checklist_catalog = ()

    def _refresh_set_type_controls(self):
        frame = self._set_type_frame
        try:
            if frame is None or not frame.winfo_exists():
                return
        except tk.TclError:
            return
        for child in frame.winfo_children():
            try:
                child.destroy()
            except tk.TclError:
                pass
        if self._present_set_types:
            self.owner._build_set_type_controls(
                frame, self._present_set_types, self.set_type_vars,
                self._on_set_type_change, columns=4)
        else:
            tk.Label(
                frame, text="No set types are available for the current scope.",
                bg=PALETTE["surface2"], fg=PALETTE["muted"], font=FONT_HELPER
            ).pack(anchor="w")

    def clear(self):
        self.paper_only.set(True)
        for variable in self.set_type_vars.values():
            variable.set(False)
        for variable in self._set_vars.values():
            variable.set(False)
        self.refresh_catalog()

    def toggle_popup(self):
        if self._popup is not None and self._popup.winfo_exists():
            if self._popup.winfo_viewable():
                self._hide_popup()
                return
        self._modal = False
        self._done_text = "Done"
        self._show_popup()

    def _show_popup(self):
        if self._popup is None or not self._popup.winfo_exists():
            self._create_popup()
        self.owner._present_hidden_popup(
            self._popup, preferred_width=820, preferred_height=860,
            min_width=760, min_height=620, lock_size=True, grab=True)

    def run_modal(self, *, done_text="Done"):
        """Show the shared picker as a one-shot modal and return True on accept."""
        self._modal = True
        self._modal_result = False
        self._done_text = str(done_text or "Done")
        self._destroy_popup()
        self._show_popup()
        popup = self._popup
        if popup is None:
            return False
        try:
            self.owner.wait_window(popup)
        except tk.TclError:
            pass
        return bool(self._modal_result)

    def _hide_popup(self):
        self._sync_set_vars_from_checklist()
        if self._popup is not None and self._popup.winfo_exists():
            try:
                self._popup.grab_release()
            except tk.TclError:
                pass
            self._popup.withdraw()

    def _finish_popup(self, accepted):
        if self._modal:
            self._modal_result = bool(accepted)
            self._destroy_popup()
        else:
            self._hide_popup()

    def _create_popup(self):
        p = PALETTE
        self._clear_popup_widget_state()
        shell = self.owner._create_set_filter_shell(self._popup_title, withdraw=True)
        popup, footer, outer = shell["popup"], shell["footer"], shell["outer"]
        popup.protocol("WM_DELETE_WINDOW", lambda: self._finish_popup(False))
        popup.bind("<Escape>", lambda _event: self._finish_popup(False))
        self._popup = popup
        popup.bind(
            "<Destroy>",
            lambda event, expected=popup: self._on_popup_destroy(event, expected),
            add="+")

        popup_head = tk.Frame(outer, bg=p["surface2"])
        popup_head.pack(fill="x")
        tk.Label(popup_head, text=self._header_text, bg=p["surface2"],
                 fg=p["accent"], font=FONT_DIALOG_TITLE).pack(side="left")
        if self._english_variable is not None:
            ClassicCheckbutton(
                popup_head, text="English only", variable=self._english_variable,
                role="option", command=(self._english_change_callback or self._notify_change)
            ).pack(side="right")
        if self._intro_text:
            tk.Label(
                outer, text=self._intro_text,
                bg=p["surface2"], fg=p["muted"], font=FONT_HELPER,
                justify="left", wraplength=720).pack(anchor="w", pady=(2, 9))

        ClassicCheckbutton(
            outer, text="Paper only", variable=self.paper_only, role="option",
            command=self._on_scope_change).pack(anchor="w", pady=(0, 8))

        type_head = tk.Frame(outer, bg=p["surface2"])
        type_head.pack(fill="x")
        tk.Label(type_head, text="SET TYPE (OPTIONAL)", bg=p["surface2"],
                 fg=p["accent"], font=FONT_HELPER_BOLD).pack(side="left")

        type_actions = tk.Frame(outer, bg=p["surface2"])
        type_actions.pack(fill="x", pady=(5, 4))
        ClassicButton(
            type_actions, text="Clear Set Types", role="compact",
            command=self._clear_set_types).pack(side="left")

        types = tk.Frame(outer, bg=p["surface2"])
        types.pack(fill="x")
        self._set_type_frame = types
        self._refresh_set_type_controls()

        tk.Frame(outer, bg=p["border"], height=1).pack(fill="x", pady=(8, 8))
        set_head = tk.Frame(outer, bg=p["surface2"])
        set_head.pack(fill="x")
        tk.Label(set_head, text="EXACT SET (OPTIONAL)", bg=p["surface2"],
                 fg=p["accent"], font=FONT_HELPER_BOLD).pack(side="left")

        findrow = tk.Frame(outer, bg=p["surface2"])
        findrow.pack(fill="x", pady=(5, 5))
        tk.Label(findrow, text="Find set:", bg=p["surface2"], fg=p["text"],
                 font=FONT_HELPER).pack(side="left", padx=(0, 6))
        self._set_search_var = tk.StringVar(master=self.owner)
        entry = ClassicEntry(findrow, textvariable=self._set_search_var)
        entry.pack(side="left", fill="x", expand=True)
        self.owner._bind_editable_focus_behavior(entry)
        self.owner._trace_write_debounced(
            self._set_search_var, self._render_individual_set_checks,
            lifecycle_widget=popup)

        list_shell = tk.Frame(outer, bg=p["border"], height=270, width=650)
        list_shell.pack(fill="both", expand=True)
        list_shell.pack_propagate(False)
        self._set_checklist = VirtualChecklistView(
            self.owner, list_shell,
            values=[(code, f"{name} ({code.upper()})") for code, name in self._eligible_sets],
            selected=self.selected_set_codes(), on_change=self._on_filter_change,
            height=270, columns=2)
        self._set_checklist.pack(fill="both", expand=True)
        self._set_checklist_catalog = tuple(code for code, _name in self._eligible_sets)
        self._status_label = tk.Label(
            outer, text="", bg=p["surface2"], fg=p["muted"],
            font=FONT_HELPER, anchor="w")
        self._status_label.pack(fill="x", pady=(5, 0))
        self._render_individual_set_checks()

        ClassicButton(
            footer, text="Select All", role="compact",
            command=lambda: self._set_visible_sets(True)).pack(side="left")
        ClassicButton(
            footer, text="Clear Sets", role="compact",
            command=self._clear_sets).pack(side="left", padx=(5, 0))
        ClassicButton(
            footer, text=self._done_text, role="primary",
            command=lambda: self._finish_popup(True)).pack(side="right")

    def _on_scope_change(self):
        self.refresh_catalog()
        callback = self._scope_change_callback
        if callback is not None:
            callback()
        self._notify_change()

    def _on_set_type_change(self):
        selected_codes = self.selected_set_codes()
        self._refresh_exact_set_catalog(selected_codes)
        self._render_individual_set_checks()
        self._update_summary()
        self._notify_change()

    def _on_filter_change(self):
        self._update_summary()
        self._notify_change()

    def _clear_set_types(self):
        for variable in self.set_type_vars.values():
            variable.set(False)
        self._on_set_type_change()

    def _clear_sets(self):
        for variable in self._set_vars.values():
            variable.set(False)
        if getattr(self, "_set_checklist", None) is not None:
            try:
                self._set_checklist.select_all(False)
            except tk.TclError:
                pass
        self._render_individual_set_checks()

    def _render_individual_set_checks(self):
        checklist = getattr(self, "_set_checklist", None)
        if checklist is None:
            self._update_summary()
            return
        try:
            if not checklist.winfo_exists():
                return
        except tk.TclError:
            return
        catalog = tuple(code for code, _name in self._eligible_sets)
        if catalog != getattr(self, "_set_checklist_catalog", ()):
            checklist.set_values(
                [(code, f"{name} ({code.upper()})") for code, name in self._eligible_sets],
                selected=self.selected_set_codes())
            self._set_checklist_catalog = catalog
        query = ""
        if self._set_search_var is not None:
            try:
                query = self._set_search_var.get().strip()
            except tk.TclError:
                pass
        checklist.filter(query)
        self._visible_set_codes = list(checklist.visible_keys)
        self._update_summary()

    def _sync_set_vars_from_checklist(self):
        checklist = getattr(self, "_set_checklist", None)
        if checklist is None:
            return
        try:
            if not checklist.winfo_exists():
                return
            selected = checklist.selected_values()
        except tk.TclError:
            return
        for code, variable in self._set_vars.items():
            variable.set(code in selected)

    def _set_visible_sets(self, value):
        checklist = getattr(self, "_set_checklist", None)
        if checklist is not None:
            try:
                if checklist.winfo_exists():
                    checklist.select_visible(bool(value))
                    self._sync_set_vars_from_checklist()
                    self._visible_set_codes = list(checklist.visible_keys)
                    self._on_filter_change()
                    return
            except tk.TclError:
                pass
        for code in self._visible_set_codes:
            variable = self._set_vars.get(code)
            if variable is not None:
                variable.set(value)
        self._on_filter_change()

    def selected_set_codes(self):
        checklist = getattr(self, "_set_checklist", None)
        if checklist is not None:
            try:
                if checklist.winfo_exists():
                    return set(checklist.selected_values())
            except tk.TclError:
                pass
        return {
            code for code, variable in self._set_vars.items()
            if bool(variable.get())
        }

    def selected_set_types(self):
        return {
            set_type for set_type, variable in self.set_type_vars.items()
            if bool(variable.get())
        }

    def select_all_present_types_and_sets(self):
        """Compatibility helper: allow any observed set while retaining Paper only."""
        for variable in self.set_type_vars.values():
            variable.set(False)
        self._refresh_exact_set_catalog(set())
        self._render_individual_set_checks()
        self._update_summary()

    def restore_selection(self, set_types, set_codes=None, *, paper_only=True):
        """Restore only values that exist in the current authoritative scope."""
        self.paper_only.set(bool(paper_only))
        self.refresh_catalog()
        wanted_types = {str(value) for value in (set_types or []) if str(value)}
        for set_type, variable in self.set_type_vars.items():
            variable.set(set_type in wanted_types)
        wanted_codes = {str(value) for value in (set_codes or []) if str(value)}
        self._refresh_exact_set_catalog(wanted_codes)
        self._render_individual_set_checks()
        self._update_summary()

    def is_default_selection(self):
        return (
            bool(self.paper_only.get())
            and not self.selected_set_types()
            and not self.selected_set_codes()
        )

    def summary_text(self):
        selected_types = self.selected_set_types()
        selected_sets = self.selected_set_codes()
        parts = ["Paper only" if self.paper_only.get() else "Paper + digital"]
        if selected_types:
            parts.append(
                f"{len(selected_types)} set type" +
                ("" if len(selected_types) == 1 else "s"))
        else:
            parts.append("Any set type")
        if selected_sets:
            parts.append(
                f"{len(selected_sets)} set" +
                ("" if len(selected_sets) == 1 else "s"))
        else:
            parts.append("Any set")
        return " · ".join(parts)

    def _update_summary(self):
        selected_types = self.selected_set_types()
        selected_sets = self.selected_set_codes()
        try:
            if self._status_label is not None and self._status_label.winfo_exists():
                self._status_label.configure(
                    text=(f"{len(selected_sets)} exact sets selected; "
                          f"{len(selected_types)} set types selected. Empty means Any."))
        except tk.TclError:
            pass
        return self.summary_text()


class SetFilterSupportMixin:
    """Own dialog shell and flat observed set-type controls shared by UI features."""

    def _create_set_filter_shell(self, title, withdraw=False):
        p = PALETTE
        popup = self._create_hidden_popup(title, transient=self, resizable=False)
        popup.configure(bg=p["border"])

        shell = tk.Frame(popup, bg=p["surface2"])
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        footer = tk.Frame(
            shell, bg=p["surface2"],
            padx=POPUP_FOOTER_PADDING[0], pady=POPUP_FOOTER_PADDING[1])
        footer.pack(side="bottom", fill="x")

        body_shell = tk.Frame(shell, bg=p["surface2"])
        body_shell.pack(side="top", fill="both", expand=True)
        body_canvas = tk.Canvas(
            body_shell, bg=p["surface2"], highlightthickness=0, bd=0)
        body_scroll = ttk.Scrollbar(
            body_shell, orient="vertical", command=body_canvas.yview,
            style="Dark.Vertical.TScrollbar")
        body_canvas.configure(yscrollcommand=body_scroll.set)
        body_canvas.pack(side="left", fill="both", expand=True)
        body_scroll.pack(side="right", fill="y")

        outer = tk.Frame(
            body_canvas, bg=p["surface2"],
            padx=POPUP_PADDING[0], pady=POPUP_PADDING[1])
        body_window = body_canvas.create_window(
            (0, 0), window=outer, anchor="nw")
        update_scroll = self._bind_autohide_canvas_scrollbar(
            body_canvas, outer, body_window, body_scroll)
        self._register_scrollable(body_canvas)

        return {
            "popup": popup, "shell": shell, "footer": footer,
            "body_shell": body_shell, "canvas": body_canvas,
            "scrollbar": body_scroll, "outer": outer,
            "window": body_window, "update_scroll": update_scroll,
        }

    def _build_set_type_controls(
            self, parent, present, variables, on_change, *, columns=4):
        """Render only set types observed in the current local Scryfall data."""
        values = sorted({str(value) for value in present if str(value)}, key=str.casefold)
        for index, set_type in enumerate(values):
            ClassicCheckbutton(
                parent, text=set_type_label(set_type), variable=variables[set_type],
                role="option", command=on_change).grid(
                    row=index // columns, column=index % columns, sticky="w",
                    padx=(0, 12), pady=1)
        for column in range(columns):
            parent.columnconfigure(column, weight=1)
