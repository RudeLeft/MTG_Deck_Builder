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
from mtgdb.ui.card_detail import (
    CardDetailMixin, _CardZoomWindow, _GalleryCardPeekWindow,
    _ResultsGalleryWindow, results_gallery_layout_metrics,
)
from mtgdb.ui.tokens import (
    CARD_ZOOM_WINDOW_MIN_SIZE, RESULT_GALLERY_CARD_MAX_WIDTH,
    RESULT_GALLERY_CARD_MIN_WIDTH, RESULT_GALLERY_CARD_TARGET_WIDTH,
    RESULT_GALLERY_GAP,
    RESULT_GALLERY_MAX_COLUMNS,
    RESULT_GALLERY_MAX_VISIBLE_ROWS,
)


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
    results_source = (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8")
    gallery_source = detail_source.split(
        "class _ResultsGalleryWindow", 1)[1].split("class _CardZoomWindow", 1)[0]
    peek_source = detail_source.split(
        "class _GalleryCardPeekWindow", 1)[1].split(
        "class _ResultsGalleryWindow", 1)[0]
    zoom_source = detail_source.split("class _CardZoomWindow", 1)[1].split(
        "class CardDetailMixin", 1)[0]
    comparison_controls_source = (
        ROOT / "mtgdb/ui/comparison_controls.py").read_text(encoding="utf-8")
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
        """Return the rotation the service resolves when the caller omits one.

        This runs after the enclosing TemporaryDirectory has already been
        cleaned up, so it must not build a cache under `temporary_directory`:
        CardImageService creates its cache directory on construction, which
        silently resurrected the deleted tree and left it behind on every run.
        """
        with tempfile.TemporaryDirectory() as posture_directory:
            probe = CardImageService(
                Path(posture_directory) / f"posture{face_index}",
                max_workers=0, fetch_bytes=lambda _url: b"")
            try:
                probe.request(
                    card, "https://example.invalid/x.jpg", face_index=face_index)
                queued = probe._tasks.get_nowait()
                return queued[2][6]
            finally:
                probe.shutdown()

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
    # IMG-010: the print-template cache is a print_png subdirectory using the
    # same partial convention, so a top-level-only sweep leaks its orphans.
    with tempfile.TemporaryDirectory() as _cache_root:
        _nested = Path(_cache_root) / "print_png"
        _nested.mkdir()
        _top_partial = Path(_cache_root) / "Card [SET].jpg.download"
        _nested_partial = _nested / "Card [SET].png.download"
        _real_asset = _nested / "Card [SET].png"
        for _path in (_top_partial, _nested_partial, _real_asset):
            _path.write_bytes(b"partial")
        _sweep_service = CardImageService(_cache_root)
        try:
            recursive_partial_sweep = (
                not _top_partial.exists()
                and not _nested_partial.exists()
                and _real_asset.exists())
        finally:
            _sweep_service.shutdown()

    # card_detail enables its Flip action on len(card_viewable_faces(card)) > 1.
    # That test is only meaningful because this helper never reports exactly one
    # viewable face: a card either has several separately imaged faces or none.
    # Pin the invariant here so the caller's threshold cannot quietly become
    # equivalent to "any face at all".
    def _faces(count, imaged=True):
        return json.dumps([
            {"name": f"Face {i}",
             "image_uris": {"normal": f"https://example.invalid/{i}.jpg"}
             if imaged else {}}
            for i in range(count)])

    viewable_face_counts = {
        len(card_viewable_faces({"card_faces": _faces(n)})) for n in range(0, 5)
    } | {
        len(card_viewable_faces({})),
        len(card_viewable_faces({"card_faces": _faces(2, imaged=False)})),
        # Split/adventure style: faces exist but share the one card image.
        len(card_viewable_faces({"card_faces": json.dumps(
            [{"name": "Fire"}, {"name": "Ice"}])})),
    }

    checks = {
        "viewable faces are never reported as exactly one": (
            1 not in viewable_face_counts
            and 0 in viewable_face_counts
            and max(viewable_face_counts) >= 2),
        "abandoned partials are swept from nested caches too": (
            recursive_partial_sweep),
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
        "main preview owns Gallery Legality Rotate and Zoom controls": (
            'text="Gallery"' in detail_source
            and 'role="compact_primary"' in detail_source
            and 'text="Legality"' in detail_source
            and 'text="Rotate"' in detail_source
            and 'text="Zoom"' in detail_source
            and "def _open_card_legality(" in detail_source
            and 'text="CARD LEGALITY"' in detail_source
            and "No playable formats listed." in detail_source
            and "self.legal_label" not in detail_source
            and "class _CardZoomWindow" in detail_source
            and "card_image_service.request(" in detail_source
            and "Image.open(" not in detail_source),
        "Results Gallery is dense resizable bounded and follows the current Results view": (
            issubclass(_ResultsGalleryWindow, object)
            and results_gallery_layout_metrics(1920, 1000)["slot_count"]
                > results_gallery_layout_metrics(1240, 860)["slot_count"]
            and results_gallery_layout_metrics(5000, 5000)["slot_count"]
                <= RESULT_GALLERY_MAX_COLUMNS * RESULT_GALLERY_MAX_VISIBLE_ROWS
            and results_gallery_layout_metrics(1240, 860, 320)["slot_count"]
                < results_gallery_layout_metrics(1240, 860, 140)["slot_count"]
            # Card art is shown at the slider's exact per-pixel width (continuous
            # scaling, live-resampled while dragging), clamped to the min/max art
            # size; the column count is how many fit and the grid is centred so
            # the leftover is a small balanced margin, never one wide gutter.
            and RESULT_GALLERY_CARD_MIN_WIDTH
                <= results_gallery_layout_metrics(1240, 860, 320)["image_w"]
                <= RESULT_GALLERY_CARD_MAX_WIDTH
            # Card width tracks the slider per pixel: distinct positions give
            # distinct sizes (not a handful of banded sizes), the width never
            # exceeds the requested target, and it never decreases as the target
            # grows.
            and len({results_gallery_layout_metrics(1240, 860, t)["image_w"]
                     for t in range(160, 341, 20)}) >= 8
            and all(results_gallery_layout_metrics(1240, 860, t)["image_w"] <= t
                    for t in range(160, 341, 20))
            and all(results_gallery_layout_metrics(1240, 860, t + 20)["image_w"]
                    >= results_gallery_layout_metrics(1240, 860, t)["image_w"]
                    for t in range(160, 341, 20))
            and results_gallery_layout_metrics(1240, 860, 360)["image_w"]
                >= results_gallery_layout_metrics(1240, 860, 140)["image_w"]
            and results_gallery_layout_metrics(1240, 860, 1)["image_w"]
                >= RESULT_GALLERY_CARD_MIN_WIDTH
            and results_gallery_layout_metrics(1240, 860, 9999)["image_w"]
                <= RESULT_GALLERY_CARD_MAX_WIDTH
            # Cards still use the width: the centred remainder is under one card,
            # so the row is not padded into wide gutters with a few big cards.
            and (results_gallery_layout_metrics(1240, 860, 320)["columns"]
                 * results_gallery_layout_metrics(1240, 860, 320)["image_w"]
                 >= 1240 - RESULT_GALLERY_CARD_MAX_WIDTH)
            and results_gallery_layout_metrics(1240, 860, 320)["margin_x"] >= 0
            and 2 * results_gallery_layout_metrics(1240, 860, 320)["margin_x"]
                < RESULT_GALLERY_CARD_MAX_WIDTH + RESULT_GALLERY_GAP
            # Dragging the slider rescales the images already held rather than
            # requesting a fresh size per pixel, and settles to crisp art on
            # release; the source images are kept for that in-memory resample.
            and 'from PIL import Image, ImageTk' in detail_source
            and 'def _live_rescale_visible(' in gallery_source
            and 'def _settle_card_size(' in gallery_source
            and 'def _resettle_crisp(' in gallery_source
            and 'Image.BILINEAR' in gallery_source
            and 'self._scale_dragging' in gallery_source
            and 'self._pil_by_card' in gallery_source
            and 'x = margin_x + column * (image_w + gap)' in gallery_source
            and 'transient=False, resizable=True' in gallery_source
            and 'self.top.resizable(True, True)' in gallery_source
            and 'text="Card Size"' in gallery_source
            and 'self.card_size_value' not in gallery_source
            and RESULT_GALLERY_CARD_TARGET_WIDTH == 280
            and 'ttk.Scale(' in gallery_source
            and 'style="Gallery.Horizontal.TScale"' in gallery_source
            and 'self.card_size_scale = tk.Scale(' not in gallery_source
            and 'RESULTS GALLERY | {count:,} CARDS' in gallery_source
            and 'self._scroll_y = 0.0' in gallery_source
            and 'self._top_row' not in gallery_source
            and 'slot["cell"].place(' in gallery_source
            and 'row_offset = int(round(self._scroll_y - first_row * stride))' in gallery_source
            and 'pixels = amount * max(28, min(72, self._layout["image_h"] // 5))' in gallery_source
            # Scrolling rebinds only the rows that enter the viewport: a wrapped
            # slot map plus a diff-based bind reposition already-shown cards
            # instead of re-rendering (and re-requesting) the whole grid.
            and 'def _slot_for_position(' in gallery_source
            and 'def _bind_visible(' in gallery_source
            and 'slot["cell"].place_configure(x=x, y=y)' in gallery_source
            and 'def _scroll_to_y(' in gallery_source
            and 'name_label = tk.Label(' not in gallery_source
            and 'channel=f"results-gallery-slot-{slot}"' in gallery_source
            and '"<Button-1>"' in gallery_source
            and '"<Button-3>"' in gallery_source
            and issubclass(_GalleryCardPeekWindow, object)
            and 'channel="results-gallery-peek"' in detail_source
            and "self._result_store.visible_count" in results_source
            and "source_index_at_view(position)" in results_source
            and "gallery.refresh_results()" in results_source
            and "context_menu_fn=self._show_gallery_card_context_menu" in results_source
            and 'label="Add to Mainboard"' in comparison_controls_source
            and 'label="Add to Sideboard"' in comparison_controls_source),
        "Gallery peek can flip multi-faced cards and rotate art (SRCH-046)": (
            # The enlarged peek offers Rotate always and Flip only when the card
            # has more than one separately imaged face, and re-requests art at
            # the new face/rotation.
            'text="Rotate"' in peek_source
            and 'text="Flip"' in peek_source
            and "def _rotate(" in peek_source
            and "def _flip(" in peek_source
            and "card_viewable_faces(self.card)" in peek_source
            and "card_display_rotation_degrees(" in peek_source
            and "self._rotation_turns = (self._rotation_turns + 1) % 4" in peek_source
            # Focus-out dismiss (SRCH-046) is preserved.
            and 'self.top.bind("<FocusOut>"' in peek_source),
        "Gallery resolves any available image and retries transient failures": (
            # A card with only an art crop or a single face image still shows,
            # and a dropped download self-heals with a one-shot retry rather
            # than sticking on "Image unavailable".
            "def _gallery_face_image_url(" in detail_source
            and 'card.get("image_art_crop")' in detail_source
            and "_gallery_face_image_url(card, 0)" in gallery_source
            and "def _retry_gallery_image(" in gallery_source
            and "self._image_retries" in gallery_source),
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
            and zoom_source.index('text="Close"')
            < zoom_source.index("self.title_label = ttk.Label(")
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
