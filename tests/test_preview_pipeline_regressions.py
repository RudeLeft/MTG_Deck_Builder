"""Regressions for main-preview polling and latest-selection image scheduling."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

import mtgdb.ui.card_detail as card_detail
import mtgdb.ui.comparison as comparison
from mtgdb.images.service import CardImageService
from mtgdb.ui.card_detail import CardDetailMixin
from mtgdb.ui.comparison import CardComparisonWindow


def _jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (160, 224), "#654321").save(buffer, format="JPEG")
    return buffer.getvalue()


def _card(index):
    return {
        "id": f"printing-{index}",
        "name": f"Card {index}",
        "set_code": "tst",
        "collector_number": str(index),
        "layout": "normal",
        "type_line": "Creature — Test",
    }


class _FakeLabel:
    def __init__(self):
        self.last = {}

    def configure(self, **kwargs):
        self.last.update(kwargs)


class _FakeDetail(CardDetailMixin):
    def __init__(self, *, motion=False, state="normal"):
        self._image_token = 7
        self._image_ready_after = None
        self._image_poll_after = None
        self._window_in_motion = motion
        self._state = state
        self._preview_loading = True
        self._img_ref = None
        self.card_image = _FakeLabel()
        self._scheduled = {}
        self._next_after = 0

    def after(self, _delay, callback):
        self._next_after += 1
        after_id = f"after-{self._next_after}"
        self._scheduled[after_id] = callback
        return after_id

    def after_cancel(self, after_id):
        self._scheduled.pop(after_id, None)

    def state(self):
        return self._state


def _deferred_ready_check(*, motion, state):
    detail = _FakeDetail(motion=motion, state=state)
    original_image_tk = card_detail.ImageTk
    card_detail.ImageTk = SimpleNamespace(PhotoImage=lambda image: ("photo", image))
    try:
        detail._image_ready(7, "decoded-image")
        ready_id = detail._image_ready_after
        deferred = (
            ready_id is not None
            and ready_id in detail._scheduled
            and detail._image_poll_after is None
        )

        # The ready retry must not occupy the event-pump scheduling slot.
        detail._start_image_event_pump()
        independent_poll = (
            detail._image_poll_after is not None
            and detail._image_poll_after != ready_id
        )

        detail._window_in_motion = False
        detail._state = "normal"
        resume = detail._scheduled.pop(ready_id)
        resume()
        resumed_cleanly = (
            detail._image_ready_after is None
            and detail._img_ref == ("photo", "decoded-image")
            and not detail._preview_loading
            and detail.card_image.last.get("image") == (
                "photo", "decoded-image")
        )
        return deferred and independent_poll and resumed_cleanly
    finally:
        card_detail.ImageTk = original_image_tk




class _DoneFuture:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def done(self):
        return True

    def result(self):
        self.calls += 1
        return self.value


def _duplicate_grid_images_share_retained_photo():
    window = CardComparisonWindow.__new__(CardComparisonWindow)
    future = _DoneFuture("decoded-image")
    first = _FakeLabel()
    second = _FakeLabel()
    size = (340, 480)
    window._image_after = None
    window._image_generation = 4
    # Requests carry the displayed face so the two faces of one printing keep
    # separate retained PhotoImages.
    window._image_requests = {
        "hand-0": ("same-printing", 4, size, 0, future),
        "hand-1": ("same-printing", 4, size, 0, future),
    }
    window._image_labels = {"hand-0": first, "hand-1": second}
    window._photos = {}

    original_image_tk = comparison.ImageTk
    created = []
    comparison.ImageTk = SimpleNamespace(
        PhotoImage=lambda image: created.append(("photo", image)) or created[-1])
    try:
        window._poll_images()
        photo = window._photos.get(("same-printing", size, 0))
        return (
            len(created) == 1
            and photo is not None
            and first.last.get("image") is photo
            and second.last.get("image") is photo
            and not window._image_requests
        )
    finally:
        comparison.ImageTk = original_image_tk


def _latest_preview_supersedes_queued_work(tmp):
    image_bytes = _jpeg_bytes()
    started = threading.Event()
    release = threading.Event()
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/1.jpg"):
            started.set()
            release.wait(2.0)
        return image_bytes

    service = CardImageService(tmp / "supersede", max_workers=1, fetch_bytes=fetch)
    try:
        first = service.request(
            _card(1), "https://example.invalid/1.jpg",
            channel="main-preview")
        if not started.wait(1.0):
            return False
        second = service.request(
            _card(2), "https://example.invalid/2.jpg",
            channel="main-preview")
        third = service.request(
            _card(3), "https://example.invalid/3.jpg",
            channel="main-preview")
        newest = service.request(
            _card(4), "https://example.invalid/4.jpg",
            channel="main-preview")

        queued_cancelled = second.cancelled() and third.cancelled()
        release.set()
        first.result(timeout=2.0)
        newest.result(timeout=2.0)
        stats = service.cache_info()
        return (
            queued_cancelled
            and fetched == [
                "https://example.invalid/1.jpg",
                "https://example.invalid/4.jpg",
            ]
            and stats["superseded"] >= 2
        )
    finally:
        release.set()
        service.shutdown()


def _shared_dedup_protects_nonpreview_consumer(tmp):
    image_bytes = _jpeg_bytes()
    started = threading.Event()
    release = threading.Event()
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/1.jpg"):
            started.set()
            release.wait(2.0)
        return image_bytes

    service = CardImageService(tmp / "shared", max_workers=1, fetch_bytes=fetch)
    try:
        first = service.request(
            _card(1), "https://example.invalid/1.jpg",
            channel="main-preview")
        if not started.wait(1.0):
            return False
        shared_preview = service.request(
            _card(2), "https://example.invalid/2.jpg",
            channel="main-preview")
        shared_other = service.request(
            _card(2), "https://example.invalid/2.jpg")
        newest = service.request(
            _card(3), "https://example.invalid/3.jpg",
            channel="main-preview")
        protected = shared_preview is shared_other and not shared_preview.cancelled()
        release.set()
        first.result(timeout=2.0)
        shared_other.result(timeout=2.0)
        newest.result(timeout=2.0)
        return protected and fetched == [
            "https://example.invalid/1.jpg",
            "https://example.invalid/2.jpg",
            "https://example.invalid/3.jpg",
        ]
    finally:
        release.set()
        service.shutdown()


def _preview_priority_beats_background_queue(tmp):
    image_bytes = _jpeg_bytes()
    started = threading.Event()
    release = threading.Event()
    fetched = []

    def fetch(url):
        fetched.append(url)
        if url.endswith("/1.jpg"):
            started.set()
            release.wait(2.0)
        return image_bytes

    service = CardImageService(tmp / "priority", max_workers=1, fetch_bytes=fetch)
    try:
        blocker = service.request(_card(1), "https://example.invalid/1.jpg")
        if not started.wait(1.0):
            return False
        background = service.request(_card(2), "https://example.invalid/2.jpg")
        preview = service.request(
            _card(3), "https://example.invalid/3.jpg",
            channel="main-preview")
        release.set()
        blocker.result(timeout=2.0)
        preview.result(timeout=2.0)
        background.result(timeout=2.0)
        return fetched == [
            "https://example.invalid/1.jpg",
            "https://example.invalid/3.jpg",
            "https://example.invalid/2.jpg",
        ]
    finally:
        release.set()
        service.shutdown()


def main():
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        checks = {
            "ready image deferred during window motion cannot poison polling": (
                _deferred_ready_check(motion=True, state="normal")),
            "ready image deferred while minimized cannot poison polling": (
                _deferred_ready_check(motion=False, state="iconic")),
            "rapid preview selection cancels obsolete queued image work": (
                _latest_preview_supersedes_queued_work(tmp)),
            "deduplicated comparison or zoom users protect shared image work": (
                _shared_dedup_protects_nonpreview_consumer(tmp)),
            "main preview work is prioritized ahead of background image queue": (
                _preview_priority_beats_background_queue(tmp)),
            "duplicate sample-hand cards share one retained Tk photo": (
                _duplicate_grid_images_share_retained_photo()),
        }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nPREVIEW PIPELINE REGRESSIONS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
