"""Headless contract checks for the centralized UI component system."""

import ast
import inspect
import os
import pathlib
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mtgdb.ui.components as C

import mtgdb.ui.comparison as CMP

import mtgdb.ui.styles as S

import mtgdb.ui.tokens as T



def main():
    gui_path = os.path.join(ROOT, "mtgdb/ui/app.py")
    with open(gui_path, encoding="utf-8") as source_file:
        gui_source = source_file.read()
    search_path = os.path.join(ROOT, "mtgdb/ui/search.py")
    with open(search_path, encoding="utf-8") as source_file:
        search_source = source_file.read()
    checklist_path = os.path.join(ROOT, "mtgdb/ui/search_checklist.py")
    with open(checklist_path, encoding="utf-8") as source_file:
        checklist_source = source_file.read()
    styles_path = os.path.join(ROOT, "mtgdb/ui/styles.py")
    with open(styles_path, encoding="utf-8") as source_file:
        style_source = source_file.read()
    table_filters_path = os.path.join(ROOT, "mtgdb/ui/table_filters.py")
    with open(table_filters_path, encoding="utf-8") as source_file:
        table_filters_source = source_file.read()
    printings_path = os.path.join(ROOT, "mtgdb/ui/search_printings.py")
    with open(printings_path, encoding="utf-8") as source_file:
        printings_source = source_file.read()
    tables_path = os.path.join(ROOT, "mtgdb/ui/tables.py")
    with open(tables_path, encoding="utf-8") as source_file:
        tables_source = source_file.read()
    detail_path = os.path.join(ROOT, "mtgdb/ui/card_detail.py")
    with open(detail_path, encoding="utf-8") as source_file:
        detail_source = source_file.read()
    comparison_path = os.path.join(ROOT, "mtgdb/ui/comparison.py")
    with open(comparison_path, encoding="utf-8") as source_file:
        comparison_source = source_file.read()
    comparison_controls_path = os.path.join(
        ROOT, "mtgdb/ui/comparison_controls.py")
    with open(comparison_controls_path, encoding="utf-8") as source_file:
        comparison_controls_source = source_file.read()
    components_path = os.path.join(ROOT, "mtgdb/ui/components.py")
    with open(components_path, encoding="utf-8") as source_file:
        components_source = source_file.read()
    set_filters_path = os.path.join(ROOT, "mtgdb/ui/set_filters.py")
    with open(set_filters_path, encoding="utf-8") as source_file:
        set_filters_source = source_file.read()
    source = "\n".join(
        (gui_source, search_source, checklist_source, table_filters_source,
         printings_source, tables_source, detail_source, comparison_source,
         comparison_controls_source))

    direct_classic_controls = []
    for module_source in (gui_source, search_source, checklist_source, table_filters_source,
                          printings_source, tables_source, detail_source, comparison_source,
                          comparison_controls_source):
        for node in ast.walk(ast.parse(module_source)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (isinstance(owner, ast.Name) and owner.id == "tk"
                    and node.func.attr in {
                        "Button", "Entry", "Checkbutton", "Radiobutton"
                    }):
                direct_classic_controls.append((node.func.attr, node.lineno))

    class FakeRoot:
        def configure(self, **_kwargs):
            pass

        def option_add(self, *_args):
            pass

    class FakeStyle:
        def __init__(self, _root):
            self.configured = set()

        def theme_use(self, *_args):
            pass

        def configure(self, name, **_kwargs):
            self.configured.add(name)

        def map(self, *_args, **_kwargs):
            pass

    original_style = S.ttk.Style
    fake_style = FakeStyle(FakeRoot())
    try:
        S.ttk.Style = lambda _root: fake_style
        S.install_ui_styles(FakeRoot())
    finally:
        S.ttk.Style = original_style

    expected_styles = set(C._BUTTON_STYLES.values())
    expected_styles.update(C._MENUBUTTON_STYLES.values())
    expected_styles.update({"Form.TEntry", "Form.TCombobox", "Form.TSpinbox"})

    def _luminance(colour):
        raw = str(colour).lstrip("#")
        red, green, blue = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * red + 0.7152 * green + 0.0722 * blue

    def _indicator_contrast(selected, background):
        return abs(_luminance(selected) - _luminance(background))

    _radio_source = inspect.getsource(C.ClassicRadiobutton.__init__)
    _radio_defaults = {
        "selectcolor": T.PALETTE[
            re.search(r'"selectcolor": PALETTE\["(\w+)"\]', _radio_source).group(1)],
        "bg": T.PALETTE[
            re.search(r'"bg": PALETTE\["(\w+)"\]', _radio_source).group(1)],
    }

    # The highlighted-suggestion commit indexes _suggest_hits immediately after
    # its bounds test, so a relaxed guard is an IndexError, not a soft failure.
    from mtgdb.ui.autocomplete import AutocompleteEntry as _Autocomplete

    class _SuggestProbe:
        """Carries only the state commit_highlighted_suggestion touches."""

        def __init__(self, hits, index):
            self._suggest_hits = list(hits)
            self._suggest_index = index
            self.text = "typed"
            self.hidden = 0

        def delete(self, *_args):
            self.text = ""

        def insert(self, _index, value):
            self.text = value

        def icursor(self, *_args):
            return None

        def _hide_suggestions(self):
            self.hidden += 1

    def _commit(hits, index):
        probe = _SuggestProbe(hits, index)
        accepted = _Autocomplete.commit_highlighted_suggestion(probe)
        return accepted, probe.text

    _hits = ["Alpha", "Beta"]
    _suggestion_bounds_ok = (
        # In range: commits and hides the popup.
        _commit(_hits, 0) == (True, "Alpha")
        and _commit(_hits, 1) == (True, "Beta")
        # One past the end must be refused, not indexed.
        and _commit(_hits, 2) == (False, "typed")
        and _commit(_hits, 99) == (False, "typed")
        # Nothing highlighted, and an empty suggestion list.
        and _commit(_hits, -1) == (False, "typed")
        and _commit([], 0) == (False, "typed"))

    # UI-011: one definition of the board wording. Assert the behaviour and that
    # no module re-implements the conditional inline.
    from mtgdb.ui.components import deck_board_label as _board_label

    _ui_dir = pathlib.Path(ROOT) / "mtgdb" / "ui"
    _inline_board_label = sorted(
        path.name for path in _ui_dir.glob("*.py")
        if path.name != "components.py"
        and '"Mainboard" if' in path.read_text(encoding="utf-8"))

    board_label_shared = (
        _board_label("main") == "Mainboard"
        and _board_label("side") == "Sideboard"
        # Anything that is not the mainboard reads as the sideboard.
        and _board_label("") == "Sideboard"
        and _board_label(None) == "Sideboard"
        and _board_label("MAIN") == "Sideboard"
        and _inline_board_label == [])

    checks = {
        "the Mainboard/Sideboard label has exactly one definition": (
            board_label_shared),
        "highlighted-suggestion commit refuses out-of-range indexes": (
            _suggestion_bounds_ok),
        "all ttk button roles resolve to a registered style": (
            set(C._BUTTON_STYLES) == {
                "standard", "primary", "compact", "compact_primary",
                "deck", "picker", "search_picker", "search_section",
                "dense", "dense_primary",
            }),
        "single-choice indicators contrast with every row background they sit on": (
            # Tk paints a radio indicator with selectcolor when on and with the
            # widget background when off. If a caller restyles the row
            # background to the same colour, selected and unselected rows render
            # identically and a one-of-many picker looks like everything is
            # selected.
            _indicator_contrast(_radio_defaults["selectcolor"],
                                _radio_defaults["bg"]) >= 60
            and _indicator_contrast(
                _radio_defaults["selectcolor"], T.PALETTE["input"]) >= 60
            and _indicator_contrast(
                _radio_defaults["selectcolor"], T.PALETTE["surface"]) >= 60),
        "classic controls use shared components": not direct_classic_controls,
        "every component role has a registered ttk style": (
            expected_styles <= fake_style.configured),
        "form and helper typography use Segoe UI role tokens": (
            T.FONT_BODY == ("Segoe UI", 10)
            and T.FONT_HELPER == ("Segoe UI", 9)),
        "pane and dialog titles share one size, and progress dialogs reuse the dialog title": (
            T.FONT_PANE_TITLE[1] == T.FONT_DIALOG_TITLE[1] == 15
            and not hasattr(T, "FONT_PROGRESS_TITLE")),
        "all color-filter mana pips share one display size": (
            T.FILTER_PIP_SIZE == 22),
        "Sideboard action uses larger standard density and stays content-sized": bool(
            re.search(r'text="Add Sideboard",\s*role="standard"', source)
            and not re.search(
                r'text="Add Sideboard"[\s\S]{0,90}?width\s*=', source)),
        "global comparison controls use standard action density": all(
            marker in source for marker in (
                'text="Add Selected", role="primary"',
                'text="Compare", role="standard"',
                'text="Clear", role="standard"',
                'role="picker"',
            )),
        "zero-count virtual choices use a red-X unavailable presentation": (
            '"Unavailable.ListChoice.TRadiobutton"' in checklist_source
            and 'f"✕ {shown}" if zero_count else shown' in checklist_source
            and 'PALETTE["bad"] if zero_count else PALETTE["text"]' in checklist_source
            and 'key in self._zero_count_keys and key not in self._selected' in checklist_source
            and 'style.configure("Unavailable.ListChoice.TRadiobutton"' in style_source
            and 'def set_context_availability(' in components_source),
        "single-select virtual choices render as themed ttk radio rows": (
            # UI-010: the classic Tk indicator is painted by the host platform
            # and ignores the dark palette, so every row reads as selected.
            'style="ListChoice.TRadiobutton"' in checklist_source
            and "if self.single_select:" in checklist_source
            and "ClassicRadiobutton(" not in checklist_source.split(
                "def set_values", 1)[0]),
        "every Any/All mode row uses the themed indicator": (
            # UI-010 originally kept these classic. They sit beside themed
            # controls and read as a different widget family, so they now carry
            # the same hollow-then-gold indicator as the deck format picker.
            'style="ListChoice.TRadiobutton"' in checklist_source
            and "ClassicRadiobutton(\n                    mode" not in checklist_source),
        "scrolling filter rows fill the viewport while short catalogs stay compact": (
            'scrolling = len(self._visible) > self._capacity' in checklist_source
            and 'minsize = 0' in checklist_source
            and 'if scrolling and grid_row < active_grid_rows:' in checklist_source
            and 'preferred_height_for_items' in checklist_source
            and C.visible_text_columns("Rarity") > 0),
        "four-value Rarity picker uses compact four-row viewport": (
            __import__("mtgdb.ui.search_checklist", fromlist=["VirtualChecklistView"])
            .VirtualChecklistView.preferred_height_for_items(4)
            == 4 * 24 + 12),
        "format radio choices are left aligned": (
            '"anchor": "w", "justify": "left"' in components_source
            and 'sticky="ew", padx=7, pady=1' in checklist_source),
        "long checklist footer is reserved before the expanding viewport": (
            checklist_source.index('foot.pack(side="bottom", fill="x", pady=(8, 0))')
            < checklist_source.index('self.list_view.pack(fill="both", expand=True)')),
        "filter bulk-action labels use Select All and Clear Selected": (
            'text="Select All"' in checklist_source
            and 'text="Clear Selected"' in checklist_source
            and 'text="Select All"' in set_filters_source
            and "Select All Shown" not in checklist_source + set_filters_source
            and "Clear Shown" not in checklist_source + set_filters_source),
        "shared buttons expose an explicit click pulse": (
            C.AppButton.CLICK_PULSE_MS >= 100
            and C.AppMenubutton.CLICK_PULSE_MS >= 100
            and 'self.state(["alternate"])' in components_source),
        "Printings Exact Set uses two virtualized columns": (
            "height=270, columns=2" in set_filters_source),
        "Rules Text chip removal fully restores the editable field": all(
            marker in components_source for marker in (
                "def _reset_entry_view(self):", "self.entry.icursor(0)",
                "self.entry.xview_moveto(0.0)", "self._chips.pack_forget()",
                "before=self.entry", "def _remove_chip(self, value):",
                "self.entry.focus_set()", "self.after_idle(")),
        "comparison limits remain two through seven": (
            CMP.MIN_COMPARISON_CARDS == 2 and CMP.MAX_COMPARISON_CARDS == 7),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    if direct_classic_controls:
        print("  Direct classic controls:", direct_classic_controls)
    print("\nUI COMPONENT CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
