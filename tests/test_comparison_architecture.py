"""Headless comparison-domain behavior and architecture regressions."""

import ast
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, os.fspath(ROOT))

from mtgdb.comparison.models import (
    ComparisonCollection,
    MAX_COMPARISON_CARDS,
    MIN_COMPARISON_CARDS,
    comparison_json_list,
)
from mtgdb.ui.comparison import comparison_layout_metrics
from mtgdb.ui.comparison_controls import (
    COMPARISON_OVER_LIMIT_NOTE,
    ComparisonFeatureMixin,
    comparison_action_columns,
    comparison_selection_overflows,
)
from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession, DeckSessionManager
from mtgdb.ui.tokens import CARD_PREVIEW_PORTRAIT_SIZE, COMPARISON_WINDOW_SIZE


def _source(name):
    return (ROOT / name).read_text(encoding="utf-8")


def _imports(source):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _class_methods(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {
        node.name for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }



class _DB:
    def get_card(self, _card_id):
        return None


class _FakeSectionLabel:
    """Minimal stand-in for the comparison heading label.

    Records configured text/style and queues ``after`` callbacks so the bounded
    over-limit pulse can be driven deterministically without a Tk event loop.
    """

    def __init__(self):
        self.text = ""
        self.style = "Section.TLabel"
        self.styles = []
        self.pending = []
        self.cancelled = []
        self._issued = 0

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "style" in kwargs:
            self.style = kwargs["style"]
            self.styles.append(kwargs["style"])

    def after(self, _delay_ms, callback):
        self._issued += 1
        token = f"after#{self._issued}"
        self.pending.append((token, callback))
        return token

    def after_cancel(self, token):
        self.cancelled.append(token)
        self.pending = [entry for entry in self.pending if entry[0] != token]

    def run_pending(self, limit=32):
        """Fire queued pulses the way the Tk event loop would."""
        fired = 0
        while self.pending and fired < limit:
            _token, callback = self.pending.pop(0)
            callback()
            fired += 1
        return fired


class _ComparisonHarness(ComparisonFeatureMixin):
    def __init__(self, session):
        self.db = _DB()
        self.comparison = ComparisonCollection()
        self.deck_sessions = DeckSessionManager([session])
        self.deck = session.deck
        self._selected_deck = None
        self.statuses = []
        self.refreshes = []
        self.tab_renders = 0
        self.odds_updates = 0
        # Selection stubs standing in for the Results viewport and the two deck
        # Treeviews, which the comparison bar reads but does not own.
        self.selected_result_ids = ()
        self.selected_board_ids = {"main": (), "side": ()}
        self._initialize_comparison()

    def _selected_result_visible_count(self):
        return len(self.selected_result_ids)

    def _result_selection_contains_visible(self, card_id):
        return str(card_id or "") in self.selected_result_ids

    def _deck_selected_ids(self, board):
        return tuple(self.selected_board_ids.get(board, ()))

    def _active_session(self):
        return self.deck_sessions.active

    def _comparison_changed(self):
        return None

    def _status(self, message):
        self.statuses.append(message)

    def _refresh_changed_deck_views(self, *boards):
        self.refreshes.append(boards)

    def _update_card_odds(self):
        self.odds_updates += 1

    def _render_deck_tabs(self):
        self.tab_renders += 1

    def _mark_deck_dirty(self):
        self.deck_sessions.active.dirty = True



def main():
    collection = ComparisonCollection()
    first = {"id": "print-a", "name": "Shared Name", "set_code": "one"}
    second = {"id": "print-b", "name": "Shared Name", "set_code": "two"}
    first_add = collection.add(first)
    second_add = collection.add(second)
    duplicate = collection.add(first)
    ordered_ids = [card["id"] for card in collection.cards()]
    toggled = collection.add(first, toggle=True)
    readded = collection.add(first)
    for index in range(3, MAX_COMPARISON_CARDS + 1):
        collection.add({"id": f"print-{index}", "name": f"Card {index}"})
    full = collection.add({"id": "overflow", "name": "Overflow"})
    full_count = len(collection)
    removed = collection.remove("print-b")
    cleared = collection.clear()

    source_deck = Deck()
    source_card = {"id": "source-card", "name": "Source Card"}
    source_deck.add(source_card, "main", 2)
    source_deck.add(source_card, "side", 1)
    source_session = DeckSession(source_deck)
    source_harness = _ComparisonHarness(source_session)
    source_harness._add_to_comparison(source_card, source_board="main")
    source_info_before = source_harness._comparison_source_info("source-card")
    source_removed_main_once = source_harness._remove_comparison_source_from_board(
        "source-card", "main")
    source_info_after_one = source_harness._comparison_source_info("source-card")
    source_removed_main_twice = source_harness._remove_comparison_source_from_board(
        "source-card", "main")
    source_info_after_two = source_harness._comparison_source_info("source-card")
    source_main_after_removals = source_deck.total("main")
    source_side_after_removals = source_deck.total("side")
    comparison_survived_board_remove = "source-card" in source_harness.comparison

    search_card = {"id": "search-card", "name": "Search Card"}
    source_harness._add_to_comparison(search_card)
    search_origin_has_no_source = (
        source_harness._comparison_source_info("search-card") is None)
    search_added_main = source_harness._add_comparison_card_to_board(
        "search-card", "main")
    search_added_side = source_harness._add_comparison_card_to_board(
        "search-card", "side")

    # --- Over-limit selection feedback (CMP-016) --------------------------
    overflow_predicate = (
        comparison_selection_overflows(0, 0) is False
        and comparison_selection_overflows(0, MAX_COMPARISON_CARDS) is False
        and comparison_selection_overflows(0, MAX_COMPARISON_CARDS + 1) is True
        and comparison_selection_overflows(4, 3) is False
        and comparison_selection_overflows(4, 4) is True
        and comparison_selection_overflows(MAX_COMPARISON_CARDS, 1) is True)

    limit_deck = Deck()
    limit_session = DeckSession(limit_deck)
    limit = _ComparisonHarness(limit_session)
    limit_label = _FakeSectionLabel()
    limit._comparison_selection_lbl = limit_label

    # Eight highlighted Results rows with nothing compared yet.
    limit.selected_result_ids = tuple(f"sel-{n}" for n in range(8))
    limit._update_comparison_bar()
    eight_selected_over_limit = (
        limit._comparison_pending_count() == 8
        and limit._comparison_over_limit is True
        and limit_label.text
        == f"COMPARE | 8 CARDS SELECTED{COMPARISON_OVER_LIMIT_NOTE}"
        and limit_label.style == "SectionAlert.TLabel")

    # The pulse is bounded: it settles on steady bright red and stops.
    pulses_fired = limit_label.run_pending()
    bounded_flash = (
        pulses_fired == limit.OVER_LIMIT_FLASH_PULSES * 2
        and limit_label.styles.count("SectionAlertDim.TLabel")
        == limit.OVER_LIMIT_FLASH_PULSES
        and limit_label.style == "SectionAlert.TLabel"
        and limit_label.pending == [])

    # Dropping back to seven restores the normal heading and wording.
    limit.selected_result_ids = tuple(f"sel-{n}" for n in range(7))
    limit._update_comparison_bar()
    under_limit_restores = (
        limit._comparison_over_limit is False
        and limit_label.text == "COMPARE | 7 CARDS SELECTED"
        and limit_label.style == "Section.TLabel")

    # Four already compared: three more fit, a fourth does not.
    partial_deck = Deck()
    partial_session = DeckSession(partial_deck)
    partial = _ComparisonHarness(partial_session)
    partial_label = _FakeSectionLabel()
    partial._comparison_selection_lbl = partial_label
    for index in range(4):
        partial.comparison.add({"id": f"cmp-{index}", "name": f"Compared {index}"})
    partial.selected_result_ids = ("new-1", "new-2", "new-3")
    partial._update_comparison_bar()
    three_more_fit = (
        partial._comparison_over_limit is False
        and partial_label.text == "COMPARE | 3 CARDS SELECTED")
    partial.selected_result_ids = ("new-1", "new-2", "new-3", "new-4")
    partial._update_comparison_bar()
    fourth_overflows = (
        partial._comparison_over_limit is True
        and partial_label.text
        == f"COMPARE | 4 CARDS SELECTED{COMPARISON_OVER_LIMIT_NOTE}"
        and partial_label.style == "SectionAlert.TLabel")

    # Re-highlighting cards that are already compared must not report overflow:
    # adding them again is a no-op, so they consume no further slots.
    partial.selected_result_ids = (
        "cmp-0", "cmp-1", "cmp-2", "cmp-3", "new-1", "new-2", "new-3")
    partial._update_comparison_bar()
    compared_reselection_not_counted = (
        partial._comparison_pending_count() == 3
        and partial._comparison_over_limit is False
        and partial_label.text == "COMPARE | 7 CARDS SELECTED")

    # The same rule applies to a mixed Results/deck selection.
    mixed_deck = Deck()
    mixed_card = {"id": "board-card", "name": "Board Card"}
    mixed_deck.add(mixed_card, "main", 1)
    mixed_session = DeckSession(mixed_deck)
    mixed = _ComparisonHarness(mixed_session)
    mixed._comparison_selection_lbl = _FakeSectionLabel()
    for index in range(MAX_COMPARISON_CARDS):
        mixed.comparison.add({"id": f"full-{index}", "name": f"Full {index}"})
    mixed.selected_board_ids = {"main": ("board-card",), "side": ()}
    mixed._update_comparison_bar()
    full_comparison_overflows = (
        mixed._comparison_pending_count() == 1
        and mixed._comparison_over_limit is True)

    # Entering the alert state once must not restart the pulse on every
    # selection change that stays over the limit.
    steady_label = _FakeSectionLabel()
    steady = _ComparisonHarness(DeckSession(Deck()))
    steady._comparison_selection_lbl = steady_label
    steady.selected_result_ids = tuple(f"s-{n}" for n in range(9))
    steady._update_comparison_bar()
    steady_label.run_pending()
    settled_styles = len(steady_label.styles)
    steady.selected_result_ids = tuple(f"s-{n}" for n in range(10))
    steady._update_comparison_bar()
    flash_not_restarted = (
        len(steady_label.styles) == settled_styles
        and steady_label.pending == []
        and steady_label.text
        == f"COMPARE | 10 CARDS SELECTED{COMPARISON_OVER_LIMIT_NOTE}")

    # A destroyed label must leave no pending timer behind (CLR-005).
    destroy_label = _FakeSectionLabel()
    destroyed = _ComparisonHarness(DeckSession(Deck()))
    destroyed._comparison_selection_lbl = destroy_label
    destroyed.selected_result_ids = tuple(f"d-{n}" for n in range(8))
    destroyed._update_comparison_bar()
    had_pending_timer = bool(destroy_label.pending)
    destroyed._cancel_comparison_over_limit_flash(
        type("_Event", (), {"widget": destroy_label})())
    flash_released_on_destroy = (
        had_pending_timer
        and destroy_label.pending == []
        and destroy_label.cancelled
        and destroyed._comparison_over_limit is False)

    model_source = _source("mtgdb/comparison/models.py")
    controls_source = _source("mtgdb/ui/comparison_controls.py")
    view_source = _source("mtgdb/ui/comparison.py")
    gui_source = _source("mtgdb/ui/app.py")
    deck_ui_source = _source("mtgdb/ui/deck.py")
    control_methods = _class_methods(controls_source, "ComparisonFeatureMixin")
    gui_methods = _class_methods(gui_source, "DeckBuilderApp")
    extracted_methods = {
        "_build_comparison_bar", "_layout_comparison_actions",
        "_selected_comparison_candidates",
        "_add_selected_to_comparison",
        "_add_to_comparison", "_add_cards_to_comparison",
        "_add_selected_results_to_comparison", "_remove_from_comparison",
        "_clear_comparison", "_comparison_changed",
        "_update_comparison_bar", "_show_comparison_notice",
        "_open_comparison_window", "_show_result_context_menu",
        "_comparison_source_record", "_comparison_source_info",
        "_remove_comparison_source_from_board",
        "_comparison_card", "_add_comparison_card_to_board",
        "_open_card_grid_window",
        "_update_card_grid_window", "_close_card_grid_window",
        "_comparison_pending_count", "_set_comparison_over_limit",
        "_schedule_comparison_over_limit_flash",
        "_advance_comparison_over_limit_flash",
        "_apply_comparison_selection_style",
        "_cancel_comparison_over_limit_flash",
    }

    # Markers that would signal card-attribute/data or dropdown UI creeping back
    # into the image-only comparison window (CMP-008/CMP-009).
    forbidden_view_markers = (
        "Combobox", "Differences Only", "Highlight Differences",
        "_build_section", "_build_gameplay", "_build_types",
        "_build_deck_format", "_build_printing", "_add_symbol_text_row",
        "_add_color_icon_row", "_add_boolean_row", "legality", "power_toughness",
        "diff_only_var", "highlight_var",
    )
    # The data helpers must not exist in the model any more (CMP-003).
    removed_model_helpers = (
        "def comparison_row_state(", "def comparison_key(",
        "def comparison_json_dict(", "def format_mana_value(",
        "def printing_short(", "def visible_symbol_text(",
        "def color_tokens(", "def colors_text(", "def power_toughness(",
        "def legality_text(", "def join_face_field(", "def type_parts(",
    )

    seven_layout = comparison_layout_metrics(
        COMPARISON_WINDOW_SIZE[0], COMPARISON_WINDOW_SIZE[1], 7)
    seven_layout_high_scale = comparison_layout_metrics(
        COMPARISON_WINDOW_SIZE[0], COMPARISON_WINDOW_SIZE[1], 7,
        tk_scaling=144 / 72)

    comparison_wraps = (
        comparison_action_columns(900, (158, 171, 173, 158)) == 4
        and comparison_action_columns(600, (158, 171, 173, 158)) == 2
        and comparison_action_columns(300, (158, 171, 173, 158)) == 1)

    checks = {
        "limits remain two through seven": (
            MIN_COMPARISON_CARDS == 2 and MAX_COMPARISON_CARDS == 7),
        "same-name exact printings remain distinct and ordered": (
            first_add.status == "added" and second_add.status == "added"
            and ordered_ids == ["print-a", "print-b"]),
        "duplicates do not mutate the collection": (
            duplicate.status == "duplicate" and duplicate.changed is False),
        "toggle removes and exact printing can be re-added": (
            toggled.status == "removed" and readded.status == "added"),
        "maximum rejects overflow": (
            full.status == "full" and full_count == MAX_COMPARISON_CARDS),
        "remove and clear report mutations": (
            removed.status == "removed" and cleared.status == "cleared"),
        "JSON list parsing accepts objects and serialized values": (
            comparison_json_list('[{"name": "Day"}]') == [{"name": "Day"}]
            and comparison_json_list({"bad": True}) == []
            and comparison_json_list(None) == []),
        "comparison model is Tk SQLite UI and network free": (
            not ({"tkinter", "sqlite3", "net"} & _imports(model_source))
            and not any(
                root.startswith("ui_") for root in _imports(model_source))),
        "comparison model holds no card-attribute display logic": all(
            marker not in model_source for marker in removed_model_helpers),
        "comparison action bar wraps four to two to one columns": comparison_wraps,
        "comparison controls own every application mutation callback": (
            extracted_methods <= control_methods),
        "DeckBuilderApp composes comparison owners without reimplementing": (
            "ComparisonFeatureMixin" in gui_source
            and "ComparisonCollection()" in gui_source
            and not (extracted_methods & gui_methods)
            and "self._build_comparison_bar(parent)" in deck_ui_source
            and 'label="Add to Comparison"' not in deck_ui_source
            and 'label="Remove from Comparison"' not in deck_ui_source),
        "comparison view reads the model without mutating storage": (
            "self.app.comparison.cards()" in view_source
            and "self.app.comparison.add(" not in view_source
            and "self.app.comparison.remove(" not in view_source
            and "self.app.comparison.clear(" not in view_source),
        "comparison window shows images and contextual actions without card names": (
            'text="Remove"' not in view_source
            and 'text=f"From {board_label}\\n{qty} {copy_label}"' in view_source
            and 'text=f"Remove from\\n{board_label}"' in view_source
            and 'text="Add to\\nMainboard"' in view_source
            and 'text="Add to\\nSideboard"' in view_source
            and "self.app._remove_from_comparison(" not in view_source
            and "self.app._remove_comparison_source_from_board(" in view_source
            and "self.app._add_comparison_card_to_board(" in view_source
            and 'tk.Label(meta, text=name' not in view_source
            and 'if self._static_cards is None:' in view_source
            and 'count_label' not in view_source
            and all(marker not in view_source
                    for marker in forbidden_view_markers)),
        "deck-origin comparison actions track one source board and remove one copy": (
            source_info_before["board"] == "main"
            and source_info_before["qty"] == 2
            and source_removed_main_once
            and source_info_after_one["qty"] == 1
            and source_removed_main_twice
            and source_info_after_two["qty"] == 0
            and source_main_after_removals == 0
            and source_side_after_removals == 1
            and comparison_survived_board_remove
            and source_session.dirty),
        "search-origin comparison cards add to either active deck board": (
            search_origin_has_no_source and search_added_main and search_added_side
            and source_deck.total("main") == 1
            and source_deck.total("side") == 2),
        "read-only card grid reuses comparison presentation without collection mutation": (
            "cards=None" in view_source
            and "def show_cards(" in view_source
            and "self._static_cards" in view_source
            and "def _open_card_grid_window(" in controls_source),
        "comparison image requests remain in the modeless view": (
            "card_image_service.request(" in view_source
            and "ImageTk.PhotoImage(" in view_source),
        "comparison viewport is fixed and has no scrollbars": (
            "ttk.Scrollbar" not in view_source
            and "tk.Canvas" not in view_source
            and "self.top.resizable(False, False)" in view_source
            and "_center_popup_with_visible_actions(" in view_source),
        "comparison cards use equal-width grid cells so the final rail is not squeezed": (
            'uniform="comparison_card"' in view_source
            and 'cardbox.grid(' in view_source),
        "comparison rail expands for elevated Tk scaling": (
            seven_layout_high_scale["meta_width"] >= 148
            and seven_layout_high_scale["image_w"] < seven_layout["image_w"]),
        "seven-card comparison uses main-preview image size when viewport permits": (
            seven_layout["columns"] == 4
            and seven_layout["rows"] == 2
            and (seven_layout["image_w"], seven_layout["image_h"])
            == CARD_PREVIEW_PORTRAIT_SIZE),
        "comparison notices are app-owned dark dialogs": (
            "messagebox" not in controls_source
            and "def _show_comparison_notice(" in controls_source
            and 'PALETTE["surface"]' in controls_source
            and 'role="compact_primary"' in controls_source),
        "over-limit selection disables Add Selected and marks the heading": (
            eight_selected_over_limit
            and 'and not over_limit' in controls_source),
        "over-limit predicate is a pure, reusable rule": overflow_predicate,
        "over-limit pulse is bounded and settles on steady red": bounded_flash,
        "dropping back under the cap restores the normal heading": under_limit_restores,
        "compared cards plus a fitting selection stay under the cap": three_more_fit,
        "one card past the cap triggers the over-limit heading": fourth_overflows,
        "re-highlighting compared cards never reports an overflow": (
            compared_reselection_not_counted),
        "a full comparison overflows on any further deck selection": (
            full_comparison_overflows),
        "staying over the cap does not restart the pulse": flash_not_restarted,
        "the heading releases its pulse timer on destroy": flash_released_on_destroy,
        "over-limit styling is routed through shared ttk styles": (
            'self._apply_comparison_selection_style("SectionAlert.TLabel")'
                in controls_source
            and "SectionAlertDim.TLabel" in controls_source
            and 'PALETTE["deck_bad"]' not in controls_source
            and 'PALETTE["deck_bad_dim"]' not in controls_source
            and "foreground=" not in controls_source),
        "comparison has no post-map titlebar refresh queue": (
            "_titlebar_after_ids" not in view_source
            and "_schedule_titlebar_refreshes" not in view_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nCOMPARISON ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
