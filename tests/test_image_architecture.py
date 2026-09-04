"""Shared card-image service and card-detail ownership contracts."""

import ast
import json
from io import BytesIO
import tempfile
import threading
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

from mtgdb.images.service import (
    CardImageService,
    card_display_rotation_degrees,
    card_face_image_url,
    card_needs_rotation,
    card_viewable_faces,
)
from mtgdb.ui.card_detail import CardDetailMixin, _CardZoomWindow
from mtgdb.ui.tokens import CARD_ZOOM_WINDOW_MIN_SIZE


def _jpeg_bytes():
    buffer = BytesIO()
    Image.new("RGB", (320, 448), "#7b542f").save(buffer, format="JPEG")
    return buffer.getvalue()


def _class_methods(source, class_name):
    tree = ast.parse(source)
    target = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {
        node.name for node in target.body if isinstance(node, ast.FunctionDef)
    }, target


def main():
    gui_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
    service_source = (
        ROOT / "mtgdb/images/service.py").read_text(encoding="utf-8")
    detail_source = (ROOT / "mtgdb/ui/card_detail.py").read_text(encoding="utf-8")
    comparison_source = (
        ROOT / "mtgdb/ui/comparison.py").read_text(encoding="utf-8")
    gui_methods, gui_class = _class_methods(gui_source, "DeckBuilderApp")
    detail_methods, _detail_class = _class_methods(
        detail_source, "CardDetailMixin")

    image_bytes = _jpeg_bytes()
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_count = {"value": 0}

    def fetch_bytes(_url):
        fetch_count["value"] += 1
        fetch_started.set()
        release_fetch.wait(2.0)
        return image_bytes

    card = {
        "id": "printing-1", "name": "Test Battle", "set_code": "tst",
        "collector_number": "1", "layout": "transform",
        "type_line": "Battle — Siege", "mana_cost": "{2}{R}",
        "power": None, "toughness": None, "oracle_text": "Test rules",
        "set_name": "Test Set", "legalities": '{"modern":"legal"}',
    }

    with tempfile.TemporaryDirectory() as temporary_directory:
        service = CardImageService(
            temporary_directory, max_workers=2, memory_limit=3,
            fetch_bytes=fetch_bytes)
        first = service.request(
            card, "https://example.invalid/card.jpg", target_size=(90, 120))
        fetch_started.wait(1.0)
        duplicate = service.request(
            card, "https://example.invalid/card.jpg", target_size=(90, 120))
        same_future = first is duplicate
        release_fetch.set()
        first_image = first.result(timeout=2.0)

        cached = service.request(
            card, "https://example.invalid/card.jpg", target_size=(90, 120))
        cached_image = cached.result(timeout=0.2)
        resized = service.request(
            card, "https://example.invalid/card.jpg", target_size=(60, 80))
        resized_image = resized.result(timeout=2.0)
        stats = service.cache_info()
        cache_files = list(Path(temporary_directory).glob("*.jpg"))
        temporary_files = list(Path(temporary_directory).glob("*.download"))
        service.shutdown()
        try:
            service.request(card, "https://example.invalid/card.jpg")
            rejected_after_shutdown = False
        except RuntimeError:
            rejected_after_shutdown = True
        capped_service = CardImageService(
            temporary_directory, max_workers=20, fetch_bytes=fetch_bytes)
        capped_workers = capped_service.cache_info()["workers"]
        capped_service.shutdown()

        interaction_directory = Path(temporary_directory) / "interaction"
        interaction_service = CardImageService(
            interaction_directory, max_workers=1,
            fetch_bytes=lambda _url: image_bytes)
        plain_card = {
            "id": "plain-printing", "name": "Plain Card",
            "layout": "normal", "type_line": "Creature — Test",
        }
        rotated_image = interaction_service.request(
            plain_card, "https://example.invalid/plain.jpg",
            target_size=(448, 320), rotation_degrees=90).result(timeout=2.0)
        upscaled_image = interaction_service.request(
            plain_card, "https://example.invalid/plain.jpg",
            target_size=(640, 896), rotation_degrees=0,
            allow_upscale=True).result(timeout=2.0)
        invalid_rotation_rejected = False
        try:
            interaction_service.request(
                plain_card, "https://example.invalid/plain.jpg",
                rotation_degrees=45)
        except ValueError:
            invalid_rotation_rejected = True
        interaction_service.shutdown()

        # IMG-010: a request that has already passed the ``_closed`` check must
        # not be able to publish its task after shutdown drains the queue.
        # ``request`` therefore enqueues while still holding the lock that
        # guards ``_closed``; stalling the put here proves the ordering.
        race_directory = Path(temporary_directory) / "shutdown_race"
        race_service = CardImageService(
            race_directory, max_workers=1,
            fetch_bytes=lambda _url: image_bytes)
        race_card = {
            "id": "race-printing", "name": "Race Card",
            "layout": "normal", "type_line": "Creature — Test",
        }
        entered_put = threading.Event()
        allow_put = threading.Event()
        original_put = race_service._tasks.put

        def stalled_put(item):
            entered_put.set()
            allow_put.wait(2.0)
            return original_put(item)

        race_service._tasks.put = stalled_put
        race_outcome = {}

        def race_request():
            try:
                race_outcome["future"] = race_service.request(
                    race_card, "https://example.invalid/race.jpg")
            except RuntimeError:
                race_outcome["rejected"] = True

        racer = threading.Thread(target=race_request, daemon=True)
        racer.start()
        entered_put.wait(2.0)
        race_service._tasks.put = original_put

        shutdown_thread = threading.Thread(
            target=race_service.shutdown, daemon=True)
        shutdown_thread.start()
        # shutdown() must block on the lock the stalled request still holds.
        shutdown_blocked = not shutdown_thread.join(0.25) and shutdown_thread.is_alive()
        allow_put.set()
        racer.join(2.0)
        shutdown_thread.join(2.0)
        race_future = race_outcome.get("future")
        no_orphaned_task = race_service._tasks.qsize() == 0
        race_future_settled = (
            race_outcome.get("rejected", False)
            or (race_future is not None
                and (race_future.cancelled() or race_future.done())))

    # Flippable faces are detected from per-face image_uris rather than a
    # layout allowlist, so split/adventure halves (one shared picture) stay
    # unflippable while any two-imaged layout works without a code change.
    transform_card = {
        "layout": "transform",
        "type_line": "Battle — Siege // Creature — Phyrexian Praetor",
        "card_faces": json.dumps([
            {"name": "Front", "type_line": "Battle — Siege",
             "image_uris": {"normal": "https://example.invalid/front.jpg"}},
            {"name": "Back", "type_line": "Creature — Phyrexian Praetor",
             "image_uris": {"normal": "https://example.invalid/back.jpg"}},
        ]),
    }
    split_card = {
        "layout": "split", "type_line": "Instant // Instant",
        "image_normal": "https://example.invalid/split.jpg",
        "card_faces": json.dumps([{"name": "Fire"}, {"name": "Ice"}]),
    }
    def _auto_rotation(card, face_index):
        """Return the rotation the service resolves when the caller omits one."""
        probe = CardImageService(
            Path(temporary_directory) / f"posture{face_index}",
            max_workers=0, fetch_bytes=lambda _url: b"")
        probe.request(card, "https://example.invalid/x.jpg", face_index=face_index)
        queued = probe._tasks.get_nowait()
        return queued[2][6]

    def _zoom_title(text, available):
        """Fit a title with a deterministic 10px-per-character stand-in font."""
        return _CardZoomWindow._shorten_to_width(
            text, lambda value: len(value) * 10, available)

    transform_faces = card_viewable_faces(transform_card)
    split_faces = card_viewable_faces(split_card)

    class DetailOwner:
        _format_catalog = ["modern", "future_format"]

    summary = CardDetailMixin._card_summary_text(card)
    legality = CardDetailMixin._legal_summary(DetailOwner(), card)
    extracted = {
        "_build_card_pane", "_show_card",
        "_rotate_card_preview", "_open_card_zoom", "_sync_preview_zoom",
        "_card_summary_text", "_legal_summary", "_start_image_event_pump",
        "_poll_image_events", "_cancel_image_ready_retry",
        "_defer_image_ready", "_image_ready", "_image_failed",
    }
    bases = {
        base.id for base in gui_class.bases if isinstance(base, ast.Name)
    }
    checks = {
        "image service uses fixed daemon concurrency": (
            stats["workers"] == 2 and capped_workers == 4
            and 'spawn_daemon(' in service_source
            and 'from mtgdb.core.background_jobs import' in service_source
            and "ThreadPoolExecutor" not in service_source),
        "identical inflight requests are deduplicated": (
            same_future and stats["deduplicated"] == 1),
        "memory cache avoids repeated decode and download work": (
            cached.done() and stats["memory_hits"] == 1
            and stats["downloads"] == 1 and fetch_count["value"] == 1),
        "different display sizes reuse one decoded source and disk download": (
            stats["decoded"] == 1 and stats["source_hits"] >= 1
            and first_image.width <= 90 and first_image.height <= 120
            and cached_image.size == first_image.size
            and resized_image.width <= 60 and resized_image.height <= 80),
        "validated cache replacement leaves no partial image": (
            len(cache_files) == 1 and not temporary_files),
        "service rejects requests after shutdown": rejected_after_shutdown,
        "rotation policy preserves planes and battles": (
            card_needs_rotation({"layout": "planar", "type_line": "Plane"})
            and card_needs_rotation(card)
            and not card_needs_rotation({"layout": "normal", "type_line": "Scheme"})),
        "manual quarter-turn rotation composes with automatic posture": (
            card_display_rotation_degrees(plain_card, 0) == 0
            and card_display_rotation_degrees(plain_card, 1) == 270
            and card_display_rotation_degrees(card, 0) == 270
            and card_display_rotation_degrees(card, 1) == 180
            and rotated_image.size == (448, 320)
            and invalid_rotation_rejected),
        "zoom requests may upscale while ordinary previews stay bounded": (
            upscaled_image.size == (640, 896)
            and "allow_upscale=True" in detail_source),
        "card text and legality fallbacks are preserved": (
            "Test Battle   {2}{R}" in summary
            and "Test rules" in summary
            and legality == "Modern"),
        "image service is Tk-free": "tkinter" not in service_source,
        "card-detail UI performs no direct network or cache file work": all(
            forbidden not in detail_source for forbidden in (
                "import mtgdb.core.net", "import mtgdb.core.cache_names", "Image.open(",
                "threading.Thread", "open(temporary")),
        "comparison uses the shared service exclusively": (
            "self.app.card_image_service.request(" in comparison_source
            and all(forbidden not in comparison_source for forbidden in (
                "import mtgdb.core.net", "import mtgdb.core.cache_names", "threading.Thread",
                "Image.open(", "_load_image_pil"))),
        "comparison reuses unchanged PhotoImages": (
            "cached_photo = self._photos.get(photo_key)" in comparison_source
            and "self._photos.clear()" not in comparison_source),
        "stale main-preview futures retain their request token": (
            "self._preview_future_token = token" in detail_source
            and "token = self._preview_future_token" in detail_source),
        "main preview uses latest-wins priority channel": (
            'channel="main-preview"' in detail_source
            and "queue.PriorityQueue()" in service_source
            and "_channel_requests" in service_source
            and "_request_consumers" in service_source
            and '"superseded"' in service_source),
        "deferred preview readiness has an independent scheduling slot": (
            "self._image_ready_after = None" in detail_source
            and "def _defer_image_ready(" in detail_source
            and "self._image_poll_after = self.after(" not in detail_source.split(
                "def _image_ready", 1)[1]
            and '"_image_ready_after"' in gui_source),
        "main preview owns Legality Rotate and Zoom controls with dark popups": (
            'text="Legality"' in detail_source
            and 'text="Rotate"' in detail_source
            and 'text="Zoom"' in detail_source
            and "def _open_card_legality(" in detail_source
            and 'text="CARD LEGALITY"' in detail_source
            and "No playable formats listed." in detail_source
            and "self.legal_label" not in detail_source
            and "class _CardZoomWindow" in detail_source
            and "card_image_service.request(" in detail_source
            and "Image.open(" not in detail_source),
        "separately imaged faces are detected without a layout allowlist": (
            [index for index, _name, _url in transform_faces] == [0, 1]
            and split_faces == []
            and card_face_image_url(transform_card, 1)
            == "https://example.invalid/back.jpg"
            and card_face_image_url(split_card, 1) is None),
        "posture is resolved per face so battle backs stay portrait": (
            card_display_rotation_degrees(transform_card, 0, face_index=0) == 270
            and card_display_rotation_degrees(
                transform_card, 0, face_index=1) == 0
            and card_display_rotation_degrees(
                transform_card, 1, face_index=1) == 270),
        "main preview owns a face flip that keys its own cache entry": (
            "def _flip_card_preview(" in detail_source
            and "card_viewable_faces(" in detail_source
            and "face_index=face_index" in detail_source
            and "self._preview_face_index" in detail_source),
        "automatic posture follows the requested face, not the card": (
            _auto_rotation(transform_card, 0) == 270
            and _auto_rotation(transform_card, 1) == 0),
        "comparison and sample hand share one face-keyed flip control": (
            "def _flip_comparison_face(" in comparison_source
            and "face_index=face_index" in comparison_source
            # Both the queue-time cache probe and the poll-time store must key
            # on the face, or the two faces of one printing collide.
            and comparison_source.count("(cid, target_size, face_index)") == 2
            # The control is built before the live-comparison guard so the
            # read-only sample hand gets it too.
            and comparison_source.index('text="Flip"')
            < comparison_source.rindex("if self._static_cards is None:")),
        "zoom offers a face flip that stays in step with the main preview": (
            "def _flip(self):" in detail_source
            and "self.flip_btn" in detail_source
            and "self.owner._preview_face_index = self._face_index"
            in detail_source),
        "zoom header keeps its controls ahead of a long card name": (
            _zoom_title("TEST BOLT", 400) == "TEST BOLT"
            # A double-faced name breaks at the face separator, never mid-word.
            and _zoom_title("FRONT NAME // BACK NAME", 140) == "FRONT NAME //…"
            # Too narrow even for the front face: fall back to a clean cut.
            and _zoom_title("FRONT NAME // BACK NAME", 130) == "FRONT NAME /…"
            and _zoom_title("AAAAAAAAAAAAAAAAAAAAAAAAAAAA", 100).endswith("…")
            and len(_zoom_title("AAAAAAAAAAAAAAAAAAAAAAAAAAAA", 100)) < 28
            # Controls are packed before the title so Tk squeezes the title.
            and detail_source.index('text="Close"')
            < detail_source.index("self.title_label = tk.Label(")
            and CARD_ZOOM_WINDOW_MIN_SIZE[0] >= 720),
        "shutdown leaves no task queued by an in-flight request": (
            shutdown_blocked and no_orphaned_task and race_future_settled),
        "DeckBuilderApp delegates card-detail ownership": (
            "CardDetailMixin" in bases
            and extracted <= detail_methods
            and not extracted & gui_methods
            and "self.card_image_service.shutdown()" in gui_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nIMAGE ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
