"""Database synchronization service, controller, and UI ownership contracts."""

import ast
import json
from pathlib import Path
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.sync import (
    DatabaseSyncCancelled, DatabaseSyncController, DatabaseSyncService,
    RulesTaxonomyError, WIZARDS_RULES_PAGE_URL,
    _discover_comprehensive_rules_txt, _extract_comprehensive_rules_supertypes,
    _official_rules_txt_url,
)
from mtgdb.database.db import CardDB
from mtgdb.database.schema import (
    CARD_TYPES_ERROR_META_KEY, RULES_SUPERTYPES_ERROR_META_KEY,
    RULES_SUPERTYPES_META_KEY,
)
import mtgdb.core.net as net


RULES_TXT_URL = "https://media.wizards.com/example/MagicCompRules-current.txt"
RULES_TXT_RAW_SPACE_URL = (
    "https://media.wizards.com/2026/downloads/MagicCompRules 20260819.txt")
RULES_TXT_ESCAPED_SPACE_URL = (
    "https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt")
RULES_DOCUMENT = (
    "Magic: The Gathering Comprehensive Rules\n"
    "These rules are effective as of August 7, 2026.\n"
    "\n205.4. Supertypes\n"
    "205.4a An object can have one or more supertypes. A card's supertypes are "
    "printed directly before its card types. The supertypes are basic, legendary, "
    "ongoing, snow, and world.\n"
    "205.4b The next rule begins here.\n"
    + ("Appendix filler for realistic document size.\n" * 400)
)


class FakeHttp:
    def __init__(self, cards, updated_at="2026-08-30T00:00:00Z"):
        self.cards = cards
        self.updated_at = updated_at
        self.downloads = 0

    def get_json(self, url):
        if "/bulk-data/" in url:
            return {
                "type": "default_cards",
                "name": "Default Cards",
                "updated_at": self.updated_at,
                "jsonl_download_uri": "https://data.example/default.json",
            }
        return {"data": ["Future Value", "Known Value"]}

    def fetch_bytes(self, url):
        if url == WIZARDS_RULES_PAGE_URL:
            return (f'<html><a href="{RULES_TXT_URL}">TXT</a></html>'
                    .encode("utf-8"))
        if url == RULES_TXT_URL:
            return RULES_DOCUMENT.encode("utf-8")
        raise AssertionError(f"unexpected fetch_bytes URL: {url}")

    def download(self, _url, destination, progress_cb=None):
        self.downloads += 1
        encoded = "\n".join(json.dumps(card) for card in self.cards).encode("utf-8")
        if progress_cb:
            progress_cb(0, len(encoded))
        Path(destination).write_bytes(encoded)
        if progress_cb:
            progress_cb(len(encoded), len(encoded))
        return destination


class BrokenCardTypesHttp(FakeHttp):
    def get_json(self, url):
        if url.endswith("/catalog/card-types"):
            raise OSError("simulated card-types catalog outage")
        return super().get_json(url)


class BrokenRulesHttp(FakeHttp):
    def fetch_bytes(self, url):
        if url == WIZARDS_RULES_PAGE_URL:
            return (f'<html><a href="{RULES_TXT_URL}">TXT</a></html>'
                    .encode("utf-8"))
        if url == RULES_TXT_URL:
            broken = (
                "Magic: The Gathering Comprehensive Rules\n"
                "205.4. Supertypes\n"
                "205.4a Supertypes may exist, but this wording no longer contains "
                "a complete recognized enumeration.\n"
                "205.4b Next rule.\n"
                + ("padding\n" * 2000)
            )
            return broken.encode("utf-8")
        return super().fetch_bytes(url)


class CancellingHttp(FakeHttp):
    def __init__(self, cards, cancel_event):
        super().__init__(cards, updated_at="2026-08-31T00:00:00Z")
        self.cancel_event = cancel_event

    def download(self, _url, destination, progress_cb=None):
        self.downloads += 1
        Path(destination).write_bytes(b"partial")
        self.cancel_event.set()
        if progress_cb:
            progress_cb(7, 100)


