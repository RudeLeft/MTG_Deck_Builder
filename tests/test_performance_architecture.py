"""Release-blocking contracts for the final UI performance architecture."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import threading
import time
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

from mtgdb.images.service import CardImageService
from mtgdb.search.catalogs import SearchCatalogController
from mtgdb.search.results import (
    CompactResultSelection,
    ResultPreparationWorker,
    SearchResultStore,
    prepare_vocabulary,
    table_value,
)
from mtgdb.ui.results import RESULT_LIVE_ROW_LIMIT, SearchResultsMixin
from mtgdb.workspace.repository import WorkspaceLoadWorker, WorkspaceSaveWorker


def _rows(count=100_000):
    return [
        {
            "id": f"p-{index}",
            "name": f"Card {count - index:06d}",
            "mana_cost": "{1}{U}",
            "cmc": index % 8,
            "type_line": "Creature — Test",
            "oracle_text": "Flying" if index % 2 else "Vigilance",
            "colors": ["U"],
            "power": str(index % 6),
            "toughness": str((index + 1) % 6),
            "rarity": "rare" if index % 2 else "common",
            "set_code": "tst",
            "set_name": "Test Set",
            "collector_number": str(index + 1),
            "released_at": "2026-01-01",
            "keywords": ["Flying"] if index % 2 else ["Vigilance"],
        }
        for index in range(count)
    ]


class _FakeTree:
    def __init__(self):
        self.rows = {}
        self.order = []
        self.insert_calls = 0

    def insert(self, _parent, _where, iid, text="", values=()):
        self.insert_calls += 1
        self.rows[iid] = {"text": text, "values": values}
        self.order.append(iid)

    def item(self, iid, **kwargs):
        self.rows[iid].update(kwargs)

    def move(self, iid, _parent, offset):
        self.order.remove(iid)
        self.order.insert(offset, iid)

    def exists(self, iid):
        return iid in self.rows

    def delete(self, iid):
        self.rows.pop(iid, None)
        if iid in self.order:
            self.order.remove(iid)

    def yview_moveto(self, _fraction):
        return None


class _ViewportHarness(SearchResultsMixin):
    def __init__(self, store):
        self._result_store = store
        self.results_tv = _FakeTree()
        self._result_top = 0
        self._result_window_start = -1
        self._result_slot_sources = {}
        self._result_live_slots = []
        self._result_selected_ids = set()
        self._result_focus_id = None
        self._result_diagnostics = {"live_tk_rows": 0, "logical_rows": store.logical_count}
        self._results_vsb = None

    def _result_visible_capacity(self):
        return 20

    def _cost_image(self, _cost):
        return None

    def _table_value(self, card, key, qty=None):
        return table_value(card, key, qty=qty)

    def _begin_result_selection_sync(self):
        return None

    def _end_result_selection_sync_later(self):
        return None

    def _apply_result_native_selection(self):
        return None

    def _update_result_scrollbar(self):
        return None


class _HydrationRepository:
    def __init__(self, store):
        self.cards = {
            str(row.get("id")): {"id": row.get("id"), "legalities": {"modern": "legal"}}
            for row in store.rows
        }

    def card_by_id(self, card_id):
        return self.cards.get(str(card_id))


class _ScanCountingStore:
    """Delegate to a real store while counting full view-index scans.

    The sparse selection path must never walk the 100k view index. Counting the
    scans proves that structurally, where a wall-clock budget only inferred it
    and could fail spuriously on a loaded or throttled machine.
    """

    class _CountingIndex:
        def __init__(self, index, owner):
            self._index = index
            self._owner = owner

        def __iter__(self):
            self._owner.view_index_scans += 1
            return iter(self._index)

        def __len__(self):
            return len(self._index)

        def __getitem__(self, item):
            return self._index[item]

    def __init__(self, store):
        self._store = store
        self.view_index_scans = 0

    def __getattr__(self, name):
        return getattr(self._store, name)

    @property
    def view_index(self):
        index = self._store.view_index
        return None if index is None else self._CountingIndex(index, self)


class _TaxonomyRepository:
    def __init__(self):
        self.version = "old"
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_once = True

    def card_types(self, _content, _paper):
        version = self.version
        if self.block_once:
            self.block_once = False
            self.started.set()
            self.release.wait(2.0)
        return [(f"{version}-type", "Creature")]

    def card_type_taxonomy_status(self):
        return (True, "")

    def supertypes(self, _content, _paper):
        return [(f"{self.version}-super", "Legendary")]

    def supertype_taxonomy_status(self):
        return (True, "")

    def formats(self, _content, _paper):
        return [f"{self.version}-format"]

    def rarities(self, _content, _paper):
        return [f"{self.version}-rarity"]

    def keyword_catalog(self, _content, _paper):
        return [(f"{self.version}-keyword", "keyword")]

    def subtype_catalog(self, _content, _paper):
        return [(f"{self.version}-subtype", "Creature")]

    def set_types(self, _content, _paper):
        return [(f"{self.version}-set-type", 1)]

    def sets(self, _allowed=None, *, content_types=None, paper_only=False):
        return [(f"{self.version}-set", f"{self.version} Set")]


class _BlockingWorkspaceRepository:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.records = []
        self.thread_ids = []

    def save(self, payload, force=False, recovery=False):
        self.thread_ids.append(threading.get_ident())
        self.records.append((payload["generation"], bool(force), bool(recovery)))
        if len(self.records) == 1:
            self.started.set()
            self.release.wait(2.0)


class _BlockingWorkspaceLoadRepository:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def load(self):
        self.started.set()
        self.release.wait(2.0)
        return None


def _wait_result(worker, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = worker.poll_latest()
        if event is not None:
            return event
        time.sleep(0.005)
    return None


def _wait_catalog(controller, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = controller.poll_latest()
        if event is not None:
            return event
        time.sleep(0.005)
    return None


def _jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (100, 100), "#563d2d").save(buffer, format="JPEG")
    return buffer.getvalue()


def main():
    raw = _rows()
    store = SearchResultStore.from_rows(raw)
    compact_row = store.row_at_source(0)
    del raw

    viewport = _ViewportHarness(store)
    viewport._populate_result_window(force=True)
    initial_slots = tuple(viewport._result_live_slots)
    initial_insert_calls = viewport.results_tv.insert_calls
    viewport._set_result_top(99_980)
    deep_slots = tuple(viewport._result_live_slots)

    viewport.search_repository = _HydrationRepository(store)
    viewport._result_selected_ids = {"p-0", "p-99999"}
    selected = viewport._selected_results()

    store.swap_view_index(range(store.logical_count))
    counting_store = _ScanCountingStore(store)
    compact_selection = CompactResultSelection(counting_store)
    compact_selection.add("p-99999")
    sparse_visible = compact_selection.visible_count()
    sparse_ids = compact_selection.ids_in_view_order(limit=7)
    sparse_view_scans = counting_store.view_index_scans
    sparse_selection_info = compact_selection.diagnostics()
    compact_selection.clear()
    store.reset_view()
    compact_selection.select_view_range(0, store.logical_count - 1)
    dense_selection_info = compact_selection.diagnostics()
    compact_selection.clear()

    view_worker = ResultPreparationWorker("performance-result-test")
    first_generation = view_worker.submit_view(store, {}, sort_col="name", sort_desc=False)
    second_generation = view_worker.submit_view(
        store, {"rarity": {"kind": "values", "values": {"Rare"}}},
        sort_col="collector", sort_desc=True)
    prepared = _wait_result(view_worker)
    third_generation = view_worker.submit_vocabulary(store, "rarity", {})
    vocabulary_event = _wait_result(view_worker)
    view_worker.shutdown()

    vocabulary_direct = prepare_vocabulary(store, "rarity", {})
    store_info = store.diagnostics()

    taxonomy_repo = _TaxonomyRepository()
    taxonomy = SearchCatalogController(taxonomy_repo)
    request_started = time.perf_counter()
    first_catalog = taxonomy.request({"card"}, True, ())
    request_elapsed = time.perf_counter() - request_started
    taxonomy_repo.started.wait(1.0)
    taxonomy.invalidate()
    taxonomy_repo.version = "new"
    newest_catalog = taxonomy.request({"card"}, True, ())
    taxonomy_repo.release.set()
    catalog_event = _wait_catalog(taxonomy)
    cached_catalog = taxonomy.request({"card"}, True, ())
    taxonomy_info = taxonomy.cache_info()
    taxonomy.shutdown()

    blocking_repo = _BlockingWorkspaceRepository()
    workspace_worker = WorkspaceSaveWorker(blocking_repo)
    main_thread = threading.get_ident()
    workspace_worker.submit({"generation": 1})
    blocking_repo.started.wait(1.0)
    workspace_worker.submit({"generation": 2})
    workspace_worker.submit({"generation": 3}, force=True, recovery=True)
    blocking_repo.release.set()
    workspace_flushed = workspace_worker.flush(timeout=3.0)
    workspace_info = workspace_worker.diagnostics()
    workspace_stopped = workspace_worker.shutdown(flush=True, timeout=1.0)

    blocking_load_repo = _BlockingWorkspaceLoadRepository()
    load_worker = WorkspaceLoadWorker(blocking_load_repo, lambda _card_id: None)
    load_worker.start()
    blocking_load_repo.started.wait(1.0)
    threading.Timer(0.02, blocking_load_repo.release.set).start()
    load_worker_stopped = load_worker.shutdown(timeout=1.0)
    load_worker_info = load_worker.diagnostics()

    image_bytes = _jpeg_bytes()
    with tempfile.TemporaryDirectory() as temporary:
        oversized_service = CardImageService(
            Path(temporary) / "oversized", max_workers=1,
            processed_budget=5_000, source_budget=1_000,
            processed_count_limit=256, source_count_limit=32,
            fetch_bytes=lambda _url: image_bytes)
        oversized_image = oversized_service.request(
            {"id": "oversized", "name": "Oversized"},
            "https://example.invalid/oversized.jpg",
            target_size=(50, 50)).result(timeout=2.0)
        oversized_info = oversized_service.cache_info()
        oversized_service.shutdown()

        bounded_service = CardImageService(
            Path(temporary) / "bounded", max_workers=1,
            processed_budget=10_000, source_budget=100_000,
            processed_count_limit=256, source_count_limit=32,
            fetch_bytes=lambda _url: image_bytes)
        for card_id in ("one", "two"):
            bounded_service.request(
                {"id": card_id, "name": card_id},
                f"https://example.invalid/{card_id}.jpg",
                target_size=(40, 40)).result(timeout=2.0)
        bounded_info = bounded_service.cache_info()
        bounded_service.shutdown()

    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/ui/results.py", "mtgdb/ui/search.py",
            "mtgdb/ui/table_filters.py", "mtgdb/ui/database_sync.py",
            "mtgdb/ui/workspace.py", "mtgdb/ui/mana.py",
            "mtgdb/ui/window.py", "mtgdb/ui/search_printings.py",
            "mtgdb/search/results.py", "mtgdb/search/catalogs.py",
            "mtgdb/images/service.py", "mtgdb/workspace/repository.py",
        )
    }

    checks = {
        "100k logical results use compact immutable rows": (
            store.logical_count == 100_000
            and not hasattr(compact_row, "__dict__")
            and isinstance(store.rows, tuple)),
        "100k logical results keep a bounded reusable Tk viewport": (
            75 <= len(initial_slots) <= RESULT_LIVE_ROW_LIMIT == 128
            and initial_slots == deep_slots
            and initial_insert_calls == viewport.results_tv.insert_calls
            and len(viewport.results_tv.rows) <= RESULT_LIVE_ROW_LIMIT),
        "offscreen exact-ID selection acts on the complete logical view": (
            [card["id"] for card in selected] == ["p-0", "p-99999"]),
        "100k Results selection is compact and sparse interactions are O(k)": (
            sparse_visible == 1
            and sparse_ids == ("p-99999",)
            and sparse_selection_info["sparse_selected"] == 1
            # O(k), proven structurally: one selected row out of 100k must be
            # resolved without a single walk of the full view index.
            and sparse_view_scans == 0
            and dense_selection_info["selected"] == 100_000
            and dense_selection_info["bytes"] <= 12_500
            and dense_selection_info["sparse_selected"] is None),
        "Results preparation is latest-generation and complete-set": (
            first_generation < second_generation
            and prepared is not None
            and prepared.generation == second_generation
            and len(prepared.payload) == 50_000),
        "column vocabulary worker covers and caches the full logical set": (
            vocabulary_event is not None
            and vocabulary_event.generation == third_generation
            and tuple(vocabulary_event.payload) == ("Common", "Rare")
            and vocabulary_direct == ("Common", "Rare")
            and store_info["vocabulary_cache_entries"] >= 1),
        "taxonomy request returns without waiting for repository scans": (
            first_catalog.kind == "started"
            # The fake repository blocks its first card_types() scan on an
            # unset event for up to 2.0s, so a synchronous request would take
            # about two seconds. Half of that keeps enormous headroom over the
            # real sub-millisecond return while still failing loudly if the
            # call ever starts waiting on the worker.
            and request_elapsed < 1.0),
        "taxonomy invalidation rejects stale database generations": (
            newest_catalog.kind == "started"
            and catalog_event is not None
            and catalog_event.payload.card_types[0][0] == "new-type"
            and cached_catalog.kind == "cached"),
        "taxonomy LRUs are bounded": (
            taxonomy_info["base_cache_entries"] <= SearchCatalogController.BASE_CACHE_LIMIT
            and taxonomy_info["set_cache_entries"] <= SearchCatalogController.SET_CACHE_LIMIT),
        "workspace durability is latest-wins and off the caller thread": (
            workspace_flushed and workspace_stopped
            and [record[0] for record in blocking_repo.records] == [1, 3]
            and all(thread_id != main_thread for thread_id in blocking_repo.thread_ids)
            and workspace_info["superseded"] >= 1),
        "workspace loader is joined before database teardown": (
            load_worker_stopped and not load_worker_info["thread_alive"]),
        "oversized decoded and processed images display but are not retained": (
            oversized_image.size == (50, 50)
            and oversized_info["processed_entries"] == 0
            and oversized_info["source_entries"] == 0
            and oversized_info["processed_oversized"] >= 1
            and oversized_info["source_oversized"] >= 1),
        "weighted image LRUs enforce byte budgets": (
            bounded_info["processed_bytes"] <= 10_000
            and bounded_info["source_bytes"] <= 100_000
            and bounded_info["processed_entries"] <= 256
            and bounded_info["source_entries"] <= 32
            and bounded_info["processed_evictions"] >= 1),
        "image memory accounting is decoded-dimension based": (
            "int(image.width) * int(image.height) * 4" in sources["mtgdb/images/service.py"]
            and "PROCESSED_CACHE_BUDGET = 96 * 1024 * 1024" in sources["mtgdb/images/service.py"]
            and "SOURCE_CACHE_BUDGET = 32 * 1024 * 1024" in sources["mtgdb/images/service.py"]),
        "image processing stays outside cache-lock critical sections": (
            "with Image.open(path) as source:" in sources["mtgdb/images/service.py"]
            and "image.resize(" in sources["mtgdb/images/service.py"]
            and "_cache_image_locked(" in sources["mtgdb/images/service.py"]),
        "mana and Search taxonomy caches are bounded": (
            "COST_CACHE_LIMIT = 512" in sources["mtgdb/ui/mana.py"]
            and "popitem(last=False)" in sources["mtgdb/ui/mana.py"]
            and "BASE_CACHE_LIMIT = 8" in sources["mtgdb/search/catalogs.py"]
            and "SET_CACHE_LIMIT = 20" in sources["mtgdb/search/catalogs.py"]),
        "startup and database replacement use asynchronous taxonomy path": (
            "self.after_idle(self._start_post_paint_initialization)" in sources["mtgdb/ui/app.py"]
            and "self._refresh_search_catalogs()" in sources["mtgdb/ui/app.py"]
            and "self.search_catalog_controller.invalidate()" in sources["mtgdb/ui/database_sync.py"]
            and "self._refresh_search_catalogs()" in sources["mtgdb/ui/database_sync.py"]),
        "failed asynchronous taxonomy restores stable interactive controls": (
            "printing_filter.cancel_loading()" in sources["mtgdb/ui/search.py"]
            and "self._pending_catalog_filter_state = None" in sources["mtgdb/ui/search.py"]
            and "def cancel_loading(" in sources["mtgdb/ui/search_printings.py"]
            and "self._set_catalog_controls_enabled(True)"
            in sources["mtgdb/ui/search_printings.py"]),
        "sash safety clamps are coalesced instead of queued per motion event": (
            "def _schedule_pane_clamp(" in sources["mtgdb/ui/window.py"]
            and "self._schedule_pane_clamp()" in sources["mtgdb/ui/window.py"]
            and "self.after_idle(clamp)" not in sources["mtgdb/ui/window.py"]),
        "workspace UI submits snapshots instead of performing durability IO": (
            "workspace_save_worker.submit(" in sources["mtgdb/ui/workspace.py"]
            and "workspace_repository.save(" not in sources["mtgdb/ui/workspace.py"]
            and "json.dump(" in sources["mtgdb/workspace/repository.py"]
            and "os.fsync(" in sources["mtgdb/workspace/repository.py"]),
        "Results filtering sorting and vocab scans have Tk-free worker owners": (
            "ResultPreparationWorker" in sources["mtgdb/search/results.py"]
            and "prepare_view_index" in sources["mtgdb/search/results.py"]
            and "prepare_vocabulary" in sources["mtgdb/search/results.py"]
            and "_build_async_result_values_filter" in sources["mtgdb/ui/table_filters.py"]),
        "internal performance diagnostics expose logical rows caches and workers": (
            # These aggregate per-instance state that no lower object holds.
            "def result_performance_info(" in sources["mtgdb/ui/results.py"]
            and "def workspace_performance_info(" in sources["mtgdb/ui/workspace.py"]
            # The owners report their own bounded caches. Asserting a UI method
            # that only forwarded to one of them kept a method alive that
            # nothing called and proved nothing about the cache behind it.
            and "def cache_info(" in sources["mtgdb/images/service.py"]
            and "def cache_info(" in sources["mtgdb/search/catalogs.py"]
            and "search_catalog_performance_info" not in sources["mtgdb/ui/search.py"]),
        "no one-Treeview-row-per-logical-result reconciler remains": (
            "_ResultTableReconciler" not in sources["mtgdb/ui/results.py"]
            and "RESULT_LIVE_ROW_LIMIT = 128" in sources["mtgdb/ui/results.py"]
            and "self._result_store.rows" in sources["mtgdb/ui/table_filters.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nPERFORMANCE ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
