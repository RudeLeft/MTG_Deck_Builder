"""Edge-case regressions for integrity, search correctness, and release hardening."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import tomllib
import urllib.error
from unittest import mock
import zipfile
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import package_release as release
from mtgdb.comparison.models import ComparisonCollection, MAX_COMPARISON_CARDS
from mtgdb.core import net
from mtgdb.database.db import CardDB
from mtgdb.database.search_queries import SearchQueryBuilder
from mtgdb.database.bulk_import import iter_card_objects
from mtgdb.deck.io import save_deck_text
from mtgdb.deck.model import Deck
from mtgdb.images.service import CardImageService
from mtgdb.search.controller import SearchController
from mtgdb.search.models import SearchCriteria
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.card_detail import CardDetailMixin
from mtgdb.ui.search import SearchFeatureMixin
from mtgdb.workspace.repository import WORKSPACE_VERSION, WorkspaceRepository


def _card(card_id, name=None, *, legality="legal"):
    return {
        "id": card_id,
        "oracle_id": f"oracle-{card_id}",
        "name": name or f"Card {card_id}",
        "games": ["paper"],
        "set": "tst",
        "set_name": "Test Set",
        "set_type": "expansion",
        "collector_number": str(card_id),
        "lang": "en",
        "legalities": {"vintage": legality},
    }


def _raises(exc_type, callback):
    try:
        callback()
    except exc_type:
        return True
    return False


def _bulk_parser_checks(tmp):
    malformed_jsonl = tmp / "malformed.jsonl"
    malformed_jsonl.write_text(
        json.dumps(_card("1")) + "\n{not-json}\n", encoding="utf-8")
    malformed_rejected = _raises(
        ValueError, lambda: list(iter_card_objects(malformed_jsonl)))

    trailing = tmp / "trailing.json"
    trailing.write_text(
        json.dumps([_card("1")]) + " trailing-garbage", encoding="utf-8")
    trailing_rejected = _raises(
        ValueError, lambda: list(iter_card_objects(trailing)))

    unterminated = tmp / "unterminated.json"
    unterminated.write_text(
        "[" + json.dumps(_card("1")), encoding="utf-8")
    unterminated_rejected = _raises(
        (ValueError, json.JSONDecodeError),
        lambda: list(iter_card_objects(unterminated)))
    return malformed_rejected, trailing_rejected, unterminated_rejected


def _bulk_distinct_count_check(tmp):
    db = CardDB(str(tmp / "cards.db"))
    try:
        db.load_cards([_card("old", "Preserve Me")])
        duplicate = _card("duplicate", "Duplicate")
        rejected = _raises(
            ValueError,
            lambda: db.load_cards(
                [dict(duplicate) for _ in range(1000)], minimum_count=1000))
        return (
            rejected
            and db.count() == 1
            and db.get_card("old")["name"] == "Preserve Me"
            and db.get_card("duplicate") is None
        )
    finally:
        db.close()


class _Response:
    def __init__(self, payload, declared=None):
        self.payload = payload
        self.headers = {}
        if declared is not None:
            self.headers["Content-Length"] = str(declared)
        self._read = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        if self._read:
            return b""
        self._read = True
        return self.payload


def _network_checks(tmp):
    destination = tmp / "download.bin"
    short = _Response(b"1234567890", declared=100)
    with mock.patch.object(net, "_open", return_value=short):
        incomplete_rejected = _raises(
            OSError,
            lambda: net.download(
                "https://example.invalid/file", destination, retries=0))
    incomplete_removed = not destination.exists()

    attempts = {"count": 0}

    def flaky_open(_url, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("transient")
        return _Response(b"image-bytes")

    with mock.patch.object(net, "_open", side_effect=flaky_open), \
            mock.patch.object(net.time, "sleep", return_value=None):
        fetched = net.fetch_bytes("https://example.invalid/image", retries=1)
    retry_ok = fetched == b"image-bytes" and attempts["count"] == 2

    # DBS-012 applies the declared-length contract to raw image bytes too, not
    # only to streamed bulk files.
    short_bytes = _Response(b"1234567890", declared=100)
    with mock.patch.object(net, "_open", return_value=short_bytes):
        short_fetch_rejected = _raises(
            OSError,
            lambda: net.fetch_bytes("https://example.invalid/image", retries=0))

    exact_bytes = _Response(b"image-bytes", declared=len(b"image-bytes"))
    with mock.patch.object(net, "_open", return_value=exact_bytes):
        exact_accepted = net.fetch_bytes(
            "https://example.invalid/image", retries=0) == b"image-bytes"

    # A malformed header is "not supplied", never an uncatchable crash.
    malformed_bytes = _Response(b"image-bytes", declared="not-a-number")
    with mock.patch.object(net, "_open", return_value=malformed_bytes):
        malformed_tolerated = net.fetch_bytes(
            "https://example.invalid/image", retries=0) == b"image-bytes"

    # A content-encoded body's declared length describes the encoded entity, so
    # it cannot be compared against decoded bytes without false truncation
    # reports. Such a response supplies no usable length.
    encoded_bytes = _Response(b"image-bytes", declared=9999)
    encoded_bytes.headers["Content-Encoding"] = "gzip"
    with mock.patch.object(net, "_open", return_value=encoded_bytes):
        encoded_tolerated = net.fetch_bytes(
            "https://example.invalid/image", retries=0) == b"image-bytes"

    identity_bytes = _Response(b"1234567890", declared=100)
    identity_bytes.headers["Content-Encoding"] = "identity"
    with mock.patch.object(net, "_open", return_value=identity_bytes):
        identity_still_verified = _raises(
            OSError,
            lambda: net.fetch_bytes("https://example.invalid/image", retries=0))

    byte_length_verified = (
        short_fetch_rejected and exact_accepted and malformed_tolerated
        and encoded_tolerated and identity_still_verified)
    # DBS-012: cleanup removes a partial file this attempt wrote, never a file
    # that was already sitting at the destination when the request failed
    # before any transfer began.
    survivor = tmp / "already-downloaded.bin"
    survivor.write_bytes(b"the previous good download")
    with mock.patch.object(
            net, "_open",
            side_effect=urllib.error.HTTPError(
                "https://example.invalid/f", 404, "gone", {}, None)):
        _raises(urllib.error.HTTPError,
                lambda: net.download("https://example.invalid/f", survivor,
                                     retries=0))
    existing_file_kept = (
        survivor.exists()
        and survivor.read_bytes() == b"the previous good download")

    # The same rule when the destination itself cannot be opened. On Windows a
    # file that refuses open() refuses remove() too, which hides the mistake;
    # on POSIX removal depends on the directory, not the file, so a destination
    # that cannot be written can still be deleted. Simulating the refusal keeps
    # the check honest on both.
    guarded = tmp / "unwritable.bin"
    guarded.write_bytes(b"the previous good download")
    real_open = open

    def refusing_open(file, *args, **kwargs):
        if str(file) == str(guarded):
            raise PermissionError(13, "permission denied")
        return real_open(file, *args, **kwargs)

    with mock.patch.object(net, "_open", return_value=_Response(b"payload")),             mock.patch("builtins.open", side_effect=refusing_open):
        _raises(PermissionError,
                lambda: net.download("https://example.invalid/f", guarded,
                                     retries=0))
    existing_file_kept = (
        existing_file_kept and guarded.exists()
        and guarded.read_bytes() == b"the previous good download")

    # DBS-015: urlopen carries file/ftp/data handlers, so an unchecked URL is
    # not merely a failed download. On Windows "file://host/share" is an SMB
    # connection that offers the machine's credentials to whoever answers.
    local = tmp / "local-secret.txt"
    local.write_bytes(b"local file contents")
    refused_schemes = all(
        _raises(ValueError, call)
        for call in (
            lambda: net.fetch_bytes(local.as_uri(), retries=0),
            lambda: net.download(local.as_uri(), tmp / "copied.bin", retries=0),
            lambda: net.get_json(local.as_uri(), retries=0),
            lambda: net.fetch_bytes("file://198.51.100.7/share/x.txt", retries=0),
            lambda: net.fetch_bytes("ftp://198.51.100.7/x.txt", retries=0),
            lambda: net.fetch_bytes("http://cards.scryfall.io/x.jpg", retries=0),
            lambda: net.fetch_bytes("data:text/plain;base64,aGk=", retries=0),
            lambda: net.fetch_bytes("cards.scryfall.io/x.jpg", retries=0)))
    # A refused scheme is rejected before the socket layer is ever reached, and
    # is permanent, so it is not retried. urlopen is the boundary that must not
    # be crossed -- patching _open would bypass the very guard under test.
    opened_urls = []

    def spy(request, timeout=None):
        opened_urls.append(request.full_url)
        return _Response(b"ok")

    with mock.patch.object(net.urllib.request, "urlopen", side_effect=spy):
        _raises(ValueError,
                lambda: net.fetch_bytes("ftp://198.51.100.7/x", retries=3))
        never_opened = opened_urls == []
        # ...and an ordinary https URL still travels the whole real path.
        https_allowed = net.fetch_bytes(
            "https://cards.scryfall.io/large/x.jpg", retries=0) == b"ok"
        https_allowed = https_allowed and opened_urls == [
            "https://cards.scryfall.io/large/x.jpg"]
    https_allowed = https_allowed and never_opened
    scheme_guarded = (
        refused_schemes and https_allowed
        and not (tmp / "copied.bin").exists()
        # The URL guards stay total: a non-string is a clear result, never a
        # stray AttributeError from urlsplit.
        and net._is_api_url(Path("https://api.scryfall.com/x")) is False
        and net._is_api_url(12) is False
        and net._is_api_url(None) is False
        and net._is_api_url("https://api.scryfall.com/x") is True)

    return (
        incomplete_rejected and incomplete_removed, retry_ok,
        byte_length_verified, existing_file_kept, scheme_guarded)


class _Reader:
    def close(self):
        pass


class _BlockingRepository:
    def __init__(self):
        self.old_started = threading.Event()
        self.release_old = threading.Event()

    def open_reader(self):
        return _Reader()

    def search(self, criteria, _reader):
        if criteria.name == "old":
            self.old_started.set()
            self.release_old.wait(2.0)
        return [{"id": criteria.name, "name": criteria.name}]


def _wait_event(controller, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = controller.poll_latest()
        if event is not None:
            return event
        time.sleep(0.005)
    return None


def _search_invalidation_check():
    repository = _BlockingRepository()
    controller = SearchController(repository)
    old = SearchCriteria(name="old")
    new = SearchCriteria(name="new")
    old_start = controller.start(old)
    if not repository.old_started.wait(1.0):
        return False
    controller.invalidate()
    new_start = controller.start(new)
    new_event = _wait_event(controller)
    accepted_new = bool(new_event and controller.accept(new_event))
    repository.release_old.set()
    time.sleep(0.03)
    stale_event = controller.poll_latest()
    return (
        old_start.kind == "started"
        and new_start.kind == "started"
        and accepted_new
        and new_event.payload.row_at_source(0).get("id") == "new"
        and stale_event is None
        and controller.start(new).kind == "unchanged"
    )



class _FakeSearchButton:
    def __init__(self):
        self.calls = []

    def state(self, values):
        self.calls.append(tuple(values))


class _FakeSearchController:
    def __init__(self):
        self.running = True
        self.invalidated = False

    def invalidate(self):
        self.invalidated = True
        self.running = False


def _search_ui_invalidation_check():
    owner = type("SearchOwner", (), {})()
    owner.search_controller = _FakeSearchController()
    owner._active_search_signature = ("old",)
    owner._search_btn = _FakeSearchButton()
    SearchFeatureMixin._invalidate_search_cache(owner)
    return (
        owner.search_controller.invalidated
        and owner._active_search_signature is None
        and ("!disabled",) in owner._search_btn.calls)

def _numeric_search_checks():
    bad_number = _raises(
        ValueError,
        lambda: SearchFeatureMixin._parse_search_number("abc", "Power minimum"))
    non_finite = all(
        _raises(
            ValueError,
            lambda value=value: SearchFeatureMixin._parse_search_number(
                value, "Power minimum"))
        for value in ("nan", "inf", "-inf", "1e309"))
    valid = SearchFeatureMixin._parse_search_number(" 2.5 ", "Power") == 2.5
    bad_range = _raises(
        ValueError,
        lambda: SearchFeatureMixin._validate_search_range("Power", 5, 2))
    builder_rejects_non_finite = _raises(
        ValueError,
        lambda: SearchQueryBuilder().add_numeric_filters(
            float("nan"), None, None, None, None, None))
    return (bad_number and non_finite and valid and bad_range
            and builder_rejects_non_finite)



def _unknown_content_filter_check(tmp):
    db = CardDB(str(tmp / "content.db"))
    try:
        db.load_cards([_card("1")])
        return _raises(
            ValueError,
            lambda: db.search(content_types=["card", "future-unknown-kind"]))
    finally:
        db.close()

def _legality_search_check(tmp):
    db = CardDB(str(tmp / "legality.db"))
    try:
        db.load_cards([
            _card("legal", legality="legal"),
            _card("restricted", legality="restricted"),
            _card("banned", legality="banned"),
        ])
        ids = {card["id"] for card in db.search(fmt="vintage")}
    finally:
        db.close()

    class Owner:
        _format_catalog = ["vintage"]

    restricted_summary = CardDetailMixin._legal_summary(
        Owner(), {"legalities": json.dumps({"vintage": "restricted"})})
    decoded_summary = CardDetailMixin._legal_summary(
        Owner(), {"legalities": {"vintage": "restricted"}})
    malformed_summary = CardDetailMixin._legal_summary(
        Owner(), {"legalities": "not-json"})
    return (
        ids == {"legal", "restricted"}
        and restricted_summary == "Vintage (Restricted)"
        and decoded_summary == "Vintage (Restricted)"
        and malformed_summary == ""
    )


def _result_identity_check():
    store = SearchResultStore.from_rows(
        [{"id": "new-first"}, {"id": "keep-me"}, {"id": "new-third"}])
    selected_id = "keep-me"
    original = store.view_position_for_id(selected_id)
    store.swap_view_index((2, 1, 0))
    reordered = store.view_position_for_id(selected_id)
    missing = SearchResultStore.from_rows([{"id": "different"}])
    return (
        original == 1
        and reordered == 1
        and missing.view_position_for_id(selected_id) is None
    )


def _atomic_deck_save_check(tmp):
    target = tmp / "deck.txt"
    target.write_text("old-content\n", encoding="utf-8")
    deck = Deck("New Deck", "modern")
    deck.add(_card("1", "New Card"), "main", 1)
    with mock.patch("mtgdb.deck.io.os.replace", side_effect=OSError("disk")):
        failed = _raises(OSError, lambda: save_deck_text(target, deck))
    preserved = target.read_text(encoding="utf-8") == "old-content\n"
    no_temp = not list(tmp.glob(".deck.txt.*.tmp"))
    save_deck_text(target, deck)
    replaced = "New Deck" in target.read_text(encoding="utf-8")
    return failed and preserved and no_temp and replaced


def _comparison_limit_check():
    return (
        ComparisonCollection(1).maximum == 2
        and ComparisonCollection(100).maximum == MAX_COMPARISON_CARDS)


def _workspace_recovery_failure_check(tmp):
    repository = WorkspaceRepository(tmp / "workspace", recovery_interval=0)
    payload = {"version": WORKSPACE_VERSION, "decks": [], "marker": "saved"}
    repository._write_recovery_snapshot = lambda *_args, **_kwargs: (  # type: ignore[method-assign]
        (_ for _ in ()).throw(OSError("recovery unavailable")))
    result = repository.save(payload, force=True, recovery=True)
    loaded = WorkspaceRepository.read_candidate(repository.session_path)
    return result.wrote_session and not result.wrote_recovery and loaded == payload


def _image_partial_cleanup_check(tmp):
    stale = tmp / "abandoned.123.download"
    stale.write_bytes(b"partial")
    service = CardImageService(tmp, max_workers=1)
    try:
        return not stale.exists()
    finally:
        service.shutdown()


def _release_contract_checks(tmp):
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/build-windows.yml").read_text(encoding="utf-8")
    versions_aligned = (
        pyproject["project"]["requires-python"] == ">=3.11"
        and "Python 3.11+" in build
        and "python-version: '3.14'" in workflow)

    archive = tmp / "membership.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{release.ARCHIVE_ROOT}/one.txt", "one")
    with mock.patch.object(release, "validate_members", return_value=None):
        exact_mismatch_rejected = _raises(
            RuntimeError,
            lambda: release.validate_archive(
                archive, expected_members={Path("one.txt"), Path("two.txt")}))
    stable_root = release.ARCHIVE_ROOT == "MTG_Deck_Builder"
    return versions_aligned, exact_mismatch_rejected, stable_root


def _legality_wording_check():
    source = (ROOT / "mtgdb/ui/deck_stats.py").read_text(encoding="utf-8")
    return (
        "BASIC FORMAT CHECK" in source
        and "BASIC FORMAT CHECKS" not in source
        and 'if problems else "Deck is legal"' not in source
        and "Basic format check passed" in source
        and "No issues found by this basic format check" in source
        and "No problems found by basic checks" not in source
        and "No issues were found by the app's available basic checks" not in source)


def main():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        malformed_jsonl, trailing_json, unterminated_json = _bulk_parser_checks(tmp)
        distinct_snapshot = _bulk_distinct_count_check(tmp)
        (short_download, byte_retry, byte_length_verified,
         existing_file_kept, scheme_guarded) = _network_checks(tmp)
        restricted_playable = _legality_search_check(tmp)
        unknown_content_rejected = _unknown_content_filter_check(tmp)
        atomic_deck = _atomic_deck_save_check(tmp)
        workspace_recovery = _workspace_recovery_failure_check(tmp)
        image_cleanup = _image_partial_cleanup_check(tmp)
        python_versions, exact_archive, stable_archive_root = _release_contract_checks(tmp)

    checks = {
        "malformed JSONL aborts bulk parsing": malformed_jsonl,
        "trailing garbage after JSON arrays is rejected": trailing_json,
        "unterminated JSON arrays are rejected": unterminated_json,
        "replacement threshold counts committed distinct cards": distinct_snapshot,
        "declared HTTP length mismatch aborts and removes partial file": short_download,
        "raw image fetch retries transient transport failures": byte_retry,
        "raw image fetch verifies declared HTTP length": byte_length_verified,
        "a failed download leaves an existing file alone": existing_file_kept,
        "only https URLs are retrieved": scheme_guarded,
        "search invalidation rejects pre-refresh worker results": _search_invalidation_check(),
        "search invalidation re-enables a button disabled by stale work": _search_ui_invalidation_check(),
        "numeric Search fields reject invalid numbers and inverted ranges": _numeric_search_checks(),
        "restricted cards remain playable in format Search and preview": restricted_playable,
        "unknown content-filter vocabulary is rejected": unknown_content_rejected,
        "result selection follows exact printing identity across datasets": _result_identity_check(),
        "deck TXT overwrite is atomic on replacement failure": atomic_deck,
        "comparison maximum is clamped to the seven-card contract": _comparison_limit_check(),
        "primary workspace save survives recovery-snapshot failure": workspace_recovery,
        "image service removes abandoned partial downloads on startup": image_cleanup,
        "Python minimum is consistently 3.11+": python_versions,
        "archive validation rejects non-exact source membership": exact_archive,
        "source archive uses a stable product root": stable_archive_root,
        "legality UI describes its checks without full-rules overclaim": _legality_wording_check(),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nHARDENING REGRESSIONS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
