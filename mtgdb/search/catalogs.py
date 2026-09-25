"""Generation-protected trusted Search taxonomy loading with bounded scope caches."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import nullcontext
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
    games: tuple[str, ...]
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
    formats_by_status: dict
    layouts: tuple
    release_years: tuple
    equivalent_layouts: tuple


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
        self._warm_queue = []
        self._stats = {
            "requests": 0, "cache_hits": 0, "loads": 0, "warmed": 0,
            "last_seconds": 0.0,
        }
        self._thread = spawn_daemon(self._run, "search-taxonomy")

    @staticmethod
    def _key(content_types, paper_only, selected_set_types=(), games=()):
        content = tuple(sorted({str(value) for value in (content_types or ()) if value}))
        if not content:
            content = ("card",)
        selected = tuple(sorted({str(value) for value in (selected_set_types or ()) if value}))
        # Platform belongs in the key: Paper, Arena and MTGO print different
        # sets, so a snapshot loaded for one platform describes a vocabulary
        # the others do not have. Leaving it out let a stale paper snapshot
        # overwrite the Arena set list the moment it arrived.
        platforms = tuple(sorted({str(value) for value in (games or ()) if value}))
        return content, bool(paper_only), platforms, selected

    @property
    def generation(self):
        with self._condition:
            return self._generation

    def request(self, content_types, paper_only, selected_set_types=(), games=()):
        key = self._key(content_types, paper_only, selected_set_types, games)
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

    def warm(self, content_types, paper_only, selected_set_types=(), games=()):
        """Pre-load a scope's catalog into cache in the background.

        Warming never emits a UI event or advances the request generation, so a
        real request always takes priority and a stale warm (e.g. a sync landed
        mid-load) is discarded by the generation check.  Used at startup to make
        the common scope switches feel instant.
        """
        key = self._key(content_types, paper_only, selected_set_types, games)
        with self._condition:
            if self._closed or self._cached_snapshot_locked(key) is not None:
                return
            if key in self._warm_queue:
                return
            self._warm_queue.append(key)
            self._condition.notify()

    def invalidate(self):
        with self._condition:
            self._generation += 1
            self._pending = None
            self._warm_queue.clear()
            self._base_cache.clear()
            self._set_cache.clear()
        self._clear_events()

    def _cached_snapshot_locked(self, key):
        content, paper_only, platforms, selected = key
        base_key = (content, paper_only, platforms)
        base = self._base_cache.get(base_key)
        sets = self._set_cache.get(key)
        if base is None or sets is None:
            return None
        self._base_cache.move_to_end(base_key)
        self._set_cache.move_to_end(key)
        return self._snapshot(key, base, sets)

    @staticmethod
    def _snapshot(key, base, sets):
        content, paper_only, platforms, selected = key
        return SearchCatalogSnapshot(
            content, paper_only, platforms, selected,
            tuple(base["card_types"]), tuple(base["card_type_status"]),
            tuple(base["supertypes"]), tuple(base["supertype_status"]),
            tuple(base["formats"]), tuple(base["rarities"]),
            tuple(base["keywords"]), tuple(base["subtypes"]),
            tuple(base["set_types"]), tuple(sets),
            dict(base["formats_by_status"]), tuple(base["layouts"]),
            tuple(base["release_years"]), tuple(base["equivalent_layouts"]),
        )

    def _load_base(self, content, paper_only, platforms=()):
        def safe(label, call, default):
            try:
                return call()
            except Exception:
                log.exception("Could not load %s catalog", label)
                return default
        # One grouped legality scan answers both the picker's per-state lists
        # and the playable list every other screen reads. Asking for them
        # separately scanned every card's legality JSON twice for the same
        # answer, which was a quarter of this loader's cold cost.
        by_status = safe(
            "format legality",
            lambda: self.repository.formats_by_status(
                content, paper_only, games=platforms or None), {})
        base = {
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
            "formats": list(by_status.get("playable") or ()),
            "rarities": safe(
                "rarity", lambda: self.repository.rarities(content, paper_only), []),
            "keywords": safe(
                "mechanic", lambda: self.repository.keyword_catalog(content, paper_only), []),
            "subtypes": safe(
                "subtype", lambda: self.repository.subtype_catalog(content, paper_only), []),
            "set_types": safe(
                "set-type",
                lambda: self.repository.set_types(
                    content, paper_only, games=platforms or None), []),
            "formats_by_status": by_status,
            "layouts": safe(
                "card-form",
                lambda: self.repository.layouts(
                    content, paper_only, games=platforms or None), []),
            "release_years": safe(
                "release-year",
                lambda: self.repository.release_years(
                    content, paper_only, games=platforms or None), []),
        }
        base["equivalent_layouts"] = safe(
            "card-form equivalence",
            lambda: self.repository.equivalent_layouts(
                content, paper_only, games=platforms or None,
                card_types=base["card_types"], supertypes=base["supertypes"],
                subtypes=base["subtypes"], keywords=base["keywords"]), ())
        return base

    def _load_sets(self, content, paper_only, selected, platforms=()):
        try:
            return self.repository.sets(
                list(selected) or None, content_types=content,
                paper_only=paper_only, games=platforms or None)
        except Exception:
            log.exception("Could not load exact-set catalog")
            return []

    def _run(self):
        while True:
            with self._condition:
                while (self._pending is None and not self._warm_queue
                       and not self._closed):
                    self._condition.wait()
                if (self._closed and self._pending is None
                        and not self._warm_queue):
                    return
                # A real (UI) request always takes priority over warming.
                if self._pending is not None:
                    generation, key = self._pending
                    self._pending = None
                    warm_only = False
                else:
                    key = self._warm_queue.pop(0)
                    generation = self._generation
                    warm_only = True
                content, paper_only, platforms, selected = key
                base_key = (content, paper_only, platforms)
                base = self._base_cache.get(base_key)
                sets = self._set_cache.get(key)
            if warm_only and base is not None and sets is not None:
                continue  # already cached; nothing to warm
            started = time.perf_counter()
            try:
                # Heavy card-table scans run on an independent WAL reader so this
                # background load does not queue in front of interactive reads.
                # The reader session is an optimization: a repository without one
                # still loads correctly on the primary connection.
                reader_session = getattr(self.repository, "reader_session", None)
                with (reader_session() if callable(reader_session)
                      else nullcontext()):
                    if base is None:
                        base = self._load_base(content, paper_only, platforms)
                    if sets is None:
                        sets = self._load_sets(
                            content, paper_only, selected, platforms)
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
                # could then mistake for current-database taxonomy.  Warming uses
                # the same guard: its captured generation moves on invalidate.
                if kind == "done" and generation == self._generation:
                    self._base_cache[base_key] = base
                    self._base_cache.move_to_end(base_key)
                    while len(self._base_cache) > self.BASE_CACHE_LIMIT:
                        self._base_cache.popitem(last=False)
                    self._set_cache[key] = sets
                    self._set_cache.move_to_end(key)
                    while len(self._set_cache) > self.SET_CACHE_LIMIT:
                        self._set_cache.popitem(last=False)
                    self._stats["warmed" if warm_only else "loads"] += 1
                    self._stats["last_seconds"] = elapsed
            if not warm_only:
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
            # Without this, _run()'s exit guard (which requires both _pending
            # and _warm_queue empty) never fires while a startup warm-load is
            # still queued: it pops and processes each queued scope's full
            # DB scan during/after the rest of app teardown instead of exiting
            # once _closed is set, exactly like invalidate() already prevents.
            self._warm_queue.clear()
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
