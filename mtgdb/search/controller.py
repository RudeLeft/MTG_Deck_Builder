"""Tk-free worker lifecycle and bounded cache for interactive card search."""

from __future__ import annotations

import queue
import threading
import time

from mtgdb.search.models import SearchCriteria, SearchEvent, SearchStart


class SearchController:
    """Execute one search at a time and deliver terminal events through a queue."""

    # A broad search materializes ~100k row objects (~0.7 s). Deliver the first
    # screenful first, in the default name order, so results paint immediately;
    # the full store follows for sort/scroll/selection. The UI only renders the
    # partial when no sort column or table filter is active (otherwise the true
    # first screen is a different subset), but the controller always offers it --
    # a capped fetch is a few milliseconds.
    FIRST_SCREEN = 100

    def __init__(self, repository):
        self.repository = repository
        self.events = queue.Queue()
        self.generation = 0
        self.running = False
        self.active_signature = None
        self._cached_signature = None
        self._cached_results = ()

    def invalidate(self):
        """Invalidate cache and every event from work already in flight."""
        self.generation += 1
        self.running = False
        self.active_signature = None
        self._cached_signature = None
        self._cached_results = ()
        self._clear_events()

    def start(self, criteria: SearchCriteria):
        signature = criteria.signature()
        if self.running:
            return SearchStart("busy", self.generation, signature)
        if signature == self.active_signature:
            return SearchStart("unchanged", self.generation, signature)
        if signature == self._cached_signature:
            self.active_signature = signature
            return SearchStart(
                "cached", self.generation, signature, self._cached_results)

        self.generation += 1
        generation = self.generation
        self.running = True
        self._clear_events()

        def worker():
            reader = None
            started = time.monotonic()
            try:
                reader = self.repository.open_reader()
                # Fast first screen (default name order). If it did not fill,
                # it already IS the whole result, so skip the second query.
                first = self.repository.search_result_store(
                    criteria, reader, limit=self.FIRST_SCREEN)
                if first.logical_count >= self.FIRST_SCREEN:
                    self.events.put(SearchEvent(
                        "partial", generation, signature, first,
                        time.monotonic() - started))
                    full = self.repository.search_result_store(criteria, reader)
                    self.events.put(SearchEvent(
                        "done", generation, signature, full,
                        time.monotonic() - started))
                else:
                    self.events.put(SearchEvent(
                        "done", generation, signature, first,
                        time.monotonic() - started))
            except Exception as exc:
                self.events.put(SearchEvent(
                    "error", generation, signature, str(exc), 0.0))
            finally:
                if reader is not None:
                    try:
                        reader.close()
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()
        return SearchStart("started", generation, signature)

    def poll_latest(self):
        """Return the newest event for the current generation, dropping stale work."""
        event = None
        try:
            while True:
                candidate = self.events.get_nowait()
                if candidate.generation == self.generation:
                    event = candidate
        except queue.Empty:
            return event

    def accept(self, event: SearchEvent):
        if event.generation != self.generation:
            return False
        # A partial (first-screen) event is not terminal: the full store is
        # still coming, so keep running and do not cache the partial store.
        if event.kind == "partial":
            return True
        self.running = False
        if event.kind == "done":
            results = event.payload
            self._cached_signature = event.signature
            self._cached_results = results
            self.active_signature = event.signature
        return True

    def _clear_events(self):
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass
