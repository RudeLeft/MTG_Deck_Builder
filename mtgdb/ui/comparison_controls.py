"""Comparison tray, exact-printing mutations, and cross-view UI coordination."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from mtgdb.comparison.models import (
    MAX_COMPARISON_CARDS, MIN_COMPARISON_CARDS, comparison_card_id,
)
from mtgdb.ui.comparison import CardComparisonWindow
from mtgdb.ui.components import AppButton, AppMenubutton, deck_board_label
from mtgdb.ui.tokens import (
    FONT_BODY,
    FONT_DIALOG_TITLE,
    PALETTE,
)


COMPARISON_OVER_LIMIT_NOTE = " (Too Many Cards Selected)"


def comparison_selection_overflows(compared_count, pending_count,
                                   maximum=MAX_COMPARISON_CARDS):
    """Return True when the pending selection cannot fit beside compared cards.

    ``pending_count`` counts only highlighted printings that are not already in
    comparison, so re-highlighting a compared card never reports an overflow.
    """
    pending = max(0, int(pending_count))
    if not pending:
        return False
    return max(0, int(compared_count)) + pending > max(1, int(maximum))


def comparison_action_columns(available_width, requested_widths, gap=6):
    """Return 4, 2, or 1 columns without clipping comparison actions."""
    widths = [max(1, int(value)) for value in requested_widths]
    if len(widths) != 4:
        raise ValueError("comparison action layout requires four controls")
    available = max(1, int(available_width))
    gap = max(0, int(gap))
    four_width = sum(widths) + gap * 3
    two_width = max(widths[0] + widths[2], widths[1] + widths[3]) + gap
    return 4 if available >= four_width else (2 if available >= two_width else 1)


class ComparisonFeatureMixin:
    """Own the comparison tray and application-to-window coordination."""

    # Bounded over-limit feedback: three bright/dim pulses, then steady red for
    # as long as the selection stays over the cap.  Bounded rather than endless
    # so the bar never animates indefinitely (see CLR-005).
    OVER_LIMIT_FLASH_MS = 600
    OVER_LIMIT_FLASH_PULSES = 3

    def _initialize_comparison(self):
        self._comparison_window = None
        self._comparison_selection_lbl = None
        self._comparison_over_limit = False
        self._comparison_over_limit_after = None
        self._comparison_over_limit_steps = 0
        self._comparison_add_selected_btn = None
        self._comparison_actions = None
        self._comparison_action_layout = None
        self._comparison_manage_btn = None
        self._comparison_manage_menu = None
        self._comparison_open_btn = None
        self._comparison_clear_btn = None
        self._comparison_source_sessions = {}
        self._card_grid_windows = {}

    def _build_comparison_bar(self, parent):
        """Build one global comparison bar in the deck workspace."""
        shell = tk.Frame(
            parent, bg=PALETTE["surface"], bd=0, highlightthickness=0)
        shell.pack(fill="x", pady=(0, 7))
        bar = tk.Frame(
            shell, bg=PALETTE["surface"], bd=0, pady=6)
        bar.pack(fill="x", expand=True)

        title_row = ttk.Frame(bar)
        title_row.pack(fill="x", pady=(0, 5))
        self._comparison_selection_lbl = ttk.Label(
            title_row, text="COMPARE | Cards Selected: 0",
            style="Section.TLabel", anchor="w")
        # Fill the row so the heading measures the available pane width: the
        # over-limit wording is far wider than the plain count and MUST wrap
        # instead of clipping at elevated Tk scaling (LAY-004).  anchor="w"
        # keeps the text on the same left edge as MAINBOARD | Cards: N.
        self._comparison_selection_lbl.pack(
            side="left", anchor="w", fill="x", expand=True)
        self._bind_debounced_wrap(self._comparison_selection_lbl)
        self._comparison_selection_lbl.bind(
            "<Destroy>", self._cancel_comparison_over_limit_flash, add="+")

        actions = tk.Frame(bar, bg=PALETTE["surface"], padx=8)
        actions.pack(fill="x")
        self._comparison_actions = actions
        self._comparison_add_selected_btn = AppButton(
            actions, text="Add Selected", role="primary",
            command=self._add_selected_to_comparison)

        self._comparison_manage_btn = AppMenubutton(
            actions, text=f"0/{MAX_COMPARISON_CARDS}", role="picker")
        self._comparison_manage_menu = tk.Menu(
            self._comparison_manage_btn, tearoff=False)
        self._comparison_manage_btn.configure(menu=self._comparison_manage_menu)

        self._comparison_open_btn = AppButton(
            actions, text="Compare", role="standard",
            command=self._open_comparison_window)

        self._comparison_clear_btn = AppButton(
            actions, text="Clear", role="standard", command=self._clear_comparison)

        bar.bind("<Configure>", self._layout_comparison_actions, add="+")
        self.after_idle(self._layout_comparison_actions)
        # Do not query cross-view selection during construction.  The deck
        # Treeviews are built immediately after this bar, and _build_deck_pane()
        # performs the first authoritative update once both boards exist.
        self._comparison_add_selected_btn.state(["disabled"])
        self._comparison_open_btn.state(["disabled"])
        self._comparison_clear_btn.state(["disabled"])

    def _layout_comparison_actions(self, _event=None):
        """Wrap actions only at settled widths; hysteresis prevents threshold churn."""
        if getattr(self, "_window_in_motion", False):
            return
        actions = self._comparison_actions
        widgets = (
            self._comparison_add_selected_btn, self._comparison_manage_btn,
            self._comparison_open_btn, self._comparison_clear_btn)
        if actions is None or any(widget is None for widget in widgets):
            return
        try:
            available = max(1, actions.winfo_width())
            widths = [max(1, widget.winfo_reqwidth()) for widget in widgets]
        except tk.TclError:
            return
        gap = 6
        layout = comparison_action_columns(available, widths, gap)
        previous = self._comparison_action_layout
        # Stay in the existing layout for a small dead-band around breakpoints.
        if previous == 4 and layout < 4:
            four_width = sum(widths) + gap * 3
            if available >= four_width - 24:
                layout = 4
        elif previous == 2:
            four_width = sum(widths) + gap * 3
            two_width = max(widths[0] + widths[2], widths[1] + widths[3]) + gap
            if layout == 4 and available < four_width + 24:
                layout = 2
            elif layout == 1 and available >= two_width - 20:
                layout = 2
        elif previous == 1 and layout > 1:
            two_width = max(widths[0] + widths[2], widths[1] + widths[3]) + gap
            if available < two_width + 20:
                layout = 1
        if layout == previous:
            return
        self._comparison_action_layout = layout
        for widget in widgets:
            widget.grid_forget()
        for column in range(4):
            actions.grid_columnconfigure(column, weight=0, uniform="")
        if layout == 4:
            for column, widget in enumerate(widgets):
                widget.grid(row=0, column=column, sticky="ew",
                            padx=(0 if column == 0 else gap, 0))
        elif layout == 2:
            placements = ((0, 0), (1, 0), (0, 1), (1, 1))
            for widget, (column, row) in zip(widgets, placements):
                widget.grid(row=row, column=column, sticky="ew",
                            padx=(0 if column == 0 else gap, 0),
                            pady=(0 if row == 0 else 5, 0))
            actions.grid_columnconfigure(0, weight=1, uniform="compare_action")
            actions.grid_columnconfigure(1, weight=1, uniform="compare_action")
        else:
            for row, widget in enumerate(widgets):
                widget.grid(row=row, column=0, sticky="ew",
                            pady=(0 if row == 0 else 5, 0))
            actions.grid_columnconfigure(0, weight=1)

    def _selected_comparison_candidates(self):
        """Return highlighted exact printings across Results and both deck boards.

        A card highlighted in more than one source appears once. Deck provenance
        wins over Results provenance; if the same printing is highlighted in both
        deck boards, the most recently active board wins.
        """
        ordered = {}
        # Comparison is capped at seven cards, so never hydrate an arbitrarily
        # large Results selection just to discover the first usable candidates.
        result_limit = MAX_COMPARISON_CARDS + len(self.comparison)
        for card in self._selected_results(limit=result_limit):
            card_id = comparison_card_id(card)
            if card_id:
                ordered[card_id] = (card, None)

        preferred = (self._selected_deck[1]
                     if getattr(self, "_selected_deck", None) else None)
        boards = ["main", "side"]
        if preferred in boards:
            boards.remove(preferred)
            boards.append(preferred)
        for board in boards:
            tree = getattr(self, "main_tv" if board == "main" else "side_tv", None)
            if tree is None:
                continue
            for card_id in self._deck_selected_ids(board):
                card = self._deck_card(card_id, board)
                if card:
                    ordered[comparison_card_id(card)] = (card, board)
        return list(ordered.values())

    def _selected_comparison_count(self):
        """Count mixed-source selection without hydrating Search cards."""
        count = self._selected_result_visible_count()
        deck_ids = set()
        for board in ("main", "side"):
            for card_id in self._deck_selected_ids(board):
                card_id = str(card_id or "")
                if card_id:
                    deck_ids.add(card_id)
        for card_id in deck_ids:
            if not self._result_selection_contains_visible(card_id):
                count += 1
        return count

    def _comparison_pending_count(self):
        """Count highlighted printings that would still need a comparison slot.

        Cards already in comparison are excluded: adding them again is a no-op,
        so re-highlighting a compared card must not report an overflow. Only the
        compared cards are inspected (at most seven), which keeps this off the
        expensive path of hydrating a large Results selection.
        """
        selected = self._selected_comparison_count()
        if not selected:
            return 0
        deck_ids = set()
        for board in ("main", "side"):
            for card_id in self._deck_selected_ids(board):
                card_id = str(card_id or "")
                if card_id:
                    deck_ids.add(card_id)
        already_compared = 0
        for card_id, _card in self.comparison.items():
            if (card_id in deck_ids
                    or self._result_selection_contains_visible(card_id)):
                already_compared += 1
        return max(0, selected - already_compared)

    def _add_selected_to_comparison(self):
        """Add highlighted cards from Results/Mainboard/Sideboard as one batch."""
        candidates = self._selected_comparison_candidates()
        if not candidates:
            self._status("Select cards in Results, Mainboard, or Sideboard first.")
            return 0
        added = 0
        for card, board in candidates:
            card_id = comparison_card_id(card)
            if not card_id or card_id in self.comparison:
                continue
            if len(self.comparison) >= MAX_COMPARISON_CARDS:
                self._show_comparison_notice(
                    "Comparison limit reached",
                    f"You can compare up to {MAX_COMPARISON_CARDS} cards at once. "
                    "Remove a card before adding another.")
                break
            if self._add_to_comparison(card, source_board=board):
                added += 1
        if not added and candidates:
            self._status("All selected cards are already in comparison.")
        self._update_comparison_bar()
        return added

    def _add_to_comparison(self, card, *, toggle=False, source_board=None):
        if not card:
            return False
        card_id = comparison_card_id(card)
        if not card_id:
            return False

        if card_id in self.comparison:
            mutation = self.comparison.add(card, toggle=toggle)
            if mutation.changed:
                self._comparison_source_sessions.pop(card_id, None)
                self._comparison_changed()
                self._status(
                    f"Removed {mutation.card.get('name') or 'card'} from "
                    f"comparison ({mutation.count}/{MAX_COMPARISON_CARDS}).")
            elif not toggle:
                self._status(
                    f"{card.get('name') or 'Card'} is already in comparison.")
            return True

        if len(self.comparison) >= MAX_COMPARISON_CARDS:
            self._show_comparison_notice(
                "Comparison limit reached",
                f"You can compare up to {MAX_COMPARISON_CARDS} cards at once. "
                "Remove a card before adding another.")
            return False

        try:
            fresh = self.db.get_card(card.get("id")) if card.get("id") else None
        except Exception:
            fresh = None
        chosen = fresh or card
        mutation = self.comparison.add(chosen)
        if not mutation.changed:
            return False
        if source_board in ("main", "side"):
            session = self._active_session()
            if session is not None and any(
                    entry["card"].get("id") == card_id
                    for entry in session.deck.entries(source_board)):
                # Keep the exact board used to add the card, not every board in
                # the same deck that happens to contain this printing.
                self._comparison_source_sessions[card_id] = (session, source_board)
        self._comparison_changed()
        self._status(
            f"Added {chosen.get('name') or 'card'} to comparison "
            f"({mutation.count}/{MAX_COMPARISON_CARDS}).")
        return True

    def _add_cards_to_comparison(self, cards, *, source_board=None):
        """Add highlighted cards in order, stopping once the seven-card cap is hit."""
        candidates = []
        for card in cards or ():
            card_id = comparison_card_id(card)
            if card_id and card_id not in self.comparison:
                candidates.append(card)
        if not candidates:
            return 0
        added = 0
        for card in candidates:
            if len(self.comparison) >= MAX_COMPARISON_CARDS:
                self._show_comparison_notice(
                    "Comparison limit reached",
                    f"You can compare up to {MAX_COMPARISON_CARDS} cards at once. "
                    "Remove a card before adding another.")
                break
            if self._add_to_comparison(card, source_board=source_board):
                added += 1
        return added

    def _add_selected_results_to_comparison(self):
        """Add highlighted Search results without hydrating an unbounded batch."""
        limit = MAX_COMPARISON_CARDS + len(self.comparison)
        return self._add_cards_to_comparison(self._selected_results(limit=limit))

    def _remove_from_comparison(self, card_id):
        card_id = str(card_id or "")
        mutation = self.comparison.remove(card_id)
        if not mutation.changed:
            return
        self._comparison_source_sessions.pop(card_id, None)
        self._comparison_changed()
        self._status(
            f"Removed {mutation.card.get('name') or 'card'} from comparison "
            f"({mutation.count}/{MAX_COMPARISON_CARDS}).")

    def _clear_source_highlights(self):
        """Clear highlighted cards in Results, Mainboard, and Sideboard only."""
        had_selection = bool(self._selected_comparison_count())
        self._result_selected_ids.clear()
        self._result_focus_id = None
        self._begin_result_selection_sync()
        try:
            self.results_tv.selection_remove(*self.results_tv.selection())
        except (tk.TclError, AttributeError):
            pass
        finally:
            self._end_result_selection_sync_later()
        for tree_name in ("main_tv", "side_tv"):
            tree = getattr(self, tree_name, None)
            if tree is None:
                continue
            try:
                tree.selection_remove(*tree.selection())
            except (tk.TclError, AttributeError):
                pass
        self._selected_deck = None
        self._update_comparison_bar()
        return had_selection

    def _clear_comparison(self):
        mutation = self.comparison.clear()
        if mutation.changed:
            self._comparison_source_sessions.clear()

        # Clear is the explicit reset action for comparison work. It also
        # releases every highlighted source row so prior multi-selection cannot
        # silently remain active after the comparison collection is cleared.
        had_selection = self._clear_source_highlights()

        if mutation.changed:
            self._comparison_changed()
        else:
            self._update_comparison_bar()
        if mutation.changed and had_selection:
            self._status("Comparison and selected cards cleared.")
        elif mutation.changed:
            self._status("Comparison cleared.")
        elif had_selection:
            self._status("Selected cards cleared.")

    def _comparison_source_record(self, card_id):
        """Return (open source session, source board) for a deck-origin card."""
        card_id = str(card_id or "")
        record = self._comparison_source_sessions.get(card_id)
        if not (isinstance(record, tuple) and len(record) == 2):
            return None
        session, board = record
        if board not in ("main", "side") or not any(
                candidate is session for candidate in self.deck_sessions):
            self._comparison_source_sessions.pop(card_id, None)
            return None
        return session, board

    def _comparison_source_info(self, card_id):
        """Return source-board provenance plus the current exact-printing quantity."""
        record = self._comparison_source_record(card_id)
        if record is None:
            return None
        session, board = record
        entry = next((
            item for item in session.deck.entries(board)
            if item["card"].get("id") == str(card_id or "")), None)
        return {
            "session": session,
            "board": board,
            "qty": int(entry.get("qty", 0)) if entry else 0,
        }

    def _remove_comparison_source_from_board(self, card_id, board):
        """Remove exactly one copy from the compared card's originating board."""
        if board not in ("main", "side"):
            return False
        card_id = str(card_id or "")
        info = self._comparison_source_info(card_id)
        if info is None or info["board"] != board or info["qty"] <= 0:
            self._refresh_comparison_window_only()
            return False
        session = info["session"]
        entry = next((
            item for item in session.deck.entries(board)
            if item["card"].get("id") == card_id), None)
        if entry is None:
            self._refresh_comparison_window_only()
            return False

        name = entry["card"].get("name") or "card"
        qty = int(entry.get("qty", 0))
        if qty > 1:
            session.deck.change_qty(card_id, board, -1)
        else:
            session.deck.remove(card_id, board)
        session.dirty = True
        self._render_deck_tabs()
        active_source = session is self.deck_sessions.active
        if active_source:
            if self._selected_deck == (card_id, board) and qty <= 1:
                self._selected_deck = None
            self._refresh_changed_deck_views(board)
            self._update_card_odds()
        else:
            self._refresh_comparison_window_only()

        remaining = max(0, qty - 1)
        board_label = deck_board_label(board)
        self._status(
            f"Removed one {name} from {board_label}"
            + (f" ({remaining} remaining)." if remaining else "."))
        return True

    def _comparison_card(self, card_id):
        card_id = str(card_id or "")
        for candidate_id, card in self.comparison.items():
            if candidate_id == card_id:
                return card
        return None

    def _add_comparison_card_to_board(self, card_id, board):
        """Add one Search-origin compared card to the active deck board."""
        if board not in ("main", "side"):
            return False
        card = self._comparison_card(card_id)
        if not card:
            return False
        self.deck.add(card, board, 1)
        self._mark_deck_dirty()
        self._refresh_changed_deck_views(board)
        board_label = deck_board_label(board)
        self._status(f"Added {card.get('name') or 'card'} to {board_label}.")
        return True


    def _refresh_comparison_window_only(self):
        window = self._comparison_window
        if window is None:
            return
        try:
            if window.top.winfo_exists():
                window.refresh()
            else:
                self._comparison_window = None
        except tk.TclError:
            self._comparison_window = None

    def _forget_card_grid_window(self, key, window):
        if self._card_grid_windows.get(key) is window:
            self._card_grid_windows.pop(key, None)

    def _open_card_grid_window(self, key, title, cards):
        """Open or refresh a read-only fixed card grid without mutating comparison."""
        cards = list(cards or ())
        if not cards:
            return None
        key = str(key or title or "card_grid")
        window = self._card_grid_windows.get(key)
        if window is not None:
            try:
                if window.top.winfo_exists():
                    window.show_cards(cards, title=title)
                    window.lift()
                    return window
            except tk.TclError:
                pass
            self._card_grid_windows.pop(key, None)

        window = CardComparisonWindow(
            self, cards=cards, title=title,
            on_close=lambda closed, k=key:
            self._forget_card_grid_window(k, closed),
        )
        self._card_grid_windows[key] = window
        return window

    def _update_card_grid_window(self, key, cards):
        window = self._card_grid_windows.get(str(key))
        if window is None:
            return False
        try:
            if window.top.winfo_exists():
                window.show_cards(cards)
                return True
        except tk.TclError:
            pass
        self._card_grid_windows.pop(str(key), None)
        return False

    def _close_card_grid_window(self, key):
        window = self._card_grid_windows.pop(str(key), None)
        if window is None:
            return
        try:
            window.close()
        except tk.TclError:
            pass

    def _comparison_changed(self):
        self._update_comparison_bar()
        window = self._comparison_window
        if window is not None:
            try:
                if window.top.winfo_exists():
                    window.refresh()
                else:
                    self._comparison_window = None
            except tk.TclError:
                self._comparison_window = None

    def _update_comparison_bar(self):
        count = len(self.comparison)
        menu = self._comparison_manage_menu
        if menu is not None:
            menu.delete(0, "end")
            if count:
                for index, (card_id, card) in enumerate(
                        self.comparison.items(), 1):
                    name = str(card.get("name") or "Card")
                    set_code = str(
                        card.get("set_code") or card.get("set") or "").upper()
                    printing = f" [{set_code}]" if set_code else ""
                    label = f"Remove {index}. {name}{printing}"
                    if len(label) > 56:
                        label = label[:53] + "…"
                    menu.add_command(
                        label=label,
                        command=lambda selected=card_id:
                        self._remove_from_comparison(selected))
            else:
                menu.add_command(
                    label="No cards in comparison",
                    state="disabled")

        if self._comparison_manage_btn is not None:
            self._comparison_manage_btn.configure(
                text=f"{count}/{MAX_COMPARISON_CARDS}")
            self._comparison_manage_btn.state(["!disabled"])

        selected_count = self._selected_comparison_count()
        over_limit = comparison_selection_overflows(
            count, self._comparison_pending_count())
        over_limit_note = COMPARISON_OVER_LIMIT_NOTE if over_limit else ""
        if self._comparison_selection_lbl is not None:
            self._comparison_selection_lbl.configure(
                text=f"COMPARE | Cards Selected: {selected_count}{over_limit_note}")
        self._set_comparison_over_limit(over_limit)
        if self._comparison_add_selected_btn is not None:
            self._comparison_add_selected_btn.state(
                ["!disabled"]
                if selected_count and count < MAX_COMPARISON_CARDS
                and not over_limit
                else ["disabled"])

        if self._comparison_open_btn is not None:
            self._comparison_open_btn.state(
                ["!disabled"] if count >= MIN_COMPARISON_CARDS
                else ["disabled"])

        if self._comparison_clear_btn is not None:
            self._comparison_clear_btn.state(
                ["!disabled"] if count or selected_count else ["disabled"])

    def _set_comparison_over_limit(self, over_limit):
        """Enter or leave the over-limit alert state exactly once per crossing.

        Entering starts a bounded bright/dim pulse that settles on steady red;
        leaving restores the normal section heading. Repeated calls while the
        state is unchanged are ignored, so ordinary selection churn neither
        restarts nor interrupts the pulse.
        """
        if bool(over_limit) == bool(self._comparison_over_limit):
            return
        self._comparison_over_limit = bool(over_limit)
        self._cancel_comparison_over_limit_flash()
        if not over_limit:
            self._apply_comparison_selection_style("Section.TLabel")
            return
        self._comparison_over_limit_steps = self.OVER_LIMIT_FLASH_PULSES * 2
        self._apply_comparison_selection_style("SectionAlert.TLabel")
        self._schedule_comparison_over_limit_flash()

    def _schedule_comparison_over_limit_flash(self):
        label = self._comparison_selection_lbl
        if label is None:
            return
        try:
            self._comparison_over_limit_after = label.after(
                self.OVER_LIMIT_FLASH_MS, self._advance_comparison_over_limit_flash)
        except tk.TclError:
            self._comparison_over_limit_after = None

    def _advance_comparison_over_limit_flash(self):
        """Alternate bright/dim red, then hold the bright alert style."""
        self._comparison_over_limit_after = None
        if not self._comparison_over_limit:
            return
        self._comparison_over_limit_steps = max(
            0, self._comparison_over_limit_steps - 1)
        self._apply_comparison_selection_style(
            "SectionAlertDim.TLabel" if self._comparison_over_limit_steps % 2
            else "SectionAlert.TLabel")
        if self._comparison_over_limit_steps:
            self._schedule_comparison_over_limit_flash()

    def _apply_comparison_selection_style(self, style_name):
        label = self._comparison_selection_lbl
        if label is None:
            return
        try:
            label.configure(style=style_name)
        except tk.TclError:
            pass

    def _cancel_comparison_over_limit_flash(self, event=None):
        """Drop any pending pulse; the label owns no timer past its destruction."""
        label = self._comparison_selection_lbl
        if event is not None and label is not None and event.widget is not label:
            return
        pending = self._comparison_over_limit_after
        self._comparison_over_limit_after = None
        self._comparison_over_limit_steps = 0
        if pending is not None and label is not None:
            try:
                label.after_cancel(pending)
            except tk.TclError:
                pass
        if event is not None:
            self._comparison_over_limit = False

    def _show_comparison_notice(self, title, message):
        """Show a feature-owned modal notice that follows the dark UI contract."""
        popup = self._create_hidden_popup(
            str(title), transient=self, resizable=False)
        popup.configure(bg=PALETTE["bg"])

        shell = tk.Frame(
            popup, bg=PALETTE["surface"], padx=18, pady=16,
            highlightthickness=1, highlightbackground=PALETTE["border"])
        shell.pack(fill="both", expand=True, padx=10, pady=10)
        tk.Label(
            shell, text=str(title), bg=PALETTE["surface"], fg=PALETTE["text"],
            font=FONT_DIALOG_TITLE, anchor="w",
        ).pack(fill="x", pady=(0, 10))
        tk.Label(
            shell, text=str(message), bg=PALETTE["surface"], fg=PALETTE["text"],
            font=FONT_BODY, justify="left", anchor="w", wraplength=390,
        ).pack(fill="x")

        actions = tk.Frame(shell, bg=PALETTE["surface"])
        actions.pack(fill="x", pady=(16, 0))
        close = lambda: popup.destroy()
        AppButton(
            actions, text="OK", role="compact_primary", command=close,
        ).pack(side="right")
        popup.protocol("WM_DELETE_WINDOW", close)
        popup.bind("<Escape>", lambda _event: close())
        popup.bind("<Return>", lambda _event: close())
        self._present_hidden_popup(
            popup, preferred_width=450, preferred_height=220,
            min_width=380, min_height=190, lock_size=True, focus=popup, grab=True)
        try:
            popup.wait_window()
        except tk.TclError:
            pass

    def _open_comparison_window(self):
        count = len(self.comparison)
        if count < MIN_COMPARISON_CARDS:
            self._show_comparison_notice(
                "Select more cards",
                f"Select at least {MIN_COMPARISON_CARDS} cards to compare.")
            return
        if self._comparison_window is not None:
            try:
                if self._comparison_window.top.winfo_exists():
                    self._comparison_window.refresh()
                    self._comparison_window.lift()
                    return
            except tk.TclError:
                pass
        self._comparison_window = CardComparisonWindow(self)


    def _show_result_context_menu(self, event):
        row = self.results_tv.identify_row(event.y)
        if not row:
            return
        source = self._result_source_for_iid(row)
        card_id = self._result_id_for_iid(row)
        if source is None or not card_id:
            return
        card = self._full_result_at(source)
        if not card:
            return

        try:
            selected = set(self.results_tv.selection())
        except tk.TclError:
            selected = set()
        if row not in selected:
            self._result_select_card_id(card_id, additive=False, ensure_visible=False)
        else:
            self._result_focus_id = card_id
            try:
                self.results_tv.focus(row)
            except tk.TclError:
                pass
        self._show_card(card)
        menu = self._dark_menu()
        menu.add_command(
            label="Add to Mainboard",
            command=lambda: self._add_to_deck("main"))
        menu.add_command(
            label="Add to Sideboard",
            command=lambda: self._add_to_deck("side"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
