"""Autocomplete Entry widget with non-focus-stealing popup behavior."""

import logging
import tkinter as tk

from mtgdb.ui.components import ClassicEntry
from mtgdb.ui.tokens import FONT_BODY, PALETTE

log = logging.getLogger("mtg")


class _AutocompletePopupBehavior:
    """Private implementation shared by both autocomplete controls."""

    MAX_VISIBLE_SUGGESTIONS = 10
    FOCUS_HIDE_DELAY_MS = 50

    def _initialize_popup_behavior(self):
        self._suggest_popup = None
        self._suggest_list = None
        self._suggest_hits = []
        self._suggest_index = -1
        self.bind("<Up>", self._on_up, add="+")
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.bind("<Configure>", lambda _event: self._reposition_popup(), add="+")
        self.bind(
            "<Destroy>", lambda _event: self._destroy_suggestion_popup(), add="+")

    def _cancel_pending_refresh(self):
        if self._suggest_after is not None:
            try:
                self.after_cancel(self._suggest_after)
            except tk.TclError:
                pass
            self._suggest_after = None

    def _ensure_suggestion_popup(self):
        if self._suggest_popup is not None:
            try:
                if self._suggest_popup.winfo_exists():
                    return
            except tk.TclError:
                pass
        p = PALETTE
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg=p["border"])
        try:
            popup.attributes("-topmost", True)
        except tk.TclError:
            pass
        listbox = tk.Listbox(
            popup, activestyle="none", exportselection=False, takefocus=False,
            background=p["input"], foreground=p["text"],
            selectbackground=p["accent"], selectforeground=p["on_accent"],
            highlightthickness=1, highlightbackground=p["border"],
            highlightcolor=p["border"], relief="flat", borderwidth=0,
            font=FONT_BODY)
        listbox.pack(fill="both", expand=True, padx=1, pady=1)
        root = self.winfo_toplevel()
        if hasattr(root, "_register_scrollable"):
            root._register_scrollable(listbox)
        listbox.bind("<Button-1>", self._on_popup_click)
        self._suggest_popup = popup
        self._suggest_list = listbox

    def _suggestions_can_show(self):
        return True

    def _show_suggestions(self, hits):
        self._suggest_hits = list(hits)
        self._suggest_index = -1
        if not hits or not self._suggestions_can_show():
            self._hide_suggestions()
            return
        self._ensure_suggestion_popup()
        listbox, popup = self._suggest_list, self._suggest_popup
        if listbox is None or popup is None:
            return
        listbox.delete(0, "end")
        visible = hits[:self.MAX_VISIBLE_SUGGESTIONS]
        for item in visible:
            listbox.insert("end", item)
        listbox.configure(height=max(1, len(visible)))
        # Complete geometry while hidden; only then expose the finished popup.
        popup.update_idletasks()
        self._reposition_popup(allow_hidden=True)
        popup.deiconify()
        popup.lift()
        try:
            self.focus_set()
            self.icursor(tk.END)
        except tk.TclError:
            pass

    def _reposition_popup(self, allow_hidden=False):
        popup = self._suggest_popup
        if popup is None:
            return
        try:
            if not popup.winfo_exists():
                return
            if not allow_hidden and not popup.winfo_viewable():
                return
            root = self.winfo_toplevel()
            if getattr(root, "_window_in_motion", False):
                return
            x = self.winfo_rootx()
            y = self.winfo_rooty() + self.winfo_height()
            width = max(self.winfo_width(), popup.winfo_reqwidth())
            height = popup.winfo_reqheight()
            if hasattr(root, "_work_area_for_widget"):
                wx, wy, ww, wh = root._work_area_for_widget(self)
                if y + height > wy + wh - 8:
                    y = self.winfo_rooty() - height
                x = min(max(wx + 4, x), max(wx + 4, wx + ww - width - 4))
                y = min(max(wy + 4, y), max(wy + 4, wy + wh - height - 4))
            popup.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def _hide_suggestions(self):
        self._suggest_index = -1
        if self._suggest_popup is not None:
            try:
                self._suggest_popup.withdraw()
            except tk.TclError:
                pass

    def _destroy_suggestion_popup(self):
        popup = self._suggest_popup
        self._suggest_popup = None
        self._suggest_list = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass

    def _select_popup_index(self, index):
        listbox = self._suggest_list
        if listbox is None or not self._suggest_hits:
            return
        max_index = min(
            len(self._suggest_hits), self.MAX_VISIBLE_SUGGESTIONS) - 1
        index = max(0, min(index, max_index))
        self._suggest_index = index
        listbox.selection_clear(0, "end")
        listbox.selection_set(index)
        listbox.see(index)

    def _on_up(self, _event=None):
        if not self._suggest_hits:
            return "break"
        if self._suggest_popup is None or not self._suggest_popup.winfo_viewable():
            self._show_suggestions(self._suggest_hits)
        if self._suggest_index < 0:
            self._suggest_index = min(
                len(self._suggest_hits), self.MAX_VISIBLE_SUGGESTIONS)
        self._select_popup_index(self._suggest_index - 1)
        return "break"

    def _on_popup_click(self, event):
        listbox = self._suggest_list
        if listbox is None:
            return "break"
        index = listbox.nearest(event.y)
        if 0 <= index < min(
                len(self._suggest_hits), self.MAX_VISIBLE_SUGGESTIONS):
            self._suggest_index = index
            self.commit_highlighted_suggestion()
        try:
            self.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _on_focus_out(self, _event=None):
        self.after(self.FOCUS_HIDE_DELAY_MS, self._hide_if_focus_left)

    def _hide_if_focus_left(self):
        try:
            if str(self.tk.call("focus") or "") != str(self):
                self._hide_suggestions()
        except (tk.TclError, KeyError):
            self._hide_suggestions()


