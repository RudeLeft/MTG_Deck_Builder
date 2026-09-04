"""Windows geometry checks for the shared UI component families.

Run at simulated Tk scaling values corresponding to 100%, 125%, and 150%.
The script creates real Tk widgets inside one permanently withdrawn root,
measures their requested geometry, and exits nonzero if the root becomes visible,
a shared role clips text, or a role drifts from the height of its paired role.
This is a deterministic Tk geometry check, not certification of Windows OS or
per-monitor DPI behavior.
"""

import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

if sys.platform != "win32":
    print("SKIP: Windows UI geometry checks require Windows.")
    raise SystemExit(0)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mtgdb.ui.components import (
    AppButton, AppCombobox, AppEntry, AppMenubutton, AppSpinbox,
    ClassicCheckbutton,
)
from mtgdb.ui.comparison import comparison_layout_metrics
from mtgdb.ui.comparison_controls import comparison_action_columns
from mtgdb.ui.search_checklist import VirtualChecklistView
from mtgdb.ui.styles import install_ui_styles
from mtgdb.ui.tokens import (
    COMPARISON_WINDOW_SIZE, MAIN_CENTER_COLUMN_WIDTH, MAIN_SIDE_MIN_WIDTH,
    PANEL_PADDING, PALETTE,
)


def _same_height(widgets, tolerance=2):
    heights = [widget.winfo_reqheight() for widget in widgets]
    return max(heights) - min(heights) <= tolerance, heights


def _text_fits(widget):
    text = str(widget.cget("text") or "")
    style_name = str(widget.cget("style") or "TButton")
    font_spec = ttk.Style(widget).lookup(style_name, "font")
    font = tkfont.Font(root=widget, font=font_spec)
    # Tk renders explicit newlines as separate lines. Measuring the raw string
    # treats the newline like horizontal content and creates a false clipping
    # failure for intentional two-line action labels. The widest rendered line
    # is the actual horizontal requirement.
    lines = text.splitlines() or [""]
    measured = max(font.measure(line) for line in lines)
    return widget.winfo_reqwidth() >= measured, (
        text, widget.winfo_reqwidth(), measured)


def _root_is_invisible(root):
    return root.state() == "withdrawn" and not root.winfo_viewable()


def _install_geometry_owner_contract(root):
    """Provide app-owner hooks required by components under geometry test.

    The real DeckBuilderApp supplies scroll registration through
    WindowServicesMixin.  This geometry gate intentionally uses exactly one
    plain hidden Tk root, so install a no-op registration hook on that root
    instead of constructing a second application/window-services owner.
    """
    root._register_scrollable = lambda _widget, target=None: target


