"""Reusable, behavior-neutral UI components for MTG Deck Builder."""

import logging
import tkinter as tk
import unicodedata
from tkinter import ttk

from mtgdb.ui.tokens import (
    CLASSIC_ENTRY_IPADY,
    FONT_BODY,
    FONT_BODY_BOLD,
    FONT_CONTROL_GLYPH,
    FONT_HELPER,
    FONT_HELPER_BOLD,
    FONT_MICRO,
    FONT_MICRO_BOLD,
    FONT_PANE_TITLE,
    PALETTE,
)

log = logging.getLogger("mtg")


def deck_board_label(board):
    """Return the display name for a deck board identifier.

    Shared so the Mainboard/Sideboard wording cannot drift between the deck
    pane, comparison provenance, and the stats panel. Anything that is not the
    mainboard reads as the sideboard, matching Deck.BOARDS.
    """
    return "Mainboard" if board == "main" else "Sideboard"


# Scryfall's legality keys are single lowercase words, so the obvious
# .capitalize() renders real multi-word format names as "Paupercommander" and
# "Standardbrawl". These are spellings only: membership still comes entirely
# from the legality data on the cards, and an unlisted key still displays.
_FORMAT_WORD_LABELS = {
    "competitivebrawl": "Competitive Brawl",
    "duel": "Duel Commander",
    "future": "Future Standard",
    "tlr": "Tarkir Dragonstorm Limited",
    "penny": "Penny Dreadful",
    "oathbreaker": "Oathbreaker",
    "oldschool": "Old School",
    "paupercommander": "Pauper Commander",
    "predh": "PreDH",
    "premodern": "Premodern",
    "standardbrawl": "Standard Brawl",
}


def format_display_name(value):
    """Return the readable name for one Scryfall format key.

    Shared so Search, the deck pane and the card preview cannot disagree about
    what a format is called. An unknown key is title-cased rather than hidden:
    a format this build has never heard of must still be selectable.
    """
    key = str(value or "").strip()
    if not key:
        return ""
    known = _FORMAT_WORD_LABELS.get(key.casefold())
    if known:
        return known
    return key.replace("_", " ").title()


_BUTTON_STYLES = {
    "standard": "TButton",
    "primary": "Primary.TButton",
    "compact": "Compact.TButton",
    "compact_primary": "CompactPrimary.TButton",
    "deck": "DeckControl.TButton",
    "picker": "Picker.TButton",
    "dense": "SearchRow.TButton",
    "dense_primary": "SearchRowAccent.TButton",
}

_MENUBUTTON_STYLES = {
    "menu": "MenuBar.TMenubutton",
    "picker": "Picker.TMenubutton",
}


def visible_text_columns(text):
    """Return a conservative Tk character width for a visible text label."""
    widest = 0
    for line in str(text or "").expandtabs(4).splitlines() or [""]:
        columns = 0
        for char in line:
            if unicodedata.combining(char):
                continue
            columns += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        widest = max(widest, columns)
    return widest


def content_safe_button_width(text, requested_width):
    """Keep explicit character widths from clipping their button labels."""
    if requested_width in (None, ""):
        return requested_width
    try:
        requested = int(requested_width)
    except (TypeError, ValueError):
        return requested_width
    if requested <= 0:
        return requested
    return max(requested, visible_text_columns(text))


def _protect_explicit_text_width(options):
    if "width" in options and "text" in options:
        options["width"] = content_safe_button_width(
            options.get("text"), options.get("width"))


