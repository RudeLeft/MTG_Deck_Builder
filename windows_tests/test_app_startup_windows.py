"""Windows pre-build smoke tests for the full DeckBuilderApp.

Startup: construct the real application against an empty temporary portable
data directory, then destroy it before mainloop.  It catches startup ordering
regressions that source/geometry contracts cannot see (for example a feature
querying a Treeview before the deck pane has created it).

Layout fit: build the same application at the window sizes and display scalings
people actually run (1366x768 laptops, 1080p at 100/125/150%) and measure what
each control really got.  The v1.5.0 UI review found text drawn at the wrong
size, buttons squeezed to "G", colour choices that could not be clicked and
chips cut in half -- all invisible to checks that read style definitions or
source text.  Scaling is simulated the way the geometry gate does it (Tk's
``scaling`` before the styles are installed).
"""

import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

if sys.platform != "win32":
    print("SKIP: full Tk startup smoke test requires Windows.")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.ui.app import DeckBuilderApp
from mtgdb.ui.search import (
    CARD_TYPE_MIN_COLUMNS, FILTER_LABEL_GAP, MATCH_MODE_LABEL_WIDTH,
    RESULTS_MIN_ROWS, SECONDARY_LABEL_WIDTH,
)
from mtgdb.ui.styles import scaled_pixels
from mtgdb.ui.tokens import FONT_BODY, FONT_PANE_TITLE

# The real catalogs (Scryfall): the longest names decide the chip cell width.
CARD_TYPES = (
    "Artifact", "Battle", "Conspiracy", "Creature", "Dungeon", "Enchantment",
    "Hero", "Instant", "Kindred", "Land", "Phenomenon", "Plane",
    "Planeswalker", "Scheme", "Sorcery", "Vanguard")
SUPERTYPES = ("Basic", "Legendary", "Ongoing", "Snow", "World")

# (window width, window height, Tk scaling, label).  Heights are maximized-window
# client heights.  Every scenario is measured for horizontal fit and text size,
# and vertically with the Advanced panel both closed and open (LAY-011).
SCENARIOS = (
    (1280, 680, 96 / 72, "1280x720 at 100%"),
    (1366, 728, 96 / 72, "1366x768 at 100%"),
    (1380, 860, 96 / 72, "default 1380x860 at 100%"),
    (1920, 1032, 96 / 72, "1920x1080 at 100%"),
    (1920, 1032, 96 / 72 * 1.25, "1920x1080 at 125%"),
    (1920, 1032, 96 / 72 * 1.5, "1920x1080 at 150%"),
    # Tall enough that the Advanced panel nearly fits: the jump to its button is
    # then clamped, which is where a jump decided too early flashed at the top.
    (2560, 1392, 96 / 72, "2560x1440 at 100%"),
)


def _startup_smoke():
    with tempfile.TemporaryDirectory(prefix="mtg-startup-smoke-") as tmp:
        data_dir = Path(tmp) / "data"
        image_dir = data_dir / "card_images"
        data_dir.mkdir(parents=True, exist_ok=True)
        db = CardDB(str(data_dir / "cards.db"))
        app = None
        try:
            app = DeckBuilderApp(
                db,
                str(image_dir),
                str(data_dir),
                str(data_dir / "startup-smoke.log"),
            )
            # Constructor completion is the contract.  Do not enter mainloop or
            # let scheduled database maintenance/network work begin.
            if app._workspace_loaded:
                raise AssertionError(
                    "workspace must remain unloaded until async restore completes")
            if app._workspace_autosave_after is not None:
                raise AssertionError(
                    "autosave must not start before async workspace restore")
            app.withdraw()
            print("  [PASS] full DeckBuilderApp constructs before mainloop")
            return 0
        finally:
            if app is not None:
                try:
                    app.destroy()
                except Exception:
                    pass
            db.close()


# ---------------------------------------------------------------- layout fit
def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _pump(app, seconds):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.update()
        time.sleep(0.01)


def _fits(widget):
    """Mapped, and given at least the width its content asked for."""
    return bool(widget.winfo_ismapped()) and (
        widget.winfo_width() >= widget.winfo_reqwidth())