class BlockingService:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def due_reason(self):
        return "scheduled"

    def sync(self, progress_cb=None, cancel_event=None):
        if progress_cb:
            progress_cb("meta", "checking")
            progress_cb("download", (10, 100))
        self.entered.set()
        while not self.release.wait(0.005):
            if cancel_event is not None and cancel_event.is_set():
                raise DatabaseSyncCancelled("cancelled")
        return 42


def _methods(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


def _wait_for_terminal(controller, timeout=2.0):
    deadline = time.monotonic() + timeout
    progress = None
    timings = []
    while time.monotonic() < deadline:
        poll = controller.poll()
        progress = poll.progress or progress
        timings.extend(poll.timings)
        if poll.terminal is not None:
            return progress, tuple(timings), poll.terminal
        time.sleep(0.005)
    return progress, tuple(timings), None


def main():
    discovered_txt = _discover_comprehensive_rules_txt(
        f'<a href="{RULES_TXT_URL}"><span>TXT</span></a>'.encode("utf-8"))
    embedded_discovered_txt = _discover_comprehensive_rules_txt(
        ('<script type="application/json">{"asset":"'
         + RULES_TXT_URL.replace("/", r"\/")
         + '"}</script>').encode("utf-8"))
    spaced_anchor_discovered_txt = _discover_comprehensive_rules_txt(
        f'<a href="{RULES_TXT_RAW_SPACE_URL}">TXT</a>'.encode("utf-8"))
    spaced_embedded_discovered_txt = _discover_comprehensive_rules_txt(
        ('<script type="application/json">{"asset":"'
         + RULES_TXT_RAW_SPACE_URL.replace("/", r"\/")
         + '"}</script>').encode("utf-8"))
    control_char_candidate_rejected = (
        _official_rules_txt_url(
            "https://media.wizards.com/2026/downloads/"
            "MagicCompRules\t20260819.txt") is None)
    rules_page_request = net._request(WIZARDS_RULES_PAGE_URL)
    rules_txt_request = net._request(RULES_TXT_URL)
    scryfall_request = net._request("https://api.scryfall.com/catalog/card-types")
    parsed_current = _extract_comprehensive_rules_supertypes(RULES_DOCUMENT)
    renumbered = (
        "999.7. Supertypes\n"
        "999.7a An object can have one or more supertypes. The supertypes are "
        "alpha, beta, and gamma.\n"
        "999.7b Next rule.\n"
    )
    parsed_renumbered = _extract_comprehensive_rules_supertypes(renumbered)
    wording_drift_rejected = False
    try:
        _extract_comprehensive_rules_supertypes(
            "205.4. Supertypes\n205.4a Supertypes include several terms.\n"
            "205.4b Next rule.\n")
    except RulesTaxonomyError:
        wording_drift_rejected = True

    cards = [
        {
            "id": f"printing-{index}",
            "oracle_id": f"oracle-{index}",
            "name": f"Card {index}",
            "type_line": "Creature — Wizard",
            "games": ["paper"],
            "set": "tst",
            "collector_number": str(index),
        }
        for index in range(1000)
    ]

    with tempfile.TemporaryDirectory() as temporary:
        db_path = Path(temporary) / "cards.db"
        db = CardDB(str(db_path))
        http = FakeHttp(cards)
        clock_value = 2_000_000_000.0
        service = DatabaseSyncService(
            db, http=http, clock=lambda: clock_value,
            monotonic=time.monotonic)
        initial_due = service.due_reason()
        stages = []
        total = service.sync(
            progress_cb=lambda stage, payload: stages.append((stage, payload)))
        # One full sync must fully satisfy due_reason. Regression guard: the
        # download path once skipped the Universes Beyond marker, so the next
        # launch saw classification_refresh and re-synced a second time before
        # settling.
        due_after_first_sync = service.due_reason()
        first_downloads = http.downloads
        temporary_removed = not (
            Path(temporary) / "scryfall_default_cards.download").exists()
        stored_card_types = db.catalog("card-types")
        stored_rules_supertypes = json.loads(
            db.get_meta(RULES_SUPERTYPES_META_KEY, "[]"))
        stored_rules_url = db.get_meta("rules:supertypes_source_url", "")
        stored_rules_hash = db.get_meta("rules:supertypes_document_sha256", "")
        metadata_complete = (
            db.get_meta("last_sync_kind") == "default_cards"
            and db.get_meta("last_sync_updated_at") == "2026-08-30T00:00:00Z"
            and float(db.get_meta("last_successful_sync_epoch")) == clock_value)
        current_stages = []
        current_total = service.sync(
            progress_cb=lambda stage, payload: current_stages.append(
                (stage, payload)))
        current_avoided_download = http.downloads == first_downloads
        due_after_success = service.due_reason()

        # Upgrading from a database created before a newly required trusted
        # catalog must self-heal without forcing a bulk-card download when the
        # upstream card snapshot itself is already current.
        db.set_meta("catalog:card-types", "[]")
        catalog_refresh_due = service.due_reason() == "catalog_refresh"
        downloads_before_catalog_refresh = http.downloads
        catalog_refresh_stages = []
        service.sync(progress_cb=lambda stage, payload: catalog_refresh_stages.append(
            (stage, payload)))
        catalog_refresh_healed = (
            db.catalog("card-types") == ["Future Value", "Known Value"]
            and http.downloads == downloads_before_catalog_refresh
            and any(stage == "current" for stage, _ in catalog_refresh_stages)
            and service.due_reason() is None)

        verified_before_broken_refresh = db.get_meta(
            RULES_SUPERTYPES_META_KEY, "")
        DatabaseSyncService(
            db, http=BrokenRulesHttp(cards), clock=lambda: clock_value + 1,
            monotonic=time.monotonic).refresh_catalogs()
        broken_rules_preserved_last_verified = (
            db.get_meta(RULES_SUPERTYPES_META_KEY, "")
            == verified_before_broken_refresh)
        broken_rules_error_recorded = bool(
            db.get_meta(RULES_SUPERTYPES_ERROR_META_KEY, ""))
        preserved_status = db.supertype_taxonomy_status()

        verified_card_types_before_broken_refresh = db.catalog("card-types")
        DatabaseSyncService(
            db, http=BrokenCardTypesHttp(cards), clock=lambda: clock_value + 2,
            monotonic=time.monotonic).refresh_catalogs()
        broken_card_types_preserved_last_verified = (
            db.catalog("card-types") == verified_card_types_before_broken_refresh)
        broken_card_types_error_recorded = bool(
            db.get_meta(CARD_TYPES_ERROR_META_KEY, ""))
        preserved_card_type_status = db.card_type_taxonomy_status()

        db.set_meta("catalog:card-types", "[]")
        DatabaseSyncService(
            db, http=BrokenCardTypesHttp(cards), clock=lambda: clock_value + 3,
            monotonic=time.monotonic).refresh_catalogs()
        missing_card_type_status = db.card_type_taxonomy_status()
        service.refresh_catalogs()
        repaired_card_type_status = db.card_type_taxonomy_status()

        # Re-establish a failed Rules attempt after the independent Card Type
        # lifecycle checks above, then remove the verified values to model a
        # fresh install whose authority fetch failed.
        DatabaseSyncService(
            db, http=BrokenRulesHttp(cards), clock=lambda: clock_value + 4,
            monotonic=time.monotonic).refresh_catalogs()
        db.set_meta(RULES_SUPERTYPES_META_KEY, "[]")
        missing_rules_due = service.due_reason() == "catalog_refresh"
        missing_status = db.supertype_taxonomy_status()
        service.refresh_catalogs()
        rules_repaired = (
            service.due_reason() is None
            and db.supertype_taxonomy_status() == (True, ""))

        failing_http = FakeHttp(
            cards[:1], updated_at="2026-08-31T00:00:00Z")
        failed_import_rejected = False
        try:
            DatabaseSyncService(
                db, http=failing_http, clock=lambda: clock_value + 60,
                monotonic=time.monotonic).sync()
        except ValueError:
            failed_import_rejected = True
        failed_import_preserved = (
            db.count() == 1000
            and db.get_meta("last_sync_updated_at")
            == "2026-08-30T00:00:00Z"
            and not (Path(temporary) / "scryfall_default_cards.download").exists())

        service_cancel = threading.Event()
        cancelling_http = CancellingHttp(cards, service_cancel)
        cancellation_raised = False
        try:
            DatabaseSyncService(
                db, http=cancelling_http, clock=lambda: clock_value + 120,
                monotonic=time.monotonic).sync(cancel_event=service_cancel)
        except DatabaseSyncCancelled:
            cancellation_raised = True
        cancellation_cleaned = (
            db.count() == 1000
            and not (Path(temporary) / "scryfall_default_cards.download").exists())
        db.close()

    worker_service = BlockingService()
    controller = DatabaseSyncController(worker_service)
    started = controller.start("manual")
    worker_service.entered.wait(1.0)
    busy = controller.start("manual")
    first_poll = controller.poll()
    worker_service.release.set()
    _progress, _timings, terminal = _wait_for_terminal(controller)

    cancelled_service = BlockingService()
    cancelled_controller = DatabaseSyncController(cancelled_service)
    cancelled_controller.start("scheduled")
    cancelled_service.entered.wait(1.0)
    shutdown_stopped = cancelled_controller.shutdown(timeout=1.0)
    _progress, _timings, cancelled_terminal = _wait_for_terminal(
        cancelled_controller)

    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/database/sync.py", "mtgdb/ui/database_sync.py",
            "mtgdb/database/db.py", "mtgdb/ui/workspace.py",
        )
    }
    gui_methods = _methods(sources["mtgdb/ui/app.py"], "DeckBuilderApp")
    ui_methods = _methods(sources["mtgdb/ui/database_sync.py"], "DatabaseSyncMixin")
    sync_methods = {
        "_initialize_database_sync", "_maybe_auto_sync", "_sync_db",
        "_show_sync_popup", "_close_sync_popup", "_poll_sync_events",
        "_apply_sync_progress", "_sync_done", "_sync_error",
        "_shutdown_database_sync",
    }
    gui_tree = ast.parse(sources["mtgdb/ui/app.py"])
    gui_class = next(
        node for node in gui_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DeckBuilderApp")
    gui_bases = {
        base.id for base in gui_class.bases if isinstance(base, ast.Name)}

    # DBS-008: a killed process never runs the download finally block, so the
    # service must clear abandoned bulk temps when it is constructed.
    with tempfile.TemporaryDirectory() as _sync_dir:
        _sync_root = Path(_sync_dir)
        _orphan = _sync_root / "scryfall_default_cards.download"
        _unrelated = _sync_root / "keep_me.download"
        _plain = _sync_root / "scryfall_notes.txt"
        for _path in (_orphan, _unrelated, _plain):
            _path.write_bytes(b"partial")
        _sweep_db = CardDB(str(_sync_root / "cards.db"))
        try:
            DatabaseSyncService(_sweep_db)
            stale_bulk_temp_swept = (
                not _orphan.exists()
                and _unrelated.exists()
                and _plain.exists())
        finally:
            _sweep_db.close()

    checks = {
        "abandoned bulk temp files are cleared at sync-service start": (
            stale_bulk_temp_swept),
        "first launch is due when the database is empty": (
            initial_due == "first_launch"),
        "service imports the complete bulk dataset": (
            total == 1000 and any(stage == "done" for stage, _ in stages)),
        "catalog and synchronization metadata are persisted": (
            stored_card_types == ["Future Value", "Known Value"]
            and stored_rules_supertypes
                == ["basic", "legendary", "ongoing", "snow", "world"]
            and stored_rules_url == RULES_TXT_URL
            and len(stored_rules_hash) == 64
            and metadata_complete),
        "Rules page TXT discovery handles anchors and embedded page data": (
            discovered_txt == RULES_TXT_URL
            and embedded_discovered_txt == RULES_TXT_URL),
        "Rules TXT discovery canonicalizes raw path spaces before transport": (
            spaced_anchor_discovered_txt == RULES_TXT_ESCAPED_SPACE_URL
            and spaced_embedded_discovered_txt == RULES_TXT_ESCAPED_SPACE_URL),
        "Rules TXT discovery rejects true URL control characters": (
            control_char_candidate_rejected),
        "HTTP requests advertise document-appropriate Wizards media types": (
            "text/html" in str(rules_page_request.get_header("Accept"))
            and "text/plain" in str(rules_txt_request.get_header("Accept"))
            and "application/json" in str(scryfall_request.get_header("Accept"))),
        "Wizards document requests are browser-compatible without changing Scryfall UA": (
            "Mozilla/5.0" in str(rules_page_request.get_header("User-agent"))
            and "Mozilla/5.0" in str(rules_txt_request.get_header("User-agent"))
            and str(scryfall_request.get_header("User-agent")) == net.USER_AGENT
            and str(rules_txt_request.get_header("Referer"))
                == WIZARDS_RULES_PAGE_URL),
        "Supertype parser accepts current and renumbered strict structures": (
            parsed_current == ["basic", "legendary", "ongoing", "snow", "world"]
            and parsed_renumbered == ["alpha", "beta", "gamma"]),
        "unrecognized Comprehensive Rules wording fails closed": wording_drift_rejected,
        "failed Rules refresh preserves taxonomy and records its failure": (
            broken_rules_preserved_last_verified
            and broken_rules_error_recorded
            and preserved_status[0] is True
            and bool(preserved_status[1])),
        "failed Card Type refresh preserves taxonomy and records its failure": (
            broken_card_types_preserved_last_verified
            and broken_card_types_error_recorded
            and preserved_card_type_status[0] is True
            and bool(preserved_card_type_status[1])),
        "missing Card Type authority is distinguishable and repairable": (
            missing_card_type_status[0] is False
            and bool(missing_card_type_status[1])
            and repaired_card_type_status == (True, "")),
        "missing Rules authority is distinguishable and repaired cleanly": (
            missing_rules_due
            and missing_status[0] is False
            and bool(missing_status[1])
            and rules_repaired),
        "temporary bulk data is removed after success": temporary_removed,
        "failed replacement preserves the committed database": (
            failed_import_rejected and failed_import_preserved),
        "cancelled download removes its partial bulk file": (
            cancellation_raised and cancellation_cleaned),
        "same upstream revision avoids a second bulk download": (
            current_total == 1000 and current_avoided_download
            and any(stage == "current" for stage, _ in current_stages)),
        "one full sync fully satisfies the due policy": (
            due_after_first_sync is None),
        "successful refresh resets the automatic due policy": (
            due_after_success is None),
        "missing trusted catalog triggers metadata refresh": catalog_refresh_due,
        "catalog-only upgrade refresh avoids redundant bulk download": (
            catalog_refresh_healed),
        "controller permits only one synchronization worker": (
            started.status == "started" and busy.status == "busy"),
        "controller coalesces progress and delivers terminal success": (
            first_poll.progress is not None
            and first_poll.progress.stage == "download"
            and terminal is not None and terminal.kind == "done"
            and terminal.payload == 42),
        "controller cancellation stops the worker cleanly": (
            shutdown_stopped and cancelled_terminal is not None
            and cancelled_terminal.kind == "cancelled"),
        "database synchronization core is Tk-free": (
            "tkinter" not in sources["mtgdb/database/sync.py"]),
        "database progress popup uses a DPI-safe explicit minimum width": (
            "preferred_width=680" in sources["mtgdb/ui/database_sync.py"]
            and "min_width=620" in sources["mtgdb/ui/database_sync.py"]
            and "lock_size=True" in sources["mtgdb/ui/database_sync.py"]),
        "database progress labels processed rows without extrapolated card totals": (
            'text=f"{loaded:,} card records processed"'
                in sources["mtgdb/ui/database_sync.py"]
            and "about {int(estimated):,} expected"
                not in sources["mtgdb/ui/database_sync.py"]),
        "CardDB owns no network synchronization orchestration": all(
            marker not in sources["mtgdb/database/db.py"] for marker in (
                "import mtgdb.core.net", "def sync(", "def refresh_catalogs(")),
        "database sync UI owns no network or worker thread": all(
            marker not in sources["mtgdb/ui/database_sync.py"] for marker in (
                "import mtgdb.core.net", "threading.Thread", "self.db.sync(")),
        "DeckBuilderApp delegates database sync presentation": (
            "DatabaseSyncMixin" in gui_bases
            and sync_methods <= ui_methods
            and not sync_methods & gui_methods),
        "workspace autosave uses the public sync-state boundary": (
            "self._database_sync_is_running()" in sources["mtgdb/ui/workspace.py"]
            and "_sync_running" not in sources["mtgdb/ui/workspace.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nDATABASE SYNC ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
