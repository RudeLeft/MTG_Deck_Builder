"""Proxic Arena export presentation: the bundle action, its progress, its report.

The export reaches two feature packages the deck-file workflow may not touch --
`mtgdb.arena` for the bundle and `mtgdb.printing` for the validated card-art
download -- so it is its own UI module with its own cluster row rather than a
boundary break inside `ui/deck_files.py` (LAYER-006).

It owns no file format and no name rule: the zip layout and Arena's art naming
belong to `arena/export.py`, the playable-name comparison to
`deck/arena_support.py`, and the PNG download, cache and HTTP policy to
`printing/service.py`.
"""

import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from mtgdb.arena.export import ArenaExportJob, build_export
from mtgdb.deck.arena_support import (
    load_supported_names, unsupported_deck_names,
)
from mtgdb.deck.file_jobs import submit_deck_file_job
from mtgdb.deck.io import deck_to_text
from mtgdb.printing.service import ensure_png
from mtgdb.ui.assets import ARENA_SUPPORTED_CARDS_FILE, _asset_path
from mtgdb.ui.components import AppButton, autohide_scrollbar
from mtgdb.ui.tokens import FONT_BODY, PALETTE

log = logging.getLogger("mtg")

ARENA_WARNING_NAME_LIMIT = 30
ARENA_WARNING_HEADING = "PROXIC ARENA EXPORT"
ARENA_WARNING_WINDOW_TITLE = "Proxic Arena Export"
ARENA_WARNING_LEAD = (
    "The following cards in your deck are not in the Proxic Arena card pool "
    "and will not play properly")
ARENA_WARNING_CLOSING = "The rest of the deck will play without issue."


def arena_warning_text(unsupported):
    """Return the notice's lead line, listed names, overflow count, and closing.

    The wording lives apart from the widgets so the sentences can be checked
    without a Tk root. The lead carries no count and so needs no subject/verb
    agreement, and it does not repeat that the deck was saved: the dialog's
    own heading says that, and both together read it twice.
    """
    shown = list(unsupported[:ARENA_WARNING_NAME_LIMIT])
    return (
        ARENA_WARNING_LEAD, shown, len(unsupported) - len(shown),
        ARENA_WARNING_CLOSING)