def _measure(app, style):
    failures = []
    body = tkfont.Font(root=app, font=FONT_BODY).actual()

    # TYP-003: a ttk label must draw its style's font, not the app default.
    for widget in _walk(app):
        if not isinstance(widget, ttk.Label):
            continue
        declared = style.lookup(str(widget.cget("style") or "TLabel"), "font")
        drawn = str(widget.cget("font"))
        if drawn and declared and tkfont.Font(
                root=app, font=drawn).actual() == body and tkfont.Font(
                root=app, font=declared).actual() != body:
            failures.append(
                f"label {widget.cget('text')!r} draws the body font instead of "
                f"its style's {declared}")

    # SIZ-003: every preview action, whole, in the fixed center column.
    for name in ("card_gallery_btn", "card_legality_btn", "card_flip_btn",
                 "card_rotate_btn", "card_zoom_btn"):
        button = getattr(app, name)
        if not _fits(button):
            failures.append(
                f"preview {button.cget('text')!r} gets "
                f"{button.winfo_width()} of {button.winfo_reqwidth()} px "
                f"(mapped={bool(button.winfo_ismapped())})")

    # Type Line chips carry their whole label (LAY-006).
    for title, frame, widgets in (
            ("Supertype", app._property_chip_frame, app._property_chip_widgets),
            ("Card Type", app._card_type_chip_frame, app._card_type_chip_widgets)):
        for chip in widgets:
            shell = getattr(chip, "_ui_chip_border_shell", None) or chip
            if not _fits(shell):
                failures.append(
                    f"{title} chip {chip.cget('text')!r} gets "
                    f"{shell.winfo_width()} of {shell.winfo_reqwidth()} px")

    # Mana Color: all six reachable inside the row (WIN-009).
    checks = list(app._color_checks.values())
    row_width = checks[0].master.winfo_width()
    for check in checks:
        if not _fits(check) or (
                check.winfo_x() + check.winfo_width() > row_width):
            failures.append(
                f"Mana Color {check.cget('text').strip()!r} is clipped or hidden")
    # The Advanced mana rows share that width; any that are on screen must fit.
    for row in app._mana_choice_rows:
        if row["frame"].winfo_ismapped():
            for widget in row["widgets"]:
                if not _fits(widget) or (
                        widget.winfo_x() + widget.winfo_width()
                        > row["frame"].winfo_width()):
                    failures.append(
                        f"Advanced mana choice {widget.cget('text').strip()!r} "
                        f"is clipped or hidden")

    # LAY-007: one label rail that holds the widest label of every row.
    rail = app._filter_label_width()
    if rail < scaled_pixels(app, 144):
        failures.append(f"label rail {rail}px is narrower than 144px scaled")
    labels = [
        child for frame in list(app._advanced_filter_rows.values())
        for child in frame.winfo_children()
        if isinstance(child, ttk.Label)
        and str(child.grid_info().get("column")) == "0"]
    for label in labels:
        if label.winfo_reqwidth() + FILTER_LABEL_GAP > rail:
            failures.append(
                f"label {label.cget('text')!r} needs "
                f"{label.winfo_reqwidth() + FILTER_LABEL_GAP}px of a {rail}px rail")
    for frame in app._advanced_filter_rows.values():
        if frame.grid_columnconfigure(0)["minsize"] != rail:
            failures.append("an Advanced row is not on the shared label rail")
            break
    secondary = scaled_pixels(app, MATCH_MODE_LABEL_WIDTH)
    for widget in _walk(app):
        if isinstance(widget, ttk.Label) and str(widget.cget("text")) in (
                "Match", "Use") and widget.winfo_reqwidth() > secondary:
            failures.append(
                f"{widget.cget('text')!r} outgrows the {secondary}px helper rail")
    if SECONDARY_LABEL_WIDTH != MATCH_MODE_LABEL_WIDTH:
        failures.append("the Match and helper rails must be one width")

    # LAY-005: tabs and the "+" glyph fit the fixed-height deck tab bar.
    bar = app._deck_tab_bar
    glyph = tkfont.Font(root=app, font=FONT_PANE_TITLE).metrics("linespace")
    if bar.winfo_height() < glyph + 2:
        failures.append(
            f"deck tab bar {bar.winfo_height()}px is shorter than the '+' "
            f"glyph ({glyph}px)")
    tabs = [child for child in bar.winfo_children()
            if child is not app._deck_plus_btn]
    if tabs and bar.winfo_height() < max(t.winfo_reqheight() for t in tabs):
        failures.append("a deck tab is taller than its bar")
    return failures


def _holds_content(popup):
    """A size-locked dialog is at least as big as the widgets inside it ask for."""
    popup.update_idletasks()
    return (popup.winfo_height() >= popup.winfo_reqheight()
            and popup.winfo_width() >= popup.winfo_reqwidth())


def _in_view(app, widget):
    zone = app._filter_zone
    top = zone.canvas.winfo_rooty()
    return (widget.winfo_rooty() >= top - 1 and widget.winfo_rooty()
            + widget.winfo_height() <= top + zone.canvas.winfo_height() + 1)


def _measure_vertical(app, state):
    """LAY-011: Search and Results stay on screen; the filters scroll."""
    failures = []
    zone = app._filter_zone
    pane = app._search_pinned_block.master
    bottom = pane.winfo_rooty() + pane.winfo_height()
    search = app._search_btn
    if not search.winfo_ismapped() or (
            search.winfo_rooty() + search.winfo_height() > bottom):
        failures.append(f"{state}: the Search button is off screen")
    rowheight = int(ttk.Style(app).lookup("Treeview", "rowheight") or 20)
    if app.results_tv.winfo_height() < RESULTS_MIN_ROWS * rowheight:
        failures.append(
            f"{state}: Results has {app.results_tv.winfo_height()}px, under "
            f"{RESULTS_MIN_ROWS} rows")
    overflow = zone.inner.winfo_reqheight() > zone.canvas.winfo_height() + 1
    awake = "disabled" not in zone.scrollbar.state()
    if overflow != awake:
        failures.append(
            f"{state}: the scrollbar is {'on' if awake else 'idle'} but the "
            f"filters {'overflow' if overflow else 'fit'}")
    if not zone.scrollbar.winfo_ismapped():
        failures.append(f"{state}: the scrollbar gutter is not reserved")
    gap = zone.scrollbar.winfo_rootx() - (
        zone.canvas.winfo_rootx() + zone.canvas.winfo_width())
    if gap < scaled_pixels(app, 6) - 1:
        failures.append(f"{state}: the controls touch the scrollbar (gap {gap}px)")
    wanted = zone.ACTIVE_STYLE if overflow else zone.IDLE_STYLE
    if str(zone.scrollbar.cget("style")) != wanted:
        failures.append(
            f"{state}: the scrollbar is drawn "
            f"{'idle' if overflow else 'awake'} when the filters "
            f"{'overflow' if overflow else 'fit'}")
    if app._advanced_expanded:
        top = zone.canvas.winfo_rooty()
        at_top = app._advanced_header.winfo_rooty() <= top + 24
        at_end = zone.canvas.canvasy(0) >= (
            zone.inner.winfo_reqheight() - zone.canvas.winfo_height() - 1)
        if not (at_top or at_end):
            failures.append(
                f"{state}: Advanced did not open with its button at the top")
    elif zone.canvas.canvasy(0) != 0:
        failures.append(f"{state}: closing Advanced did not return to the first row")
    return failures


def _measure_zone_input(app):
    """The wheel scrolls the zone without editing a field; focus scrolls into view."""
    failures = []
    zone = app._filter_zone
    for name in ("q_power_min", "q_released_min"):
        field = getattr(app, name, None)
        if field is None:
            continue
        zone.reveal(field)
        _pump(app, 0.2)
        before = field.get()
        field.event_generate("<MouseWheel>", delta=-120, x=4, y=4)
        _pump(app, 0.2)
        if field.get() != before:
            failures.append(f"the wheel over {name} edited it")
    if zone.inner.winfo_reqheight() > zone.canvas.winfo_height() + 1:
        zone.scroll_to_top()
        _pump(app, 0.3)
        app.q_released_max.focus_force()
        _pump(app, 0.3)
        if not _in_view(app, app.q_released_max):
            failures.append("focusing an off-screen filter did not scroll it into view")
        app.q_name.focus_force()
        _pump(app, 0.3)
    return failures


