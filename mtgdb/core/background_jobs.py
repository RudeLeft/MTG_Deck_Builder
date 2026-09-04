"""Shared background-job infrastructure.

Central home for the worker conventions that images, printing, and syncing all
rely on: a common cancellation exception, a cooperative cancel check, a daemon
thread factory, and a generation-tagged single-worker controller.

Tk-free by contract: this module must never import tkinter or build widgets.
Background work communicates with the UI through the typed event queues that
subclasses populate; the UI polls them on the Tk thread.
"""

from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("mtg")


class JobCancelled(RuntimeError):
    """Raised when application shutdown cooperatively cancels a background job.

    Subsystem-specific cancellations subclass this so callers can catch the
    shared type or the specific one interchangeably.
    """


def check_cancel(
        cancel_event, message="Background job was cancelled.",
        exception_type=JobCancelled):
    """Raise the requested cancellation type when the cancel event is set."""
    if cancel_event is not None and cancel_event.is_set():
        raise exception_type(message)


def spawn_daemon(target, name):
    """Create, start, and return a daemon worker thread.

    The single point that fixes the daemon-thread convention shared by the
    image worker pool and the generation-tagged single-worker controllers.
    """
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


class GenerationalWorker:
    """One background worker with a generation-tagged event queue.

    Owns the generation counter, typed-event queue, running flag, cooperative
    cancel event, and worker thread that the single-job controllers share.
    Subclasses build their own typed start/poll results and worker body and
    delegate the lifecycle mechanics here:

    * ``_begin()`` reserves the next generation (or reports busy),
    * ``_spawn(worker, name)`` launches the daemon worker,
    * ``_drain()`` yields queued events tagged for the current generation,
    * ``cancel()``/``shutdown()``/``_clear_events()`` manage teardown.
    """

    def __init__(self):
        self.events = queue.Queue()
        self.generation = 0
        self.running = False
        self._cancel_event = threading.Event()
        self._thread = None

    def _begin(self):
        """Reserve and return the next generation, or ``None`` when running."""
        if self.running:
            return None
        self.generation += 1
        self.running = True
        self._cancel_event.clear()
        self._clear_events()
        return self.generation

    def _spawn(self, worker, name):
        self._thread = spawn_daemon(worker, name)

    def _drain(self):
        """Yield queued events whose generation matches the current one."""
        try:
            while True:
                event = self.events.get_nowait()
                if event.generation == self.generation:
                    yield event
        except queue.Empty:
            pass

    def cancel(self):
        self._cancel_event.set()

    def shutdown(self, timeout=1.0):
        self.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self.running = False
        return stopped

    def _clear_events(self):
        try:
            while True:
                self.events.get_nowait()
        except queue.Empty:
            pass
