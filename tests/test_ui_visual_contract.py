"""Headless visual contracts for component roles, contrast, and text sizing."""

import ast
import os
from pathlib import Path
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mtgdb.ui.components as C

import mtgdb.ui.styles as S
import mtgdb.ui.search as SearchUI

import mtgdb.ui.tokens as T



def _channel(value):
    value /= 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(color):
    color = color.lstrip("#")
    rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    red, green, blue = (_channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(left, right):
    lighter, darker = sorted((_luminance(left), _luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _literal_keyword(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            try:
                return ast.literal_eval(keyword.value)
            except (TypeError, ValueError):
                return None
    return None


def _undersized_static_buttons(source):
    failures = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            constructor = node.func.id
        elif isinstance(node.func, ast.Attribute):
            constructor = node.func.attr
        else:
            continue
        if constructor not in {"AppButton", "AppMenubutton", "ClassicButton"}:
            continue
        text = _literal_keyword(node, "text")
        width = _literal_keyword(node, "width")
        if isinstance(text, str) and isinstance(width, int) and width > 0:
            required = C.visible_text_columns(text)
            if width < required:
                failures.append((constructor, text, width, required, node.lineno))
    return failures


class FakeRoot:
    def configure(self, **_kwargs):
        pass

    def option_add(self, *_args):
        pass


class FakeStyle:
    def __init__(self, _root):
        self.configured = {}
        self.mapped = {}

    def theme_use(self, *_args):
        pass

    def configure(self, name, **kwargs):
        self.configured[name] = kwargs

    def map(self, name, **kwargs):
        self.mapped[name] = kwargs


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeChip:
    def __init__(self, value):
        self._ui_chip_variable = FakeVariable(value)
        self.configured = {}

    def configure(self, **kwargs):
        self.configured.update(kwargs)


class FakeGridChip:
    def __init__(self, requested_width):
        self.requested_width = requested_width
        self.grid = {}

    def winfo_reqwidth(self):
        return self.requested_width

    def winfo_exists(self):
        return True

    def grid_configure(self, **kwargs):
        self.grid.update(kwargs)


class FakeGridFrame:
    def __init__(self, width):
        self.width = width
        self.columns = {}

    def winfo_width(self):
        return self.width

    def columnconfigure(self, column, **kwargs):
        self.columns[column] = kwargs


def _card_type_grid_contract():
    owner = type("FakeSearchOwner", (), {})()
    owner._card_type_chip_frame = FakeGridFrame(430)
    owner._card_type_chip_widgets = tuple(FakeGridChip(80) for _ in range(10))
    owner._card_type_chip_columns = SearchUI.CARD_TYPE_MIN_COLUMNS

    SearchUI.SearchFeatureMixin._layout_card_type_chips(owner)
    wide_columns = owner._card_type_chip_columns
    wide_positions = [
        (chip.grid.get("row"), chip.grid.get("column"))
        for chip in owner._card_type_chip_widgets
    ]

    owner._card_type_chip_frame.width = 400
    SearchUI.SearchFeatureMixin._layout_card_type_chips(owner)
    narrow_columns = owner._card_type_chip_columns
    narrow_positions = [
        (chip.grid.get("row"), chip.grid.get("column"))
        for chip in owner._card_type_chip_widgets
    ]

    return (
        SearchUI.CARD_TYPE_MIN_COLUMNS == 4
        and SearchUI.CARD_TYPE_MAX_COLUMNS == 5
        and wide_columns == 5
        and wide_positions[5] == (1, 0)
        and narrow_columns == 4
        and narrow_positions[4] == (1, 0)
    )


def main():
    paths = [os.path.join(ROOT, name) for name in (
        "mtgdb/ui/app.py", "mtgdb/ui/search.py", "mtgdb/ui/search_checklist.py",
        "mtgdb/ui/search_printings.py", "mtgdb/ui/set_filters.py",
        "mtgdb/ui/table_filters.py", "mtgdb/ui/results.py",
        "mtgdb/ui/tables.py", "mtgdb/ui/card_detail.py", "mtgdb/ui/comparison.py",
        "mtgdb/ui/comparison_controls.py", "mtgdb/ui/deck.py", "mtgdb/ui/deck_stats.py")]
    sources = {}
    for path in paths:
        with open(path, encoding="utf-8") as source_file:
            sources[os.path.basename(path)] = source_file.read()
    gui_source = "\n".join(
        source for name, source in sources.items()
        if name not in {"comparison.py", "comparison_controls.py"})
    comparison_source = sources["comparison.py"]
    comparison_controls_source = sources["comparison_controls.py"]
    combined_source = "\n".join(sources.values())

    original_style = S.ttk.Style
    fake_style = FakeStyle(FakeRoot())
    try:
        S.ttk.Style = lambda _root: fake_style
        S.install_ui_styles(FakeRoot())
    finally:
        S.ttk.Style = original_style

    primary_styles = {
        C._BUTTON_STYLES[role]
        for role in ("primary", "compact_primary", "dense_primary")
    }
    secondary_styles = set(C._BUTTON_STYLES.values()) - primary_styles
    primary_contract = all(
        fake_style.configured[name].get("background") == T.PALETTE["accent"]
        and fake_style.configured[name].get("foreground") == T.PALETTE["on_accent"]
        for name in primary_styles
    )
    secondary_contract = all(
        fake_style.configured[name].get("background") != T.PALETTE["accent"]
        and fake_style.configured[name].get("foreground") == T.PALETTE["text"]
        for name in secondary_styles
    )
    classic_primary_contract = all(
        C._CLASSIC_BUTTON_ROLES[role].get("bg") == T.PALETTE["accent"]
        and C._CLASSIC_BUTTON_ROLES[role].get("fg") == T.PALETTE["on_accent"]
        for role in ("primary", "compact_primary", "dense_primary", "tab_add")
    )

    selected_chip = FakeChip(True)
    unselected_chip = FakeChip(False)
    C.ClassicCheckbutton._sync_chip_contrast(selected_chip)
    C.ClassicCheckbutton._sync_chip_contrast(unselected_chip)

    styles_source = (Path(ROOT) / "mtgdb/ui/styles.py").read_text(encoding="utf-8")
    components_source = (Path(ROOT) / "mtgdb/ui/components.py").read_text(encoding="utf-8")
    local_hardcoded_accent_text = "#111111" in "\n".join([
        gui_source,
        comparison_source,
        comparison_controls_source,
        styles_source,
        components_source,
    ])

    deck_pack = gui_source.find('deck_actions.pack(side="right")')
    search_pack = gui_source.find('search_actions.pack(side="left")')
    # Optional filters share one control column: each row grids its control at
    # column 1 against a label column of the same fixed minimum width.
    advanced_format_rarity_alignment = all(fragment in sources["search.py"] for fragment in (
        'def _build_filter_format(',
        'def _build_filter_rarity(',
        'self._format_btn.grid(row=0, column=1, sticky="ew", pady=2)',
        'self._rarity_btn.grid(row=0, column=1, sticky="ew", pady=2)',
        'frame.columnconfigure(0, minsize=FILTER_LABEL_WIDTH)',
    ))
    button_family_pairs = (
        ("TButton", "Primary.TButton"),
        ("Compact.TButton", "CompactPrimary.TButton"),
        ("SearchRow.TButton", "SearchRowAccent.TButton"),
    )

    checks = {
        "gold/on-accent contrast meets normal-text target": (
            _contrast(T.PALETTE["accent"], T.PALETTE["on_accent"]) >= 4.5),
        "every semantic primary button is gold with dark text": primary_contract,
        "classic primary buttons use the same gold contrast": (
            classic_primary_contract),
        "every semantic secondary button is dark with light text": secondary_contract,
        "selected filter chips use on-accent text": (
            selected_chip.configured.get("fg") == T.PALETTE["on_accent"]),
        "selected entry text uses on-accent contrast": (
            C.classic_entry_options()["selectforeground"]
            == T.PALETTE["on_accent"]),
        "unselected filter chips use normal light text": (
            unselected_chip.configured.get("fg") == T.PALETTE["text"]),
        "no local hard-coded accent foreground remains": not local_hardcoded_accent_text,
        "explicit static button widths fit their labels": (
            not _undersized_static_buttons(combined_source)),
        "component guard expands an undersized Compare width": (
            C.content_safe_button_width("Compare", 5) == 7),
        "component guard expands Add Sideboard width": (
            C.content_safe_button_width("Add Sideboard", 9) == 13),
        "standard primary and secondary actions share height padding": (
            T.PAD_STANDARD[1] == T.PAD_PRIMARY[1]),
        "compact primary and secondary actions share one padding token": (
            fake_style.configured["Compact.TButton"]["padding"]
            == fake_style.configured["CompactPrimary.TButton"]["padding"]),
        "deck controls use enlarged body-font density": (
            fake_style.configured["DeckControl.TButton"].get("font") == T.FONT_BODY
            and fake_style.configured["DeckControl.TButton"].get("padding") == T.PAD_DECK),
        "dense primary and secondary actions share height padding": (
            T.PAD_DENSE[1] == T.PAD_DENSE_PRIMARY[1]),
        "primary and secondary ttk families share border width": all(
            fake_style.configured[secondary]["borderwidth"]
            == fake_style.configured[primary]["borderwidth"]
            for secondary, primary in button_family_pairs),
        "every dark secondary button matches the thin Rules Text field border": (
            all(fake_style.configured[name].get("bordercolor") == T.PALETTE["border"]
                and fake_style.configured[name].get("relief") == "solid"
                for name in ("TButton", "Compact.TButton", "DeckControl.TButton",
                             "Picker.TButton", "SearchRow.TButton"))
            and all(
                ("disabled", T.PALETTE["border"])
                    in fake_style.mapped[name].get("bordercolor", ())
                and ("active", T.PALETTE["border"])
                    in fake_style.mapped[name].get("bordercolor", ())
                for name in ("TButton", "Compact.TButton", "DeckControl.TButton",
                             "Picker.TButton", "SearchRow.TButton"))
            and fake_style.configured["Picker.TMenubutton"].get("bordercolor")
                == T.PALETTE["border"]
            and fake_style.configured["Picker.TMenubutton"].get("relief") == "solid"
            and fake_style.configured["MenuBar.TMenubutton"].get("bordercolor")
                == T.PALETTE["border"]
            and fake_style.configured["MenuBar.TMenubutton"].get("relief") == "solid"
            and ("disabled", T.PALETTE["border"])
                in fake_style.mapped["Picker.TMenubutton"].get("bordercolor", ())
            and ("active", T.PALETTE["border"])
                in fake_style.mapped["Picker.TMenubutton"].get("bordercolor", ())
            and ("active", T.PALETTE["border"])
                in fake_style.mapped["MenuBar.TMenubutton"].get("bordercolor", ())
            and all(
                C._classic_button_outline(role) == T.PALETTE["border"]
                for role in C._CLASSIC_BUTTON_ROLES
                if role not in C._CLASSIC_PRIMARY_ROLES)
            and all(
                C._classic_button_outline(role) == T.PALETTE["accent"]
                for role in C._CLASSIC_PRIMARY_ROLES)
            and all(fake_style.configured[name].get("bordercolor") == T.PALETTE["accent"]
                    for name in ("Primary.TButton", "CompactPrimary.TButton",
                                 "SearchRowAccent.TButton"))),
        "secondary button click pulse is visually distinct": (
            ("alternate", T.PALETTE["select"])
                in fake_style.mapped["TButton"].get("background", ())
            and ("alternate", T.PALETTE["accent"])
                in fake_style.mapped["TButton"].get("bordercolor", ())),
        "picker and editable-field vertical padding match": (
            T.PAD_PICKER[1] == T.FORM_CONTROL_PADDING[1]
            == T.CLASSIC_ENTRY_IPADY),
        "Advanced Format and Rarity use the shared aligned control column": (
            advanced_format_rarity_alignment),
        "Format Any is an explicit mutually-exclusive radio selection": (
            'selected = {current}' in sources["search.py"]
            and 'selected = {current} if current else set()' not in sources["search.py"]
            and 'chosen = "" if "" in self._selected else next(' in sources["search_checklist.py"]
            and 'self._selected = {key}' in sources["search_checklist.py"]),
        "single-select picker reserves five extra viewport pixels": (
            'SINGLE_SELECT_VIEWPORT_EXTRA = 5' in sources["search_checklist.py"]
            and 'list_height += self.list_view.SINGLE_SELECT_VIEWPORT_EXTRA'
                in sources["search_checklist.py"]),
        "virtual checklist capacity follows DPI-scaled row requests": (
            'self._effective_row_height = max(' in sources["search_checklist.py"]
            and 'row.winfo_reqheight() + 2' in sources["search_checklist.py"]
            and 'height // self._effective_row_height' in sources["search_checklist.py"]
            and 'row_height=self.list_view.effective_row_height'
                in sources["search_checklist.py"]),
        "scrolling checklists fill their viewport while short lists stay compact": (
            'scrolling = len(self._visible) > self._capacity'
                in sources["search_checklist.py"]
            and 'configured_body = max(1, self._last_layout_height - 2)' in sources["search_checklist.py"]
            and 'fill_target = max(0, body_height - 2) if scrolling else 0'
                in sources["search_checklist.py"]
            and 'elif layout_changed:' in sources["search_checklist.py"]
            and 'self._effective_row_height,'
                in sources["search_checklist.py"]),
        "Card Type chips use responsive four/five-column grid": (
            _card_type_grid_contract()),
        "each choice style matches the surface behind it": (
            # One style carried the list viewport's input background
            # everywhere, painting a black box on the lighter surfaces the
            # mode rows sit on.
            fake_style.configured["ListChoice.TRadiobutton"]["background"]
            == T.PALETTE["input"]
            and fake_style.configured["FormChoice.TRadiobutton"]["background"]
            == T.PALETTE["surface"]
            and fake_style.configured["DialogChoice.TRadiobutton"]["background"]
            == T.PALETTE["surface2"]
            # UI-010: selected and resting indicators must still differ.
            and all(
                dict(fake_style.mapped[name]["indicatorbackground"])["selected"]
                != fake_style.configured[name]["indicatorbackground"]
                for name in ("ListChoice.TRadiobutton",
                             "FormChoice.TRadiobutton",
                             "DialogChoice.TRadiobutton"))),
        "Supertypes use one compact five-column row": (
            SearchUI.SUPERTYPE_COLUMNS == 5
            and 'columns=SUPERTYPE_COLUMNS,' in sources["search.py"]),
        "redundant pane titles are removed while section labels remain": (
            all(title not in combined_source for title in (
                'text="Card Search"', 'text="Current Deck"',
                'text="Card Preview"', 'text="Deck Stats"'))
            and 'MAINBOARD | Cards:' in combined_source
            and 'SIDEBOARD | Cards:' in combined_source
            and 'text="RESULTS | 0 Cards", style="Section.TLabel"' in combined_source),
        "primary Search rows share one vertical spacing token": (
            sources["search.py"].count("pady=SEARCH_ROW_PADY") >= 6),
        "Card Name uses full remaining row width": (
            'text="Card Name"' in sources["search.py"]
            and 'row=0, column=1, columnspan=3, sticky="ew"'
            in sources["search.py"]),
        "Printings owns the English-only control placement": (
            'popup_head, text="English only"' in sources["set_filters.py"]
            and 'variable=self._english_variable' in sources["set_filters.py"]
            and 'english_variable=owner.english_only' in sources["search_printings.py"]),
        "right-side deck actions receive packing priority": (
            0 <= deck_pack < search_pack),
        "global comparison bar uses standard-density actions in deck workspace": (
            'text="Add Selected", role="primary"' in comparison_controls_source
            and 'text="Compare", role="standard"' in comparison_controls_source
            and 'text="Clear", role="standard"' in comparison_controls_source
            and 'self._build_comparison_bar(parent)' in sources["deck.py"]
            and '_build_comparison_bar' not in sources["search.py"]
            and 'comparison_action_columns(available, widths, gap)'
            in comparison_controls_source),
        "comparison bar uses the exact Mainboard section-heading style and left edge": (
            'parent, bg=PALETTE["surface"], bd=0, highlightthickness=0'
                in comparison_controls_source
            and 'parent, bg=PALETTE["accent"]' not in comparison_controls_source
            and 'shell, bg=PALETTE["surface"], bd=0, pady=6'
                in comparison_controls_source
            and 'self._comparison_selection_lbl = ttk.Label(' in comparison_controls_source
            and 'style="Section.TLabel", anchor="w"' in comparison_controls_source
            and 'self._comparison_selection_lbl.pack(' in comparison_controls_source
            and 'side="left", anchor="w", fill="x", expand=True)'
                in comparison_controls_source
            and 'self._bind_debounced_wrap(self._comparison_selection_lbl)'
                in comparison_controls_source
            and 'font=FONT_HELPER_BOLD' not in comparison_controls_source
            and 'text="COMPARE | Cards Selected: 0"' in comparison_controls_source
            and 'text=f"COMPARE | Cards Selected: {selected_count}{over_limit_note}"'
                in comparison_controls_source),
        "picker list radio rows carry a themed, state-distinguishing indicator": (
            # UI-010.  The real failure this guards is a radio whose selected and
            # unselected indicators paint the same, so every row reads as
            # selected.  Assert the style exists, sits on the list background,
            # and that its selected indicator actually differs from its resting
            # one -- a check on classic selectcolor could never catch this,
            # because Windows paints that indicator itself.
            fake_style.configured.get("ListChoice.TRadiobutton", {}).get(
                "background") == T.PALETTE["input"]
            and fake_style.configured["ListChoice.TRadiobutton"][
                "indicatorbackground"] == T.PALETTE["input"]
            and dict(fake_style.mapped["ListChoice.TRadiobutton"][
                "indicatorbackground"])["selected"] == T.PALETTE["accent"]
            and dict(fake_style.mapped["ListChoice.TRadiobutton"][
                "indicatorbackground"])["selected"]
            != fake_style.configured["ListChoice.TRadiobutton"][
                "indicatorbackground"]),
        "over-limit comparison heading flashes red through shared styles only": (
            fake_style.configured.get("SectionAlert.TLabel", {}).get("foreground")
                == T.PALETTE["deck_bad"]
            and fake_style.configured.get("SectionAlertDim.TLabel", {}).get(
                "foreground") == T.PALETTE["deck_bad_dim"]
            # Same weight, family and background as the normal heading, so the
            # alert state never reflows or repaints the comparison bar.
            and all(
                fake_style.configured.get(name, {}).get(key)
                == fake_style.configured["Section.TLabel"][key]
                for name in ("SectionAlert.TLabel", "SectionAlertDim.TLabel")
                for key in ("font", "background"))
            and T.PALETTE["deck_bad_dim"] != T.PALETTE["deck_bad"]
            and 'style="SectionAlert.TLabel"' not in comparison_controls_source
            and '_apply_comparison_selection_style("SectionAlert.TLabel")'
                in comparison_controls_source),
        "comparison notices use the dark component system": (
            "messagebox" not in comparison_controls_source
            and 'PALETTE["surface"]' in comparison_controls_source
            and 'role="compact_primary"' in comparison_controls_source),
        "comparison and sample-hand window titles use gold dialog typography": (
            'fg=p["accent"], font=FONT_DIALOG_TITLE' in comparison_source),
        "comparison view contains no horizontal or vertical scrollbar": (
            "Scrollbar" not in comparison_source
            and "tk.Canvas" not in comparison_source),
        "main workspace panes have no unintended surrounding outline": (
            fake_style.configured["Workspace.TFrame"].get("borderwidth") == 0
            and fake_style.configured["Workspace.TFrame"].get("relief") == "flat"
            and fake_style.configured["Preview.TFrame"].get("borderwidth") == 0
            and fake_style.configured["Preview.TFrame"].get("relief") == "flat"
            and 'left = ttk.Frame(main, padding=PANEL_PADDING, style="Workspace.TFrame")'
                in sources["app.py"]
            and 'right = ttk.Frame(main, padding=PANEL_PADDING, style="Workspace.TFrame")'
                in sources["app.py"]
            and 'style="Preview.TFrame",' in sources["app.py"]
            and 'style="Card.TFrame"' not in sources["app.py"]
            and fake_style.configured["Treeview"].get("borderwidth") == 0
            and fake_style.configured["Treeview"].get("bordercolor")
                == T.PALETTE["surface"]
            and fake_style.configured["Treeview"].get("lightcolor")
                == T.PALETTE["surface"]),
        "main shell has no draggable Search center or Preview Stats sashes": (
            "ttk.PanedWindow" not in sources["app.py"]
            and 'uniform="main_side"' in sources["app.py"]
            and T.MAIN_CENTER_COLUMN_WIDTH == 470
            and T.MAIN_CARD_PREVIEW_HEIGHT == 558
            and T.MAIN_SEARCH_WEIGHT == 51
            and T.MAIN_DECK_WEIGHT == 49
            and sources["app.py"].count('style="PaneDivider.TSeparator"') == 3),
        "card preview panel has no surrounding border box": (
            fake_style.configured["Preview.TFrame"].get("borderwidth") == 0
            and fake_style.configured["Preview.TFrame"].get("relief") == "flat"),
        "larger comparison metrics remain centralized": (
            T.COMPARISON_WINDOW_SIZE == (1840, 1120)
            and T.COMPARISON_IMAGE_MAX_WIDTH == 340),
        "feature UI uses role tokens instead of local font tuples": (
            "font=(FONT_FAMILY" not in gui_source),
        "all shared font roles use Segoe UI": all(
            font[0] == T.FONT_FAMILY for font in (
                T.FONT_BODY, T.FONT_BODY_BOLD, T.FONT_HELPER,
                T.FONT_HELPER_BOLD, T.FONT_MICRO, T.FONT_MICRO_BOLD,
                T.FONT_PANE_TITLE, T.FONT_DIALOG_TITLE,
                T.FONT_PROGRESS_TITLE,
            )),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    failures = _undersized_static_buttons(combined_source)
    if failures:
        print("  Undersized controls:", failures)
    print("\nUI VISUAL CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
