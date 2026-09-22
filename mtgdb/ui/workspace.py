"""Tk workspace-state capture, restore, geometry, and autosave scheduling."""

import logging
import time
import tkinter as tk

from mtgdb.workspace.repository import (
    WorkspaceLoadWorker, WorkspaceRepository, WorkspaceSaveWorker,
)


log = logging.getLogger("mtg")
WORKSPACE_AUTOSAVE_MS = 1200


class WorkspaceMixin:
    """Adapt live widgets and feature state to the Tk-free workspace repository."""

    def _initialize_workspace(self, data_dir):
        self.workspace_repository = WorkspaceRepository(data_dir)
        self.workspace_save_worker = WorkspaceSaveWorker(self.workspace_repository)
        self.workspace_load_worker = WorkspaceLoadWorker(
            self.workspace_repository, self.db.get_card,
            bulk_lookup=self.db.hydrate_cards)
        self._workspace_autosave_after = None
        self._workspace_load_after = None
        self._workspace_capture_last_ms = 0.0
        self._workspace_capture_max_ms = 0.0
        self._workspace_restoring = False
        self._workspace_loaded = False

    def _workspace_geometry_state(self):
        data = {}
        # Main Search/center/Deck and Preview/Stats geometry is deterministic.
        # Persist only the one user-adjustable Mainboard/Sideboard sash.
        for key, widget in (("boards", getattr(self, "_boards_panes", None)),):
            if widget is None:
                continue
            positions = []
            try:
                count = len(widget.panes())
                for i in range(max(0, count - 1)):
                    positions.append(int(widget.sashpos(i)))
            except tk.TclError:
                positions = []
            if positions:
                data[key] = positions
        return data

    def _workspace_snapshot(self):
        started = time.perf_counter()
        self._capture_active_session_state()
        snapshot = self.workspace_repository.capture_snapshot(
            sessions=self.deck_sessions,
            active_index=self.deck_sessions.active_index,
            search=self._capture_search_workspace_state(),
            geometry=self._workspace_geometry_state(),
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        self._workspace_capture_last_ms = elapsed
        self._workspace_capture_max_ms = max(
            self._workspace_capture_max_ms, elapsed)
        return snapshot

    def _save_workspace_session(self, force=False, recovery=False):
        if self._workspace_restoring or not self._workspace_loaded:
            return False
        try:
            # Tk captures only cheap detached structural state. Deep JSON-safe
            # shaping, signatures, serialization, fsync, and replacement stay
            # on the writer thread.
            snapshot = self._workspace_snapshot()
            return self.workspace_save_worker.submit(
                snapshot, force=force, recovery=recovery) is not None
        except Exception:
            log.exception("Could not capture workspace session")
            return False

    def _shutdown_workspace(self, timeout=None):
        load_stopped = self.workspace_load_worker.shutdown(timeout=timeout)
        save_stopped = self.workspace_save_worker.shutdown(
            flush=True, timeout=timeout)
        return load_stopped and save_stopped

    def workspace_performance_info(self):
        data = self.workspace_save_worker.diagnostics()
        data.update({
            "capture_last_ms": self._workspace_capture_last_ms,
            "capture_max_ms": self._workspace_capture_max_ms,
            "loader": self.workspace_load_worker.diagnostics(),
        })
        return data

    def _schedule_workspace_autosave(self):
        if self._workspace_autosave_after is not None:
            try:
                self.after_cancel(self._workspace_autosave_after)
            except tk.TclError:
                pass

        def tick():
            self._workspace_autosave_after = None
            try:
                if (not self._database_sync_is_running()
                        and not self._window_in_motion):
                    self._save_workspace_session()
            finally:
                try:
                    if self.winfo_exists():
                        self._workspace_autosave_after = self.after(
                            WORKSPACE_AUTOSAVE_MS, tick)
                except tk.TclError:
                    pass

        self._workspace_autosave_after = self.after(WORKSPACE_AUTOSAVE_MS, tick)

    def _apply_workspace_restore(self, payload, sessions):
        if payload is None:
            return
        self._workspace_restoring = True
        try:
            if sessions:
                self.deck_sessions.replace(
                    sessions, active_index=payload.get("active_deck", 0))
                self._load_active_session_state()

            self._restored_workspace_geometry = payload.get("geometry", {})
            search = payload.get("search", {})
            if self._restore_search_workspace_state(search):
                if getattr(self, "_search_catalog_loading", False):
                    self._pending_search_request = True
                else:
                    self._do_search()
            self.workspace_repository.remember(payload)
        finally:
            self._workspace_restoring = False

    def _finish_workspace_initialization(self):
        self._workspace_loaded = True
        self._schedule_workspace_autosave()
        try:
            self.after_idle(self._restore_workspace_geometry)
        except tk.TclError:
            pass

    def _restore_workspace_session_async(self):
        """Load, parse, and hydrate the saved workspace without blocking Tk."""
        generation = self.workspace_load_worker.start()

        def poll():
            self._workspace_load_after = None
            try:
                done, result, error = self.workspace_load_worker.poll(generation)
                if not done:
                    self._workspace_load_after = self.after(20, poll)
                    return
                if error is not None:
                    log.error("Workspace background restore failed: %s", error)
                elif result is not None:
                    self._apply_workspace_restore(result.payload, result.sessions)
            except tk.TclError:
                return
            except Exception:
                log.exception("Could not apply restored workspace session")
            finally:
                if self._workspace_load_after is None:
                    self._finish_workspace_initialization()

        self._workspace_load_after = self.after(1, poll)

    # The board PanedWindow keeps resizing through startup, so a sash set once at
    # the wrong instant is dragged short by the next layout and never recovers.
    # Re-apply each tick until the sash actually holds at the saved position
    # through a layout pass, confirm it, then stop (or give up after the cap).
    # A late startup layout pass can resize the boards pane ~2 seconds in (e.g.
    # the comparison bar settling), dragging the sash after it looked stable, so
    # "held for CONFIRM ticks" alone exits too early. Keep re-applying (correcting
    # any drift) until BOTH a minimum settle window has passed and the sash has
    # held, capped so a pathological layout cannot loop forever.
    #
    # But that same re-apply window must not fight the user: if they drag the sash
    # during those seconds, we would snap it back. ``_boards_sash_grabbed`` is set
    # only by a real drag on the boards sash (never by startup window sizing), so
    # the moment it is set we stop restoring and leave the sash where they put it.
    _SASH_RESTORE_TOLERANCE = 4
    _SASH_RESTORE_RETRY_MS = 25
    _SASH_RESTORE_MIN_ATTEMPTS = 160
    _SASH_RESTORE_MAX_ATTEMPTS = 320
    _SASH_RESTORE_CONFIRM = 3

    def _restore_workspace_geometry(self, _attempt=0, _holds=0):
        state = getattr(self, "_restored_workspace_geometry", None)
        if not isinstance(state, dict):
            return
        # A deliberate sash drag takes precedence over the saved split: once the
        # user has grabbed the sash, stop restoring instead of reverting them.
        if getattr(self, "_boards_sash_grabbed", False):
            return
        # Older workspaces may contain ``main``/``middle`` sash positions; those
        # keys are intentionally ignored now that the main shell is fixed-layout.
        panes = [
            (widget, state.get(key))
            for key, widget in (("boards", getattr(self, "_boards_panes", None)),)
            if widget is not None
            and isinstance(state.get(key), list) and state.get(key)
        ]
        if not panes:
            return
        drifted = False
        for widget, positions in panes:
            for i, pos in enumerate(positions):
                target = max(40, int(pos))
                try:
                    # The reading reflects the previous set plus any layout since,
                    # so a short value here means the pane was still growing.
                    if abs(int(widget.sashpos(i)) - target) > self._SASH_RESTORE_TOLERANCE:
                        drifted = True
                        widget.sashpos(i, target)
                except (tk.TclError, TypeError, ValueError):
                    pass
        holds = 0 if drifted else _holds + 1
        settled = (holds >= self._SASH_RESTORE_CONFIRM
                   and _attempt >= self._SASH_RESTORE_MIN_ATTEMPTS)
        if not settled and _attempt < self._SASH_RESTORE_MAX_ATTEMPTS:
            try:
                self.after(
                    self._SASH_RESTORE_RETRY_MS,
                    lambda: self._restore_workspace_geometry(_attempt + 1, holds))
            except tk.TclError:
                pass
