"""Tk-free, bounded card-image download, cache, and decoding service."""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future
import logging
import os
import queue
import threading

import mtgdb.core.cache_names as cache_names

import mtgdb.core.net as net
from mtgdb.core.background_jobs import spawn_daemon
from mtgdb.core.scryfall_json import card_faces as scryfall_card_faces


try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    Image = None
    HAVE_PIL = False


log = logging.getLogger("mtg")

ROTATE_LAYOUTS = frozenset({"planar"})
ROTATE_DEGREES = -90
PROCESSED_CACHE_BUDGET = 96 * 1024 * 1024
SOURCE_CACHE_BUDGET = 32 * 1024 * 1024
PROCESSED_CACHE_COUNT_LIMIT = 256
SOURCE_CACHE_COUNT_LIMIT = 32


def card_needs_rotation(card):
    """Return whether a card image must be rotated into its displayed posture."""
    if (card.get("layout") or "") in ROTATE_LAYOUTS:
        return True
    return "Battle" in (card.get("type_line") or "")


def _face_image_url(face):
    """Return one face's own image URL, or None when it shares the card image."""
    uris = face.get("image_uris") if isinstance(face, dict) else None
    if not isinstance(uris, dict):
        return None
    return uris.get("normal") or uris.get("small") or uris.get("png") or None


def card_viewable_faces(card):
    """Return ``[(index, name, url)]`` for faces that own a distinct image.

    Detection is data-driven rather than a layout allowlist: Scryfall places
    ``image_uris`` on each face only when the faces are physically separate
    pictures. Split, flip, and adventure cards therefore report no viewable
    faces because their halves share one card image, while any present or
    future two-imaged layout works without a code change. Fewer than two
    imaged faces means the card cannot be flipped.
    """
    faces = []
    for index, face in enumerate(scryfall_card_faces(card)):
        url = _face_image_url(face)
        if url:
            faces.append((index, str(face.get("name") or ""), url))
    return faces if len(faces) > 1 else []


def card_face_image_url(card, face_index=0):
    """Return the image URL for one face, or None when the face has no own image."""
    for index, _name, url in card_viewable_faces(card):
        if index == int(face_index):
            return url
    return None


def _face_posture_card(card, face_index):
    """Return the card mapping whose type line governs one face's posture.

    Battles are double-faced with a landscape front and a portrait back, so the
    joined card-level type line cannot decide posture once a back face is
    viewable. Layout stays card-level because layouts such as ``planar`` rotate
    regardless of face.
    """
    faces = scryfall_card_faces(card)
    index = int(face_index)
    if 0 <= index < len(faces) and faces[index].get("type_line"):
        return {
            "layout": card.get("layout"),
            "type_line": faces[index].get("type_line"),
        }
    return card


def card_default_rotation_degrees(card, face_index=0):
    """Return the automatic display rotation for one face in quarter-turn degrees."""
    posture = _face_posture_card(card, face_index)
    return ROTATE_DEGREES % 360 if card_needs_rotation(posture) else 0


def card_display_rotation_degrees(card, quarter_turns=0, face_index=0):
    """Compose automatic posture with clockwise user-requested quarter turns."""
    turns = int(quarter_turns) % 4
    base = card_default_rotation_degrees(card, face_index)
    return (base + (turns * ROTATE_DEGREES)) % 360


