"""Generation-protected trusted Search taxonomy loading with bounded scope caches."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
import queue
import threading
import time

from mtgdb.core.background_jobs import spawn_daemon


log = logging.getLogger("mtg")


@dataclass(frozen=True, slots=True)
class SearchCatalogSnapshot:
    content_types: tuple[str, ...]
    paper_only: bool
    selected_set_types: tuple[str, ...]
    card_types: tuple
    card_type_status: tuple
    supertypes: tuple
    supertype_status: tuple
    formats: tuple
    rarities: tuple
    keywords: tuple
    subtypes: tuple
    set_types: tuple
    sets: tuple


@dataclass(frozen=True, slots=True)
class SearchCatalogEvent:
    generation: int
    key: tuple
    kind: str
    payload: object
    elapsed: float = 0.0


class SearchCatalogController:
    """Latest-wins taxonomy worker with bounded Content/Paper/Set-Type LRUs."""

    BASE_CACHE_LIMIT = 8
    SET_CACHE_LIMIT = 20

    def __init__(self, repository):
        self.repository = repository
        self.events = queue.Queue()
        self._condition = threading.Condition()
        self._generation = 0
        self._pending = None
        self._closed = False
        self._base_cache = OrderedDict()
        self._set_cache = OrderedDict()
        self._stats = {
            "requests": 0, "cache_hits": 0, "loads": 0,
            "last_seconds": 0.0,
        }
        self._thread = spawn_daemon(self._run, "search-taxonomy")

    @staticmethod
    def _key(content_types, paper_only, selected_set_types=()):
        content = tuple(sorted({str(value) for value in (content_types or ()) if value}))
        if not content:
            content = ("card",)
        selected = tuple(sorted({str(value) for value in (selected_set_types or ()) if value}))
        return content, bool(paper_only), selected

    @property
    def generation(self):
        with self._condition:
            return self._generation

    def request(self, content_types, paper_only, selected_set_types=()):
        key = self._key(content_types, paper_only, selected_set_types)
        with self._condition:
            if self._closed:
                raise RuntimeError("Search catalog controller is closed")
            self._stats["requests"] += 1
            cached = self._cached_snapshot_locked(key)
            self._generation += 1
            generation = self._generation
            if cached is not None:
                self._stats["cache_hits"] += 1
                return SearchCatalogEvent(generation, key, "cached", cached, 0.0)
            self._pending = (generation, key)
            self._condition.notify()
            return SearchCatalogEvent(generation, key, "started", None, 0.0)

    def invalidate(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            self._base_cache.clear()
            self._set_cache.clear()
        self._clear_events()

    def _cached_snapshot_locked(self, key):
        content, paper_only, selected = key
        base_key = (content, paper_only)
        base = self._base_cache.get(base_key)
        sets = self._set_cache.get(key)
        if base is None or sets is None:
            return None
        self._base_cache.move_to_end(base_key)
        self._set_cache.move_to_end(key)
        return self._snapshot(key, base, sets)

    @staticmethod
    def _snapshot(key, base, sets):
        content, paper_only, selected = key
        return SearchCatalogSnapshot(
            content, paper_only, selected,
            tuple(base["card_types"]), tuple(base["card_type_status"]),
            tuple(base["supertypes"]), tuple(base["supertype_status"]),
            tuple(base["formats"]), tuple(base["rarities"]),
            tuple(base["keywords"]), tuple(base["subtypes"]),
            tuple(base["set_types"]), tuple(sets),
        )

    def _load_base(self, content, paper_only):
        def safe(label, call, default):
            try:
                return call()
            except Exception:
                log.exception("Could not load %s catalog", label)
                return default
        return {
            "card_types": safe(
                "card-type", lambda: self.repository.card_types(content, paper_only), []),
            "card_type_status": safe(
                "card-type authority status",
                self.repository.card_type_taxonomy_status, (False, "")),
            "supertypes": safe(
                "supertype", lambda: self.repository.supertypes(content, paper_only), []),
            "supertype_status": safe(
                "supertype authority status",
                self.repository.supertype_taxonomy_status, (False, "")),
            "formats": safe(
                "format", lambda: self.repository.formats(content, paper_only), []),
            "rarities": safe(
                "rarity", lambda: self.repository.rarities(content, paper_only), []),
            "keywords": safe(
                "mechanic", lambda: self.repository.keyword_catalog(content, paper_only), []),
            "subtypes": safe(
                "subtype", lambda: self.repository.subtype_catalog(content, paper_only), []),
            "set_types": safe(
                "set-type", lambda: self.repository.set_types(content, paper_only), []),
        }

    def _load_sets(self, content, paper_only, selected):
        try:
            return self.repository.sets(
                list(selected) or None, content_types=content, paper_only=paper_only)
        except Exception:
            log.exception("Could not load exact-set catalog")
            return []

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed and self._pending is None:
                    return
                generation, key = self._pending
                self._pending = None
                content, paper_only, selected = key
                base_key = (content, paper_only)
                base = self._base_cache.get(base_key)
                sets = self._set_cache.get(key)
            started = time.perf_counter()
            try:
                if base is None:
                    base = self._load_base(content, paper_only)
                if sets is None:
                    sets = self._load_sets(content, paper_only, selected)
                snapshot = self._snapshot(key, base, sets)
                kind = "done"
                payload = snapshot
            except Exception as exc:
                log.exception("Search taxonomy preparation failed")
                kind = "error"
                payload = str(exc)
            elapsed = time.perf_counter() - started
            with self._condition:
                # A database refresh or newer scope can invalidate a request
                # while repository scans are running. Stale work may finish,
                # but it must never repopulate caches that a newer generation
                # could then mistake for current-database taxonomy.
                if kind == "done" and generation == self._generation:
                    self._base_cache[base_key] = base
                    self._base_cache.move_to_end(base_key)
                    while len(self._base_cache) > self.BASE_CACHE_LIMIT:
                        self._base_cache.popitem(last=False)
                    self._set_cache[key] = sets
                    self._set_cache.move_to_end(key)
                    while len(self._set_cache) > self.SET_CACHE_LIMIT:
                        self._set_cache.popitem(last=False)
                    self._stats["loads"] += 1
                    self._stats["last_seconds"] = elapsed
            self.events.put(SearchCatalogEvent(
                generation, key, kind, payload, elapsed))

    def poll_latest(self):
        latest = None
        current = self.generation
        try:
            while True:
                event = self.events.get_nowait()
                if event.generation == current:
                    latest = event
        except queue.Empty:
            return latest

    def cache_info(self):
        with self._condition:
            return {
                **self._stats,
                "base_cache_entries": len(self._base_cache),
                "set_cache_entries": len(self._set_cache),
                "generation": self._generation,
            }

    def shutdown(self, timeout=0.75):
        with self._condition:
            self._closed = True
            self._pending = None
            self._generation += 1
            self._condition.notify_all()
        self._thread.join(max(0.0, float(timeout)))
        return not self._thread.is_alive()

    def _clear_events(self):
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass
