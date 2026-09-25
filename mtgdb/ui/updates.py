"""Update banner: check GitHub, then download, stage, and restart into the build.

On startup a daemon thread checks GitHub for the latest release (and for an
update already staged from a previous session). When a newer one exists, a
dismissible banner appears below the menu. In the packaged app the banner's
action downloads the release, verifies and stages it beside the data folder, and
-- after the app exits -- a batch helper swaps the program files (keeping
``data\\``) and relaunches the new version. Running from source, where there is
nothing to swap, the action falls back to opening the releases page. Every
failure (offline, rate limit, bad download) is silent and never blocks the UI.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import webbrowser

from mtgdb.core import self_update
from mtgdb.core.background_jobs import spawn_daemon
from mtgdb.core.net import get_json, download, fetch_bytes
from mtgdb.core.update_check import (
    LATEST_RELEASE_URL, RELEASES_PAGE_URL, is_newer, latest_release)
from mtgdb.core.version import app_version
from mtgdb.ui.tokens import FONT_HELPER_BOLD, PALETTE

log = logging.getLogger("mtg")

# Launch the swap helper fully detached so it outlives the very process it is
# waiting to replace. DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP; zero on any
# non-Windows run, which never reaches the spawn (self-update is frozen-only).
_DETACHED_FLAGS = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class UpdateCheckMixin:
    """Check GitHub for a newer release and drive the update banner."""

    def _build_update_banner(self, parent):
        """Create the (hidden) update banner; it is packed only when needed."""
        palette = PALETTE
        self._update_page_url = None
        self._update_banner_below = None
        self._update_tag = None
        self._update_action_command = None
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

        # One action label whose text and behavior change with the banner state
        # (Update -> progress -> Restart now / Open page). A single dispatcher
        # calls whatever command the current state installed.
        action = tk.Label(
            bar, text="", bg=palette["bg"], fg=palette["accent"],
            font=FONT_HELPER_BOLD, padx=12, pady=4, cursor="hand2")
        self._update_action = action
        action.bind("<Button-1>", lambda _event: self._run_update_action())
        return bar

    # -- banner plumbing --------------------------------------------------

    def _run_update_action(self):
        command = self._update_action_command
        if command is not None:
            command()

    def _set_update_action(self, text, command):
        """Show the action label with ``text`` and ``command``, or hide it."""
        self._update_action_command = command
        action = getattr(self, "_update_action", None)
        if action is None:
            return
        try:
            if command is None or not text:
                action.pack_forget()
            else:
                action.configure(text=text)
                if not action.winfo_ismapped():
                    action.pack(side="right", padx=8, pady=3)
        except tk.TclError:
            pass

    def _pack_update_banner(self):
        banner = getattr(self, "_update_banner", None)
        if banner is None:
            return
        try:
            below = getattr(self, "_update_banner_below", None)
            if below is not None:
                banner.pack(fill="x", side="top", before=below)
            else:
                banner.pack(fill="x", side="top")
        except tk.TclError:
            pass

    def _set_update_text(self, text):
        try:
            self._update_banner_label.configure(text=text)
        except tk.TclError:
            pass

    # -- startup check ----------------------------------------------------

    def _start_update_check(self):
        """Kick off the background release check (safe to call once at startup)."""
        current = app_version()

        def worker():
            # An apply that may still be in flight owns the scratch dir (its
            # helper is backing up / swapping / rolling back); never touch it.
            if self_update.verify_is_recent(self.data_dir):
                return
            # A verified update staged last session but never applied needs no
            # network: offer the restart straight away.
            staged = self_update.pending_version(self.data_dir)
            if staged and is_newer(staged, current):
                self._post(lambda: self._show_update_ready(staged))
                return
            # No live apply and nothing newer staged: clear any leftover scratch
            # (an incomplete download, an orphaned apply marker, or a stale
            # already-installed staged build) before checking for a new release.
            self_update.clear_update(self.data_dir)
            tag, page = latest_release(get_json)
            if tag and is_newer(tag, current):
                self._post(lambda: self._show_update_available(tag, page))

        spawn_daemon(worker, "update-check")

    def _post(self, callback):
        """Run ``callback`` on the Tk thread, ignoring a torn-down interpreter."""
        try:
            self.after(0, callback)
        except (tk.TclError, RuntimeError):
            pass

    # -- banner states ----------------------------------------------------

    def _show_update_available(self, tag, page):
        self._update_tag = tag
        self._update_page_url = page or RELEASES_PAGE_URL
        self._set_update_text(
            f"A new version ({tag}) is available — you have {app_version()}.")
        # Only the frozen app can swap its own files; from source, the action
        # opens the releases page instead of attempting an impossible in-place
        # update of a checked-out tree.
        if getattr(sys, "frozen", False):
            self._set_update_action("Update", self._begin_update)
        else:
            self._set_update_action("Download", self._open_update_page)
        self._pack_update_banner()
        log.info("Update available: %s (running %s)", tag, app_version())

    def _show_update_progress(self, percent):
        self._set_update_text(f"Downloading update…  {percent}%")
        self._set_update_action("", None)
        self._pack_update_banner()

    def _show_update_ready(self, tag):
        self._update_tag = tag
        self._set_update_text(
            f"Update {tag} is ready to install — you have {app_version()}.")
        self._set_update_action("Restart now", self._apply_update)
        self._pack_update_banner()

    def _show_update_failed(self):
        self._set_update_text("Update failed. Open the releases page to update manually.")
        self._set_update_action("Open page", self._open_update_page)
        self._pack_update_banner()

    def _dismiss_update_banner(self):
        banner = getattr(self, "_update_banner", None)
        if banner is not None:
            try:
                banner.pack_forget()
            except tk.TclError:
                pass

    # -- actions ----------------------------------------------------------

    def _begin_update(self):
        """Download and stage the newest release on a background thread."""
        if not getattr(sys, "frozen", False):
            self._open_update_page()
            return
        self._show_update_progress(0)

        def worker():
            try:
                self._download_and_stage()
            except Exception:
                log.exception("In-app update failed")
                self_update.clear_update(self.data_dir)
                self._post(self._show_update_failed)
            else:
                self._post(lambda: self._show_update_ready(self._update_tag))

        spawn_daemon(worker, "update-download")

    def _download_and_stage(self):
        """Fetch the release zip, verify it, and extract it into staging."""
        data = get_json(LATEST_RELEASE_URL)
        url, _size, digest = self_update.select_release_asset(data)
        if not url:
            raise RuntimeError("latest release has no Windows asset to install")
        os.makedirs(self_update.update_dir(self.data_dir), exist_ok=True)
        destination = self_update.download_path(self.data_dir)

        def progress(read, total):
            percent = int(read * 100 / total) if total else 0
            self._post(lambda: self._show_update_progress(percent))

        download(url, destination, progress_cb=progress)
        # Prefer the published SHA256SUMS entry, then GitHub's asset digest; a
        # well-formed hash from either MUST match, and the structure check always
        # runs. Only when neither hash exists does verification fall back to
        # structure alone (older releases that predate the checksums file).
        expected = self._published_hash(data, url) or digest
        if not self_update.verify_zip(destination, expected):
            raise RuntimeError("downloaded update failed verification")
        self_update.extract_staged(destination, self.data_dir)
        self_update.write_pending(self.data_dir, self._update_tag)

    def _published_hash(self, release_json, asset_url):
        """Return the sha256 recorded for the asset in SHA256SUMS.txt, or None."""
        sums_url = self_update.select_checksums_url(release_json)
        if not sums_url:
            return None
        try:
            text = fetch_bytes(sums_url).decode("utf-8", "replace")
        except Exception:
            return None
        asset_name = asset_url.rsplit("/", 1)[-1]
        return self_update.expected_sha256(text, asset_name)

    def _apply_update(self):
        """Launch the swap helper, then close the app so it can replace files."""
        try:
            # Mark the apply as in-flight before the helper starts, so a launch
            # during the swap leaves the scratch dir to the helper (and its
            # rollback) instead of clearing it.
            self_update.write_verify(self.data_dir, self._update_tag)
            script = self_update.build_swap_script(self.data_dir)
            handle, script_path = tempfile.mkstemp(
                prefix="mtgupdate-", suffix=".bat")
            with os.fdopen(handle, "w", encoding="mbcs", newline="") as batch:
                batch.write(script)
            subprocess.Popen(
                ["cmd", "/c", script_path],
                creationflags=_DETACHED_FLAGS, close_fds=True)
        except Exception:
            log.exception("Could not launch the update helper")
            self._show_update_failed()
            return
        log.info("Applying update %s on restart; closing the app", self._update_tag)
        self._on_app_close()

    def _open_update_page(self):
        url = getattr(self, "_update_page_url", None) or RELEASES_PAGE_URL
        try:
            webbrowser.open(url, new=2)
        except Exception:
            log.exception("Could not open the releases page")
        self._dismiss_update_banner()
