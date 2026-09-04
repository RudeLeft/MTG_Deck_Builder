"""Tk adapter for print-template selection, progress, and completion."""

from __future__ import annotations

import logging
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from mtgdb.printing.service import PrintJob
from mtgdb.ui.tokens import (
    FONT_BODY, FONT_BODY_BOLD, FONT_HELPER, FONT_HELPER_BOLD,
    FONT_PROGRESS_TITLE, PALETTE,
)


log = logging.getLogger("mtg")


class PrintingMixin:
    """Own print dialogs, progress presentation, Tk polling, and shutdown."""

    def _initialize_printing(self):
        self._print_popup = None
        self._print_poll_after = None
        self._print_close_after = None
        self._print_stage_label = None
        self._print_detail_label = None
        self._print_progressbar = None
        self._print_percent_label = None

    def _show_print_popup(self, total_cards):
        self._cancel_print_popup_close()
        # Match the sync adapter: a popup left over from a previous job would
        # otherwise be overwritten without being destroyed, stranding a grabbed
        # window whose close button is disabled.
        if self._print_popup is not None and self._print_popup.winfo_exists():
            self._close_print_popup()
        palette = PALETTE
        popup = self._create_hidden_popup(
            "Creating Print Template", transient=self, resizable=False)
        popup.configure(bg=palette["border"])
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        self._print_popup = popup

        shell = tk.Frame(
            popup, bg=palette["surface"], padx=28, pady=24)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(
            shell, text="PRINT DECK", bg=palette["surface"],
            fg=palette["accent"], font=FONT_HELPER_BOLD
        ).pack(anchor="w")
        tk.Label(
            shell, text="Creating high-quality proxy sheets",
            bg=palette["surface"], fg=palette["text"],
            font=FONT_PROGRESS_TITLE
        ).pack(anchor="w", pady=(3, 7))
        tk.Label(
            shell,
            text=(
                f"{total_cards:,} card"
                f"{'s' if total_cards != 1 else ''} will be placed at full "
                "2.5 x 3.5 inch size, nine cards per US Letter page. "
                "High-resolution PNG images are cached locally for future "
                "prints."),
            bg=palette["surface"], fg=palette["muted"], font=FONT_BODY,
            justify="left", wraplength=620
        ).pack(anchor="w", fill="x", pady=(0, 16))

        box = tk.Frame(
            shell, bg=palette["surface2"],
            highlightbackground=palette["border"], highlightthickness=1,
            padx=16, pady=14)
        box.pack(fill="x")
        self._print_stage_label = tk.Label(
            box, text="Preparing card images…", bg=palette["surface2"],
            fg=palette["text"], font=FONT_BODY_BOLD, anchor="w")
        self._print_stage_label.pack(fill="x")
        self._print_detail_label = tk.Label(
            box, text="Checking the local high-resolution PNG cache.",
            bg=palette["surface2"], fg=palette["muted"],
            font=FONT_HELPER, anchor="w")
        self._print_detail_label.pack(fill="x", pady=(3, 10))

        row = tk.Frame(box, bg=palette["surface2"])
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        self._print_progressbar = ttk.Progressbar(
            row, mode="determinate", maximum=max(total_cards, 1), value=0,
            style="Gold.Horizontal.TProgressbar", length=430)
        self._print_progressbar.grid(row=0, column=0, sticky="ew")
        self._print_percent_label = tk.Label(
            row, text="0%", width=8, anchor="e", bg=palette["surface2"],
            fg=palette["accent"], font=FONT_HELPER_BOLD)
        self._print_percent_label.grid(row=0, column=1, padx=(12, 0))

        tk.Label(
            shell,
            text="The finished PDF will open from the location you selected.",
            bg=palette["surface"], fg=palette["muted"], font=FONT_HELPER
        ).pack(anchor="w", pady=(13, 0))
        self._present_hidden_popup(
            popup, preferred_width=700,
            preferred_height=max(350, popup.winfo_reqheight()),
            min_width=640, min_height=330, lock_size=True, grab=True)

    def _cancel_print_popup_close(self):
        pending = self._print_close_after
        self._print_close_after = None
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass

    def _schedule_print_popup_close(self, popup, delay_ms=650):
        self._cancel_print_popup_close()

        def close_expected():
            self._print_close_after = None
            self._close_print_popup(popup)

        self._print_close_after = self.after(delay_ms, close_expected)

    def _close_print_popup(self, expected_popup=None):
        popup = self._print_popup
        if expected_popup is not None and popup is not expected_popup:
            return
        self._cancel_print_popup_close()
        if popup is not None and popup.winfo_exists():
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            popup.destroy()
        self._print_popup = None
        self._print_stage_label = None
        self._print_detail_label = None
        self._print_progressbar = None
        self._print_percent_label = None

    def _create_print_template(self):
        total = self.deck.total("main") + self.deck.total("side")
        if total <= 0:
            messagebox.showinfo(
                "Print Deck",
                "Add cards to the deck before creating a print template.")
            return
        if self.print_controller.running:
            return

        safe_name = re.sub(
            r'[<>:"/\\|?*]+', "_",
            self.deck.name or "MTG Deck").strip() or "MTG Deck"
        path = filedialog.asksaveasfilename(
            title="Create Print Template",
            defaultextension=".pdf",
            initialfile=f"{safe_name} - Print Template.pdf",
            filetypes=[("PDF files", "*.pdf")])
        if not path:
            return

        job = PrintJob.from_deck(
            self.deck, path,
            os.path.join(self.image_cache_dir, "print_png"))
        started = self.print_controller.start(job)
        if started.status != "started":
            return
        self._show_print_popup(started.total_cards)
        self._start_print_event_pump()

    def _start_print_event_pump(self):
        if self._print_poll_after is not None:
            try:
                self.after_cancel(self._print_poll_after)
            except tk.TclError:
                pass
        self._print_poll_after = self.after(40, self._poll_print_events)

    def _poll_print_events(self):
        """Apply coalesced print-worker events on Tk's main thread."""
        self._print_poll_after = None
        poll = self.print_controller.poll()
        if poll.progress is not None:
            try:
                current, maximum, detail = poll.progress.payload
                self._update_print_progress(
                    poll.progress.stage, current, maximum, detail)
            except Exception:
                # As in the sync adapter: the popup holds a grab and disables
                # its close button, so a rendering error must not kill the pump.
                log.exception(
                    "Could not render print progress for stage %s",
                    poll.progress.stage)

        if poll.terminal is not None:
            if poll.terminal.kind == "done":
                self._print_done(poll.terminal.payload)
            elif poll.terminal.kind == "error":
                self._print_failed(poll.terminal.payload)
            else:
                self._close_print_popup()
            return

        if self.print_controller.running:
            self._print_poll_after = self.after(40, self._poll_print_events)

    def _update_print_progress(self, stage, current, maximum, detail):
        if self._print_progressbar is None:
            return
        maximum = max(int(maximum or 1), 1)
        current = min(max(int(current or 0), 0), maximum)
        self._print_progressbar.configure(maximum=maximum, value=current)
        self._print_percent_label.configure(
            text=f"{(current / maximum) * 100:.0f}%")
        if stage == "download":
            self._print_stage_label.configure(
                text="Preparing high-resolution PNGs…")
            self._print_detail_label.configure(
                text=f"Checking/downloading: {detail}")
        elif stage == "layout":
            self._print_stage_label.configure(
                text="Building printable pages…")
            self._print_detail_label.configure(
                text=f"Placed {current:,} of {maximum:,} cards at full print size")
        elif stage == "done":
            self._print_stage_label.configure(text="Print template is ready")
            self._print_detail_label.configure(
                text="The PDF was created successfully.")

    def _print_done(self, result):
        if self._print_progressbar is not None:
            self._print_progressbar.configure(maximum=100, value=100)
        if self._print_percent_label is not None:
            self._print_percent_label.configure(text="100%")
        if self._print_stage_label is not None:
            self._print_stage_label.configure(text="Print template is ready")
        if self._print_detail_label is not None:
            # PrintResult already carries the renderer's own page count; do not
            # derive a second one here that could disagree with the PDF.
            pages = result.pages
            self._print_detail_label.configure(
                text=(
                    f"Created {pages} page{'s' if pages != 1 else ''} "
                    f"with {result.total_cards} cards."))
        popup = self._print_popup
        if popup is not None:
            self._schedule_print_popup_close(popup)

    def _print_failed(self, message):
        self._close_print_popup()
        messagebox.showerror(
            "Print Deck",
            f"Could not create the print template.\n\n{message}")

    def _shutdown_printing(self):
        if self._print_poll_after is not None:
            try:
                self.after_cancel(self._print_poll_after)
            except tk.TclError:
                pass
            self._print_poll_after = None
        self.print_controller.shutdown(timeout=1.0)
        self._close_print_popup()