class AppButton(ttk.Button):
    CLICK_PULSE_MS = 140

    def __init__(self, master=None, *, role="standard", **kwargs):
        kwargs.setdefault("style", _BUTTON_STYLES[role])
        _protect_explicit_text_width(kwargs)
        super().__init__(master, **kwargs)
        self._ui_click_pulse_after = None
        self.bind("<ButtonRelease-1>", self._pulse_click_feedback, add="+")
        self.bind("<Destroy>", self._release_click_feedback, add="+")

    def _pulse_click_feedback(self, event=None):
        try:
            if self.instate(["disabled"]):
                return
            if event is not None and not (
                    0 <= int(event.x) < self.winfo_width()
                    and 0 <= int(event.y) < self.winfo_height()):
                return
            pending = self._ui_click_pulse_after
            if pending is not None:
                self.after_cancel(pending)
            self.state(["alternate"])
            self._ui_click_pulse_after = self.after(
                self.CLICK_PULSE_MS, self._clear_click_feedback)
        except tk.TclError:
            self._ui_click_pulse_after = None

    def _clear_click_feedback(self):
        self._ui_click_pulse_after = None
        try:
            self.state(["!alternate"])
        except tk.TclError:
            pass

    def _release_click_feedback(self, event=None):
        if event is not None and event.widget is not self:
            return
        pending = self._ui_click_pulse_after
        self._ui_click_pulse_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass


class AppMenubutton(ttk.Menubutton):
    CLICK_PULSE_MS = 140

    def __init__(self, master=None, *, role="menu", **kwargs):
        kwargs.setdefault("style", _MENUBUTTON_STYLES[role])
        _protect_explicit_text_width(kwargs)
        super().__init__(master, **kwargs)
        self._ui_click_pulse_after = None
        self.bind("<ButtonRelease-1>", self._pulse_click_feedback, add="+")
        self.bind("<Destroy>", self._release_click_feedback, add="+")

    def _pulse_click_feedback(self, event=None):
        try:
            if self.instate(["disabled"]):
                return
            if event is not None and not (
                    0 <= int(event.x) < self.winfo_width()
                    and 0 <= int(event.y) < self.winfo_height()):
                return
            pending = self._ui_click_pulse_after
            if pending is not None:
                self.after_cancel(pending)
            self.state(["alternate"])
            self._ui_click_pulse_after = self.after(
                self.CLICK_PULSE_MS, self._clear_click_feedback)
        except tk.TclError:
            self._ui_click_pulse_after = None

    def _clear_click_feedback(self):
        self._ui_click_pulse_after = None
        try:
            self.state(["!alternate"])
        except tk.TclError:
            pass

    def _release_click_feedback(self, event=None):
        if event is not None and event.widget is not self:
            return
        pending = self._ui_click_pulse_after
        self._ui_click_pulse_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass


class AppEntry(ttk.Entry):
    def __init__(self, master=None, **kwargs):
        kwargs.setdefault("style", "Form.TEntry")
        super().__init__(master, **kwargs)


class AppCombobox(ttk.Combobox):
    def __init__(self, master=None, **kwargs):
        kwargs.setdefault("style", "Form.TCombobox")
        super().__init__(master, **kwargs)


class AppSpinbox(ttk.Spinbox):
    def __init__(self, master=None, **kwargs):
        kwargs.setdefault("style", "Form.TSpinbox")
        super().__init__(master, **kwargs)