class AutocompleteEntry(_AutocompletePopupBehavior, ClassicEntry):
    """Plain text entry with a custom, non-focus-stealing autocomplete popup."""

    SUGGEST_DELAY_MS = 90
    FOCUS_HIDE_DELAY_MS = 60

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self._suggest = None
        self._suggest_after = None
        self._initialize_popup_behavior()
        self.bind("<KeyRelease>", self._on_keyrelease, add="+")
        self.bind("<Down>", self._on_down, add="+")
        self.bind("<Escape>", self._on_escape, add="+")
        self.bind("<Button-1>", self._on_click, add="+")

    def set(self, value):
        self.delete(0, tk.END)
        if value is not None:
            self.insert(0, str(value))
        self.icursor(tk.END)
        self._cancel_pending_refresh()
        self._hide_suggestions()

    def set_suggest_source(self, func):
        self._suggest = func

    def _match_list(self, typed):
        if not typed or self._suggest is None:
            return []
        try:
            return list(self._suggest(typed))
        except Exception:
            log.exception("Name autocomplete suggestion lookup failed")
            return []

    def _schedule_refresh(self):
        self._cancel_pending_refresh()
        typed = self.get()
        self._suggest_after = self.after(
            self.SUGGEST_DELAY_MS,
            lambda value=typed: self._refresh_suggestions(value))

    def _refresh_suggestions(self, typed):
        self._suggest_after = None
        if not self.winfo_exists() or self.get() != typed:
            return
        hits = self._match_list(typed)
        self._show_suggestions(hits) if hits else self._hide_suggestions()

    def commit_highlighted_suggestion(self):
        if self._suggest_index < 0 or self._suggest_index >= len(self._suggest_hits):
            return False
        value = self._suggest_hits[self._suggest_index]
        self.delete(0, tk.END)
        self.insert(0, value)
        self.icursor(tk.END)
        self._hide_suggestions()
        return True

    def _on_keyrelease(self, event):
        if event.keysym in (
            "Up", "Down", "Return", "KP_Enter", "Tab", "Escape",
            "Left", "Right", "Home", "End", "Prior", "Next",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R", "Meta_L", "Meta_R",
        ):
            return
        self._schedule_refresh()

    def _on_down(self, _event=None):
        if not self._suggest_hits:
            self._schedule_refresh()
            return "break"
        if self._suggest_popup is None or not self._suggest_popup.winfo_viewable():
            self._show_suggestions(self._suggest_hits)
        self._select_popup_index(self._suggest_index + 1)
        return "break"

    def _on_escape(self, _event=None):
        self._cancel_pending_refresh()
        self._hide_suggestions()
        return "break"

    def _on_click(self, _event=None):
        self.after_idle(self.focus_set)
        if self.get().strip():
            self.after_idle(self._schedule_refresh)