def _measure_dialogs(app, style):
    failures = []
    app._show_card({
        "id": "c1", "name": "Bear", "type_line": "Creature",
        "legalities": {"modern": "legal", "standard": "legal"}})
    _pump(app, 0.5)
    app._open_card_legality()
    _pump(app, 0.5)
    legality = app._preview_legality_popup
    app._legality_problems = ["Deck has too many copies of Bear"]
    app.deck.fmt = "Modern"
    before = set(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))
    app._show_legality_details()
    _pump(app, 0.5)
    format_check = [w for w in app.winfo_children()
                    if isinstance(w, tk.Toplevel) and w not in before][0]

    sizes = {}
    for name, popup in (("Card Legality", legality),
                        ("Basic Format Check", format_check)):
        close = [w for w in _walk(popup)
                 if w.winfo_class() in ("TButton", "Button")
                 and str(w.cget("text")) == "Close"]
        sizes[name] = (close[0].winfo_class(), close[0].winfo_width(),
                       close[0].winfo_height()) if close else None
        if close and (close[0].winfo_height() < close[0].winfo_reqheight()
                      or close[0].winfo_width() < close[0].winfo_reqwidth()
                      or not close[0].winfo_ismapped()):
            failures.append(f"{name}: the Close button is squeezed or hidden")
        title = [w for w in _walk(popup) if isinstance(w, ttk.Label)
                 and str(w.cget("style")) == "DialogTitle.TLabel"]
        if not title or str(title[0].cget("font")):
            failures.append(f"{name}: title does not draw its style font")
        else:
            wanted = tkfont.Font(
                root=app, font=style.lookup("DialogTitle.TLabel", "font")
            ).metrics("linespace")
            if title[0].winfo_reqheight() < wanted:
                failures.append(f"{name}: title is shorter than a 15 pt line")
        for bar in [w for w in _walk(popup) if isinstance(w, ttk.Scrollbar)]:
            if bar.winfo_ismapped():
                failures.append(f"{name}: a scrollbar is drawn with nothing to scroll")
    if None in sizes.values() or len(set(sizes.values())) != 1:
        failures.append(f"Close buttons differ between dialogs: {sizes}")
    if not _holds_content(legality):
        failures.append("Card Legality is smaller than its content")
    for popup in (format_check, legality):
        popup.destroy()

    # Progress dialogs are size-locked too; their last line must still be there.
    app._show_print_popup(60)
    _pump(app, 0.4)
    if not _holds_content(app._print_popup):
        failures.append("the Print popup is smaller than its content")
    app._print_popup.destroy()
    app._show_sync_popup("first_launch")
    _pump(app, 0.4)
    if not _holds_content(app._sync_popup):
        failures.append("the database progress popup is smaller than its content")
    app._sync_popup.destroy()

    # The modal comparison notice blocks until closed, so measure from a timer.
    notice = []

    def inspect_notice():
        for popup in [w for w in app.winfo_children()
                      if isinstance(w, tk.Toplevel)]:
            buttons = [w for w in _walk(popup)
                       if w.winfo_class() in ("TButton", "Button")]
            if not _holds_content(popup) or not buttons or any(
                    b.winfo_height() < b.winfo_reqheight() for b in buttons):
                notice.append("the comparison notice clips its OK button")
            popup.destroy()

    app.after(500, inspect_notice)
    app._show_comparison_notice(
        "Compare Cards",
        "Select at least two cards in Results or a deck board, then choose Compare.")
    failures.extend(notice)
    return failures


