"""Batch-selection interaction regressions for Results and deck boards."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.comparison.models import ComparisonCollection
from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession, DeckSessionManager
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.comparison_controls import ComparisonFeatureMixin
from mtgdb.ui.deck import DeckEditorMixin
from mtgdb.ui.results import SearchResultsMixin


class _Tree:
    def __init__(self, ids=()):
        self.ids = list(ids)
        self.selected = list(self.ids)
        self.focused = self.ids[0] if self.ids else ""

    def get_children(self, _parent=""):
        return tuple(self.ids)

    def selection(self):
        return tuple(self.selected)

    def selection_set(self, *ids):
        self.selected = list(ids)

    def selection_remove(self, *ids):
        removed = set(ids)
        self.selected = [iid for iid in self.selected if iid not in removed]

    def focus(self, iid=None):
        if iid is not None:
            self.focused = iid
        return self.focused

    def exists(self, iid):
        return iid in self.ids

    def see(self, _iid):
        return None


class _DB:
    def get_card(self, _card_id):
        return None


class _SearchRepository:
    def __init__(self, cards):
        self.cards = {card["id"]: card for card in cards}

    def card_by_id(self, card_id):
        return self.cards.get(card_id)


class _DeckBatchHarness(DeckEditorMixin):
    def __init__(self, deck):
        self.deck = deck
        self.db = _DB()
        self.main_tv = _Tree(entry["card"]["id"] for entry in deck.entries("main"))
        self.side_tv = _Tree(entry["card"]["id"] for entry in deck.entries("side"))
        self.side_tv.selected = []
        self.side_tv.focused = ""
        first = self.main_tv.focused or self.side_tv.focused
        self._selected_deck = (first, "main") if first else None
        self.dirty_count = 0
        self.odds_updates = 0
        self.previews = []

    def _mark_deck_dirty(self):
        self.dirty_count += 1

    def _refresh_changed_deck_views(self, *views):
        for board in views:
            tv = self.main_tv if board == "main" else self.side_tv
            tv.ids = [entry["card"]["id"] for entry in self.deck.entries(board)]
            tv.selected = [iid for iid in tv.selected if iid in tv.ids]
            if tv.focused not in tv.ids:
                tv.focused = tv.selected[0] if tv.selected else ""

    def _update_card_odds(self):
        self.odds_updates += 1

    def _show_card(self, card):
        self.previews.append(card.get("id"))


class _ResultHarness(SearchResultsMixin, DeckEditorMixin):
    def __init__(self, cards):
        cards = list(cards)
        self._result_store = SearchResultStore.from_rows(cards)
        self.search_repository = _SearchRepository(cards)
        self._result_selected_ids = {card["id"] for card in cards}
        self._result_focus_id = cards[0]["id"] if cards else None
        self.results_tv = _Tree(str(index) for index in range(len(cards)))
        self.deck = Deck()
        self.dirty_count = 0
        self.refreshes = []
        self.statuses = []

    def _mark_deck_dirty(self):
        self.dirty_count += 1

    def _refresh_changed_deck_views(self, *views):
        self.refreshes.append(tuple(views))

    def _status(self, message):
        self.statuses.append(message)


class _CompareBatchHarness(SearchResultsMixin, DeckEditorMixin, ComparisonFeatureMixin):
    def __init__(self, cards):
        cards = list(cards)
        self._result_store = SearchResultStore.from_rows(cards)
        self.search_repository = _SearchRepository(cards)
        self._result_selected_ids = {card["id"] for card in cards}
        self._result_focus_id = cards[0]["id"] if cards else None
        self.results_tv = _Tree(str(index) for index in range(len(cards)))
        self.db = _DB()
        self.comparison = ComparisonCollection()
        session = DeckSession(Deck())
        self.deck_sessions = DeckSessionManager([session])
        self.deck = session.deck
        self.main_tv = _Tree()
        self.side_tv = _Tree()
        self.statuses = []
        self.notices = []
        self._selected_deck = None
        self._result_selection_sync = False
        self._result_selection_sync_after = None
        self._initialize_comparison()

    def _active_session(self):
        return self.deck_sessions.active

    def _comparison_changed(self):
        return None

    def _status(self, message):
        self.statuses.append(message)

    def _show_comparison_notice(self, title, message):
        self.notices.append((title, message))

    def after_idle(self, callback):
        callback()
        return None

    def after_cancel(self, _after_id):
        return None

    def _update_card_odds(self):
        return None



class _GalleryAddHarness(SearchResultsMixin, DeckEditorMixin, ComparisonFeatureMixin):
    """Drive the Results Gallery right-click -> Add-to-deck path off Tk.

    ``results_tv`` deliberately lists a single live row while the store holds
    several cards: the Gallery browses the whole logical result set, so adding a
    card it shows must resolve by id through the store, never through the small
    live Treeview window.
    """

    def __init__(self, cards):
        cards = list(cards)
        self._result_store = SearchResultStore.from_rows(cards)
        self.search_repository = _SearchRepository(cards)
        self._result_selected_ids = {card["id"] for card in cards}
        self._result_focus_id = cards[0]["id"] if cards else None
        self._result_selection_anchor_id = None
        self._result_top = 0
        # Only the first row is "live" in the Treeview; the Gallery still shows
        # every card, so an add must not depend on Treeview membership.
        self.results_tv = _Tree(("0",))
        self.deck = Deck()
        self.dirty_count = 0
        self.refreshes = []
        self.statuses = []
        self.shown = []
        self.popups = []

    def _mark_deck_dirty(self):
        self.dirty_count += 1

    def _refresh_changed_deck_views(self, *views):
        self.refreshes.append(tuple(views))

    def _status(self, message):
        self.statuses.append(message)

    def _show_card(self, card):
        self.shown.append(card)

    def _popup_result_add_menu(self, x_root, y_root):
        self.popups.append((x_root, y_root))

    def _populate_result_window(self, **_kwargs):
        return None


def _card(card_id, name):
    # legalities/image_normal make this a complete Results record so the
    # regression never needs repository access.
    return {
        "id": card_id,
        "name": name,
        "legalities": {},
        "image_normal": f"https://example.invalid/{card_id}.jpg",
    }


def main():
    card_a = _card("a", "Alpha")
    card_b = _card("b", "Beta")
    card_c = _card("c", "Gamma")

    result_harness = _ResultHarness((card_a, card_b))
    result_harness._add_to_deck("main")
    result_bulk_add = (
        result_harness.deck.total("main") == 2
        and result_harness.dirty_count == 1
        and result_harness.results_tv.selection() == ("0", "1")
    )

    # Results Gallery right-click adds the clicked card, resolved by id through
    # the store -- not the current Results selection or the live Treeview window.
    card_d = _card("d", "Delta")
    gallery_harness = _GalleryAddHarness((card_a, card_b, card_c, card_d))
    gallery_pre_multi = len(gallery_harness._selected_result_ids_in_view_order()) == 4
    clicked = gallery_harness._result_gallery_card_at(3)  # card_d, beyond the live row
    gallery_harness._show_gallery_card_context_menu(clicked, 11, 22)
    gallery_harness._add_to_deck("main")
    gallery_add_clicked_card = (
        clicked["id"] == "d"
        # the prior four-card selection is replaced by only the clicked card
        and set(gallery_harness._selected_result_ids_in_view_order()) == {"d"}
        and gallery_harness.deck.total("main") == 1
        and gallery_harness.deck.entries("main")[0]["card"]["id"] == "d"
        and gallery_harness.popups == [(11, 22)]
        and gallery_harness.shown and gallery_harness.shown[-1]["id"] == "d"
    )
    missing_harness = _GalleryAddHarness((card_a, card_b, card_c))
    missing_harness._show_gallery_card_context_menu(
        {"id": "zzz", "name": "Ghost"}, 0, 0)
    gallery_missing_card_ignored = (
        missing_harness.popups == []
        and missing_harness.deck.total("main") == 0
        # a card no longer in the view is refused before the selection is touched
        and len(missing_harness._selected_result_ids_in_view_order()) == 3
    )

    compare_harness = _CompareBatchHarness((card_a, card_b))
    compare_harness._add_selected_results_to_comparison()
    result_bulk_compare = (
        tuple(card["id"] for card in compare_harness.comparison.cards()) == ("a", "b")
        and compare_harness.results_tv.selection() == ("0", "1")
        and not compare_harness.notices
    )

    mixed_harness = _CompareBatchHarness((card_a, card_b, card_c))
    mixed_harness.results_tv.selection_set("0")
    mixed_harness._result_selected_ids = {"a"}
    mixed_harness._result_focus_id = "a"
    mixed_harness.deck.add(card_b, "main", 1)
    mixed_harness.deck.add(card_c, "side", 1)
    mixed_harness.main_tv = _Tree(("b",))
    mixed_harness.side_tv = _Tree(("c",))
    mixed_harness._selected_deck = ("c", "side")
    mixed_selection_count = mixed_harness._selected_comparison_count()
    mixed_harness._add_selected_to_comparison()
    mixed_sources_compare = (
        mixed_selection_count == 3
        and tuple(card["id"] for card in mixed_harness.comparison.cards()) == ("a", "b", "c")
        and mixed_harness._comparison_source_info("b")["board"] == "main"
        and mixed_harness._comparison_source_info("c")["board"] == "side"
        and mixed_harness.results_tv.selection() == ("0",)
        and mixed_harness.main_tv.selection() == ("b",)
        and mixed_harness.side_tv.selection() == ("c",)
    )
    source_clear_harness = _CompareBatchHarness((card_a, card_b))
    source_clear_harness._add_selected_results_to_comparison()
    source_clear_harness._clear_source_highlights()
    source_only_clear_ok = (
        tuple(card["id"] for card in source_clear_harness.comparison.cards()) == ("a", "b")
        and source_clear_harness.results_tv.selection() == ()
        and source_clear_harness._selected_comparison_count() == 0
    )

    mixed_harness._clear_comparison()
    comparison_clear_resets_sources = (
        not mixed_harness.comparison.cards()
        and mixed_harness.results_tv.selection() == ()
        and mixed_harness.main_tv.selection() == ()
        and mixed_harness.side_tv.selection() == ()
        and mixed_harness._selected_comparison_count() == 0
    )

    deck = Deck()
    deck.add(card_a, "main", 2)
    deck.add(card_b, "main", 2)
    deck_harness = _DeckBatchHarness(deck)
    deck_harness.main_tv.selection_set("a", "b")
    deck_harness.main_tv.focus("b")
    deck_harness._selected_deck = ("b", "main")

    deck_harness._deck_qty(1)
    plus_ok = [entry["qty"] for entry in deck.entries("main")] == [3, 3]
    deck_harness._deck_qty(-1)
    minus_ok = [entry["qty"] for entry in deck.entries("main")] == [2, 2]

    floor_deck = Deck()
    floor_deck.add(card_a, "main", 1)
    floor_deck.add(card_b, "main", 1)
    floor_harness = _DeckBatchHarness(floor_deck)
    floor_harness.main_tv.selection_set("a", "b")
    floor_harness._deck_qty(-1)
    minus_floor_ok = [
        entry["qty"] for entry in floor_deck.entries("main")
    ] == [1, 1]

    multi_remove_deck = Deck()
    multi_remove_deck.add(card_a, "main", 2)
    multi_remove_deck.add(card_b, "main", 3)
    multi_remove_deck.add(card_c, "main", 1)
    multi_remove_harness = _DeckBatchHarness(multi_remove_deck)
    multi_remove_harness.main_tv.selection_set("a", "b")
    multi_remove_harness.main_tv.focus("b")
    multi_remove_harness._selected_deck = ("b", "main")
    multi_remove_harness._deck_remove()
    remove_all_selected_ok = (
        [entry["card"]["id"] for entry in multi_remove_deck.entries("main")] == ["c"]
        and multi_remove_harness.main_tv.selection() == ("c",)
        and multi_remove_harness._selected_deck == ("c", "main")
    )

    single_remove_deck = Deck()
    single_remove_deck.add(card_a, "main", 1)
    single_remove_deck.add(card_b, "main", 4)
    single_remove_deck.add(card_c, "main", 1)
    single_remove_harness = _DeckBatchHarness(single_remove_deck)
    single_remove_harness.main_tv.selection_set("b")
    single_remove_harness.main_tv.focus("b")
    single_remove_harness._selected_deck = ("b", "main")
    single_remove_harness._deck_remove()
    single_remove_advances_ok = (
        [entry["card"]["id"] for entry in single_remove_deck.entries("main")] == ["a", "c"]
        and single_remove_harness.main_tv.selection() == ("c",)
        and single_remove_harness._selected_deck == ("c", "main")
        and single_remove_harness.previews[-1:] == ["c"]
    )

    move_deck = Deck()
    move_deck.add(card_a, "main", 2)
    move_deck.add(card_b, "main", 1)
    move_harness = _DeckBatchHarness(move_deck)
    move_harness.main_tv.selection_set("a", "b")
    move_harness.main_tv.focus("a")
    move_harness._selected_deck = ("a", "main")
    move_harness._deck_move()
    move_ok = (
        move_deck.total("main") == 0
        and move_deck.total("side") == 3
        and move_harness.side_tv.selection() == ("a", "b")
        and move_harness._selected_deck == ("a", "side")
    )

    search_source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    controls_source = (ROOT / "mtgdb/ui/comparison_controls.py").read_text(encoding="utf-8")
    deck_source = (ROOT / "mtgdb/ui/deck.py").read_text(encoding="utf-8")
    native_ctrl_selection = (
        '<Control-Button-1>' not in search_source
        and 'selectmode="extended"' in search_source
        and 'selectmode="extended"' in deck_source
        and '_toggle_result_comparison' not in controls_source
        and 'other.selection_remove' not in deck_source
    )
    search_context_names_mainboard = (
        'label="Add to Mainboard"' in controls_source
        and 'label="Add to Deck"' not in controls_source
    )
    global_comparison_bar = (
        'text="Add Selected"' in controls_source
        and 'text="COMPARE | 0 CARDS SELECTED"' in controls_source
        and 'self._build_comparison_bar(parent)' in deck_source
        and '_build_comparison_bar' not in search_source
    )
    comparison_context_actions_removed = (
        'label="Add All Selected to Comparison"' not in controls_source
        and 'label="Add All Selected to Comparison"' not in deck_source
        and 'label="Add to Comparison"' not in controls_source
        and 'label="Add to Comparison"' not in deck_source
        and 'label="Remove from Comparison"' not in controls_source
        and 'label="Remove from Comparison"' not in deck_source
    )
    deck_batch_search_routing = (
        'label="Search for these cards"' in deck_source
        and 'self._search_for_deck_cards(cards)' in deck_source
        and 'self._apply_cards_search_preset(cards)' in deck_source
    )
    # The comparison bar is constructed before Mainboard/Sideboard Treeviews.
    # Selection helpers must therefore be safe during that partial startup state.
    startup_safe_deck_selection = (
        DeckEditorMixin()._deck_selected_ids("main") == ()
        and DeckEditorMixin()._deck_selected_ids("side") == ()
    )
    comparison_bar_def = controls_source.split(
        "    def _build_comparison_bar", 1)[1].split(
        "    def _layout_comparison_actions", 1)[0]
    comparison_bar_defers_selection_query = (
        "self._update_comparison_bar()" not in comparison_bar_def
    )

    checks = {
        "highlighted Search rows bulk-add to Mainboard without losing selection": result_bulk_add,
        "highlighted Search rows bulk-add to comparison without losing selection": result_bulk_compare,
        "Gallery right-click adds the clicked card resolved by id, not the live window": gallery_add_clicked_card,
        "Gallery right-click on a card no longer in view is safely ignored": gallery_missing_card_ignored,
        "deck plus increments every highlighted printing": plus_ok,
        "deck minus decrements every highlighted printing": minus_ok,
        "deck Remove deletes every highlighted entry and advances selection": remove_all_selected_ok,
        "single-row Remove selects and previews the next visible card": single_remove_advances_ok,
        "deck minus never deletes the final copy": minus_floor_ok,
        "deck board move transfers and reselects the complete highlighted batch": move_ok,
        "Ctrl-click remains native extended Treeview selection": native_ctrl_selection,
        "Search context menu names the Mainboard destination explicitly": search_context_names_mainboard,
        "global comparison bar accepts mixed Results and deck selections": mixed_sources_compare,
        "comparison selected count matches the exact mixed-source union": mixed_selection_count == 3,
        "comparison selected label sits beside COMPARE with explicit wording": (
            'text="COMPARE | 0 CARDS SELECTED"' in controls_source
            and 'text=f"COMPARE | {selected_count} CARDS SELECTED{over_limit_note}"'
                in controls_source),
        "an over-limit selection appends the explicit too-many wording": (
            'COMPARISON_OVER_LIMIT_NOTE = " (TOO MANY CARDS SELECTED)"'
                in controls_source
            and 'over_limit_note = COMPARISON_OVER_LIMIT_NOTE if over_limit else ""'
                in controls_source),
        "accepted Results view swaps refresh comparison selected count": (
            'self._restore_pending_result_selection()\n        # Table filters can hide or reveal selected logical Results'
                in (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8")
            and 'self._update_comparison_bar()'
                in (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8").split(
                    'def _apply_prepared_result_view', 1)[1].split(
                    'def _set_result_count', 1)[0]),
        "Clear removes comparison membership and all source highlights": comparison_clear_resets_sources,
        "source-only clear deselects rows without removing comparison cards": source_only_clear_ok,
        "Search Clear is wired to the source-only highlight reset": (
            'clear_highlights = getattr(self, "_clear_source_highlights", None)' in search_source
            and 'clear_highlights()' in search_source),
        "comparison controls live in the deck workspace with Add Selected": global_comparison_bar,
        "Results and deck context menus expose no comparison membership actions": comparison_context_actions_removed,
        "multi-selected deck cards route to one exact-name batch Search": deck_batch_search_routing,
        "deck selection helpers are safe before board Treeviews exist": startup_safe_deck_selection,
        "comparison bar defers selection query until boards exist": comparison_bar_defers_selection_query,
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nMULTISELECT INTERACTIONS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