_CLASSIC_BUTTON_ROLES = {
    "secondary": {
        "bg": PALETTE["surface3"], "fg": PALETTE["text"],
        "activebackground": PALETTE["surface"],
        "activeforeground": PALETTE["accent"],
        "font": FONT_BODY, "padx": 10, "pady": 4,
    },
    "primary": {
        "bg": PALETTE["accent"], "fg": PALETTE["on_accent"],
        "activebackground": PALETTE["accent2"],
        "activeforeground": PALETTE["on_accent"],
        "font": FONT_BODY_BOLD, "padx": 12, "pady": 4,
    },
    "compact": {
        "bg": PALETTE["surface3"], "fg": PALETTE["text"],
        "activebackground": PALETTE["surface"],
        "activeforeground": PALETTE["accent"],
        "font": FONT_HELPER, "padx": 8, "pady": 4,
    },
    "compact_primary": {
        "bg": PALETTE["accent"], "fg": PALETTE["on_accent"],
        "activebackground": PALETTE["accent2"],
        "activeforeground": PALETTE["on_accent"],
        "font": FONT_HELPER_BOLD, "padx": 12, "pady": 4,
    },
    "dense": {
        "bg": PALETTE["surface2"], "fg": PALETTE["text"],
        "activebackground": PALETTE["surface3"],
        "activeforeground": PALETTE["text"],
        "font": FONT_MICRO, "padx": 3, "pady": 2,
    },
    "dense_primary": {
        "bg": PALETTE["accent"], "fg": PALETTE["on_accent"],
        "activebackground": PALETTE["accent2"],
        "activeforeground": PALETTE["on_accent"],
        "font": FONT_MICRO_BOLD, "padx": 4, "pady": 2,
    },
    "disclosure": {
        "bg": PALETTE["surface2"], "fg": PALETTE["text"],
        "activebackground": PALETTE["surface3"],
        "activeforeground": PALETTE["accent"],
        "font": FONT_HELPER_BOLD, "padx": 10, "pady": 7,
        "anchor": "w", "cursor": "hand2",
    },
    "chip_close": {
        "bg": PALETTE["surface3"], "fg": PALETTE["muted"],
        "activebackground": PALETTE["surface3"],
        "activeforeground": PALETTE["accent"],
        "font": FONT_BODY_BOLD, "padx": 3, "pady": 0,
        "cursor": "hand2",
    },
    "tab_add": {
        "bg": PALETTE["accent"], "fg": PALETTE["on_accent"],
        "activebackground": PALETTE["accent2"],
        "activeforeground": PALETTE["on_accent"],
        "font": FONT_PANE_TITLE,
        "width": 3, "padx": 0, "pady": 1, "cursor": "hand2",
    },
    "tab_overflow": {
        "bg": PALETTE["surface3"], "fg": PALETTE["text"],
        "activebackground": PALETTE["surface"],
        "activeforeground": PALETTE["accent"],
        "font": FONT_CONTROL_GLYPH,
        "padx": 8, "pady": 2, "cursor": "hand2",
    },
}


_CLASSIC_PRIMARY_ROLES = {"primary", "compact_primary", "dense_primary", "tab_add"}
_CLASSIC_OUTLINE_EXEMPT_ROLES = {"chip_close"}


def _classic_button_outline(role):
    """Return the shared one-pixel outline for a classic button role."""
    if role in _CLASSIC_PRIMARY_ROLES:
        return PALETTE["accent"]
    return PALETTE["border"]


class ClassicButton(tk.Button):
    CLICK_PULSE_MS = 140

    def __init__(self, master=None, *, role="secondary", **kwargs):
        outlined = role not in _CLASSIC_OUTLINE_EXEMPT_ROLES
        outline = _classic_button_outline(role)
        options = {
            "relief": "flat", "bd": 0,
            "highlightthickness": 1 if outlined else 0,
            "highlightbackground": outline, "highlightcolor": outline,
        }
        options.update(_CLASSIC_BUTTON_ROLES[role])
        options.update(kwargs)
        _protect_explicit_text_width(options)
        super().__init__(master, **options)
        self._ui_click_outline = outline
        self._ui_click_background = str(self.cget("background"))
        self._ui_click_activebackground = str(self.cget("activebackground"))
        self._ui_click_pulse_background = (
            PALETTE["accent2"] if role in _CLASSIC_PRIMARY_ROLES
            else PALETTE["select"]
        )
        self._ui_click_pulse_after = None
        self.bind("<ButtonRelease-1>", self._pulse_click_feedback, add="+")
        self.bind("<Destroy>", self._release_click_feedback, add="+")

    def _pulse_click_feedback(self, event=None):
        try:
            if str(self.cget("state")) == "disabled":
                return
            if event is not None and not (
                    0 <= int(event.x) < self.winfo_width()
                    and 0 <= int(event.y) < self.winfo_height()):
                return
            pending = self._ui_click_pulse_after
            if pending is not None:
                self.after_cancel(pending)
            self.configure(
                relief="sunken", background=self._ui_click_pulse_background,
                activebackground=self._ui_click_pulse_background,
                highlightbackground=PALETTE["accent"],
                highlightcolor=PALETTE["accent"])
            self._ui_click_pulse_after = self.after(
                self.CLICK_PULSE_MS, self._clear_click_feedback)
        except tk.TclError:
            self._ui_click_pulse_after = None

    def _clear_click_feedback(self):
        self._ui_click_pulse_after = None
        try:
            self.configure(
                relief="flat", background=self._ui_click_background,
                activebackground=self._ui_click_activebackground,
                highlightbackground=self._ui_click_outline,
                highlightcolor=self._ui_click_outline)
        except tk.TclError:
            pass

    def _release_click_feedback(self, event=None):
        if event is not None and event.widget is not self:
            return
        pending = self._ui_click_pulse_after
        self._ui_click_pulse_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass


_CLASSIC_ENTRY_ROLES = {
    "standard": {"font": FONT_BODY},
    "compact": {"font": FONT_HELPER},
}


def classic_entry_options(role="standard"):
    options = {
        "bg": PALETTE["input"], "fg": PALETTE["text"],
        "insertbackground": PALETTE["text"],
        "selectbackground": PALETTE["accent"],
        "selectforeground": PALETTE["on_accent"], "relief": "flat", "bd": 0,
        "highlightthickness": 1, "highlightbackground": PALETTE["border"],
        "highlightcolor": PALETTE["accent"],
    }
    options.update(_CLASSIC_ENTRY_ROLES[role])
    return options


class ClassicEntry(tk.Entry):
    def __init__(self, master=None, *, role="standard",
                 layout_ipady=CLASSIC_ENTRY_IPADY, **kwargs):
        self._ui_layout_ipady = max(0, int(layout_ipady))
        options = classic_entry_options(role)
        options.update(kwargs)
        super().__init__(master, **options)

    def pack(self, cnf=None, **kwargs):
        kwargs.setdefault("ipady", self._ui_layout_ipady)
        return super().pack(cnf or {}, **kwargs)

    def grid(self, cnf=None, **kwargs):
        kwargs.setdefault("ipady", self._ui_layout_ipady)
        return super().grid(cnf or {}, **kwargs)


_CHECK_ROLES = {
    "option": {
        "bg": PALETTE["surface2"], "activebackground": PALETTE["surface2"],
        "selectcolor": PALETTE["input"], "font": FONT_HELPER,
    },
    "list": {
        "bg": PALETTE["input"], "activebackground": PALETTE["input"],
        "selectcolor": PALETTE["surface2"], "font": FONT_HELPER,
    },
    "chip": {
        "indicatoron": False, "bg": PALETTE["surface3"],
        "activebackground": PALETTE["accent2"],
        "activeforeground": PALETTE["on_accent"],
        "selectcolor": PALETTE["accent"],
        "font": FONT_HELPER, "padx": 7, "pady": 3, "cursor": "hand2",
    },
}