def _run_scenario(width, height, scaling, label):
    original = {name: getattr(DeckBuilderApp, name) for name in (
        "_setup_style", "_maximize_window", "_maybe_auto_sync")}

    def scaled_setup(self):
        self.tk.call("tk", "scaling", scaling)
        return original["_setup_style"](self)

    def invisible_fixed_size(self):
        # Replaces "open maximized": a fixed size, fully transparent from the
        # first mapped frame, since this is a measurement and not a demo.
        self.attributes("-alpha", 0.0)

    DeckBuilderApp._setup_style = scaled_setup
    DeckBuilderApp._maximize_window = invisible_fixed_size
    # No network: an empty library would otherwise start the first-run download.
    DeckBuilderApp._maybe_auto_sync = lambda self: None
    with tempfile.TemporaryDirectory(prefix="mtg-layout-fit-") as tmp:
        data_dir = Path(tmp) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db = CardDB(str(data_dir / "cards.db"))
        app = None
        try:
            app = DeckBuilderApp(
                db, str(data_dir / "card_images"), str(data_dir),
                str(data_dir / "layout-fit.log"))
            app.state("normal")
            app.geometry(f"{width}x{height}+0+0")
            _pump(app, 1.5)
            # Install the real Type Line vocabulary and let the layout settle.
            app._search_type_line_cold_start = False
            app._card_type_catalog = list(CARD_TYPES)
            app._property_catalog = list(SUPERTYPES)
            app._card_type_chip_columns = CARD_TYPE_MIN_COLUMNS
            app._card_type_chip_widgets = app._render_trusted_chips(
                app._card_type_chip_frame, app._card_type_catalog,
                app.card_type_vars, columns=CARD_TYPE_MIN_COLUMNS,
                tooltip_key="card_type")
            app._render_supertype_chips()
            _pump(app, 1.0)
            style = ttk.Style(app)
            # A click, like a person's: opening scrolls Advanced to the top.  Record
            # every position the view takes: it may rest at its start and at its
            # end, and pass through nothing else (no flash of the button at the
            # top before it drops to where it belongs).
            zone = app._filter_zone
            positions = []
            report = zone.scrollbar.set

            def record(first, last):
                positions.append(round(zone.canvas.canvasy(0)))
                report(first, last)

            zone.canvas.configure(yscrollcommand=record)
            _pump(app, 0.3)
            positions.clear()
            resting = round(zone.canvas.canvasy(0))
            app._toggle_advanced_filters()
            _pump(app, 1.5)
            failures = []
            # Where it started, where it ends up, and nothing in between.
            if len(set(positions) | {resting}) > 2:
                failures.append(
                    f"opening Advanced moved the view {resting} -> "
                    f"{sorted(set(positions))}")
            failures += _measure(app, style)
            failures += _measure_vertical(app, "Advanced open")
            failures += _measure_zone_input(app)
            positions.clear()
            resting = round(zone.canvas.canvasy(0))
            app._toggle_advanced_filters()
            _pump(app, 1.0)
            if len(set(positions) | {resting}) > 2:
                failures.append(
                    f"closing Advanced moved the view {resting} -> "
                    f"{sorted(set(positions))}")
            failures += _measure_vertical(app, "Advanced closed")
            failures += _measure_dialogs(app, style)
            return failures
        finally:
            for name, function in original.items():
                setattr(DeckBuilderApp, name, function)
            if app is not None:
                try:
                    app.destroy()
                except Exception:
                    pass
            db.close()


def _layout_fit():
    # BLD-004: like the simulated-scaling geometry gate, this measures real font
    # metrics, which the headless CI runner does not reproduce; it is a local
    # build gate.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("SKIP: layout-fit measurement is a local build gate (BLD-004).")
        return 0
    status = 0
    for width, height, scaling, label in SCENARIOS:
        failures = _run_scenario(width, height, scaling, label)
        if failures:
            status = 1
            print(f"  [FAIL] layout fits at {label}")
            for failure in failures:
                print(f"         - {failure}")
        else:
            print(f"  [PASS] layout fits at {label}")
    return status


def main():
    status = _startup_smoke()
    status = _layout_fit() or status
    return status


if __name__ == "__main__":
    raise SystemExit(main())