def _run_scale(root, percent, scaling):
    root.tk.call("tk", "scaling", scaling)
    install_ui_styles(root)

    shell = tk.Frame(root)
    shell.pack()
    standard = AppButton(shell, text="Secondary", role="standard")
    primary = AppButton(shell, text="Primary", role="primary")
    compact = AppButton(shell, text="Cancel", role="compact")
    compact_primary = AppButton(shell, text="Apply", role="compact_primary")
    preview_legality = AppButton(shell, text="Legality", role="compact")
    preview_rotate = AppButton(shell, text="Rotate", role="compact")
    preview_zoom = AppButton(shell, text="Zoom", role="compact")
    view_hand = AppButton(shell, text="View hand", role="compact")
    deck = AppButton(shell, text="Move to Sideboard", role="deck")
    search_secondary = AppButton(shell, text="Add Sideboard", role="standard")
    search_primary = AppButton(shell, text="Add Mainboard", role="primary")
    comparison_remove_mainboard = AppButton(
        shell, text="Remove from\nMainboard", role="compact")
    comparison_remove_sideboard = AppButton(
        shell, text="Remove from\nSideboard", role="compact")
    comparison_holder = tk.Frame(shell)
    compare_add = AppButton(
        comparison_holder, text="Add Selected", role="standard")
    compare_menu = AppMenubutton(
        comparison_holder, text="Compared 0/7", role="picker")
    compare_primary = AppButton(
        comparison_holder, text="Compare", role="primary")
    compare_clear = AppButton(
        comparison_holder, text="Clear", role="standard")
    protected_compare = AppButton(
        shell, text="Compare", role="primary", width=5)

    entry = AppEntry(shell)
    combo = AppCombobox(shell, values=("Any format",), state="readonly")
    spin = AppSpinbox(shell, from_=0, to=30)
    picker = AppButton(shell, text="Any", role="picker")
    selected_var = tk.BooleanVar(value=True)
    selected_chip = ClassicCheckbutton(
        shell, text="Creature", variable=selected_var, role="chip")

    # Advanced Content uses the shared label/control grid.  The compact
    # Cards | Tokens | Emblems | Art Series group starts at the same x-position
    # as the picker controls beneath it and does not stretch across the row.
    advanced_holder = ttk.Frame(shell, width=MAIN_SIDE_MIN_WIDTH)
    advanced_holder.columnconfigure(1, weight=1)
    ttk.Label(advanced_holder, text="Content").grid(
        row=0, column=0, sticky="w", padx=(0, 8), pady=2)
    content_holder = ttk.Frame(advanced_holder)
    content_holder.grid(row=0, column=1, sticky="w", pady=2)
    content_vars = [tk.BooleanVar(master=root) for _ in range(4)]
    content_chips = [
        ClassicCheckbutton(
            content_holder, text=text, variable=content_vars[index],
            indicatoron=False, role="chip", padx=1)
        for index, text in enumerate(("Cards", "Tokens", "Emblems", "Art Series"))
    ]
    content_separators = []
    for index, chip in enumerate(content_chips):
        column = index * 2
        chip.grid(row=0, column=column, sticky="w", padx=0, pady=0)
        if index < len(content_chips) - 1:
            separator = ttk.Label(content_holder, text="|", style="Muted.TLabel")
            separator.grid(row=0, column=column + 1, sticky="ns", padx=(1, 1))
            content_separators.append(separator)
    content_reference = AppButton(advanced_holder, text="Any", role="picker")
    content_reference.grid(row=1, column=1, sticky="ew", pady=2)

    # Exercise virtual-filter row math at each simulated scaling value without
    # mapping a window. The configured-height path is the same one used after
    # the hidden picker is sized and catches bottom-row clipping at high DPI.
    format_values = [("", "Any")] + [
        (f"format-{index}", f"Format {index}") for index in range(15)
    ] + [("penny", "Penny")]
    format_view = VirtualChecklistView(
        root, shell, values=format_values, selected={"", "format-1"},
        single_select=True, height=390)
    format_height = format_view.preferred_height_for_items(
        len(format_values), row_height=format_view.effective_row_height)
    format_height += format_view.SINGLE_SELECT_VIEWPORT_EXTRA
    format_view.configure(height=format_height)
    format_view._on_configure(type("Event", (), {"height": format_height})())

    long_view = VirtualChecklistView(
        root, shell, values=[(str(i), f"Choice {i}") for i in range(60)],
        height=390)
    long_height = long_view.preferred_height_for_items(
        60, row_height=long_view.effective_row_height)
    long_view.configure(height=long_height)
    long_view._on_configure(type("Event", (), {"height": long_height})())

    short_view = VirtualChecklistView(
        root, shell, values=(("c", "Common"), ("u", "Uncommon"),
                             ("r", "Rare"), ("m", "Mythic")),
        height=120)
    short_height = short_view.preferred_height_for_items(
        4, row_height=short_view.effective_row_height)
    short_view.configure(height=short_height)
    short_view._on_configure(type("Event", (), {"height": short_height})())

    widgets = [
        standard, primary, compact, compact_primary, preview_legality, preview_rotate, preview_zoom,
        view_hand, deck, search_secondary, search_primary,
        comparison_remove_mainboard, comparison_remove_sideboard,
        protected_compare, entry, combo, spin, picker,
        selected_chip,
    ]
    for widget in widgets:
        widget.pack()
    advanced_holder.pack(fill="x")
    comparison_holder.pack()
    compare_add.pack(side="left")
    compare_menu.pack(side="left", padx=(6, 0))
    compare_primary.pack(side="left", padx=(6, 0))
    compare_clear.pack(side="left", padx=(6, 0))
    root.update_idletasks()

    standard_ok, standard_heights = _same_height(
        [standard, primary, search_secondary, search_primary,
         compare_add, compare_primary, compare_clear])
    compact_ok, compact_heights = _same_height(
        [compact, compact_primary, preview_legality, preview_rotate, preview_zoom, view_hand])
    deck_larger = deck.winfo_reqheight() > compact.winfo_reqheight()
    form_ok, form_heights = _same_height([entry, combo, spin, picker], tolerance=4)
    text_results = [_text_fits(widget) for widget in (
        standard, primary, compact, compact_primary, preview_legality, preview_rotate, preview_zoom,
        view_hand, deck, search_secondary, search_primary,
        comparison_remove_mainboard, comparison_remove_sideboard,
        compare_add, compare_primary, compare_clear, protected_compare,
    )]
    content_row_width = (
        sum(widget.winfo_reqwidth() for widget in content_chips)
        + sum(separator.winfo_reqwidth() for separator in content_separators)
    )

    seven_layout = comparison_layout_metrics(
        COMPARISON_WINDOW_SIZE[0], COMPARISON_WINDOW_SIZE[1], 7,
        tk_scaling=scaling)

    comparison_action_widths = tuple(widget.winfo_reqwidth() for widget in (
        compare_add, compare_menu, compare_primary, compare_clear))
    wrap_columns = {
        width: comparison_action_columns(width, comparison_action_widths)
        for width in (300, 500, 800)
    }
    def wrap_fits(width, columns):
        gap = 6
        if columns == 4:
            needed = sum(comparison_action_widths) + gap * 3
        elif columns == 2:
            needed = max(
                comparison_action_widths[0] + comparison_action_widths[2],
                comparison_action_widths[1] + comparison_action_widths[3]) + gap
        else:
            needed = max(comparison_action_widths)
        return needed <= width

    checks = {
        "geometry root remains invisible": _root_is_invisible(root),
        "standard family heights": standard_ok,
        "compact family heights": compact_ok,
        "deck controls are visibly larger than compact controls": deck_larger,
        "comparison bar actions match standard button height": standard_ok,
        "comparison bar wraps without clipping across narrow/wide panes": all(
            wrap_fits(width, columns) for width, columns in wrap_columns.items()),
        "comparison manage picker matches form-control height": (
            abs(compare_menu.winfo_reqheight() - picker.winfo_reqheight()) <= 2),
        "fixed card preview width fits Legality Rotate and Zoom": (
            sum(widget.winfo_reqwidth() for widget in (
                preview_legality, preview_rotate, preview_zoom)) + 12
            <= MAIN_CENTER_COLUMN_WIDTH - (2 * PANEL_PADDING)),
        "seven-card fixed comparison grid remains readable": (
            seven_layout["columns"] == 4
            and seven_layout["rows"] == 2
            and seven_layout["image_w"] >= 290
            and seven_layout["image_h"] >= 410),
        "comparison side rail fits source-board actions": (
            max(
                comparison_remove_mainboard.winfo_reqwidth(),
                comparison_remove_sideboard.winfo_reqwidth(),
            ) <= seven_layout["meta_width"]),
        "form field and picker heights": form_ok,
        "every tested text label fits": all(result[0] for result in text_results),
        "undersized Compare width is expanded": (
            int(protected_compare.cget("width")) >= len("Compare")),
        "selected gold chip uses dark text": (
            selected_chip.cget("fg") == PALETTE["on_accent"]),
        "compact Content row fits the Advanced control column": (
            content_row_width <= MAIN_SIDE_MIN_WIDTH
            and content_holder.winfo_x() + content_chips[0].winfo_x()
                == content_reference.winfo_x()),
        "Format Any normalizes every specific format away": (
            format_view.selected_values() == {""}),
        "Format radio rows fit DPI-scaled requested height with five-pixel clearance": (
            format_view.SINGLE_SELECT_VIEWPORT_EXTRA == 5
            and format_view.effective_row_height
                >= format_view._rows[0].winfo_reqheight() + 2
            and sum(
                format_view._body.grid_rowconfigure(i)["minsize"]
                for i in range(format_view._visible_rows)
            ) <= format_height - 2),
        "scrolling filter rows consume the long-list viewport": (
            len(long_view.visible_keys) > long_view._capacity
            and sum(
                long_view._body.grid_rowconfigure(i)["minsize"]
                for i in range(long_view._visible_rows)
            ) >= long_height - 6),
        "short Rarity-style lists remain compact": (
            len(short_view.visible_keys) <= short_view._capacity
            and short_view._body.grid_rowconfigure(0)["minsize"] == 0),
    }

    details = {
        "standard": standard_heights,
        "compact": compact_heights,
        "comparison_widths": (
            compare_add.winfo_width(), compare_menu.winfo_width(),
            compare_primary.winfo_width(), compare_clear.winfo_width()),
        "form": form_heights,
        "content_row_width": content_row_width,
        "text": [detail for passed, detail in text_results if not passed],
    }
    ok = True
    print(f"\n  Simulated Tk scale: {percent}% (Tk {scaling:.4f})")
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("  Geometry:", details)
    shell.destroy()
    root.update_idletasks()
    ok &= _root_is_invisible(root)
    return ok


def main():
    scales = (
        (100, 96 / 72),
        (125, 120 / 72),
        (150, 144 / 72),
    )
    root = tk.Tk()
    root.withdraw()
    _install_geometry_owner_contract(root)
    root.geometry("1x1-32000-32000")
    root.overrideredirect(True)
    try:
        root.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    try:
        results = [
            _run_scale(root, percent, scaling)
            for percent, scaling in scales
        ]
        ok = all(results) and _root_is_invisible(root)
    finally:
        root.destroy()
    print("\nWINDOWS SIMULATED TK GEOMETRY (100/125/150):",
          "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
