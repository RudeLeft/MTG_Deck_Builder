"""Non-intrusive 'update available' banner backed by a background version check.

On startup a daemon thread asks GitHub for the latest release and compares it to
the running version. When a newer one exists, a dismissible banner appears below
the menu offering a Download link (the releases page). A failed check (offline,
rate limit) is silent and never blocks startup or the UI.
"""

from __future__ import annotations

import logging
import tkinter as tk
import webbrowser

from mtgdb.core.background_jobs import spawn_daemon
from mtgdb.core.net import get_json
from mtgdb.core.update_check import RELEASES_PAGE_URL, is_newer, latest_release
from mtgdb.core.version import app_version
from mtgdb.ui.tokens import FONT_HELPER_BOLD, PALETTE

log = logging.getLogger("mtg")


class UpdateCheckMixin:
    """Check GitHub for a newer release and offer a dismissible banner."""

    def _build_update_banner(self, parent):
        """Create the (hidden) update banner; it is packed only when needed."""
        palette = PALETTE
        self._update_page_url = None
        self._update_banner_below = None
        bar = tk.Frame(parent, bg=palette["accent"])
        self._update_banner = bar

        self._update_banner_label = tk.Label(
            bar, text="", bg=palette["accent"], fg=palette["bg"],
            font=FONT_HELPER_BOLD, padx=12, pady=5)
        self._update_banner_label.pack(side="left")

        dismiss = tk.Label(
            bar, text="Dismiss", bg=palette["accent"], fg=palette["bg"],
            font=FONT_HELPER_BOLD, padx=12, pady=5, cursor="hand2")
        dismiss.pack(side="right")
        dismiss.bind("<Button-1>", lambda _event: self._dismiss_update_banner())

        download = tk.Label(
            bar, text="Download", bg=palette["bg"], fg=palette["accent"],
            font=FONT_HELPER_BOLD, padx=12, pady=4, cursor="hand2")
        download.pack(side="right", padx=8, pady=3)
        download.bind("<Button-1>", lambda _event: self._open_update_page())
        return bar

    def _start_update_check(self):
        """Kick off the background release check (safe to call once at startup)."""
        current = app_version()

        def worker():
            tag, page = latest_release(get_json)
            if tag and is_newer(tag, current):
                try:
                    self.after(0, lambda: self._show_update_banner(tag, page))
                except (tk.TclError, RuntimeError):
                    pass

        spawn_daemon(worker, "update-check")

    def _show_update_banner(self, tag, page):
        banner = getattr(self, "_update_banner", None)
        if banner is None:
            return
        self._update_page_url = page or RELEASES_PAGE_URL
        try:
            self._update_banner_label.configure(
                text=f"A new version ({tag}) is available.")
            below = getattr(self, "_update_banner_below", None)
            if below is not None:
                banner.pack(fill="x", side="top", before=below)
            else:
                banner.pack(fill="x", side="top")
        except tk.TclError:
            return
        log.info("Update available: %s (running %s)", tag, app_version())

    def _dismiss_update_banner(self):
        banner = getattr(self, "_update_banner", None)
        if banner is not None:
            try:
                banner.pack_forget()
            except tk.TclError:
                pass

    def _open_update_page(self):
        url = getattr(self, "_update_page_url", None) or RELEASES_PAGE_URL
        try:
            webbrowser.open(url, new=2)
        except Exception:
            log.exception("Could not open the releases page")
        self._dismiss_update_banner()
