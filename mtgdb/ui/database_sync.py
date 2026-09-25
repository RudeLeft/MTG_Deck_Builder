"""Tk adapter for database synchronization scheduling and progress."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from mtgdb.database.sync import DatabaseDamagedError
from mtgdb.ui.tokens import (
    FONT_BODY, FONT_BODY_BOLD, FONT_HELPER, FONT_HELPER_BOLD,
    FONT_PROGRESS_TITLE, PALETTE,
)


log = logging.getLogger("mtg")


_SYNC_REASON_TEXT = {
    "first_launch": "The initial card library is being downloaded now.",
    "catalog_refresh": (
        "Authoritative Scryfall filter catalogs are being refreshed. "
        "If the card snapshot is already current, no bulk download is needed."),
    "classification_refresh": (
        "A search rule changed, so the stored card data is being re-checked. "
        "Nothing large is downloaded if your card snapshot is already current."),
    "scheduled": (
        "Your local card library is more than 48 hours old, so it is being "
        "refreshed."),
}
# A manual update says what was asked, not how old the data is: it may have been
# refreshed minutes ago.
_SYNC_MANUAL_TEXT = (
    "Checking Scryfall for a newer card list. If your library is already "
    "current, nothing large is downloaded.")


def sync_reason_text(reason):
    """Why the refresh dialog is showing, in words that are true for ``reason``."""
    return _SYNC_REASON_TEXT.get(reason, _SYNC_MANUAL_TEXT)


class DatabaseSyncMixin:
    """Own database-sync scheduling, progress presentation, and Tk polling."""

    # database sync
    # ======================================================================

    def _initialize_database_sync(self):
        self._sync_popup = None
        self._sync_poll_after = None
        self._sync_close_after = None
        self._sync_overall_percent = 0.0
        self._sync_stage_label = None
        self._sync_detail_label = None
        self._sync_progressbar = None
        self._sync_percent_label = None

    def _database_sync_is_running(self):
        return self.database_sync_controller.running

    def _maybe_auto_sync(self):
        if self.database_sync_controller.running:
            return
        try:
            reason = self.database_sync_controller.due_reason()
        except DatabaseDamagedError as exc:
            self._sync_error(str(exc), kind="DatabaseDamagedError")
            return
        if reason:
            self._sync_db(reason=reason)

    def _show_sync_popup(self, reason):
        self._cancel_sync_popup_close()
        if self._sync_popup is not None and self._sync_popup.winfo_exists():
            self._sync_popup.destroy()

        p = PALETTE
        popup = self._create_hidden_popup(
            "Updating Card Database", transient=self, resizable=False)
        popup.configure(bg=p["border"])
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        self._sync_popup = popup

        shell = tk.Frame(popup, bg=p["surface"], padx=28, pady=24)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        if reason == "first_launch":
            eyebrow, title = "FIRST-TIME SETUP", "Preparing your card library"
        elif reason == "catalog_refresh":
            eyebrow, title = "SEARCH METADATA", "Refreshing trusted filter catalogs"
        else:
            eyebrow, title = "DATABASE REFRESH", "Updating your card library"

        tk.Label(
            shell, text=eyebrow, bg=p["surface"], fg=p["accent"],
            font=FONT_HELPER_BOLD
        ).pack(anchor="w")
        tk.Label(
            shell, text=title, bg=p["surface"], fg=p["text"],
            font=FONT_PROGRESS_TITLE
        ).pack(anchor="w", pady=(3, 8))

        intro = (
            "MTG Deck Builder keeps a complete Scryfall card database locally so "
            "searches are fast and continue to work offline. "
            + sync_reason_text(reason))
        tk.Label(
            shell, text=intro, bg=p["surface"], fg=p["muted"],
            font=FONT_BODY, justify="left", wraplength=610
        ).pack(anchor="w", fill="x", pady=(0, 18))

        stage_box = tk.Frame(
            shell, bg=p["surface2"], highlightbackground=p["border"],
            highlightthickness=1, padx=16, pady=14)
        stage_box.pack(fill="x")

        self._sync_stage_label = tk.Label(
            stage_box, text="Connecting to Scryfall…",
            bg=p["surface2"], fg=p["text"],
            font=FONT_BODY_BOLD, anchor="w")
        self._sync_stage_label.pack(fill="x")
        self._sync_detail_label = tk.Label(
            stage_box, text="Checking for the latest full card dataset.",
            bg=p["surface2"], fg=p["muted"],
            font=FONT_HELPER, anchor="w")
        self._sync_detail_label.pack(fill="x", pady=(3, 10))

        barrow = tk.Frame(stage_box, bg=p["surface2"])
        barrow.pack(fill="x")
        barrow.columnconfigure(0, weight=1)
        self._sync_progressbar = ttk.Progressbar(
            barrow, mode="indeterminate", style="Gold.Horizontal.TProgressbar",
            length=430)
        self._sync_progressbar.grid(row=0, column=0, sticky="ew")
        self._sync_progressbar.start(14)

        self._sync_percent_label = tk.Label(
            barrow, text="", width=8, anchor="e",
            bg=p["surface2"], fg=p["accent"],
            font=FONT_HELPER_BOLD)
        self._sync_percent_label.grid(row=0, column=1, padx=(12, 0))

        tk.Label(
            shell,
            text="Please keep MTG Deck Builder open while this finishes. "
                 "No action is required.",
            bg=p["surface"], fg=p["muted"],
            font=FONT_HELPER, justify="left"
        ).pack(anchor="w", pady=(14, 0))

        self._present_hidden_popup(
            popup, preferred_width=680,
            preferred_height=max(360, popup.winfo_reqheight()),
            min_width=620, min_height=340, lock_size=True, grab=True)

    def _cancel_sync_popup_close(self):
        pending = self._sync_close_after
        self._sync_close_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass

    def _schedule_sync_popup_close(self, popup, delay_ms=700):
        self._cancel_sync_popup_close()

        def close_expected():
            self._sync_close_after = None
            self._close_sync_popup(popup)

        self._sync_close_after = self.after(delay_ms, close_expected)

    def _close_sync_popup(self, expected_popup=None):
        popup = self._sync_popup
        if expected_popup is not None and popup is not expected_popup:
            return
        self._cancel_sync_popup_close()
        if popup is not None and popup.winfo_exists():
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            popup.destroy()
        self._sync_popup = None
        self._sync_stage_label = None
        self._sync_detail_label = None
        self._sync_progressbar = None
        self._sync_percent_label = None

    def _sync_db(self, reason="manual"):
        if self.database_sync_controller.running:
            return
        self._sync_overall_percent = 0.0
        started = self.database_sync_controller.start(reason)
        if started.status != "started":
            return
        self._show_sync_popup(reason)
        self._start_sync_event_pump()

    def _start_sync_event_pump(self):
        if self._sync_poll_after is not None:
            try:
                self.after_cancel(self._sync_poll_after)
            except tk.TclError:
                pass
        self._sync_poll_after = self.after(40, self._poll_sync_events)

    def _poll_sync_events(self):
        """Drain worker events on Tk's main thread and coalesce noisy progress."""
        self._sync_poll_after = None
        poll = self.database_sync_controller.poll()
        for event in poll.timings:
            phase, elapsed = event.payload
            log.info(
                "Database refresh phase %s finished in %.2f seconds",
                phase, float(elapsed or 0.0))

        if poll.progress is not None:
            try:
                self._apply_sync_progress(
                    poll.progress.stage, poll.progress.payload)
            except Exception:
                # The popup disables its close button and holds a grab, so a
                # rendering error must not be allowed to kill the pump and
                # strand an unclosable modal over the app.
                log.exception(
                    "Could not render database sync progress for stage %s",
                    poll.progress.stage)

        if poll.terminal is not None:
            if poll.terminal.kind == "done":
                self._sync_done(poll.terminal.payload)
            elif poll.terminal.kind == "error":
                self._sync_error(poll.terminal.payload, kind=poll.terminal.stage)
            else:
                self._close_sync_popup()
            return

        if self.database_sync_controller.running:
            self._sync_poll_after = self.after(40, self._poll_sync_events)

    def _set_sync_percent(self, pct):
        """Monotonic overall progress so the bar never jumps backward by phase."""
        pct = max(self._sync_overall_percent, min(99.0, float(pct)))
        self._sync_overall_percent = pct
        bar = self._sync_progressbar
        if bar is None:
            return
        bar.stop()
        bar.configure(mode="determinate", maximum=100, value=pct)
        if self._sync_percent_label is not None:
            self._sync_percent_label.configure(text=f"{pct:.0f}%")

    def _apply_sync_progress(self, stage, info):
        """Render one coalesced progress update on the Tk main thread."""
        bar = self._sync_progressbar
        if bar is None:
            return

        if stage == "meta":
            self._set_sync_percent(2)
            text = str(info or "")
            if "Downloading" in text:
                self._sync_stage_label.configure(text="Starting card download…")
                self._sync_detail_label.configure(
                    text="Scryfall's current Default Cards file is ready to transfer.")
            else:
                self._sync_stage_label.configure(text="Checking Scryfall…")
                self._sync_detail_label.configure(text=text)

        elif stage == "catalogs":
            self._set_sync_percent(3)
            self._sync_stage_label.configure(text="Refreshing Magic terminology…")
            if isinstance(info, (tuple, list)) and len(info) >= 3:
                name, current, total = info[:3]
                label = str(name or "type and ability catalogs").replace("-", " ")
                self._sync_detail_label.configure(
                    text=f"{label.title()} · {int(current)} of {int(total)}")
            else:
                self._sync_detail_label.configure(text=str(info or ""))

        elif stage == "current":
            self._set_sync_percent(96)
            self._sync_stage_label.configure(text="Database is already current")
            self._sync_detail_label.configure(text=str(info or ""))

        elif stage == "download":
            read, total = info
            read = int(read or 0)
            if total:
                total = int(total)
                fraction = min(1.0, read / max(total, 1))
                self._set_sync_percent(3 + 42 * fraction)  # 3% -> 45%
                self._sync_detail_label.configure(
                    text=f"{read / (1 << 20):,.1f} MB of "
                         f"{total / (1 << 20):,.1f} MB downloaded")
            else:
                # Unknown Content-Length: keep a moving phase-local estimate
                # below the next phase boundary; bytes remain visible in detail.
                estimate = min(42.0, 8.0 + (read / (8 << 20)))
                self._set_sync_percent(3 + estimate)
                self._sync_detail_label.configure(
                    text=f"{read / (1 << 20):,.1f} MB downloaded")
            self._sync_stage_label.configure(text="Downloading card data…")

        elif stage == "load_start":
            self._set_sync_percent(46)
            size = int(info or 0)
            self._sync_stage_label.configure(text="Building local card database…")
            self._sync_detail_label.configure(
                text=(f"Processing {size / (1 << 20):,.1f} MB of downloaded card data"
                      if size else "Processing downloaded card data"))

        elif stage == "load_source":
            read, total, loaded, estimated = info
            read = float(read or 0)
            total = float(total or 0)
            loaded = int(loaded or 0)
            byte_fraction = min(1.0, read / total) if total > 0 else 0.0
            row_fraction = 0.0
            if estimated:
                row_fraction = min(1.0, loaded / max(float(estimated), 1.0))
            # Byte progress and committed-row progress advance at slightly
            # different moments. Use whichever is further along so a SQLite
            # batch write cannot make the overall bar look frozen.
            fraction = max(byte_fraction, row_fraction)
            self._set_sync_percent(46 + 36 * fraction)  # 46% -> 82%

        elif stage == "load":
            loaded, read, total, estimated = info
            loaded = int(loaded or 0)
            read = float(read or 0)
            total = float(total or 0)
            byte_fraction = min(1.0, read / total) if total > 0 else 0.0
            row_fraction = 0.0
            if estimated:
                row_fraction = min(1.0, loaded / max(float(estimated), 1.0))
            fraction = max(byte_fraction, row_fraction)
            self._set_sync_percent(46 + 36 * fraction)
            self._sync_stage_label.configure(text="Building local card database…")
            self._sync_detail_label.configure(
                text=f"{loaded:,} card records processed")

        elif stage == "maintenance":
            phase, current, total = info
            current = int(current or 0)
            total = max(1, int(total or 1))
            fraction = min(1.0, current / total)
            if phase == "drop_indexes":
                # Index removal is deliberately kept at the beginning of the
                # database-build phase; it makes the subsequent inserts faster.
                self._set_sync_percent(46)
                self._sync_stage_label.configure(text="Preparing database for fast import…")
                self._sync_detail_label.configure(text="Temporarily removing search indexes")
            elif phase == "classify":
                self._set_sync_percent(82 + 2 * fraction)
                self._sync_stage_label.configure(text="Finalizing card metadata…")
                self._sync_detail_label.configure(
                    text="Classifying set-wide Universes Beyond metadata")
            elif phase == "indexes":
                self._set_sync_percent(84 + 12 * fraction)  # 84% -> 96%
                self._sync_stage_label.configure(text="Building search indexes…")
                self._sync_detail_label.configure(
                    text=f"Search index {current} of {total}")
            elif phase == "commit":
                self._set_sync_percent(96 + 2 * fraction)  # 96% -> 98%
                self._sync_stage_label.configure(text="Saving database safely…")
                self._sync_detail_label.configure(
                    text="Committing the completed card library to disk")

        elif stage == "cleanup":
            self._set_sync_percent(99)
            self._sync_stage_label.configure(text="Finishing update…")
            self._sync_detail_label.configure(text=str(info or "Cleaning up temporary files"))

    def _reconcile_after_database_change(self):
        """Drop every cache built from the old cards and rebuild it off Tk."""
        self._invalidate_search_cache()
        # Database replacement invalidates every trusted taxonomy snapshot.
        # Rebuild through the same generation-protected background path used
        # for startup and cold Search scopes; do not scan the new DB on Tk.
        self.search_catalog_controller.invalidate()
        self.search_context_controller.invalidate()
        # The bitset facet index caches every card, so a rebuilt database must
        # drop it or it would serve stale contextual counts.  Rebuild it now in
        # the background so the first post-sync filter pick is instant.
        self.search_context_controller.reset_facet_index()
        self.search_context_controller.warm_facet_index()
        self._update_search_filter_summary()
        self._refresh_search_catalogs()

    def _sync_done(self, total):
        if self._sync_poll_after is not None:
            try:
                self.after_cancel(self._sync_poll_after)
            except tk.TclError:
                pass
            self._sync_poll_after = None
        self._sync_overall_percent = 100.0
        if self._sync_progressbar is not None:
            self._sync_progressbar.stop()
            self._sync_progressbar.configure(mode="determinate", maximum=100, value=100)
        if self._sync_stage_label is not None:
            self._sync_stage_label.configure(text="Card library is ready")
        if self._sync_detail_label is not None:
            self._sync_detail_label.configure(
                text=f"{total:,} cards are available for local searching.")
        if self._sync_percent_label is not None:
            self._sync_percent_label.configure(text="100%")

        self._reconcile_after_database_change()
        popup = self._sync_popup
        if popup is not None:
            self._schedule_sync_popup_close(popup)

    def _sync_error(self, msg, kind=""):
        if self._sync_poll_after is not None:
            try:
                self.after_cancel(self._sync_poll_after)
            except tk.TclError:
                pass
            self._sync_poll_after = None
        self._close_sync_popup()
        where = ("\n\nFull details are in the local error log:\n" + self.log_path
                 if self.log_path else "")
        # A failure AFTER the cards were replaced still left new cards behind:
        # search caches built from the old ones must not survive it.
        service = getattr(self.database_sync_controller, "service", None)
        if getattr(service, "cards_replaced", False):
            self._reconcile_after_database_change()
        if kind == "DatabaseDamagedError":
            messagebox.showerror(
                "Card database needs rebuilding",
                f"{msg}{where}")
            return
        messagebox.showerror(
            "Database update failed",
            "MTG Deck Builder couldn't refresh the local card database.\n\n"
            f"Details: {msg}\n\n"
            "Your existing local database has been left available when possible.\n"
            "You can retry from Database > Update Database."
            f"{where}")

    def _shutdown_database_sync(self):
        if self._sync_poll_after is not None:
            try:
                self.after_cancel(self._sync_poll_after)
            except tk.TclError:
                pass
            self._sync_poll_after = None
        self.database_sync_controller.shutdown(timeout=3.0)
        self._close_sync_popup()
