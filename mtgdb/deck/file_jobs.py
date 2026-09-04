"""Tk-free asynchronous deck file job helpers."""

from __future__ import annotations

from concurrent.futures import Future

from mtgdb.core.background_jobs import spawn_daemon


def submit_deck_file_job(callable_, *args, name="mtg-deck-file", **kwargs):
    """Run one deck import/export/durability operation on a daemon worker."""
    future = Future()

    def run():
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(callable_(*args, **kwargs))
        except BaseException as exc:
            future.set_exception(exc)

    spawn_daemon(run, name)
    return future
