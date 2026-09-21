"""Workspace repository, deck-session lifecycle, and UI adapter contracts."""

import ast
import json
from pathlib import Path
import tempfile
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession, DeckSessionManager
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.deck import DeckEditorMixin
from mtgdb.ui.search import SearchFeatureMixin
from mtgdb.workspace.repository import (
    DEFAULT_RECOVERY_KEEP, WorkspaceRepository)


class Clock:
    def __init__(self, value=1_700_000_000):
        self.value = float(value)

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value += seconds


def _card(card_id="printing-a", name="Test Card"):
    return {
        "id": card_id,
        "oracle_id": "oracle-a",
        "name": name,
        "set_code": "tst",
        "collector_number": "7",
        "type_line": "Creature",
        "cmc": 2,
        "mana_cost": "{1}{G}",
    }


def _class_methods(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return cls, {
        node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


class _Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value


class _Entry:
    """The two Entry/StringVar calls the session loader makes."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def delete(self, *_args):
        self._value = ""

    def insert(self, _index, value):
        self._value = value


class _SessionCloseOwner(DeckEditorMixin):
    """The deck-pane state that closing a tab reads back over.

    Column sorts and table filters live only here until a capture stores them
    in the active session, so this owner is enough to prove that closing any
    tab -- not just the active one -- captures before it reloads.
    """

    def __init__(self, sessions, active_index=0):
        self.deck_sessions = DeckSessionManager(sessions, active_index=active_index)
        self.deck = self.deck_sessions.active.deck
        self.deck_name = _Entry(self.deck.name)
        self.deck_format = _Entry(self.deck.fmt or "commander")
        self._selected_deck = None
        self._table_filters = {"main": {}, "side": {}}
        self._deck_sorts = {"main": [None, False], "side": [None, False]}
        self.refreshed = 0

    def _sync_deck_meta(self):
        self.deck.name = self.deck_name.get().strip() or "Untitled Deck"

    def _refresh_deck_format_button(self):
        pass

    def _render_deck_tabs(self):
        pass

    def _refresh_deck_views(self):
        self.refreshed += 1


class _PendingSelectionOwner:
    def __init__(self):
        self._pending_result_restore_id = "printing-b"
        self._cards = [_card("printing-a"), _card("printing-b")]
        self._result_store = SearchResultStore.from_rows(self._cards)
        self.selected_id = None
        self.shown = None

    def _result_select_card_id(self, card_id, ensure_visible=True):
        self.selected_id = card_id
        return self._result_store.view_position_for_id(card_id) is not None

    def _selected_result(self):
        return next(
            (card for card in self._cards if card["id"] == self.selected_id), None)

    def _show_card(self, card):
        self.shown = card


def _sash_restore_survives_late_layout():
    """The restored Mainboard/Sideboard sash must survive a late startup layout.

    Regression: startup resizes the boards pane ~2s in (the comparison bar
    settling), which dragged the sash short after it first looked stable, so the
    saved split reset on every reopen. The restore must keep correcting drift
    until the settle window passes, not exit at the first apparent hold.
    """
    from mtgdb.ui.workspace import WorkspaceMixin

    target = 672

    class _Pane:
        def __init__(self):
            self.pos = 300  # startup default, far from the saved split

        def sashpos(self, _index, value=None):
            if value is None:
                return self.pos
            self.pos = int(value)
            return self.pos

    class _App(WorkspaceMixin):
        def __init__(self, pane):
            self._boards_panes = pane
            self._restored_workspace_geometry = {"boards": [target]}
            self._queue = []

        def after(self, _ms, callback):
            self._queue.append(callback)
            return "after-id"

    pane = _Pane()
    app = _App(pane)
    # A one-time layout event drags the sash after it first looks settled but
    # before the minimum settle window elapses; an early-exiting restore would
    # already have stopped and never correct it.
    late_drift_tick = WorkspaceMixin._SASH_RESTORE_MIN_ATTEMPTS // 2
    app._restore_workspace_geometry()
    guard = WorkspaceMixin._SASH_RESTORE_MAX_ATTEMPTS + 5
    for tick in range(1, guard + 1):
        if tick == late_drift_tick:
            pane.pos = 623  # the pane shrinks and drags the sash short
        if app._queue:
            app._queue.pop(0)()
    return pane.pos == target and not app._queue


def _restore_batch_hydrates_off_the_primary_lock():
    """Session restore must hydrate every deck in one batch reader pass.

    Regression: restore hydrated each card through the primary-lock lookup on a
    background thread, so startup catalog scans (which hold that lock) starved
    it and a restored deck took ~17s to appear. It must use the batch reader
    hydrator instead and never fall back to the per-card lookup on success.
    """
    from mtgdb.workspace.repository import WorkspaceLoadWorker, WorkspaceRepository

    deck_one = Deck("One", "modern")
    deck_one.add({"id": "x1", "name": "A"}, "main", 2)
    deck_one.add({"id": "x2", "name": "B"}, "side", 1)
    deck_two = Deck("Two", "modern")
    deck_two.add({"id": "x3", "name": "C"}, "main", 4)
    manager = DeckSessionManager(
        [DeckSession(deck=deck_one), DeckSession(deck=deck_two)], active_index=0)

    with tempfile.TemporaryDirectory() as temporary:
        repository = WorkspaceRepository(temporary)
        repository.save(repository.build_payload(
            manager, 0, search={}, geometry={}))

        per_card_calls = []
        bulk_calls = []

        def per_card(card_id):
            per_card_calls.append(card_id)
            return None

        def bulk(card_ids):
            ids = list(card_ids)
            bulk_calls.append(ids)
            return {cid: {"id": cid, "name": "hydrated-" + cid} for cid in ids}

        worker = WorkspaceLoadWorker(repository, per_card, bulk_lookup=bulk)
        worker._generation = 1
        worker._running = True
        worker._run(1)
        _done, result, error = worker.poll(1)

    if error is not None or result is None:
        return False
    hydrated_names = {
        entry["card"].get("name")
        for session in result.sessions
        for entry in WorkspaceRepository.deck_to_data(session.deck)["entries"]
    }
    return (
        len(bulk_calls) == 1
        and set(bulk_calls[0]) == {"x1", "x2", "x3"}
        and not per_card_calls
        and hydrated_names == {"hydrated-x1", "hydrated-x2", "hydrated-x3"})


def main():
    first = Deck("First", "modern")
    first.add(_card(), "main", 3)
    second = Deck("Second", "commander")
    second.add(_card("printing-b", "Other Card"), "side", 1)

    session_a = DeckSession(
        first, path="first.txt", dirty=True,
        selected=["printing-a", "main"],
        filters={"main": {"name": "Test"}, "side": {}},
        sorts={"main": ["name", True], "side": [None, False]},
    )
    session_b = DeckSession(second)
    manager = DeckSessionManager([session_a, session_b], active_index=1)
    manager.activate(0)
    activated_first = manager.active is session_a
    appended_index = manager.append(DeckSession(Deck("Third", "vintage")))
    removed = manager.remove(1)
    manager.remove(1)
    manager.remove(0)
    default_after_last_close = (
        len(manager) == 1 and manager.active_index == 0
        and manager.active.deck.name == "Untitled Deck")


    # WSP-008: closing a deck tab reloads the surviving session over the live
    # widgets, so the live column sort and table filters must be captured
    # first. Closing a tab that is not the active one used to skip that
    # capture and silently revert the sort and filters on the deck the user
    # was still working in.
    close_owner = _SessionCloseOwner(
        [DeckSession(Deck("Kept", "modern")),
         DeckSession(Deck("Other", "modern")),
         DeckSession(Deck("Third", "modern"))])
    close_owner._deck_sorts["main"] = ["name", True]
    close_owner._table_filters["main"]["name"] = {"kind": "text", "text": "goblin"}
    close_owner._selected_deck = ("printing-a", "main")
    close_owner._close_deck_session(2)
    live_state_survives_other_close = (
        close_owner._deck_sorts["main"] == ["name", True]
        and close_owner._table_filters["main"] == {
            "name": {"kind": "text", "text": "goblin"}}
        and close_owner._selected_deck == ("printing-a", "main")
        and len(close_owner.deck_sessions) == 2
        and close_owner.deck_sessions.active.deck.name == "Kept")

    # The same view state must still be per-deck: switching to another tab
    # shows that deck's own sorts and filters, not the previous one's.
    close_owner._switch_deck_session(1)
    switched_away = (
        close_owner._deck_sorts["main"] == [None, False]
        and close_owner._table_filters["main"] == {})
    close_owner._switch_deck_session(0)
    switched_back = (
        close_owner._deck_sorts["main"] == ["name", True]
        and close_owner._table_filters["main"] == {
            "name": {"kind": "text", "text": "goblin"}})
    per_deck_view_state = switched_away and switched_back

    pending_owner = _PendingSelectionOwner()
    SearchFeatureMixin._restore_pending_result_selection(pending_owner)

    malformed = DeckSession(
        Deck(), path=42, selected=["printing-a", "invalid"],
        filters={"main": [], "side": "bad"},
        sorts={"main": [], "side": ["qty"]},
    )

    with tempfile.TemporaryDirectory() as temporary:
        clock = Clock()
        repository = WorkspaceRepository(
            temporary, recovery_interval=30, recovery_keep=8, clock=clock)
        persisted_manager = DeckSessionManager(
            [session_a, session_b], active_index=1)
        payload = repository.build_payload(
            persisted_manager, persisted_manager.active_index,
            search={"name": "bolt", "had_results": True},
            geometry={"boards": [520]},
        )
        first_save = repository.save(payload)
        clock.advance(5)
        same_payload = dict(payload, saved_at=clock())
        unchanged_save = repository.save(same_payload)

        serialized = repository.session_to_data(session_a)
        restored = repository.session_from_data(serialized)
        fresh = dict(_card(), artist="Fresh Artist")
        restored_fresh = repository.deck_from_data(
            serialized["deck"], lambda card_id: fresh
            if card_id == "printing-a" else None)

        for index in range(10):
            clock.advance(1)
            snapshot_payload = dict(
                payload, saved_at=clock(), search={"snapshot": index})
            repository.save(snapshot_payload, force=True, recovery=True)
        recovery_files = sorted(repository.recovery_dir.glob("session_*.json"))
        temporary_files = list(Path(temporary).rglob("*.tmp"))

        # WSP-011 bounds recovery history. The repository above is constructed
        # with an explicit recovery_keep, which proves the mechanism but leaves
        # the shipped default unverified -- raising DEFAULT_RECOVERY_KEEP to an
        # unbounded value passed every gate. Exercise a default-constructed
        # repository so the number the application actually uses is the one
        # under test.
        default_clock = Clock()
        default_repository = WorkspaceRepository(
            str(Path(temporary) / "defaults"), clock=default_clock)
        for index in range(DEFAULT_RECOVERY_KEEP + 12):
            default_clock.advance(1)
            default_repository.save(
                dict(payload, saved_at=default_clock(),
                     search={"snapshot": index}),
                force=True, recovery=True)
        default_recovery_files = sorted(
            default_repository.recovery_dir.glob("session_*.json"))

        repository.session_path.write_text("{broken", encoding="utf-8")
        recovered_payload = repository.load()
        repository.session_path.write_text(
            json.dumps({"version": 1, "decks": "invalid"}),
            encoding="utf-8")
        invalid_candidate = repository.read_candidate(repository.session_path)

    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/ui/deck.py", "mtgdb/ui/search.py",
            "mtgdb/ui/results.py", "mtgdb/ui/workspace.py", "mtgdb/deck/sessions.py",
            "mtgdb/workspace/repository.py",
        )
    }
    gui_class, gui_methods = _class_methods(sources["mtgdb/ui/app.py"], "DeckBuilderApp")
    _, workspace_methods = _class_methods(
        sources["mtgdb/ui/workspace.py"], "WorkspaceMixin")
    gui_bases = {
        base.id for base in gui_class.bases if isinstance(base, ast.Name)}

    workspace_owned = {
        "_initialize_workspace", "_workspace_geometry_state",
        "_workspace_snapshot", "_save_workspace_session",
        "_schedule_workspace_autosave", "_restore_workspace_session_async",
        "_apply_workspace_restore", "_restore_workspace_geometry",
    }
    combined_core = sources["mtgdb/deck/sessions.py"] + sources["mtgdb/workspace/repository.py"]
    production_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in ROOT.glob("*.py")
    }
    checks = {
        "closing another deck keeps the active deck's sort and filters":
            live_state_survives_other_close,
        "each deck still carries its own sorts and filters": per_deck_view_state,
        "session manager owns active-index lifecycle": (
            activated_first and appended_index == 2
            and removed is session_b and default_after_last_close),
        "session state sanitizes malformed persisted views": (
            malformed.path is None and malformed.selected is None
            and malformed.filters == {"main": {}, "side": {}}
            and malformed.sorts == {
                "main": [None, False], "side": [None, False]}),
        "workspace payload preserves deck sessions and exact printings": (
            payload["version"] == 1 and payload["active_deck"] == 1
            and len(payload["decks"]) == 2
            and payload["decks"][0]["deck"]["entries"][0]["card"]["id"]
            == "printing-a"
            and payload["decks"][0]["dirty"] is True
            and payload["decks"][0]["selected"] == ["printing-a", "main"]),
        "session round trip preserves independent view state": (
            restored.deck.name == "First" and restored.deck.total("main") == 3
            and restored.path == "first.txt" and restored.dirty
            and restored.selected == ("printing-a", "main")
            and restored.filters["main"] == {"name": "Test"}
            and restored.sorts["main"] == ["name", True]),
        "workspace restore prefers fresh exact-printing metadata": (
            restored_fresh.entries("main")[0]["card"]["artist"]
            == "Fresh Artist"),
        "unchanged autosave avoids rewriting the session": (
            first_save.wrote_session and first_save.wrote_recovery
            and not unchanged_save.wrote_session
            and not unchanged_save.wrote_recovery),
        "atomic writes leave no temporary files": not temporary_files,
        "recovery history remains bounded": len(recovery_files) == 8,
        "the shipped recovery-history default is bounded and small": (
            DEFAULT_RECOVERY_KEEP == 8
            and len(default_recovery_files) == DEFAULT_RECOVERY_KEEP),
        "corrupt primary falls back to newest valid recovery": (
            recovered_payload is not None
            and recovered_payload["search"] == {"snapshot": 9}),
        "invalid workspace payload is rejected": invalid_candidate is None,
        "workspace core is Tk, SQLite, and UI free": all(
            marker not in combined_core for marker in (
                "tkinter", "sqlite3", "from ui_", "import ui_")),
        "workspace adapter owns only retained board-sash geometry and autosave coordination": (
            workspace_owned <= workspace_methods
            and '(("boards", getattr(self, "_boards_panes", None)),)'
                in sources["mtgdb/ui/workspace.py"]
            and '"_main_panes"' not in sources["mtgdb/ui/workspace.py"]
            and '"_middle_panes"' not in sources["mtgdb/ui/workspace.py"]),
        "restored board sash survives a late startup layout": (
            _sash_restore_survives_late_layout()),
        "restore batch-hydrates decks off the primary lock": (
            _restore_batch_hydrates_off_the_primary_lock()
            and "bulk_lookup=self.db.hydrate_cards"
                in sources["mtgdb/ui/workspace.py"]),
        "search feature owns its workspace capture and restore contract": (
            "def _capture_search_workspace_state(" in sources["mtgdb/ui/search.py"]
            and "def _restore_search_workspace_state(" in sources["mtgdb/ui/search.py"]
            and "q_name" not in sources["mtgdb/ui/workspace.py"]
            and "card_type_vars" not in sources["mtgdb/ui/workspace.py"]),
        "workspace result selection waits for the logical Results view": (
            pending_owner._pending_result_restore_id is None
            and pending_owner.selected_id == "printing-b"
            and pending_owner.shown["id"] == "printing-b"
            and "self._restore_pending_result_selection()"
            in sources["mtgdb/ui/results.py"]
            and "selected_result_id" not in sources["mtgdb/ui/workspace.py"]),
        "DeckBuilderApp delegates workspace UI ownership": (
            "WorkspaceMixin" in gui_bases
            and not workspace_owned & gui_methods),
        "workspace adapter performs no direct file persistence": all(
            marker not in sources["mtgdb/ui/workspace.py"] for marker in (
                "open(", "json.dump", "json.load", "os.replace",
                "os.remove", ".unlink(",
            )),
        "all production modules use typed deck sessions": all(
            marker not in source
            for source in production_sources.values()
            for marker in ("_deck_sessions", "_active_deck_session")),
        "application close retains forced recovery save": (
            "def _on_app_close(" in sources["mtgdb/ui/app.py"]
            and "self._save_workspace_session(force=True, recovery=True)"
            in sources["mtgdb/ui/app.py"]),
        "autosave starts only after asynchronous workspace restore finishes": (
            "self._workspace_loaded = True" not in sources["mtgdb/ui/app.py"]
            and "self._schedule_workspace_autosave()" not in sources["mtgdb/ui/app.py"]
            and "def _finish_workspace_initialization(" in sources["mtgdb/ui/workspace.py"]
            and "self._workspace_loaded = True" in sources["mtgdb/ui/workspace.py"]
            and "self._schedule_workspace_autosave()" in sources["mtgdb/ui/workspace.py"]),
        "workspace shutdown joins the background restore loader": (
            "workspace_load_worker.shutdown(" in sources["mtgdb/ui/workspace.py"]
            and '"_workspace_load_after"' in sources["mtgdb/ui/app.py"]
            and "def shutdown(self, timeout=None):" in sources["mtgdb/workspace/repository.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nWORKSPACE ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