class ClassicCheckbutton(tk.Checkbutton):
    def __init__(self, master=None, *, role="option", **kwargs):
        options = {
            "fg": PALETTE["text"], "activeforeground": PALETTE["text"],
            "highlightthickness": 0, "bd": 0, "anchor": "w",
        }
        options.update(_CHECK_ROLES[role])
        options.update(kwargs)
        super().__init__(master, **options)
        self._ui_chip_variable = None
        self._ui_chip_trace = None
        if role == "chip":
            variable = options.get("variable")
            if hasattr(variable, "trace_add") and hasattr(variable, "get"):
                self._ui_chip_variable = variable
                self._ui_chip_trace = variable.trace_add(
                    "write", self._schedule_chip_contrast_sync)
                self.bind("<Destroy>", self._release_chip_trace, add="+")
            self._sync_chip_contrast()

    def _schedule_chip_contrast_sync(self, *_args):
        try:
            self.after_idle(self._sync_chip_contrast)
        except tk.TclError:
            pass

    def _sync_chip_contrast(self):
        selected = False
        if self._ui_chip_variable is not None:
            try:
                selected = bool(self._ui_chip_variable.get())
            except (tk.TclError, TypeError, ValueError):
                selected = False
        try:
            self.configure(
                fg=(PALETTE["on_accent"] if selected else PALETTE["text"]),
                disabledforeground=PALETTE["muted"],
            )
        except tk.TclError:
            pass

    def _release_chip_trace(self, event=None):
        if event is not None and event.widget is not self:
            return
        variable = self._ui_chip_variable
        trace_id = self._ui_chip_trace
        self._ui_chip_variable = None
        self._ui_chip_trace = None
        if variable is not None and trace_id is not None:
            try:
                variable.trace_remove("write", trace_id)
            except (tk.TclError, ValueError):
                pass


class ClassicRadiobutton(tk.Radiobutton):
    def __init__(self, master=None, **kwargs):
        options = {
            "bg": PALETTE["surface2"], "fg": PALETTE["text"],
            # Tk fills the indicator with ``selectcolor`` when the radio is on
            # and with the widget background when it is off, so the two must
            # contrast. Callers that restyle the row background would otherwise
            # collapse both states to one colour and leave every row looking
            # identically selected.
            "selectcolor": PALETTE["accent"],
            "activebackground": PALETTE["surface2"],
            "activeforeground": PALETTE["text"],
            "highlightthickness": 0, "font": FONT_BODY,
            "anchor": "w", "justify": "left",
        }
        options.update(kwargs)
        super().__init__(master, **options)


class ToolTip:
    """Small dark hover tooltip for controls whose search semantics need context."""

    def __init__(self, widget, text, delay=450, wraplength=360):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after = None
        self._popup = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._owner_destroyed, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        widget = self.widget
        if widget is None:
            return
        try:
            self._after = widget.after(self.delay, self._show)
        except tk.TclError:
            pass

    def _cancel(self):
        pending = self._after
        self._after = None
        widget = self.widget
        if pending is not None and widget is not None:
            try:
                widget.after_cancel(pending)
            except tk.TclError:
                pass

    def _show(self):
        self._after = None
        widget = self.widget
        if widget is None:
            return
        try:
            if not widget.winfo_exists() or self._popup is not None:
                return
            x = widget.winfo_pointerx() + 14
            y = widget.winfo_pointery() + 16
            tip = tk.Toplevel(widget)
            tip.withdraw()
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.configure(bg=PALETTE["accent"])
            body = tk.Label(
                tip, text=self.text, justify="left", anchor="w",
                wraplength=self.wraplength, padx=9, pady=7,
                bg=PALETTE["surface2"], fg=PALETTE["text"],
                font=FONT_HELPER)
            body.pack(padx=1, pady=1)
            tip.update_idletasks()
            sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
            x = min(x, max(0, sw - tip.winfo_reqwidth() - 8))
            y = min(y, max(0, sh - tip.winfo_reqheight() - 8))
            tip.geometry(f"+{max(0, x)}+{max(0, y)}")
            tip.deiconify()
            tip.lift()
            self._popup = tip
        except tk.TclError:
            self._popup = None

    def _hide(self, _event=None):
        self._cancel()
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None

    def _owner_destroyed(self, event):
        if event.widget is not self.widget:
            return
        self._hide()
        self.widget = None