class ArenaExportMixin:
    """Own the Proxic Arena bundle action and the dialogs that report it."""

    def _export_arena(self):
        """Bundle the active deck and its card art for Proxic Arena.

        Arena draws real art only for files its client already holds, so a
        decklist on its own plays through generated art. This writes both as
        one zip -- the decklist, plus `real_cards/<Card Name> [SET].png` for
        every distinct card -- and reports which cards Arena cannot play.

        It returns nothing: the bundle is written on a worker, so a value
        returned here could only mean "started", which DUI-020 forbids a
        caller from mistaking for "done".
        """
        index = self.deck_sessions.active_index
        if not self.deck_sessions.is_valid_index(index):
            return
        self._sync_deck_meta()
        deck = self.deck_sessions[index].deck
        if not deck.total("main") and not deck.total("side"):
            self._show_arena_list_dialog(
                "This deck has no cards to export yet.", [], 0, "")
            return

        path = filedialog.asksaveasfilename(
            title="Proxic Arena Export", defaultextension=".zip",
            initialfile=f"{deck.name}.zip",
            filetypes=[("Zip Archives", "*.zip")])
        if not path:
            return

        detached = self._detached_deck_copy(deck)
        job = ArenaExportJob.from_deck(
            detached, deck_to_text(detached), path,
            os.path.join(self.image_cache_dir, "print_png"))
        cancel_event = threading.Event()
        # Progress crosses threads through a queue rather than a Tk call from
        # the worker; Tk polls it (BEH-002).
        progress = queue.Queue()

        def run_export():
            def report(stage, current, total, detail):
                progress.put((stage, current, total, detail))
            supported = load_supported_names(
                _asset_path(ARENA_SUPPORTED_CARDS_FILE))
            unsupported = unsupported_deck_names(detached, supported)
            result = build_export(
                job, ensure_png, progress_cb=report,
                cancel_event=cancel_event)
            return result, unsupported

        def exported(outcome):
            self._close_arena_export_progress()
            result, unsupported = outcome
            log.info(
                "Arena export wrote %s: %d images for %d cards, %d without art,"
                " %d unplayable", result.output_path, result.image_count,
                result.card_count, len(result.missing_art), len(unsupported))
            self._status(f"Exported {os.path.basename(result.output_path)}")
            self._report_arena_export(result, unsupported)

        self._status("Exporting for Proxic Arena\u2026")
        popup = self._show_arena_export_progress(len(job.cards), cancel_event)
        self._arena_export_poll(progress, popup)
        future = submit_deck_file_job(run_export, name="mtg-arena-export")
        self._poll_deck_file_job(
            future, exported, error_title="Proxic Arena export failed",
            on_error=self._close_arena_export_progress)


    def _show_arena_export_progress(self, total, cancel_event):
        """Open the dark export progress popup and return it."""
        p = PALETTE
        popup = self._create_hidden_popup(
            "Proxic Arena Export", transient=self, resizable=False)
        popup.configure(bg=p["border"])
        # Closing the window carries the same intent as Cancel.
        popup.protocol("WM_DELETE_WINDOW", cancel_event.set)

        shell = tk.Frame(popup, bg=p["surface"], padx=18, pady=16)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        ttk.Label(
            shell, text="PROXIC ARENA EXPORT", style="DialogTitle.TLabel"
        ).pack(anchor="w", pady=(0, 10))
        self._arena_export_label = tk.Label(
            shell, text=f"Downloading card art for {total} cards\u2026",
            bg=p["surface"], fg=p["text"], font=FONT_BODY,
            justify="left", anchor="w", wraplength=430)
        self._arena_export_label.pack(anchor="w", fill="x")
        self._arena_export_bar = ttk.Progressbar(
            shell, orient="horizontal", mode="determinate",
            maximum=max(total, 1),
            style="Gold.Horizontal.TProgressbar", length=430)
        self._arena_export_bar.pack(fill="x", pady=(12, 0))

        foot = tk.Frame(shell, bg=p["surface"])
        foot.pack(fill="x", pady=(12, 0))
        AppButton(
            foot, text="Cancel", role="compact", command=cancel_event.set
        ).pack(side="right")

        self._arena_export_popup = popup
        self._present_hidden_popup(
            popup, preferred_width=500, min_width=460, min_height=200,
            grab=False, fit_content=True)
        return popup


    def _arena_export_poll(self, progress, popup):
        """Drain worker progress onto Tk until the popup is gone."""
        if getattr(self, "_arena_export_popup", None) is not popup:
            return
        latest = None
        try:
            while True:
                latest = progress.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            stage, current, total, _detail = latest
            try:
                self._arena_export_bar.configure(
                    maximum=max(total, 1), value=current)
                self._arena_export_label.configure(
                    text="Writing the zip\u2026" if stage == "package"
                    else f"Downloading card art ({current}/{total})\u2026")
            except (tk.TclError, AttributeError):
                return
        try:
            self._arena_export_after = self.after(
                120, lambda: self._arena_export_poll(progress, popup))
        except tk.TclError:
            pass


    def _close_arena_export_progress(self):
        """Tear the progress popup down once, cancelling its own poll (BEH-006)."""
        handle = getattr(self, "_arena_export_after", None)
        if handle:
            try:
                self.after_cancel(handle)
            except (tk.TclError, ValueError):
                pass
        self._arena_export_after = None
        popup = getattr(self, "_arena_export_popup", None)
        self._arena_export_popup = None
        self._arena_export_bar = None
        self._arena_export_label = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass


    def _report_arena_export(self, result, unsupported):
        """Report what the bundle holds, and what Arena still cannot play.

        One dialog rather than two: the write and the playability answer belong
        to the same action, and a second popup to dismiss is not news.
        """
        written = (
            f"Wrote {os.path.basename(result.output_path)}: "
            f"{result.image_count} card images for {result.card_count} cards.")
        if unsupported:
            lead, shown, remaining, closing = arena_warning_text(unsupported)
            lead = f"{written}\n\n{lead}"
        else:
            lead, shown, remaining = written, [], 0
            closing = "Every card in this deck will play in Proxic Arena."
        if result.missing_art:
            closing = (
                f"{closing}\n\nNo art could be downloaded for "
                f"{len(result.missing_art)} card(s); Proxic Arena draws its "
                "own for those.")
        self._show_arena_list_dialog(lead, shown, remaining, closing)


    def _show_arena_list_dialog(self, lead, shown, remaining, closing):
        """Present an Arena notice as an app-owned dark dialog, not a native one.

        A native `messagebox` paints the host platform's own grey chrome and
        reads as a different application beside the charcoal/gold popups, the
        way comparison notices did before CMP-012. This mirrors the Basic
        Format Check dialog: one gold `DialogTitle.TLabel`, a bordered dark
        list whose scrollbar appears only when it overflows (UI-016), and a
        single compact secondary `Close` (UI-012). With nothing to list the
        list is omitted rather than drawn empty.
        """
        p = PALETTE
        popup = self._create_hidden_popup(
            ARENA_WARNING_WINDOW_TITLE, transient=self)
        popup.configure(bg=p["border"])
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        shell = tk.Frame(popup, bg=p["surface"], padx=18, pady=16)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        ttk.Label(
            shell, text=ARENA_WARNING_HEADING, style="DialogTitle.TLabel"
        ).pack(anchor="w", pady=(0, 10))

        # UI-017: a left-justified classic label is anchored west, or the text
        # block is centered inside a label wider than itself.
        tk.Label(
            shell, text=lead, bg=p["surface"], fg=p["text"], font=FONT_BODY,
            justify="left", anchor="w", wraplength=520
        ).pack(anchor="w", fill="x", pady=(0, 10))

        if shown:
            list_shell = tk.Frame(
                shell, bg=p["border"], highlightthickness=1,
                highlightbackground=p["border"])
            list_shell.pack(fill="both", expand=True)
            list_shell.rowconfigure(0, weight=1)
            list_shell.columnconfigure(0, weight=1)

            names = tk.Listbox(
                list_shell, activestyle="none", exportselection=False,
                background=p["input"], foreground=p["text"],
                selectbackground=p["accent"],
                selectforeground=p["on_accent"],
                highlightthickness=0, relief="flat", bd=0,
                height=min(max(len(shown), 3), 12),
                font=FONT_BODY)
            scroll = ttk.Scrollbar(
                list_shell, orient="vertical", command=names.yview,
                style="Dark.Vertical.TScrollbar")
            names.configure(yscrollcommand=autohide_scrollbar(scroll))
            names.grid(row=0, column=0, sticky="nsew")
            scroll.grid(row=0, column=1, sticky="ns")
            self._register_scrollable(names)

            for name in shown:
                names.insert("end", f"  {name}")
            if remaining:
                # Counted rather than dropped silently.
                names.insert("end", f"  +{remaining} more")

        if closing:
            tk.Label(
                shell, text=closing, bg=p["surface"], fg=p["text"],
                font=FONT_BODY, justify="left", anchor="w", wraplength=520
            ).pack(anchor="w", fill="x", pady=(10, 0))

        foot = tk.Frame(shell, bg=p["surface"])
        foot.pack(fill="x", pady=(12, 0))
        AppButton(
            foot, text="Close", role="compact", command=popup.destroy
        ).pack(side="right")

        popup.bind("<Escape>", lambda _e: popup.destroy())
        self._present_hidden_popup(
            popup, preferred_width=560, preferred_height=420,
            min_width=460, min_height=300, grab=True, fit_content=True)
