"""Regression coverage for four cloud-review cleanup/latent findings.

- ui/components.py: ClassicButton forwards the tk.Button API it wraps.
- deck/analysis.py: analyze_deck and opening_land_stats share one land-odds
  formula instead of two copies that could drift apart.
- ui/window.py: the horizontal axis of the autohide scrollbar helper actually
  does something, instead of silently no-opping.
- ui/components.py: AppButton/AppMenubutton share one click-pulse
  implementation instead of two identical copies.

#30 (the unused _legal_summary wrapper) has no standalone check here: removing
it is exercised by the existing test_hardening_regressions.py and
test_image_architecture.py call sites, which were updated to call
_legal_format_rows (the real, live method) directly, and the guardrail at
test_project_guardrails.py already asserts CardDetailMixin exclusively owns it.
"""

from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.analysis import analyze_deck, opening_land_stats
from mtgdb.deck.model import Deck
from mtgdb.ui.components import AppButton, AppMenubutton, ClassicButton


def _card(card_id, name, type_line, **extra):
    row = {"id": card_id, "name": name, "type_line": type_line}
    row.update(extra)
    return row


def _classic_button_api_check(root):
    """ClassicButton must honour every option its docstring claims to forward.

    It wraps a tk.Button in a border frame (needed to actually paint a
    border on Windows Tk -- see the class docstring), but a caller reasonably
    expects it to behave like the tk.Button it replaced.
    """
    btn = ClassicButton(root, text="Hi", role="secondary")
    try:
        btn.configure(text="Bye")
        configure_forwards = btn.cget("text") == "Bye" == btn["text"]
        btn["state"] = "disabled"
        item_setitem_forwards = btn.cget("state") == "disabled"
        seen = []
        btn.bind("<Enter>", lambda _event: seen.append("outer"))
        # The inner button (which covers nearly the whole frame) must also
        # carry the hover binding, or a tooltip attached to this widget would
        # almost never see the pointer actually enter/leave.
        inner_has_enter = bool(btn._ui_button.bind("<Enter>"))
        return configure_forwards and item_setitem_forwards and inner_has_enter
    finally:
        btn.destroy()


def _land_odds_shared_formula_check():
    """analyze_deck and opening_land_stats must report identical odds.

    They previously carried two independent copies of the same formula
    (analyze_deck computed it inline from its own single-pass counts;
    opening_land_stats did its own separate traversal), which a future change
    to the draw-count clamp or probability bounds could land in only one of.
    """
    deck = Deck()
    deck.add(_card("land-1", "Forest", "Basic Land — Forest"), "main", 17)
    deck.add(_card("spell-1", "Bolt", "Instant", cmc=1), "main", 23)

    snapshot = analyze_deck(deck)
    _size, _lands, inline_average, inline_probability = snapshot.opening_land_stats
    _size2, _lands2, standalone_average, standalone_probability = (
        opening_land_stats(deck))

    return (
        inline_average == standalone_average
        and inline_probability == standalone_probability
        # Sanity: the numbers are not both trivially zero (a bug that made
        # both copies return 0.0 would otherwise still "agree").
        and inline_average > 0 and inline_probability > 0)


def _horizontal_autohide_check(root):
    """The horizontal axis must actually enable/disable the scrollbar.

    The apply() closure only implemented `if axis == "vertical":` with no
    else branch, so axis="horizontal" silently did nothing regardless of
    whether the content actually overflowed the viewport.
    """
    from mtgdb.ui.window import WindowServicesMixin

    class _Host(WindowServicesMixin, tk.Frame):
        def __init__(self, master):
            tk.Frame.__init__(self, master)

    host = _Host(root)
    canvas = tk.Canvas(host, width=100, height=50)
    inner = tk.Frame(canvas)
    window = canvas.create_window(0, 0, window=inner, anchor="nw")
    scrollbar = ttk.Scrollbar(host, orient="horizontal")
    try:
        host._bind_autohide_canvas_scrollbar(
            canvas, inner, window, scrollbar, axis="horizontal")
        # Content wider than the canvas viewport.
        wide = tk.Frame(inner, width=500, height=10)
        wide.pack()
        canvas.update_idletasks()
        host.update_idletasks()
        # Drive the same idle-scheduled apply() the <Configure> bindings use.
        canvas.event_generate("<Configure>")
        host.update_idletasks()
        host.after(50, lambda: None)
        host.update()
        enabled_when_overflowing = "disabled" not in scrollbar.state()
        return enabled_when_overflowing
    finally:
        host.destroy()


def _click_pulse_shared_mixin_check():
    """AppButton and AppMenubutton must share one click-pulse implementation.

    Each previously carried an identical ~35-line copy of
    _pulse_click_feedback/_clear_click_feedback/_release_click_feedback,
    which a future tweak to pulse timing or bounds/disabled handling could
    land in only one of.
    """
    source = (ROOT / "mtgdb/ui/components.py").read_text(encoding="utf-8")
    has_mixin = "_TtkClickPulseMixin" in source
    button_uses_mixin = "class AppButton(_TtkClickPulseMixin" in source
    menubutton_uses_mixin = "class AppMenubutton(_TtkClickPulseMixin" in source
    # Exactly two definitions of each method should remain: one shared by
    # AppButton/AppMenubutton on the mixin, and ClassicButton's own separate
    # implementation (a different, non-ttk widget with different mechanics,
    # legitimately not part of this consolidation) -- not three, which is
    # what AppButton and AppMenubutton each carrying their own copy meant.
    two_copies_each = all(
        source.count(f"def {name}(") == 2
        for name in ("_pulse_click_feedback", "_clear_click_feedback",
                     "_release_click_feedback"))
    return (
        has_mixin and button_uses_mixin and menubutton_uses_mixin
        and two_copies_each
        and AppButton.CLICK_PULSE_MS >= 100
        and AppMenubutton.CLICK_PULSE_MS >= 100)


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        checks = {
            "ClassicButton forwards configure/cget/__getitem__/__setitem__ "
            "and hover binds to the inner button":
                _classic_button_api_check(root),
            "analyze_deck and opening_land_stats share one land-odds formula":
                _land_odds_shared_formula_check(),
            "horizontal autohide scrollbar enables when content overflows":
                _horizontal_autohide_check(root),
            "AppButton/AppMenubutton share one click-pulse mixin":
                _click_pulse_shared_mixin_check(),
        }
    finally:
        root.destroy()
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nREVIEW FIXES (CLUSTER 5):", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
