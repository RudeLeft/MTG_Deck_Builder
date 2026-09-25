"""Database synchronization service, controller, and UI ownership contracts."""

import ast
import json
import re
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
    CARD_TYPES_ERROR_META_KEY, CATALOGS_ATTEMPT_META_KEY,
    RULES_SUPERTYPES_ERROR_META_KEY, RULES_SUPERTYPES_META_KEY,
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


def _review_cards(count=1500):
    return [
        {"object": "card", "id": f"c{i:05d}", "name": f"Card {i}",
         "type_line": "Creature \u2014 Bear", "lang": "en", "games": ["paper"],
         "set": "tst", "set_name": "Test", "set_type": "core",
         "collector_number": str(i), "rarity": "common",
         "released_at": "2020-01-01", "legalities": {"modern": "legal"},
         "image_uris": {"png": "p"}, "cmc": 2.0,
         "oracle_text": "Some rules text " * 20}
        for i in range(count)]


def _database_review_checks():
    """Edge cases around building, updating and checking the card database.

    Each of these reproduced against a temporary database and a fake network:
    a healthy database that was briefly locked was judged corrupt and wiped;
    damage below the first page could never be repaired; a shutdown right after
    the import committed forced a full re-download; a catalog source that always
    fails reopened the refresh dialog on every launch; a failed "is my data
    current?" check downloaded the whole file anyway; a failure after the cards
    were replaced left stale caches; and a manual update claimed the library was
    "more than 48 hours old".
    """
    import sqlite3
    from types import SimpleNamespace
    from mtgdb.database import schema
    from mtgdb.database.sync import (
        DatabaseDamagedError, DatabaseSyncUnavailable, CATALOG_RETRY_SECONDS)
    from mtgdb.ui import database_sync as ui_sync

    results = {}
    cards = _review_cards()

    # ---- 1. a locked / I/O-error database is NOT corrupt -----------------------
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "cards.db")
        db = CardDB(path)
        db.load_cards(cards[:300])
        db.close()

        garbage = Path(tmp) / "garbage.db"
        garbage.write_bytes(b"this is not a sqlite database " * 200)
        garbage_error = None
        connection = sqlite3.connect(str(garbage))
        try:
            connection.execute("SELECT 1 FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            garbage_error = exc
        finally:
            connection.close()

        # A REAL lock: a rollback-journal database held EXCLUSIVE by another
        # connection.  (WAL databases keep readers unblocked, so use DELETE mode.)
        locked_path = str(Path(tmp) / "locked.db")
        holder = sqlite3.connect(locked_path, isolation_level=None)
        holder.execute("PRAGMA journal_mode=DELETE")
        holder.execute("CREATE TABLE t (x)")
        holder.execute("BEGIN EXCLUSIVE")
        locked_error = None
        blocked = sqlite3.connect(locked_path, timeout=0)
        try:
            blocked.execute("SELECT 1 FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            locked_error = exc
        finally:
            blocked.close()
            holder.execute("ROLLBACK")
            holder.close()

        results["only an error that says the file is damaged counts as corruption"] = (
            garbage_error is not None and schema.is_corruption_error(garbage_error)
            and locked_error is not None
            and not schema.is_corruption_error(locked_error)
            and not schema.is_corruption_error(OSError("disk I/O error"))
            and not schema.is_corruption_error(
                sqlite3.OperationalError("disk I/O error")))

        real_connect = sqlite3.connect
        original_delays = schema.PROBE_RETRY_DELAYS

        def probe_with(behaviour):
            """Run the probe while sqlite3.connect misbehaves as ``behaviour`` says."""
            schema.PROBE_RETRY_DELAYS = (0, 0)
            attempts = {"n": 0}

            class Failing:
                def __init__(self, fail_at):
                    self.fail_at = fail_at

                def execute(self, *_a, **_k):
                    if self.fail_at == "execute":
                        raise sqlite3.OperationalError(behaviour["message"])
                    return self

                def fetchone(self):
                    return None

                def close(self):
                    pass

            def connect(*a, **k):
                attempts["n"] += 1
                if behaviour.get("recover_after") and attempts["n"] > behaviour["recover_after"]:
                    return real_connect(*a, **k)
                if behaviour["at"] == "connect":
                    raise sqlite3.OperationalError(behaviour["message"])
                return Failing("execute")

            sqlite3.connect = connect
            try:
                return schema._database_is_corrupt(path), attempts["n"]
            finally:
                sqlite3.connect = real_connect
                schema.PROBE_RETRY_DELAYS = original_delays

        transient = [
            probe_with({"at": "execute", "message": "database is locked"}),
            probe_with({"at": "execute", "message": "disk I/O error"}),
            probe_with({"at": "connect", "message": "unable to open database file"}),
        ]
        recovered = probe_with({"at": "execute", "message": "database is locked",
                                "recover_after": 1})
        results["a locked or unreadable database is retried, never judged corrupt"] = (
            all(verdict is False and attempts >= 3 for verdict, attempts in transient)
            and recovered[0] is False and recovered[1] == 2
            and schema._database_is_corrupt(str(garbage)) is True
            and schema._database_is_corrupt(path) is False)

        # ... and a transient failure never quarantines the healthy file.
        schema.PROBE_RETRY_DELAYS = (0,)
        sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked"))
        try:
            def blocked_open():
                try:
                    schema.open_primary_connection(path, [])
                except sqlite3.OperationalError:
                    return "raised"
                return "opened"
            sqlite3.connect = real_connect
            calls = {"n": 0}

            def flaky_connect(*a, **k):
                calls["n"] += 1
                if calls["n"] <= 2:
                    raise sqlite3.OperationalError("database is locked")
                return real_connect(*a, **k)

            sqlite3.connect = flaky_connect
            conn = schema.open_primary_connection(path, [])
            conn.close()
        finally:
            sqlite3.connect = real_connect
            schema.PROBE_RETRY_DELAYS = original_delays
        check = sqlite3.connect(path)
        try:
            cards_left = check.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        finally:
            check.close()
        results["a briefly locked healthy database keeps its cards at launch"] = (
            cards_left == 300 and not Path(path + ".corrupt").exists())

    # ---- 2. damage deeper than page 1 is repaired on the next launch ------------
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "cards.db")
        db = CardDB(path)
        db.load_cards(_review_cards(3000))
        db.close()
        size = Path(path).stat().st_size
        with open(path, "r+b") as handle:
            handle.seek(size // 2)
            handle.write(b"\xde\xad\xbe\xef" * 4096)
        passes_startup_probe = schema._database_is_corrupt(path) is False
        db = CardDB(path)
        service = DatabaseSyncService(db, http=FakeHttp(cards))
        first_error = None
        try:
            service.sync()
        except DatabaseDamagedError as exc:
            first_error = exc
        sentinel = Path(path + schema.REBUILD_SENTINEL_SUFFIX)
        asked_for_rebuild = sentinel.exists()
        db.close()
        # The next launch has nothing holding the file open.
        connection = schema.open_primary_connection(path, [])
        connection.close()
        fresh = CardDB(path)
        fresh_service = DatabaseSyncService(fresh, http=FakeHttp(cards))
        due = fresh_service.due_reason()
        rebuilt_total = fresh_service.sync()
        fresh.close()
        results["deep damage is reported, then rebuilt from Scryfall on the next launch"] = (
            passes_startup_probe and first_error is not None
            and "next time MTG Deck Builder starts" in str(first_error)
            and asked_for_rebuild
            and Path(path + ".corrupt").exists() and not sentinel.exists()
            and due == "first_launch" and rebuilt_total == len(cards))

    # ---- 3. shutdown right after the import commits -----------------------------
    with tempfile.TemporaryDirectory() as tmp:
        db = CardDB(str(Path(tmp) / "cards.db"))
        cancel = threading.Event()
        http = FakeHttp(cards, "2026-09-01T00:00:00Z")
        service = DatabaseSyncService(db, http=http)
        real_load = db.load_cards

        def load_then_shutdown(objects, **kwargs):
            callback = kwargs["maintenance_cb"]

            def watching(phase, current, total):
                if phase == "commit" and current >= total:
                    cancel.set()      # the user closes the app the instant it commits
                return callback(phase, current, total)
            kwargs["maintenance_cb"] = watching
            return real_load(objects, **kwargs)

        db.load_cards = load_then_shutdown
        try:
            completed = service.sync(cancel_event=cancel) == len(cards)
        except DatabaseSyncCancelled:
            completed = False
        db.load_cards = real_load
        recorded = db.get_meta("last_sync_updated_at") == "2026-09-01T00:00:00Z"
        next_launch = DatabaseSyncService(db, http=http)
        downloads_before = http.downloads
        next_launch.sync()
        results["a shutdown right after the commit still records the download"] = (
            completed and recorded and http.downloads == downloads_before
            and db.get_meta("last_sync_kind") == "default_cards")

        # The record is part of the SAME transaction as the cards: a load that
        # is rejected leaves neither.
        rejected_marker = None
        try:
            db.load_cards(iter(_review_cards(5)), minimum_count=1000,
                          meta={"marker": "should-not-persist"})
        except ValueError:
            rejected_marker = db.get_meta("marker", "")
        db.load_cards(iter(_review_cards(1200)), minimum_count=1000,
                      meta=lambda: {"marker": "with-the-cards"})
        results["the download record commits with the cards or not at all"] = (
            rejected_marker == "" and db.get_meta("marker") == "with-the-cards")
        db.close()

    # ---- 4. a source that always fails is retried on a schedule -----------------
    with tempfile.TemporaryDirectory() as tmp:
        db = CardDB(str(Path(tmp) / "cards.db"))
        now = [2_000_000_000.0]
        broken = BrokenRulesHttp(cards)
        first = DatabaseSyncService(db, http=broken, clock=lambda: now[0])
        first.sync()
        launches = [
            DatabaseSyncService(db, http=broken, clock=lambda: now[0]).due_reason()
            for _ in range(4)]
        now[0] += CATALOG_RETRY_SECONDS + 60
        after_backoff = DatabaseSyncService(
            db, http=broken, clock=lambda: now[0]).due_reason()
        # A stamp from the future (clock moved back) must not wedge the schedule.
        db.set_meta(CATALOGS_ATTEMPT_META_KEY, f"{now[0] + 10 * 86400:.6f}")
        future_stamp = DatabaseSyncService(
            db, http=broken, clock=lambda: now[0]).due_reason()
        results["a failing catalog source is retried after a day, not every launch"] = (
            launches == [None, None, None, None]
            and after_backoff == "catalog_refresh"
            and future_stamp == "catalog_refresh")
        db.close()

    # ---- 5. a failed "is my data current?" check must not download everything ---
    class MetadataOutage(FakeHttp):
        def get_json(self, url):
            if "/bulk-data/" in url:
                raise OSError("simulated rate limit on the metadata call")
            return super().get_json(url)

    with tempfile.TemporaryDirectory() as tmp:
        db = CardDB(str(Path(tmp) / "cards.db"))
        DatabaseSyncService(db, http=FakeHttp(cards, "2026-09-01T00:00:00Z")).sync()
        outage = MetadataOutage(cards, "2026-09-01T00:00:00Z")
        stamp_clock = 2_100_000_000.0
        unavailable = None
        try:
            DatabaseSyncService(db, http=outage, clock=lambda: stamp_clock).sync()
        except DatabaseSyncUnavailable as exc:
            unavailable = exc
        catalogs_still_attempted = (
            float(db.get_meta(CATALOGS_ATTEMPT_META_KEY)) == stamp_clock)
        kept = db.count()
        db.close()
    with tempfile.TemporaryDirectory() as tmp:
        empty = CardDB(str(Path(tmp) / "cards.db"))
        first_outage = MetadataOutage(cards, "2026-09-01T00:00:00Z")
        first_total = DatabaseSyncService(empty, http=first_outage).sync()
        empty.close()
    results["a failed currency check with a usable library downloads nothing"] = (
        unavailable is not None and outage.downloads == 0
        and "left unchanged" in str(unavailable)
        and catalogs_still_attempted and kept == len(cards))
    results["with no library at all the blind download is still the way in"] = (
        first_total == len(cards) and first_outage.downloads == 1)

    # ---- 6. a failure after the cards were replaced -----------------------------
    class HousekeepingFails(DatabaseSyncService):
        def _record_compatibility_diagnostics(self):
            raise RuntimeError("diagnostics exploded")

    with tempfile.TemporaryDirectory() as tmp:
        db = CardDB(str(Path(tmp) / "cards.db"))
        service = HousekeepingFails(db, http=FakeHttp(cards))
        try:
            total = service.sync()
        except Exception:
            total = None
        results["housekeeping after the commit cannot turn a good update into a failure"] = (
            total == len(cards) and service.cards_replaced is True
            and db.get_meta("last_sync_updated_at") != "")
        db.close()

    class Recorder:
        def __init__(self):
            self.errors = []

        def showerror(self, title, message):
            self.errors.append((title, message))

    real_messagebox = ui_sync.messagebox
    recorder = Recorder()
    ui_sync.messagebox = recorder
    try:
        class Owner(ui_sync.DatabaseSyncMixin):
            pass

        def owner_with(replaced):
            owner = Owner()
            owner._sync_poll_after = None
            owner.log_path = ""
            owner.reconciled = 0
            owner.closed = 0
            owner._close_sync_popup = lambda: setattr(owner, "closed", owner.closed + 1)
            owner._reconcile_after_database_change = (
                lambda: setattr(owner, "reconciled", owner.reconciled + 1))
            owner.database_sync_controller = SimpleNamespace(
                running=False, service=SimpleNamespace(cards_replaced=replaced),
                due_reason=lambda: (_ for _ in ()).throw(
                    DatabaseDamagedError("The local card database file is damaged (x)")))
            return owner

        after_replace = owner_with(True)
        after_replace._sync_error("boom")
        untouched = owner_with(False)
        untouched._sync_error("boom")
        damaged = owner_with(False)
        damaged._sync_error(
            "The local card database file is damaged (x). Restart.",
            kind="DatabaseDamagedError")
        launch = owner_with(False)
        launch._maybe_auto_sync()
    finally:
        ui_sync.messagebox = real_messagebox
    results["an error after the cards were replaced still refreshes the search caches"] = (
        after_replace.reconciled == 1 and untouched.reconciled == 0
        and recorder.errors[0][0] == "Database update failed")
    results["a damaged database is explained without the misleading 'retry' advice"] = (
        recorder.errors[1][0] == "Database update failed"
        and recorder.errors[2][0] == "Card database needs rebuilding"
        and "left available" not in recorder.errors[2][1]
        and "Restart" in recorder.errors[2][1]
        # The launch-time check reports damage the same way (and starts no sync).
        and len(recorder.errors) == 4
        and recorder.errors[3][0] == "Card database needs rebuilding")

    class DamagedService:
        def due_reason(self):
            return "scheduled"

        def sync(self, progress_cb=None, cancel_event=None):
            raise DatabaseDamagedError("The local card database file is damaged (x)")

    controller = DatabaseSyncController(DamagedService())
    controller.start("manual")
    terminal = _wait_for_terminal(controller)[2]
    controller.shutdown()
    results["the controller tells the UI which kind of error it was"] = (
        terminal is not None and terminal.kind == "error"
        and terminal.stage == "DatabaseDamagedError")

    # ---- 7. the dialog says what is true for each reason ------------------------
    results["a manual update does not claim the library is 48 hours old"] = (
        "48 hours" not in ui_sync.sync_reason_text("manual")
        and "48 hours" not in ui_sync.sync_reason_text("classification_refresh")
        and "48 hours" in ui_sync.sync_reason_text("scheduled")
        and "downloaded now" in ui_sync.sync_reason_text("first_launch"))

    # ---- 8. the repository map does not pin a stale schema number ---------------
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    results["the repository map states no schema version number to go stale"] = (
        re.search(r"schema\.py\s+#\s+schema v\d+", agents) is None
        and "schema version" in agents)
    return results


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
        # A database from before the catalog existed has never attempted it.
        db.set_meta(CATALOGS_ATTEMPT_META_KEY, "")
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

    checks.update(_database_review_checks())

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nDATABASE SYNC ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
