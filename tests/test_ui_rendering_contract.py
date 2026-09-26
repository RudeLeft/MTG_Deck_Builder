"""What the UI actually DRAWS, not just what its style definitions say.

The v1.5.0 UI review found that every check passed while every dialog title drew
10 pt regular: the contract tests read ``style.configure`` calls, and an
application-wide ``*Font`` option silently beat them.  These checks measure real
Tk widgets (TYP-003), the status/mana colours (CLR-007, CLR-008), and the
responsive helpers (LAY-006, LAY-007, WIN-009) that decide whether text fits.
"""

import ast
import os
import re
import sys
import time
import tkinter as tk
from tkinter import font as tkfont, ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mtgdb.ui.components as C
import mtgdb.ui.mana as MANA
import mtgdb.ui.search as SearchUI
import mtgdb.ui.set_filters as SetFilters
import mtgdb.ui.styles as S
import mtgdb.ui.tokens as T
from mtgdb.ui.window import WindowServicesMixin

UI_DIR = os.path.join(ROOT, "mtgdb", "ui")


def _read(name):
    with open(os.path.join(UI_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def _ui_sources():
    return {name: _read(name) for name in sorted(os.listdir(UI_DIR))
            if name.endswith(".py")}


def _luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _calls(tree, dotted):
    """Every call to a function spelled *dotted* (``tk.Label`` or ``AppButton``)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (f"{func.value.id}.{func.attr}"
                    if isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    else func.id if isinstance(func, ast.Name) else "")
            if name == dotted:
                found.append(node)
    return found


def _keyword(node, name):
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _literal(node):
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, SyntaxError):
        return None


class _Scaled:
    """A widget whose Tk reports a chosen scaling (or fails to answer)."""

    def __init__(self, scaling):
        self.tk = self
        self.scaling = scaling

    def call(self, *_args):
        if isinstance(self.scaling, Exception):
            raise self.scaling
        return self.scaling


class _Chip:
    def __init__(self, width):
        self.width = width
        self.grid_options = {}

    def winfo_reqwidth(self):
        return self.width

    def winfo_exists(self):
        return True

    def grid_configure(self, **options):
        self.grid_options.update(options)

    grid = grid_configure


class _Frame:
    def __init__(self, width):
        self.width = width
        self.columns = {}

    def winfo_width(self):
        return self.width

    def columnconfigure(self, column, **options):
        self.columns[column] = options


class _Scrollbar:
    def __init__(self):
        self.visible = True
        self.range = None

    def set(self, first, last):
        self.range = (first, last)

    def grid_remove(self):
        self.visible = False

    def grid(self):
        self.visible = True


def _typography_checks(results):
    try:
        root = tk.Tk()
    except tk.TclError:
        print("  [SKIP] no display: drawn-font checks need Tk")
        return
    root.withdraw()
    try:
        S.install_ui_styles(root)
        style = ttk.Style(root)
        label_styles = (
            "Muted.TLabel", "Section.TLabel", "SectionAlert.TLabel",
            "SectionAlertDim.TLabel", "SectionWorking.TLabel",
            "SectionWorkingDim.TLabel", "DialogTitle.TLabel",
            "RaisedDialogTitle.TLabel", "SearchHoverMuted.TLabel",
            "ActivityWorking.TLabel")
        drift = []
        for name in label_styles:
            declared = style.lookup(name, "font")
            plain = ttk.Label(root, text="Heading", style=name)
            explicit = ttk.Label(root, text="Heading", style=name, font=declared)
            plain.pack()
            explicit.pack()
            root.update_idletasks()
            # A widget-level font (the `*Font` default) is what used to win.
            if str(plain.cget("font")) or (
                    plain.winfo_reqheight() != explicit.winfo_reqheight()
                    or plain.winfo_reqwidth() != explicit.winfo_reqwidth()):
                drift.append(name)
            plain.destroy()
            explicit.destroy()
        results["ttk labels draw the font their style declares (TYP-003)"] = (
            not drift)
        if drift:
            print("    drift:", drift)

        title = tkfont.Font(
            root=root, font=style.lookup("DialogTitle.TLabel", "font")).actual()
        section = tkfont.Font(
            root=root, font=style.lookup("Section.TLabel", "font")).actual()
        results["dialog titles are 15 pt bold and headings bold, as TYP-002 says"] = (
            title["size"] == 15 and title["weight"] == "bold"
            and section["weight"] == "bold")

        # The filter zone's idle bar paints nothing but the surface it sits on.
        idle = C.ScrollZone.IDLE_STYLE
        results["the filter zone's idle scrollbar paints only the surface (LAY-011)"] = all(
            style.lookup(idle, option) == T.PALETTE["surface"]
            for option in ("background", "troughcolor", "bordercolor",
                           "darkcolor", "lightcolor", "arrowcolor")) and (
            style.lookup(idle, "arrowsize") == style.lookup(
                C.ScrollZone.ACTIVE_STYLE, "arrowsize"))

        # The default must still reach the widgets that rely on it.
        entry = ttk.Entry(root)
        entry.pack()
        root.update_idletasks()
        results["form fields still take the body font from the app default"] = (
            tkfont.Font(root=root, font=entry.cget("font")).actual()["size"]
            == T.FONT_BODY[1])

        # A disabled scrollbar (nothing to scroll) melts into its trough.
        background = dict(
            (state, colour) for state, colour in style.map(
                "Dark.Vertical.TScrollbar", "background") if colour)
        results["a disabled scrollbar is not a full-length gold bar (UI-016)"] = (
            background.get("disabled") == T.PALETTE["surface2"]
            and background.get("disabled") != T.PALETTE["accent"])
    finally:
        root.destroy()

    root = tk.Tk()
    root.withdraw()
    try:
        root.tk.call("tk", "scaling", 96 / 72 * 1.25)
        rail_125 = SearchUI.SearchFeatureMixin._filter_label_width(root)
    finally:
        root.destroy()
    root = tk.Tk()
    root.withdraw()
    try:
        root.tk.call("tk", "scaling", 96 / 72)
        rail_100 = SearchUI.SearchFeatureMixin._filter_label_width(root)
    finally:
        root.destroy()
    results["the primary label rail is 144 px at 100% and follows display scaling"] = (
        rail_100 == 144 and rail_125 == 180)


def _method_source(source, name):
    """Source of one function or method, found by name."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _scroll_zone_checks(results, sources):
    """The scrolling filter zone (LAY-011), measured on real Tk widgets."""
    search = sources["search.py"]
    pane = _method_source(search, "_build_search_pane")
    toggle = _method_source(search, "_toggle_advanced_filters")
    results["the filter zone is packed last, so the pinned block keeps its space (LAY-011)"] = (
        'pinned.pack(side="bottom", fill="both", expand=True)' in pane
        and 'zone.pack(side="top", fill="x")' in pane
        and pane.index('pinned.pack(side="bottom"') < pane.index('zone.pack(side="top"')
        and 'zone = ScrollZone(' in pane
        # Created before the pinned block, so Tab visits the filters first.
        and pane.index("zone = ScrollZone(") < pane.index("pinned = ttk.Frame(")
        and "self._build_search_actions(pinned)" in pane
        and "self._build_results_table(pinned)" in pane
        and "self._build_advanced_filter_zone(zone.inner)" in pane)
    results["Results is always given six rows, and asks for exactly that"] = (
        SearchUI.RESULTS_MIN_ROWS == 6
        and "height=RESULTS_MIN_ROWS" in _method_source(
            search, "_build_results_table"))
    results["a click on Advanced moves the view; a workspace restore does not"] = (
        "expand is None" in toggle
        and "zone.scroll_to(self._advanced_header)" in toggle
        and "zone.scroll_to_top()" in toggle)
    results["hover, focus and the wheel all know about the zone"] = (
        "zone.holds_pointer(" in _method_source(search, "_sync_search_row_hover")
        and '"<FocusIn>", self._reveal_focused_filter' in search
        and "register(zone.canvas, target=zone.wheel_target())" in pane
        and "self._guard_filter_zone_wheel()" in pane)

    try:
        root = tk.Tk()
    except tk.TclError:
        print("  [SKIP] no display: ScrollZone behaviour checks need Tk")
        return
    try:
        root.attributes("-alpha", 0.0)
        root.geometry("420x400+0+0")
        S.install_ui_styles(root)

        def pump():
            # A resize takes several idle passes to settle (layout, Configure,
            # the zone's own sync, layout again); a fixed count keeps it simple.
            for _ in range(25):
                root.update()
                time.sleep(0.012)

        holder = ttk.Frame(root)
        holder.pack(fill="both", expand=True)
        pinned = tk.Frame(holder, height=150, width=100)
        zone = C.ScrollZone(holder, scroll_step=48)
        rows = []
        for _ in range(20):
            row = tk.Frame(zone.inner, height=40, width=200)
            row.pack(fill="x")
            rows.append(row)
        # The order the Search pane uses: pinned block first, zone last.
        pinned.pack(side="bottom", fill="both", expand=True)
        zone.pack(side="top", fill="x")
        pump()

        def enabled():
            return "disabled" not in zone.scrollbar.state()

        tall = (pinned.winfo_height() == 150
                and abs(zone.canvas.winfo_height() - 250) <= 2
                and enabled())
        top0 = zone.canvas.canvasy(0)
        zone.scroll_units(1)
        pump()
        stepped = round(zone.canvas.canvasy(0) - top0) == 48
        zone.canvas.yview_moveto(0)
        zone._yview("scroll", 1, "units")
        pump()
        arrow = round(zone.canvas.canvasy(0)) == 48
        zone.wheel_target().yview_scroll(-1, "units")
        pump()
        wheeled = round(zone.canvas.canvasy(0)) == 0
        zone.scroll_to(rows[10])
        pump()
        aligned = abs(zone.canvas.canvasy(0) - (rows[10].winfo_y() - 6)) <= 1
        zone.scroll_to_top()
        pump()
        returned = zone.canvas.canvasy(0) == 0
        zone.reveal(rows[19])
        pump()
        revealed = (zone.canvas.canvasy(0) + zone.canvas.winfo_height()
                    >= rows[19].winfo_y() + 40)
        results["a tall zone is squeezed to what is left, its pinned block keeps its size"] = (
            tall and str(zone.scrollbar.cget("style")) == C.ScrollZone.ACTIVE_STYLE)
        results["wheel notches and scrollbar arrows move a step; jumps land exactly"] = (
            stepped and arrow and wheeled and aligned and returned and revealed)
        results["the zone can say what belongs to it and where the pointer is"] = (
            zone.contains(rows[0]) and not zone.contains(holder)
            and zone.holds_pointer(zone.canvas.winfo_rootx() + 5,
                                   zone.canvas.winfo_rooty() + 5)
            and not zone.holds_pointer(zone.canvas.winfo_rootx() + 5,
                                       zone.canvas.winfo_rooty() + 500))

        # The wheel over a spinbox or combobox scrolls the zone, never edits it.
        spin = ttk.Spinbox(zone.inner, from_=0, to=20)
        combo = ttk.Combobox(zone.inner, values=("2019", "2020"), state="readonly")
        spin.pack()
        combo.pack()
        owner = type("Owner", (), {})()
        owner._filter_zone = zone
        owner.FILTER_ZONE_WHEEL_GUARDED = (
            SearchUI.SearchFeatureMixin.FILTER_ZONE_WHEEL_GUARDED)
        owner._wheel_units = WindowServicesMixin._wheel_units.__get__(owner)
        owner._scroll_filter_zone_from_field = (
            SearchUI.SearchFeatureMixin._scroll_filter_zone_from_field.__get__(owner))
        SearchUI.SearchFeatureMixin._guard_filter_zone_wheel(owner)
        pump()
        zone.canvas.yview_moveto(0)
        pump()
        guarded = True
        for field in (spin, combo):
            before_value, before_top = field.get(), zone.canvas.canvasy(0)
            field.event_generate("<MouseWheel>", delta=-120, x=4, y=4)
            pump()
            guarded = guarded and (
                field.get() == before_value
                and round(zone.canvas.canvasy(0) - before_top) == 48)
        results["the wheel over Power or Released scrolls the zone and never edits them"] = guarded

        # A short zone takes only what it needs: no scrolling, a quiet gutter.
        # Shrink it while scrolled deep: an off-screen canvas item never reports
        # its new size, so the first wheel notch must re-sync before it scrolls.
        zone.reveal(rows[19])
        pump()
        for row in rows[2:]:
            row.destroy()
        spin.destroy()
        combo.destroy()
        root.update_idletasks()
        zone.scroll_units(1)
        recovered = zone.canvas.canvasy(0) == 0
        results["the wheel recovers a view left past the end of shrunken content"] = recovered
        pump()
        bar_gap = zone.scrollbar.winfo_rootx() - (
            zone.canvas.winfo_rootx() + zone.canvas.winfo_width())
        results["the scrollbar keeps a gap from the controls beside it"] = (
            # A literal, not the constant: a gate that reads the value under test
            # passes when that value is zeroed.
            C.ScrollZone.BAR_GAP == 6 and bar_gap >= 5)
        results["a zone that fits keeps its gutter but disables and hides the scrollbar"] = (
            abs(zone.canvas.winfo_height() - 80) <= 2
            and not enabled() and zone.scrollbar.winfo_ismapped()
            and str(zone.scrollbar.cget("style")) == C.ScrollZone.IDLE_STYLE
            and zone.canvas.canvasy(0) == 0
            and pinned.winfo_height() >= 300)
    finally:
        root.destroy()


def _scroll_zone_settle_checks(results):
    """Opening Advanced jumps once, straight to where the button ends up."""
    try:
        root = tk.Tk()
    except tk.TclError:
        return
    try:
        root.attributes("-alpha", 0.0)
        root.geometry("420x1000+0+0")
        S.install_ui_styles(root)

        def pump():
            for _ in range(30):
                root.update()
                time.sleep(0.012)

        holder = ttk.Frame(root)
        holder.pack(fill="both", expand=True)
        pinned = tk.Frame(holder, height=150, width=100)
        zone = C.ScrollZone(holder, scroll_step=48)
        rows = []

        def add_rows(count):
            for _ in range(count):
                row = tk.Frame(zone.inner, height=50, width=200)
                row.pack(fill="x")
                rows.append(row)

        add_rows(6)
        pinned.pack(side="bottom", fill="both", expand=True)
        zone.pack(side="top", fill="x")
        pump()

        tops = []
        forward = zone.scrollbar.set

        def log(first, last):
            tops.append(round(zone.canvas.canvasy(0)))
            forward(first, last)

        zone.canvas.configure(yscrollcommand=log)
        # The situation on a large window: the form (300 px) fits, so the zone
        # is 300 px tall; opening Advanced grows it to 900 px, which the window
        # can give only part of (850 of 1000, or less on a small screen).  A jump
        # to the row at y=300 is clamped to what remains scrollable, but decided
        # against the OLD 300 px viewport it lands at 294 first.  The final place
        # is worked out from the measured sizes, so a screen that shortens the
        # window changes the answer but not the rule.
        tops.clear()
        add_rows(12)
        zone.scroll_to(rows[6])
        pump()
        final = min(rows[6].winfo_y() - 6, 900 - zone.canvas.winfo_height())
        opened = sorted(set(tops))
        results["opening moves the view once, straight to its final place"] = (
            round(zone.canvas.canvasy(0)) == final and opened[-1] == final
            and len(opened) <= 2 and max(tops) <= final)
    finally:
        root.destroy()

    # Closing, on a window too short to show the whole form: scrolled to the
    # bottom of 1400 px of rows, the panel goes and the content drops to 600 px.
    # Tk clamps the view against the smaller content (to 295) before any jump can
    # land, so the form must go to the top before that, not after.
    try:
        root = tk.Tk()
        root.attributes("-alpha", 0.0)
        root.geometry("420x455+0+0")
        S.install_ui_styles(root)
        holder = ttk.Frame(root)
        holder.pack(fill="both", expand=True)
        pinned = tk.Frame(holder, height=150, width=100)
        zone = C.ScrollZone(holder, scroll_step=48)
        rows = []
        for _ in range(28):
            row = tk.Frame(zone.inner, height=50, width=200)
            row.pack(fill="x")
            rows.append(row)
        pinned.pack(side="bottom", fill="both", expand=True)
        zone.pack(side="top", fill="x")

        def settle():
            for _ in range(30):
                root.update()
                time.sleep(0.012)

        settle()
        zone.canvas.yview_moveto(1)
        settle()
        tops = []
        forward = zone.scrollbar.set

        def note(first, last):
            tops.append(round(zone.canvas.canvasy(0)))
            forward(first, last)

        zone.canvas.configure(yscrollcommand=note)
        settle()
        tops.clear()          # attaching the logger reports the current view
        for row in rows[12:]:
            row.destroy()
        zone.scroll_to_top()
        settle()
        results["closing returns to the first row without passing through another"] = (
            zone.canvas.canvasy(0) == 0 and set(tops) <= {0})
    finally:
        root.destroy()


def main():
    sources = _ui_sources()
    trees = {name: ast.parse(text) for name, text in sources.items()}
    results = {}

    # ---------------------------------------------------------------- colour
    palette = T.PALETTE
    text_colours = ("good", "bad", "bad_bright")
    results["status text clears 4.5:1 on both dark surfaces (CLR-007)"] = all(
        _contrast(palette[name], palette[surface]) >= 4.5
        for name in text_colours for surface in ("surface", "surface2"))
    results["there is one red and one green for status text, no darker duplicates"] = (
        not any(key in palette for key in (
            "deck_good", "deck_bad", "deck_bad_dim"))
        and not any(
            re.search(r'deck_(good|bad)', text) for name, text in sources.items()
            if name != "tokens.py"))
    results["the alert flash pair differs but stays legible"] = (
        palette["bad_bright"] != palette["bad"])

    mana_hex = set(T.MANA_FILL.values()) | set(T.MANA_BORDER.values())
    results["mana colours are defined once, in tokens (CLR-008)"] = (
        MANA.MANA_FILL is T.MANA_FILL and MANA.MANA_BORDER is T.MANA_BORDER
        and all(T.DECK_COLOR_SEGMENT_COLORS[key] == T.MANA_FILL[key]
                for key in "WUBRG")
        and T.DECK_COLOR_SEGMENT_COLORS["Colorless"] == T.MANA_FILL["C"]
        and not re.search(r'#[0-9A-Fa-f]{6}', sources["mana.py"])
        and not any(colour in text for name, text in sources.items()
                    if name != "tokens.py" for colour in mana_hex))

    # ---------------------------------------------------------- scaling helpers
    results["display_scale is 1.0 at 100%, follows the scaling, and never fails"] = (
        S.display_scale(_Scaled(96 / 72)) == 1.0
        and abs(S.display_scale(_Scaled(96 / 72 * 1.5)) - 1.5) < 1e-9
        and S.display_scale(_Scaled(1.0)) == 1.0       # below 100% never shrinks
        and S.display_scale(_Scaled(RuntimeError("no Tk"))) == 1.0
        and S.scaled_pixels(_Scaled(96 / 72 * 1.25), 144) == 180
        and S.scaled_pixels(_Scaled(96 / 72), 34) == 34)
    results["Search secondary rails, the label rail and the deck tab bar all scale"] = (
        'scaled_pixels(self, FILTER_LABEL_WIDTH)' in sources["search.py"]
        and 'scaled_pixels(mode, MATCH_MODE_LABEL_WIDTH)' in sources["search.py"]
        and 'scaled_pixels(mode, HELPER_LABEL_WIDTH)' in sources["search_checklist.py"]
        and 'height=scaled_pixels(self, 34)' in sources["deck.py"])

    # ------------------------------------------------- responsive chip / mana rows
    grid = SearchUI.SearchFeatureMixin._layout_chip_grid

    def fit(width, chips, options, current):
        owner = type("Owner", (), {})()
        owner._layout_chip_grid = grid.__get__(owner)
        frame = _Frame(width)
        widgets = tuple(_Chip(chips) for _ in range(16))
        return SearchUI._fit_chip_columns(widgets, width, options, current)

    results["Card Type chips fall back to fewer, wider columns instead of being cut (LAY-006)"] = (
        fit(556, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 4) == 5
        and fit(400, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 5) == 4
        # three columns of a padded 92 px each need 276, not 3*88 + gaps = 272:
        # the middle column's chip loses the gap on both sides
        and fit(275, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 4) == 2
        and fit(276, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 4) == 3
        and fit(180, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 3) == 2
        # nothing fits: the smallest layout, never a crash
        and fit(50, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 3) == 2
        # hysteresis: a wider grid needs headroom, the current one does not
        and fit(460, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 4) == 4
        and fit(460, 88, SearchUI.CARD_TYPE_COLUMN_OPTIONS, 5) == 5)

    supertypes = tuple(_Chip(width) for width in (45, 73, 65, 47, 50))
    supertype_owner = type("Owner", (), {})()
    supertype_owner._window_in_motion = False
    supertype_owner._property_chip_frame = _Frame(280)
    supertype_owner._property_chip_widgets = supertypes
    supertype_owner._supertype_chip_columns = SearchUI.SUPERTYPE_COLUMNS
    supertype_owner._layout_chip_grid = grid.__get__(supertype_owner)
    SearchUI.SearchFeatureMixin._layout_supertype_chips(supertype_owner)
    results["Supertype chips are fitted to the pane like Card Type chips"] = (
        supertype_owner._supertype_chip_columns == 3
        and supertypes[3].grid_options.get("row") == 1
        and supertypes[3].grid_options.get("column") == 0
        and all(supertype_owner._property_chip_frame.columns[c].get("uniform")
                == "search-supertype-chip" for c in range(3)))

    mana = tuple(_Chip(width) for width in (84, 74, 86, 66, 80, 100))
    results["mana rows wrap to two rows of three, then three of two, never clip"] = (
        SearchUI._fit_mana_choice_columns(mana, 556, 6) == 6
        and SearchUI._fit_mana_choice_columns(mana, 300, 6) == 3
        and SearchUI._fit_mana_choice_columns(mana, 200, 6) == 2
        and SearchUI._fit_mana_choice_columns(mana, 100, 3) == 2
        # unwrapping needs headroom (hysteresis); staying wrapped does not
        and SearchUI._fit_mana_choice_columns(
            mana, SearchUI._mana_choice_required_width(mana, 6), 3) == 3
        and SearchUI._fit_mana_choice_columns(
            mana, SearchUI._mana_choice_required_width(mana, 6) + 8, 3) == 6
        and SearchUI._mana_choice_required_width(mana, 3)
            == max(84, 66) + max(74, 80) + max(86, 100) + 2 * SearchUI.MANA_CHOICE_GAP)

    class _Gridded:
        def __init__(self):
            self.options = {}

        def grid(self, **options):
            self.options = options
    gridded = tuple(_Gridded() for _ in range(6))
    SearchUI._grid_mana_choices(gridded, 3)
    results["wrapped mana choices are placed row-major"] = (
        [(g.options["row"], g.options["column"]) for g in gridded]
        == [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)])

    # ------------------------------------------------------- dialogs and controls
    close_buttons = []
    for name, tree in trees.items():
        for kind in ("AppButton", "ClassicButton"):
            for call in _calls(tree, kind):
                if _literal(_keyword(call, "text")) == "Close":
                    close_buttons.append(
                        (name, kind, _literal(_keyword(call, "role"))))
    results["every Close button is the same compact ttk button (UI-012)"] = (
        len(close_buttons) >= 5
        and all(kind == "AppButton" and role == "compact"
                for _name, kind, role in close_buttons))

    unanchored = []
    for name, tree in trees.items():
        for call in _calls(tree, "tk.Label"):
            if _literal(_keyword(call, "justify")) == "left" and (
                    _literal(_keyword(call, "anchor")) != "w"):
                unanchored.append((name, call.lineno))
    results["left-justified classic labels are anchored west, not centered"] = (
        not unanchored)
    if unanchored:
        print("    unanchored:", unanchored)

    progress = (sources["database_sync.py"], sources["printing.py"])
    results["progress dialogs use the shared gold dialog title (UI-014, TYP-002)"] = (
        all('style="DialogTitle.TLabel"' in text for text in progress)
        and not any("FONT_PROGRESS_TITLE" in text
                    for text in sources.values()))

    filters = sources["table_filters.py"]
    start = filters.index("def _build_symbol_group_filter_editor")
    end = filters.index("def _build_text_filter_editor")
    body = filters[start:end]
    results["column-filter Any/All/None are radios like Search, not a dropdown (UI-010)"] = (
        'for mode_name in ("Any", "All", "None"):' in body
        and 'style="DialogChoice.TRadiobutton"' in body
        and "AppCombobox(" not in body)

    results["platform names read Paper, Arena and MTGO everywhere"] = (
        SetFilters.GAME_PLATFORM_SUMMARY_LABELS
        == {"paper": "Paper", "arena": "Arena", "mtgo": "MTGO"}
        and "GAME_PLATFORM_SUMMARY_LABELS[key]" in sources["search_printings.py"]
        and "key.title()" not in sources["search_printings.py"])

    bar = _Scrollbar()
    command = C.autohide_scrollbar(bar)
    command("0.0", "1.0")
    hidden_when_it_fits = not bar.visible
    command("0.0", "0.4")
    shown_when_it_overflows = bar.visible
    command("0.0", "1.0")
    results["a dialog list hides its scrollbar until it has something to scroll"] = (
        hidden_when_it_fits and shown_when_it_overflows and not bar.visible
        and bar.range == ("0.0", "1.0")
        and "autohide_scrollbar(scroll)" in sources["card_detail.py"]
        and "autohide_scrollbar(scroll)" in sources["deck_stats.py"])

    preview = sources["card_detail.py"]
    results["preview action buttons are content-sized so the row fits (SIZ-003)"] = (
        preview.count("width=0,") >= 5
        and 'role="compact_primary", width=0' in preview)

    _typography_checks(results)
    _scroll_zone_checks(results, sources)
    _scroll_zone_settle_checks(results)

    ok = True
    for label, passed in results.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and bool(passed)
    print("\nUI RENDERING CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