class TokenBubbleEntry(tk.Frame):
    """Dark token/chip entry used for multi-term Rules Text searches."""

    def __init__(self, master=None, search_command=None, **kwargs):
        p = PALETTE
        super().__init__(
            master, bg=p["input"], highlightthickness=1,
            highlightbackground=p["border"], highlightcolor=p["accent"],
            bd=0, **kwargs)
        self._tokens = []
        self._search_command = search_command
        self._entry_reset_after = None
        # Keep the chip strip completely out of geometry while it is empty.
        # An empty packed Frame retains its last requested width after its last
        # chip is destroyed, which otherwise leaves a ghost gap that makes the
        # editable field look as though the removed chip is still present.
        self._chips = tk.Frame(self, bg=p["input"])
        self.entry = ClassicEntry(self, highlightthickness=0, layout_ipady=0)
        self.entry.pack(side="left", fill="both", expand=True, padx=5, pady=3)
        self.entry.bind("<Return>", self._on_return)
        self.entry.bind("<KP_Enter>", self._on_return)
        self.entry.bind("<BackSpace>", self._on_backspace, add="+")
        self.bind("<Destroy>", self._release_entry_reset, add="+")

    def focus_set(self):
        self.entry.focus_set()

    def _pending(self):
        return self.entry.get().strip().strip(",").strip()

    def _on_return(self, _event=None):
        if self._pending():
            self.add(self._pending())
        elif self._search_command:
            self._search_command()
        return "break"


    def _on_backspace(self, _event=None):
        if not self.entry.get() and self._tokens:
            self.remove(self._tokens[-1])
            return "break"

    def add(self, value):
        value = " ".join(str(value or "").strip().split())
        if not value:
            return
        if any(existing.casefold() == value.casefold() for existing in self._tokens):
            self.entry.delete(0, "end")
            self._reset_entry_view()
            return
        self._tokens.append(value)
        self.entry.delete(0, "end")
        self._redraw()
        self._reset_entry_view()

    def remove(self, value):
        folded = str(value).casefold()
        self._tokens = [v for v in self._tokens if v.casefold() != folded]
        self._redraw()
        self._reset_entry_view()

    def _remove_chip(self, value):
        """Remove a clicked chip and return editing focus to the true field start."""
        self.remove(value)
        try:
            self.entry.focus_set()
        except tk.TclError:
            pass

    def clear(self):
        self._tokens.clear()
        self.entry.delete(0, "end")
        self._redraw()
        self._reset_entry_view()

    def _apply_entry_view_reset(self):
        try:
            self.entry.selection_clear()
            self.entry.icursor(0)
            self.entry.xview_moveto(0.0)
        except tk.TclError:
            pass

    def _finish_entry_view_reset(self):
        self._entry_reset_after = None
        self._apply_entry_view_reset()

    def _reset_entry_view(self):
        """Return the editable field to its true left edge after chip changes."""
        self._apply_entry_view_reset()
        pending = self._entry_reset_after
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        try:
            # Run once more after Tk has recomputed the chip strip geometry.
            self._entry_reset_after = self.after_idle(
                self._finish_entry_view_reset)
        except tk.TclError:
            self._entry_reset_after = None

    def _release_entry_reset(self, event=None):
        if event is not None and event.widget is not self:
            return
        pending = self._entry_reset_after
        self._entry_reset_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass

    def values(self, commit_pending=True):
        if commit_pending and self._pending():
            self.add(self._pending())
        return list(self._tokens)

    def _redraw(self):
        p = PALETTE
        for child in self._chips.winfo_children():
            child.destroy()
        for value in self._tokens:
            chip = tk.Frame(self._chips, bg=p["surface3"], bd=0)
            chip.pack(side="left", padx=(0, 4))
            tk.Label(
                chip, text=value, bg=p["surface3"], fg=p["text"],
                font=FONT_HELPER, padx=6, pady=2).pack(side="left")
            ClassicButton(
                chip, text="×", command=lambda v=value: self._remove_chip(v),
                role="chip_close").pack(side="left", padx=(0, 2))
        if self._tokens:
            if not self._chips.winfo_manager():
                self._chips.pack(
                    side="left", fill="y", padx=(3, 0), pady=2,
                    before=self.entry)
        elif self._chips.winfo_manager():
            self._chips.pack_forget()