class CardImageService:
    """Share image work across previews with fixed daemon concurrency.

    The service returns standard ``Future`` objects. Consumers poll those
    futures from Tk; workers never import or invoke Tk APIs.
    """

    _STOP = object()

    def __init__(
            self, cache_dir, *, max_workers=4, memory_limit=None,
            processed_budget=PROCESSED_CACHE_BUDGET,
            source_budget=SOURCE_CACHE_BUDGET,
            processed_count_limit=PROCESSED_CACHE_COUNT_LIMIT,
            source_count_limit=SOURCE_CACHE_COUNT_LIMIT, fetch_bytes=None):
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)
        self.available = HAVE_PIL
        self.worker_count = min(4, max(1, int(max_workers)))
        # ``memory_limit`` remains a compatibility count override only; bytes are
        # always the authoritative memory ceiling.
        if memory_limit is not None:
            processed_count_limit = memory_limit
        self.processed_budget = max(1, int(processed_budget))
        self.source_budget = max(1, int(source_budget))
        self.processed_count_limit = max(1, int(processed_count_limit))
        self.source_count_limit = max(1, int(source_count_limit))
        self._fetch_bytes = fetch_bytes or net.fetch_bytes
        self._tasks = queue.PriorityQueue()
        self._lock = threading.RLock()
        self._inflight = {}
        self._request_consumers = {}
        self._channel_requests = {}
        self._task_sequence = 0
        self._processed_cache = OrderedDict()
        self._source_cache = OrderedDict()
        self._processed_cache_bytes = 0
        self._source_cache_bytes = 0
        self._path_locks = tuple(threading.Lock() for _ in range(32))
        self._closed = False
        self._remove_stale_downloads()
        self._stats = {
            "requests": 0, "memory_hits": 0, "deduplicated": 0,
            "superseded": 0, "downloads": 0, "decoded": 0,
            "processed_hits": 0, "processed_misses": 0,
            "processed_evictions": 0, "processed_oversized": 0,
            "source_hits": 0, "source_misses": 0,
            "source_evictions": 0, "source_oversized": 0,
        }
        self._workers = []
        if self.available:
            for index in range(self.worker_count):
                worker = spawn_daemon(
                    self._worker_loop, f"card-image-{index + 1}")
                self._workers.append(worker)

    def request(self, card, url, *, face_index=0, target_size=(340, 480),
                rotate=None, rotation_degrees=None, allow_upscale=False,
                channel=None):
        """Return a future for one decoded and display-sized PIL image.

        ``rotation_degrees`` is limited to quarter turns. When omitted, the
        historical ``rotate`` flag and automatic card-posture policy remain
        compatible. ``allow_upscale`` is reserved for interactive zoom views.
        A named ``channel`` is latest-wins: newer requests supersede queued work
        used only by that channel while preserving deduplicated unscoped users.
        """
        if not self.available:
            future = Future()
            future.set_exception(RuntimeError("Pillow is unavailable"))
            return future
        face_index = max(0, int(face_index))
        target_size = tuple(max(1, int(value)) for value in target_size)
        if rotation_degrees is None:
            if rotate is None:
                # Posture is per-face: a battle's landscape front and portrait
                # back share one card-level type line, so the requested face
                # must decide the automatic rotation.
                rotation_degrees = card_default_rotation_degrees(
                    card, face_index)
            else:
                rotation_degrees = ROTATE_DEGREES % 360 if bool(rotate) else 0
        rotation_degrees = int(rotation_degrees) % 360
        if rotation_degrees % 90:
            raise ValueError("Card image rotation must be a multiple of 90 degrees")
        allow_upscale = bool(allow_upscale)
        identity = str(
            card.get("id") or card.get("oracle_id") or card.get("name") or "card")
        request_key = (
            identity, str(url or ""), face_index, target_size,
            rotation_degrees, allow_upscale)

        channel = str(channel) if channel is not None else None
        cached = None
        with self._lock:
            if self._closed:
                raise RuntimeError("Card image service is closed")
            self._stats["requests"] += 1
            if channel is not None:
                previous_key = self._channel_requests.get(channel)
                if previous_key is not None and previous_key != request_key:
                    self._detach_channel_locked(channel, previous_key)
                self._channel_requests[channel] = request_key

            cached_entry = self._processed_cache.get(request_key)
            if cached_entry is not None:
                cached, _weight = cached_entry
                self._stats["memory_hits"] += 1
                self._stats["processed_hits"] += 1
                self._processed_cache.move_to_end(request_key)
            else:
                self._stats["processed_misses"] += 1

                existing = self._inflight.get(request_key)
                if existing is not None and existing.cancelled():
                    self._inflight.pop(request_key, None)
                    self._request_consumers.pop(request_key, None)
                    existing = None
                if existing is not None:
                    consumer = self._request_consumers.setdefault(
                        request_key, {
                            "channels": set(), "unscoped": False,
                            "future": existing,
                        })
                    if channel is None:
                        consumer["unscoped"] = True
                    else:
                        consumer["channels"].add(channel)
                    self._stats["deduplicated"] += 1
                    return existing

                future = Future()
                self._inflight[request_key] = future
                consumer = {
                    "channels": set(), "unscoped": channel is None,
                    "future": future,
                }
                if channel is not None:
                    consumer["channels"].add(channel)
                self._request_consumers[request_key] = consumer
                task = (
                    request_key, future, dict(card), str(url or ""), face_index,
                    target_size, rotation_degrees, allow_upscale)
                priority = 0 if channel is not None else 10
                self._task_sequence += 1
                # Enqueue while still holding the lock that guards ``_closed``.
                # Publishing outside it allowed a request that had already
                # passed the closed check to land after shutdown drained the
                # queue, leaving an uncancelled future and a task that sorts
                # ahead of the STOP sentinels.
                self._tasks.put((priority, self._task_sequence, task))

        if cached is not None:
            future = Future()
            # Processed cache entries are immutable display assets. Returning
            # the shared image avoids a large pixel-buffer copy on the Tk
            # caller thread for cached Preview/Zoom/Compare hits.
            future.set_result(cached)
            return future
        return future

    def _detach_channel_locked(self, channel, request_key):
        """Detach one latest-wins consumer and cancel its queued orphan work."""
        if self._channel_requests.get(channel) == request_key:
            self._channel_requests.pop(channel, None)
        consumer = self._request_consumers.get(request_key)
        if consumer is None:
            return
        consumer["channels"].discard(channel)
        if consumer["channels"] or consumer["unscoped"]:
            return
        future = self._inflight.get(request_key)
        if future is not None and future.cancel():
            self._stats["superseded"] += 1
            if self._inflight.get(request_key) is future:
                self._inflight.pop(request_key, None)
            self._request_consumers.pop(request_key, None)

    @staticmethod
    def _image_weight(image):
        return max(1, int(image.width) * int(image.height) * 4)

    def _cache_image_locked(
            self, cache, key, image, *, budget, count_limit, bytes_attr,
            eviction_stat, oversized_stat):
        weight = self._image_weight(image)
        if weight > budget:
            self._stats[oversized_stat] += 1
            return False
        previous = cache.pop(key, None)
        current_bytes = getattr(self, bytes_attr)
        if previous is not None:
            current_bytes -= previous[1]
        cache[key] = (image, weight)
        current_bytes += weight
        cache.move_to_end(key)
        while cache and (current_bytes > budget or len(cache) > count_limit):
            _old_key, (_old_image, old_weight) = cache.popitem(last=False)
            current_bytes -= old_weight
            self._stats[eviction_stat] += 1
        setattr(self, bytes_attr, max(0, current_bytes))
        return True

    def cache_info(self):
        with self._lock:
            return {
                **self._stats,
                "memory_entries": len(self._processed_cache),
                "processed_entries": len(self._processed_cache),
                "processed_bytes": self._processed_cache_bytes,
                "processed_budget": self.processed_budget,
                "source_entries": len(self._source_cache),
                "source_bytes": self._source_cache_bytes,
                "source_budget": self.source_budget,
                "inflight": len(self._inflight),
                "workers": len(self._workers),
                "closed": self._closed,
            }

    def shutdown(self):
        """Reject new work, logically cancel queued work, and stop workers."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        while True:
            try:
                _priority, _sequence, task = self._tasks.get_nowait()
            except queue.Empty:
                break
            if task is self._STOP:
                continue
            request_key, future = task[0], task[1]
            future.cancel()
            with self._lock:
                if self._inflight.get(request_key) is future:
                    self._inflight.pop(request_key, None)
                consumer = self._request_consumers.get(request_key)
                if consumer is not None and consumer.get("future") is future:
                    self._request_consumers.pop(request_key, None)
        for _worker in self._workers:
            with self._lock:
                self._task_sequence += 1
                sequence = self._task_sequence
            self._tasks.put((100, sequence, self._STOP))
        for worker in self._workers:
            worker.join(timeout=0.25)

    def _remove_stale_downloads(self):
        """Remove partial image downloads left by an interrupted prior process."""
        try:
            names = os.listdir(self.cache_dir)
        except OSError:
            return
        for name in names:
            if not name.endswith(".download"):
                continue
            try:
                os.remove(os.path.join(self.cache_dir, name))
            except OSError:
                pass

    def _worker_loop(self):
        while True:
            _priority, _sequence, task = self._tasks.get()
            if task is self._STOP:
                return
            (request_key, future, card, url, face_index, target_size,
             rotation_degrees, allow_upscale) = task
            if not future.set_running_or_notify_cancel():
                self._finish_request(request_key, future)
                continue
            try:
                image = self._load_image(
                    card, url, face_index, target_size, rotation_degrees,
                    allow_upscale)
                cache_image = image.copy()
                with self._lock:
                    self._cache_image_locked(
                        self._processed_cache, request_key, cache_image,
                        budget=self.processed_budget,
                        count_limit=self.processed_count_limit,
                        bytes_attr="_processed_cache_bytes",
                        eviction_stat="processed_evictions",
                        oversized_stat="processed_oversized")
                future.set_result(image)
            except Exception as exc:
                identity = card.get("id") or card.get("name") or "card"
                log.warning("Image load failed for %s (%s): %s", identity, url, exc)
                future.set_exception(exc)
            finally:
                self._finish_request(request_key, future)

    def _finish_request(self, request_key, future):
        with self._lock:
            if self._inflight.get(request_key) is future:
                self._inflight.pop(request_key, None)
            consumer = self._request_consumers.get(request_key)
            if consumer is not None and consumer.get("future") is future:
                self._request_consumers.pop(request_key, None)

    def _load_image(self, card, url, face_index, target_size,
                    rotation_degrees, allow_upscale):
        if not url:
            raise ValueError("Card has no image URL")
        identity = str(
            card.get("id") or card.get("oracle_id") or card.get("name") or "card")
        legacy = os.path.join(self.cache_dir, f"{identity}.jpg")
        base = cache_names.cache_path(card, self.cache_dir, ".jpg", legacy)
        if face_index > 0:
            stem, extension = os.path.splitext(base)
            path = f"{stem} - face {face_index + 1}{extension}"
        else:
            path = base

        path_lock = self._path_locks[hash(os.path.normcase(path)) % len(self._path_locks)]
        with path_lock:
            if not self._valid_image(path):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                data = self._fetch_bytes(url)
                temporary = f"{path}.{threading.get_ident()}.download"
                try:
                    with open(temporary, "wb") as target:
                        target.write(data)
                    if not self._valid_image(temporary):
                        raise ValueError("Downloaded card image is invalid")
                    os.replace(temporary, path)
                    with self._lock:
                        self._stats["downloads"] += 1
                finally:
                    try:
                        os.remove(temporary)
                    except FileNotFoundError:
                        pass

        try:
            stat = os.stat(path)
            source_key = (os.path.normcase(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            source_key = (os.path.normcase(path), 0, 0)
        source_image = None
        with self._lock:
            source_entry = self._source_cache.get(source_key)
            if source_entry is not None:
                source_image, _weight = source_entry
                self._source_cache.move_to_end(source_key)
                self._stats["source_hits"] += 1
            else:
                self._stats["source_misses"] += 1
        image = source_image.copy() if source_image is not None else None
        if image is None:
            with Image.open(path) as source:
                image = source.convert("RGB")
                image.load()
            cache_image = image.copy()
            with self._lock:
                self._stats["decoded"] += 1
                self._cache_image_locked(
                    self._source_cache, source_key, cache_image,
                    budget=self.source_budget,
                    count_limit=self.source_count_limit,
                    bytes_attr="_source_cache_bytes",
                    eviction_stat="source_evictions",
                    oversized_stat="source_oversized")
        if rotation_degrees:
            image = image.rotate(rotation_degrees, expand=True)
        if allow_upscale:
            scale = min(
                target_size[0] / max(1, image.width),
                target_size[1] / max(1, image.height),
            )
            resized = (
                max(1, int(round(image.width * scale))),
                max(1, int(round(image.height * scale))),
            )
            if resized != image.size:
                image = image.resize(resized, Image.LANCZOS)
        else:
            image.thumbnail(target_size, Image.LANCZOS)
        image.load()
        return image

    @staticmethod
    def _valid_image(path):
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            return False
        try:
            with Image.open(path) as candidate:
                candidate.verify()
            return True
        except Exception:
            return False
