"""Deck-editor and deck-statistics UI ownership and behavior contracts."""

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.model import Deck
from mtgdb.ui.deck import DeckEditorMixin, deck_action_layout_mode
from mtgdb.ui.components import deck_board_label
from mtgdb.ui.deck_stats import DeckStatsMixin


EDITOR_METHODS = {
    "_build_deck_pane", "_update_board_headers", "_make_deck_tree",
    "_active_session", "_session_title", "_render_deck_tabs",
    "_show_deck_overflow_menu", "_show_new_deck_menu",
    "_show_deck_tab_menu", "_capture_active_session_state",
    "_load_active_session_state", "_switch_deck_session",
    "_append_deck_session", "_mark_deck_dirty", "_confirm_close_session",
    "_close_active_deck", "_close_deck_session", "_close_other_decks",
    "_add_to_deck", "_show_deck_context_menu", "_search_for_deck_card",
    "_on_deck_select", "_deck_selected_ids", "_active_deck_selection",
    "_restore_deck_selections", "_restore_deck_selection",
    "_neighbor_after_removal",
    "_deck_card", "_deck_qty", "_deck_remove", "_deck_move",
    "_refresh_single_deck_view", "_refresh_changed_deck_views",
    "_refresh_deck_views", "_on_deck_format_selected", "_sync_deck_meta",
    "_new_deck",
}
STATS_METHODS = {
    "_build_stats_panel", "_refresh_stats", "_on_curve_mode",
    "_schedule_curve_redraw", "_finish_curve_resize", "_draw_curve",
    "_render_legend", "_render_mana_check", "_render_types",
    "_render_draw_odds", "_update_card_odds", "_draw_hand",
    "_fresh_sample_hand_card", "_view_hand", "_on_hand_select",
    "_render_legality", "_show_legality_details",
}


def _class_methods(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return cls, {
        node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


class _Tree:
    def __init__(self, card_id):
        self.card_ids = [card_id] if card_id else []
        self.selected = [card_id] if card_id else []
        self.focused = card_id or ""

    def exists(self, card_id):
        return card_id in self.card_ids

    def get_children(self, _parent=""):
        return tuple(self.card_ids)

    def selection(self):
        return tuple(self.selected)

    def selection_set(self, *card_ids):
        self.selected = list(card_ids)

    def selection_remove(self, *card_ids):
        removed = set(card_ids)
        self.selected = [card_id for card_id in self.selected if card_id not in removed]

    def focus(self, card_id=None):
        if card_id is not None:
            self.focused = card_id
        return self.focused

    def see(self, _card_id):
        return None


class _DeckHarness(DeckEditorMixin):
    def __init__(self, deck, card_id):
        self.deck = deck
        self.main_tv = _Tree(card_id)
        self.side_tv = _Tree("")
        self._selected_deck = (card_id, "main")
        self.dirty_count = 0
        self.refreshes = []
        self.odds_updates = 0

    def _mark_deck_dirty(self):
        self.dirty_count += 1

    def _refresh_changed_deck_views(self, *views):
        self.refreshes.append(views)

    def _update_card_odds(self):
        self.odds_updates += 1



class _StateButton:
    def __init__(self):
        self.last_state = None

    def state(self, value):
        self.last_state = tuple(value)


class _HandTree:
    def __init__(self):
        self.rows = []

    def get_children(self):
        return tuple(str(index) for index in range(len(self.rows)))

    def delete(self, _iid):
        if self.rows:
            self.rows.pop(0)

    def insert(self, _parent, _where, **kwargs):
        self.rows.append(kwargs)


class _FreshHandDB:
    def __init__(self, cards):
        self.cards = {card["id"]: dict(card) for card in cards}

    def get_card(self, card_id):
        card = self.cards.get(card_id)
        return dict(card) if card else None


class _StatsHarness(DeckStatsMixin):
    def __init__(self, deck, db=None):
        self.deck = deck
        self.db = db
        self.hand_tv = _HandTree()
        self.hand_view_btn = _StateButton()
        self._hand = []
        self.updated_grid = None
        self.opened_grid = None

    def _cost_image(self, _cost, _height):
        return None

    def _update_card_grid_window(self, key, cards):
        self.updated_grid = (key, list(cards))
        return True

    def _open_card_grid_window(self, key, title, cards):
        self.opened_grid = (key, title, list(cards))
        return object()



def main():
    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/ui/deck.py", "mtgdb/ui/deck_files.py",
            "mtgdb/ui/deck_stats.py", "mtgdb/ui/printing.py")
    }
    gui_class, gui_methods = _class_methods(sources["mtgdb/ui/app.py"], "DeckBuilderApp")
    _, editor_methods = _class_methods(sources["mtgdb/ui/deck.py"], "DeckEditorMixin")
    _, stats_methods = _class_methods(
        sources["mtgdb/ui/deck_stats.py"], "DeckStatsMixin")
    _, deck_file_methods = _class_methods(
        sources["mtgdb/ui/deck_files.py"], "DeckFileWorkflowMixin")
    _, printing_methods = _class_methods(
        sources["mtgdb/ui/printing.py"], "PrintingMixin")
    gui_bases = {
        base.id for base in gui_class.bases if isinstance(base, ast.Name)}

    card = {
        "id": "printing-a", "name": "Test Card", "cmc": 2,
        "type_line": "Creature", "mana_cost": "{1}{G}",
    }
    deck = Deck()
    deck.add(card, "main", 2)
    harness = _DeckHarness(deck, card["id"])
    harness._deck_qty(1)
    quantity_after_add = deck.entries("main")[0]["qty"]
    harness._deck_qty(-1)
    quantity_after_subtract = deck.entries("main")[0]["qty"]
    harness._deck_move()
    moved_to_side = deck.total("main") == 0 and deck.total("side") == 2

    hand_deck = Deck()
    hand_card = {
        "id": "hand-printing", "name": "Hand Card",
        "cmc": 1, "type_line": "Creature", "mana_cost": "{G}",
    }
    hand_deck.add(hand_card, "main", 7)
    fresh_hand_card = dict(hand_card, image_normal="https://example.invalid/fresh.jpg")
    replacement_hand_card = {
        "id": "hand-replacement", "name": "Replacement Hand Card",
        "cmc": 2, "type_line": "Creature", "mana_cost": "{1}{G}",
        "image_normal": "https://example.invalid/replacement.jpg",
    }
    stats_harness = _StatsHarness(
        hand_deck, _FreshHandDB([fresh_hand_card, replacement_hand_card]))
    stats_harness._draw_hand()
    stats_harness._view_hand()
    initial_hand = list(stats_harness._hand)
    hand_deck.remove("hand-printing", "main")
    hand_deck.add(replacement_hand_card, "main", 7)
    stats_harness._draw_hand()
    refreshed_hand = list(stats_harness._hand)

    editor_source = sources["mtgdb/ui/deck.py"]
    stats_source = sources["mtgdb/ui/deck_stats.py"]
    gui_source = sources["mtgdb/ui/app.py"]
    dpi_widths = (48, 48, 112, 196)
    wide_needed = sum(dpi_widths) + 12
    two_needed = max(dpi_widths[0] + dpi_widths[1] + 4,
                     dpi_widths[2] + dpi_widths[3] + 4)
    # Stats snapshot freshness. The mana and draw-odds panels share one cached
    # analysis; if the generation test is inverted they serve stale figures after
    # every edit, which looks like the panel simply failing to update.
    class _SnapshotHarness(DeckStatsMixin):
        def __init__(self, deck):
            self.deck = deck

    _snapshot_deck = Deck("Snapshot", "modern")
    _snapshot_card = {
        "id": "snap-1", "name": "Snapshot Card", "type_line": "Creature",
        "mana_cost": "{G}", "cmc": 1, "color_identity": ["G"],
    }
    _snapshot_deck.add(_snapshot_card, "main", 1)
    _snapshot = _SnapshotHarness(_snapshot_deck)

    _first = _snapshot._analysis_snapshot()
    _cached = _snapshot._analysis_snapshot()
    _reused_while_unchanged = _cached is _first
    _snapshot_deck.add(_snapshot_card, "main", 3)          # generation advances
    _after_edit = _snapshot._analysis_snapshot()
    _recomputed_after_edit = (
        _after_edit is not _first
        and _after_edit.generation == _snapshot_deck.generation
        and _after_edit.stats["main_total"] == 4)
    _stable_again = _snapshot._analysis_snapshot() is _after_edit

    # Save As must fold the live name/format editors into the deck only when the
    # tab being saved is the one those editors belong to. Saving a background tab
    # after syncing would stamp the active tab's name onto a different deck.
    from unittest import mock as _mock
    import mtgdb.ui.deck_files as _deck_files

    class _SaveSession:
        def __init__(self, name):
            self.deck = Deck(name, "modern")
            self.path = None
            self.dirty = True

    class _SaveSessions:
        def __init__(self, count, active):
            self._sessions = [_SaveSession(f"Deck {i}") for i in range(count)]
            self.active_index = active

        def is_valid_index(self, index):
            return isinstance(index, int) and 0 <= index < len(self._sessions)

        def __getitem__(self, index):
            return self._sessions[index]

    class _SaveProbe(_deck_files.DeckFileWorkflowMixin):
        def __init__(self, count, active):
            self.deck_sessions = _SaveSessions(count, active)
            self.syncs = 0

        def _sync_deck_meta(self):
            self.syncs += 1

    def _save_syncs(target_index, active_index):
        probe = _SaveProbe(3, active_index)
        # An empty path aborts before any file work; the sync decision has
        # already been made by then.
        with _mock.patch.object(
                _deck_files.filedialog, "asksaveasfilename", return_value=""):
            result = probe._save_session_as(target_index)
        return probe.syncs, result

    save_sync_scoping = (
        _save_syncs(1, 1) == (1, False)      # saving the active tab syncs once
        and _save_syncs(0, 1) == (0, False)  # a background tab must not sync
        and _save_syncs(2, 1) == (0, False)
        and _save_syncs(9, 1) == (0, False))  # invalid index does nothing

    # DUI-019. Closing a dirty session destroys it the moment
    # _confirm_close_session returns True, so that value must mean "written",
    # not "submitted". Exercise the waiting path against a failed write.
    import concurrent.futures

    from mtgdb.ui import deck_files as deck_files_module
    from mtgdb.ui.deck_files import DeckFileWorkflowMixin

    class _RecordingMessagebox:
        def __init__(self):
            self.errors = []

        def showerror(self, title, message):
            self.errors.append((title, message))

    class _SaveHarness(DeckFileWorkflowMixin):
        def __init__(self):
            self.saved_calls = 0

        def _saved(self, _result):
            self.saved_calls += 1

    recorded = _RecordingMessagebox()
    original_messagebox = deck_files_module.messagebox
    save_harness = _SaveHarness()
    failed_future = concurrent.futures.Future()
    failed_future.set_exception(OSError("target folder is read-only"))
    ok_future = concurrent.futures.Future()
    ok_future.set_result(None)
    try:
        deck_files_module.messagebox = recorded
        failed_write_reported = save_harness._await_deck_file_job(
            failed_future, save_harness._saved, error_title="Save failed")
        saved_calls_after_failure = save_harness.saved_calls
        good_write_reported = save_harness._await_deck_file_job(
            ok_future, save_harness._saved, error_title="Save failed")
        saved_calls_after_success = save_harness.saved_calls
    finally:
        deck_files_module.messagebox = original_messagebox

    deck_file_source = sources["mtgdb/ui/deck_files.py"]

    checks = {
        "failed save is reported as not written and keeps the session": (
            failed_write_reported is False
            and saved_calls_after_failure == 0
            and len(recorded.errors) == 1
            and recorded.errors[0][0] == "Save failed"),
        "completed save is reported as written": (
            good_write_reported is True
            and saved_calls_after_success == 1),
        "closing a dirty session waits for the deck-file write": (
            "def _save_session_as(self, index, *, wait=False)"
            in deck_file_source
            and "_save_session_as(index, wait=True)" in editor_source
            and "return self._await_deck_file_job(" in deck_file_source),
        "Save As syncs the live editors only for the active tab": (
            save_sync_scoping),
        "deck stats reuse one analysis snapshot until the deck changes": (
            _reused_while_unchanged and _stable_again),
        "deck stats recompute analysis after every deck mutation": (
            _recomputed_after_edit),
        "deck board identifiers map to their display names": (
            deck_board_label("main") == "Mainboard"
            and deck_board_label("side") == "Sideboard"
            and deck_board_label("") == "Sideboard"),
        "DeckBuilderApp composes deck UI and file-workflow mixins": (
            {"DeckEditorMixin", "DeckStatsMixin", "DeckFileWorkflowMixin"}
            <= gui_bases),
        "deck editor owns every mapped editor method": (
            EDITOR_METHODS <= editor_methods),
        "deck statistics owns every mapped statistics method": (
            STATS_METHODS <= stats_methods),
        "gui defines no extracted deck UI method": (
            not (EDITOR_METHODS | STATS_METHODS) & gui_methods),
        "editor routes mutations through the Deck API": all(
            marker in editor_source for marker in (
                "self.deck.add(", "self.deck.change_qty(",
                "self.deck.remove(", "self.deck.move(")),
        "quantity and move callbacks preserve behavior": (
            quantity_after_add == 3 and quantity_after_subtract == 2
            and moved_to_side and harness.dirty_count == 3
            and harness.refreshes == [("main",), ("main",), ("main", "side")]),
        "deck controls retain their callback manifest": all(
            marker in editor_source + stats_source for marker in (
                "command=lambda: self._deck_qty(1)",
                "command=lambda: self._deck_qty(-1)",
                "command=self._deck_remove", "command=self._deck_move",
                'command=self._draw_hand', 'command=self._view_hand',
            )),
        "deck table selection bindings remain present": all(
            marker in editor_source for marker in (
                '"<<TreeviewSelect>>"', '"<Button-3>"',
                "self._bind_column_drag(tv, view)",
            )),
        "deck layout retains tabs and split boards with counts in section headings": (
            'text="Current Deck"' not in editor_source
            and 'deck_count_lbl' not in editor_source
            and 'MAINBOARD | Cards:' in editor_source
            and 'SIDEBOARD | Cards:' in editor_source
            and all(marker in editor_source for marker in (
                'orient="vertical"', 'role="tab_add"', 'role="tab_overflow"'))),
        "deck actions use DPI-safe requested widths without clipping": (
            deck_action_layout_mode(wide_needed, dpi_widths) == "wide"
            and deck_action_layout_mode(wide_needed - 1, dpi_widths, "wide") == "two"
            and deck_action_layout_mode(two_needed, dpi_widths, "two") == "two"
            and deck_action_layout_mode(two_needed - 1, dpi_widths, "two") == "stack"
            and "winfo_reqwidth()" in editor_source
            and "width >= 420" not in editor_source),
        "deck format uses shared authoritative checklist catalog": (
            "open_search_checklist(" in editor_source
            and "self._format_catalog" in editor_source
            and "AppCombobox" not in editor_source
            and "_format_catalog.append" not in editor_source
            and '.replace("_", " ").capitalize()' in editor_source),
        "statistics UI consumes domain-owned calculations": all(
            marker in stats_source for marker in (
                "analyze_deck(self.deck)",
                "card_draw_odds(self.deck, name)",
                "curve_breakdown(self.deck, mode)",
                "snapshot.color_pips", "snapshot.color_sources",
                "snapshot.opening_land_stats", "sample_hand(self.deck)",
                "legality_problems(self.deck)",
            )),
        "Deck Stats explains probability and mana metrics in plain language": (
            'Mainboard: {n} cards · {lands} lands' in stats_source
            and 'Average lands in a 7-card opening hand: {avg:.1f}' in stats_source
            and 'Chance an opening hand has 2–4 lands: {p24:.1%}' in stats_source
            and 'Copies in Mainboard: {copies}' in stats_source
            and 'Chance to have it in your opening 7: {opener:.1%}' in stats_source
            and 'Chance to have seen it by turn 3: {turn3:.1%}' in stats_source
            and 'Chance to have seen it by turn 6: {turn6:.1%}' in stats_source
            and 'Mana-producing cards: {n_sources}\\n' in stats_source
            and 'Red colored Sources numbers indicate missing/light mana source support.'
                in stats_source),
        "Deck Stats uses the professional scrollable dashboard layout": (
            "stats_summary" not in stats_source
            and "average mana value" not in stats_source
            and "avg MV" not in stats_source
            and "Turn estimates: on the play" not in stats_source
            and "Deck size · copy limits · sideboard · card legality" not in stats_source
            and 'def dashboard_section(title, *, actions=None):' in stats_source
            and 'dashboard_section("DECK OVERVIEW")' in stats_source
            and 'dashboard_section("MANA CURVE", actions=curve_actions)' in stats_source
            and 'dashboard_section("OPENING HAND & DRAW ODDS")' in stats_source
            and 'dashboard_section("SAMPLE HAND", actions=hand_actions)' in stats_source
            and 'dashboard_section("FORMAT LEGALITY", actions=legality_actions)' in stats_source
            and 'height=118' in stats_source
            and 'height=7,' in stats_source
            and 'orient="vertical", command=self.hand_tv.yview' not in stats_source
            and 'self.hand_horizontal_scroll = ttk.Scrollbar(' in stats_source
            and 'orient="horizontal", command=self.hand_tv.xview' in stats_source
            and 'self.hand_tv.configure(xscrollcommand=update_hand_horizontal_scroll)'
                in stats_source
            and '"name", width=315, minwidth=140, stretch=False' in stats_source
            and 'text="BASIC FORMAT CHECK"' in stats_source
            and 'font=FONT_DIALOG_TITLE' in stats_source
            and 'see Details' not in stats_source
            and "No problems found by basic checks" not in stats_source
            and "No issues were found by the app's available basic checks" not in stats_source),
        "Mana Curve By type and By color legends include numeric totals": (
            'total = sum(bucket.get(lab, 0) for bucket in buckets)' in stats_source
            and 'text=f" {lab}: {total}"' in stats_source),
        "sample hand inline rows show card names": (
            'columns=("name",), show="tree headings"' in stats_source
            and 'values=(card.get("name") or "",)' in stats_source),
        "sample hand reuses the shared large-card comparison grid": all(
            marker in stats_source for marker in (
                'text="View hand"',
                '_open_card_grid_window(',
                '_update_card_grid_window("sample_hand", self._hand)',
                '_close_card_grid_window("sample_hand")',
            )),
        "sample hand hydrates current exact printing before large-card view": (
            all(card.get("image_normal") == "https://example.invalid/fresh.jpg"
                for card in initial_hand)),
        "sample hand redraw uses current deck quantities rather than a cached pool": (
            len(refreshed_hand) == 7
            and {card.get("id") for card in refreshed_hand} == {"hand-replacement"}),
        "sample hand View hand preserves seven duplicate instances": (
            len(initial_hand) == 7
            and stats_harness.hand_view_btn.last_state == ("!disabled",)
            and stats_harness.updated_grid[0] == "sample_hand"
            and len(stats_harness.updated_grid[1]) == 7
            and stats_harness.opened_grid[0:2]
            == ("sample_hand", "Sample Opening Hand")
            and len(stats_harness.opened_grid[2]) == 7),
        "deck mutations refresh source-aware comparison actions": (
            "self._refresh_comparison_window_only()" in editor_source),
        "statistics UI owns no probability formula": (
            "math.comb" not in stats_source
            and "copies /" not in stats_source
            and "total_mana_value" not in stats_source),
        "deck UI modules own no persistence or SQL": all(
            marker not in editor_source + stats_source for marker in (
                "sqlite3", "SELECT ", "INSERT ", "UPDATE ",
                "deck_to_text(", "deck_from_text(", "json.dump",
                "print_template.",
            )),
        "deck appearance values come from shared tokens": (
            "DECK_TYPE_SEGMENT_COLORS" in stats_source
            and "DECK_COLOR_SEGMENT_COLORS" in stats_source
            and 'foreground=PALETTE["deck_good"]' in stats_source
            and 'foreground=PALETTE["deck_bad"]' in stats_source),
        "deck-file workflow owns open save import and JSON export": (
            {"_choose_import_sets", "_open_deck", "_save_session_as",
             "_save_deck", "_deck_json_payload", "_export_all_decks_json"}
            <= deck_file_methods
            and not ({"_choose_import_sets", "_open_deck", "_save_session_as",
                      "_save_deck", "_deck_json_payload",
                      "_export_all_decks_json"} & gui_methods)
            and "def _on_app_close(" in gui_source),
        "printing workflow is delegated outside deck and gui UI": (
            "PrintingMixin" in gui_bases
            and "_create_print_template" in printing_methods
            and "_create_print_template" not in gui_methods
            and 'command=self._create_print_template' in gui_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nDECK UI ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
