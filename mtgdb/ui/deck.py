"""Deck-editor presentation, session tabs, and board interaction callbacks."""

import tkinter as tk
from tkinter import messagebox, ttk

from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession
from mtgdb.ui.components import AppButton, AppEntry, ClassicButton
from mtgdb.ui.search_checklist import open_search_checklist
from mtgdb.ui.tables import TABLE_COLUMNS, TABLE_COLUMN_ORDER
from mtgdb.ui.tokens import (
    FONT_CONTROL_GLYPH, FONT_HELPER, FONT_HELPER_BOLD, PALETTE,
)


def deck_action_layout_mode(available_width, requested_widths, previous=None,
                            *, gap=4, hysteresis=18):
    """Choose a non-clipping layout from actual requested widget widths."""
    widths = [max(1, int(value)) for value in requested_widths]
    if not widths:
        return "stack"
    while len(widths) < 4:
        widths.append(widths[-1])
    width = max(1, int(available_width))
    wide_needed = sum(widths[:4]) + int(gap) * 3
    two_needed = max(
        widths[0] + widths[1] + int(gap),
        widths[2] + widths[3] + int(gap),
    )
    margin = max(0, int(hysteresis))
    if previous == "wide":
        if width >= wide_needed:
            return "wide"
        return "two" if width >= two_needed else "stack"
    if previous == "two":
        if width >= wide_needed + margin:
            return "wide"
        return "two" if width >= two_needed else "stack"
    if previous == "stack":
        if width >= wide_needed + margin:
            return "wide"
        if width >= two_needed + margin:
            return "two"
        return "stack"
    if width >= wide_needed:
        return "wide"
    if width >= two_needed:
        return "two"
    return "stack"


class DeckEditorMixin:
    """Own the deck pane while delegating domain and shared services."""

    def _build_deck_pane(self, parent):
        # Browser-style tab strip.  Tabs switch DeckSession state while the
        # Mainboard/Sideboard widgets below remain a single shared UI.
        self._deck_tab_bar = tk.Frame(parent, bg=PALETTE["surface2"], height=34)
        self._deck_tab_bar.pack(fill="x", pady=(0, 7))
        self._deck_tab_bar.pack_propagate(False)
        self._render_deck_tabs()

        namerow = ttk.Frame(parent)
        namerow.pack(fill="x", pady=(0, 6))
        self.deck_name = AppEntry(namerow)
        self.deck_name.insert(0, self.deck.name)
        self.deck_name.pack(side="left", fill="x", expand=True)
        self._bind_editable_focus_behavior(self.deck_name)
        self.deck_name.bind("<FocusOut>", lambda e: self._sync_deck_meta(), add="+")
        self.deck_format = tk.StringVar(master=self, value=self.deck.fmt or "commander")
        self._deck_format_btn = AppButton(
            namerow, text="", role="picker", command=self._choose_deck_format)
        self._deck_format_btn.pack(side="left", padx=(6, 0))
        self._refresh_deck_format_button()

        # Comparison is a global workspace action: highlighted cards may come
        # from Results, Mainboard, Sideboard, or any combination of those views.
        self._build_comparison_bar(parent)

        # Mainboard and sideboard are separated by a draggable vertical sash so
        # the user can choose how much room each list receives.
        boards = ttk.PanedWindow(parent, orient="vertical")
        boards.pack(fill="both", expand=True, pady=(2, 0))

        main_frame = ttk.Frame(boards)
        side_frame = ttk.Frame(boards)
        boards.add(main_frame, weight=3)
        boards.add(side_frame, weight=1)
        self._boards_panes = boards
        self._register_paned_motion(boards)

        main_head = ttk.Frame(main_frame)
        main_head.pack(fill="x", pady=(2, 3))
        self.mainboard_header_lbl = ttk.Label(
            main_head, text="", style="Section.TLabel")
        self.mainboard_header_lbl.pack(side="left")
        main_columns_btn = AppButton(
            main_head, text="Edit Columns", role="compact",
            command=lambda: self._toggle_column_popup("main", main_columns_btn))
        main_columns_btn.pack(side="right")
        AppButton(
            main_head, text="Clear Filters", role="compact",
            command=lambda: self._clear_table_filter("main")
        ).pack(side="right", padx=(0, 6))
        self.main_tv = self._make_deck_tree(main_frame, "main", height=8)

        side_head = ttk.Frame(side_frame)
        side_head.pack(fill="x", pady=(4, 3))
        self.sideboard_header_lbl = ttk.Label(
            side_head, text="", style="Section.TLabel")
        self.sideboard_header_lbl.pack(side="left")
        side_columns_btn = AppButton(
            side_head, text="Edit Columns", role="compact",
            command=lambda: self._toggle_column_popup("side", side_columns_btn))
        side_columns_btn.pack(side="right")
        AppButton(
            side_head, text="Clear Filters", role="compact",
            command=lambda: self._clear_table_filter("side")
        ).pack(side="right", padx=(0, 6))
        self.side_tv = self._make_deck_tree(side_frame, "side", height=4)

        ctrl = ttk.Frame(parent)
        ctrl.pack(fill="x", pady=(8, 0))
        self._deck_action_frame = ctrl
        self._deck_action_widgets = [
            AppButton(ctrl, text="+", width=3, role="deck",
                      command=lambda: self._deck_qty(1)),
            AppButton(ctrl, text="–", width=3, role="deck",
                      command=lambda: self._deck_qty(-1)),
            AppButton(ctrl, text="Remove", role="deck", command=self._deck_remove),
            AppButton(ctrl, text="Move to Sideboard", role="deck", command=self._deck_move),
        ]
        self._deck_move_btn = self._deck_action_widgets[-1]
        self._deck_action_layout_mode = None
        ctrl.bind("<Configure>", self._layout_deck_actions, add="+")
        self.after_idle(self._layout_deck_actions)
        self._update_board_headers()
        self._update_comparison_bar()

    def _layout_deck_actions(self, _event=None):
        """Reflow deck actions only at settled widths; never churn during sash drag."""
        if getattr(self, "_window_in_motion", False):
            return
        frame = getattr(self, "_deck_action_frame", None)
        widgets = getattr(self, "_deck_action_widgets", ())
        if frame is None or not widgets:
            return
        try:
            width = max(1, frame.winfo_width())
            requested_widths = tuple(widget.winfo_reqwidth() for widget in widgets)
        except tk.TclError:
            return
        previous = getattr(self, "_deck_action_layout_mode", None)
        mode = deck_action_layout_mode(width, requested_widths, previous)
        if mode == previous:
            return
        self._deck_action_layout_mode = mode
        for widget in widgets:
            widget.grid_forget()
        for column in range(4):
            frame.columnconfigure(column, weight=0)
        if mode == "wide":
            for column, widget in enumerate(widgets):
                widget.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 3, 0))
            frame.columnconfigure(3, weight=1)
        elif mode == "two":
            for index, widget in enumerate(widgets):
                widget.grid(row=index // 2, column=index % 2, sticky="ew",
                            padx=(0 if index % 2 == 0 else 4, 0), pady=(0, 3))
            frame.columnconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)
        else:
            for row, widget in enumerate(widgets):
                widget.grid(row=row, column=0, sticky="ew", pady=(0, 3))
            frame.columnconfigure(0, weight=1)

    def _update_board_headers(self):
        """Keep board totals beside their section names without a duplicate count row."""
        main = getattr(self, "mainboard_header_lbl", None)
        side = getattr(self, "sideboard_header_lbl", None)
        if main is not None:
            main.configure(text=f"MAINBOARD | Cards: {self.deck.total('main')}")
        if side is not None:
            side.configure(text=f"SIDEBOARD | Cards: {self.deck.total('side')}")

    def _make_deck_tree(self, parent, view, height=8):
        ordinary = tuple(c for c in TABLE_COLUMN_ORDER
                         if c != "cost" and view in TABLE_COLUMNS[c]["views"])
        holder = ttk.Frame(parent)
        holder.pack(fill="both", expand=True)
        tv = ttk.Treeview(holder, columns=ordinary, show="tree headings", height=height,
                          selectmode="extended")
        self._setup_table_columns(tv, view)
        tv.tag_configure("odd", background=PALETTE["stripe"])
        tv.tag_configure("even", background=PALETTE["surface"])
        vsb = ttk.Scrollbar(holder, orient="vertical", command=tv.yview,
                            style="Dark.Vertical.TScrollbar")
        hsb = ttk.Scrollbar(holder, orient="horizontal", command=tv.xview,
                            style="Dark.Horizontal.TScrollbar")
        tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._register_scrollable(tv)
        tv.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tv.bind("<<TreeviewSelect>>", lambda e, t=tv: self._on_deck_select(t))
        tv.bind("<Button-3>",
                lambda e, t=tv, b=("main" if view == "main" else "side"):
                    self._show_deck_context_menu(e, t, b))
        self._bind_column_drag(tv, view)
        return tv

    def _active_session(self):
        return self.deck_sessions.active

    def _session_title(self, session):
        return session.title

    def _render_deck_tabs(self):
        """Redraw deck tabs with fixed right-side New/overflow controls."""
        bar = self._deck_tab_bar
        if bar is None:
            return
        for child in bar.winfo_children():
            child.destroy()
        self._deck_tab_widgets = []

        # Pack controls first on the right so many/long deck tabs can never push
        # the New Deck control outside the visible bar.
        # High-visibility New Deck control: solid MTG-gold square with a
        # large dark plus so it remains obvious beside the deck tabs.
        plus = ClassicButton(
            bar, text="+", role="tab_add", command=self._show_new_deck_menu)
        plus.pack(side="right", fill="y")
        self._deck_plus_btn = plus

        max_visible = 5
        count = len(self.deck_sessions)
        if count > max_visible:
            overflow = ClassicButton(
                bar, text="⋯", role="tab_overflow",
                command=self._show_deck_overflow_menu)
            overflow.pack(side="right", fill="y", padx=(3, 3))

            half = max_visible // 2
            start_i = max(0, self.deck_sessions.active_index - half)
            start_i = min(start_i, count - max_visible)
            visible_indices = range(start_i, start_i + max_visible)
        else:
            visible_indices = range(count)

        for i in visible_indices:
            session = self.deck_sessions[i]
            active = i == self.deck_sessions.active_index
            bg = PALETTE["surface"] if active else PALETTE["surface3"]
            fg = PALETTE["accent"] if active else PALETTE["text"]
            tab = tk.Frame(
                bar, bg=bg, highlightthickness=1,
                highlightbackground=PALETTE["accent"] if active else PALETTE["border"])
            tab.pack(side="left", fill="y", padx=(0, 3))

            full_title = self._session_title(session)
            shown_title = full_title if len(full_title) <= 22 else full_title[:19] + "…"
            name = tk.Label(
                tab, text=shown_title, bg=bg, fg=fg,
                font=(FONT_HELPER_BOLD if active else FONT_HELPER),
                padx=9, pady=5, cursor="hand2")
            name.pack(side="left", fill="y")
            close = tk.Label(
                tab, text="×", bg=bg, fg=PALETTE["muted"],
                font=FONT_CONTROL_GLYPH, padx=5, pady=3, cursor="hand2")
            close.pack(side="left", fill="y")

            for widget in (tab, name):
                widget.bind("<Button-1>",
                            lambda e, n=i: self._switch_deck_session(n))
                widget.bind("<Button-2>",
                            lambda e, n=i: self._close_deck_session(n))
            close.bind("<Button-1>",
                       lambda e, n=i: self._close_deck_session(n))
            for widget in (tab, name, close):
                widget.bind("<Button-3>",
                            lambda e, n=i: self._show_deck_tab_menu(e, n))
            self._deck_tab_widgets.append(tab)

    def _show_deck_overflow_menu(self):
        """List every open deck when the tab strip cannot display them all."""
        menu = self._dark_menu()
        for i, session in enumerate(self.deck_sessions):
            label = self._session_title(session)
            if i == self.deck_sessions.active_index:
                label = "✓ " + label
            menu.add_command(
                label=label,
                command=lambda n=i: self._switch_deck_session(n))
        try:
            x = self._deck_plus_btn.winfo_rootx()
            y = self._deck_plus_btn.winfo_rooty() + self._deck_plus_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _show_new_deck_menu(self):
        menu = self._dark_menu()
        menu.add_command(label="New Deck", command=self._new_deck)
        menu.add_command(label="Open Deck TXT...", command=self._open_deck)
        try:
            x = self._deck_plus_btn.winfo_rootx()
            y = self._deck_plus_btn.winfo_rooty() + self._deck_plus_btn.winfo_height()
            menu.tk_popup(x, y)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _show_deck_tab_menu(self, event, index):
        if not self.deck_sessions.is_valid_index(index):
            return
        menu = self._dark_menu()
        menu.add_command(label="Activate Deck",
                         command=lambda: self._switch_deck_session(index))
        menu.add_command(label="Save Deck As...",
                         command=lambda: self._save_session_as(index))
        menu.add_separator()
        menu.add_command(label="Close Deck",
                         command=lambda: self._close_deck_session(index))
        if len(self.deck_sessions) > 1:
            menu.add_command(
                label="Close Other Decks",
                command=lambda: self._close_other_decks(index))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _capture_active_session_state(self):
        session = self._active_session()
        if session is None:
            return
        # Sync visible metadata without treating a no-op as a modification.
        self._sync_deck_meta()
        session.selected = self._selected_deck
        session.filters["main"] = dict(self._table_filters["main"])
        session.filters["side"] = dict(self._table_filters["side"])
        session.sorts["main"] = list(self._deck_sorts["main"])
        session.sorts["side"] = list(self._deck_sorts["side"])

    def _load_active_session_state(self):
        session = self._active_session()
        if session is None:
            return
        self.deck = session.deck
        self._selected_deck = session.selected
        self._table_filters["main"] = dict(session.filters.get("main", {}))
        self._table_filters["side"] = dict(session.filters.get("side", {}))
        self._deck_sorts["main"] = list(session.sorts.get("main", [None, False]))
        self._deck_sorts["side"] = list(session.sorts.get("side", [None, False]))

        self.deck_name.delete(0, "end")
        self.deck_name.insert(0, self.deck.name)
        self.deck_format.set(self.deck.fmt or "commander")
        self._refresh_deck_format_button()
        self._render_deck_tabs()
        self._refresh_deck_views()

    def _switch_deck_session(self, index):
        if not self.deck_sessions.is_valid_index(index):
            return
        if index == self.deck_sessions.active_index:
            return
        self._capture_active_session_state()
        self.deck_sessions.activate(index)
        self._load_active_session_state()

    def _append_deck_session(self, deck, path=None, dirty=False):
        self._capture_active_session_state()
        self.deck_sessions.append(DeckSession(
            deck=deck, path=path, dirty=dirty))
        self._load_active_session_state()

    def _mark_deck_dirty(self):
        if self.deck_sessions.mark_active_dirty():
            self._render_deck_tabs()

    def _confirm_close_session(self, index):
        session = self.deck_sessions[index]
        if not session.dirty:
            return True
        name = session.deck.name or "Untitled Deck"
        answer = messagebox.askyesnocancel(
            "Unsaved Deck",
            f'Save changes to "{name}" before closing?')
        if answer is None:
            return False
        if answer:
            return self._save_session_as(index)
        return True

    def _close_active_deck(self):
        self._close_deck_session(self.deck_sessions.active_index)

    def _close_deck_session(self, index):
        if not self.deck_sessions.is_valid_index(index):
            return False
        if index == self.deck_sessions.active_index:
            self._capture_active_session_state()
        if not self._confirm_close_session(index):
            return False

        self.deck_sessions.remove(index)
        self._load_active_session_state()
        return True

    def _close_other_decks(self, keep_index):
        if not self.deck_sessions.is_valid_index(keep_index):
            return
        self._switch_deck_session(keep_index)
        # Close from right to left so indices stay stable.
        for i in range(len(self.deck_sessions) - 1, -1, -1):
            if i == self.deck_sessions.active_index:
                continue
            if not self._close_deck_session(i):
                break

    def _add_to_deck(self, board):
        cards = self._selected_results()
        if not cards:
            return
        for card in cards:
            self.deck.add(card, board, 1)
        self._mark_deck_dirty()
        self._refresh_changed_deck_views(board)
        board_label = "Mainboard" if board == "main" else "Sideboard"
        if len(cards) > 1:
            self._status(f"Added {len(cards)} selected cards to {board_label}.")

    def _show_deck_context_menu(self, event, tv, board):
        """Preserve a deck multi-selection and offer actions for the clicked row."""
        row = tv.identify_row(event.y)
        if not row:
            return

        try:
            selected = set(tv.selection())
            if row not in selected:
                tv.selection_set(row)
            tv.focus(row)
            tv.see(row)
        except tk.TclError:
            return
        self._selected_deck = (row, board)

        card = self._deck_card(row, board)
        if not card:
            return

        # Keep the exact right-clicked printing in the preview while batch
        # actions continue to use the complete highlighted selection.
        self._show_card(card)
        selected_cards = [
            self._deck_card(card_id, board)
            for card_id in self._deck_selected_ids(board)
        ]
        selected_cards = [candidate for candidate in selected_cards if candidate]

        menu = self._dark_menu()
        if len(selected_cards) > 1:
            menu.add_command(
                label="Search for these cards",
                command=lambda cards=tuple(selected_cards):
                self._search_for_deck_cards(cards))
        else:
            menu.add_command(
                label="Search for this card",
                command=lambda c=card: self._search_for_deck_card(c))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass

    def _search_for_deck_card(self, card):
        """Show all locally available printings of one selected deck card."""
        return self._search_for_deck_cards((card,))

    def _search_for_deck_cards(self, cards):
        """Search exact card names for the complete highlighted deck batch."""
        cards = tuple(card for card in (cards or ()) if card)
        if not cards or not self._apply_cards_search_preset(cards):
            return
        self._do_search()

        self._clear_result_selection()
        try:
            self.results_tv.focus_set()
        except tk.TclError:
            pass
        self._show_card(cards[0])

    def _deck_selected_ids(self, board):
        """Return visible highlighted card IDs for one deck board.

        Board widgets are created after the comparison bar during startup, so
        selection queries must be safe while the deck pane is only partially
        constructed.
        """
        attr = "main_tv" if board == "main" else "side_tv"
        tv = getattr(self, attr, None)
        if tv is None:
            return ()
        try:
            selected = set(tv.selection())
            return tuple(iid for iid in tv.get_children("") if iid in selected)
        except (tk.TclError, AttributeError):
            return ()

    def _active_deck_selection(self):
        """Return (board, tree, selected IDs, focused primary ID)."""
        preferred = self._selected_deck[1] if self._selected_deck else None
        boards = (preferred, "side" if preferred == "main" else "main") if preferred else ("main", "side")
        for board in boards:
            if board not in ("main", "side"):
                continue
            tv = self.main_tv if board == "main" else self.side_tv
            selected = self._deck_selected_ids(board)
            if not selected:
                continue
            try:
                focused = tv.focus()
            except tk.TclError:
                focused = ""
            primary = focused if focused in selected else selected[0]
            return board, tv, selected, primary
        return None

    def _restore_deck_selections(self, card_ids, board, focus_id=None):
        """Restore every surviving highlighted deck row after reconciliation."""
        tv = self.main_tv if board == "main" else self.side_tv
        visible = tuple(card_id for card_id in card_ids if card_id and tv.exists(card_id))
        try:
            tv.selection_remove(*tv.selection())
            if visible:
                tv.selection_set(*visible)
                primary = focus_id if focus_id in visible else visible[0]
                tv.focus(primary)
                tv.see(primary)
                self._selected_deck = (primary, board)
                move_button = getattr(self, "_deck_move_btn", None)
                if move_button is not None:
                    move_button.configure(
                        text="Move to Sideboard" if board == "main"
                        else "Move to Mainboard")
                return visible
        except tk.TclError:
            pass
        if self._selected_deck and self._selected_deck[1] == board:
            self._selected_deck = None
        return ()

    def _on_deck_select(self, tv):
        sel = tuple(tv.selection())
        board = "main" if tv is self.main_tv else "side"
        if not sel:
            if self._selected_deck and self._selected_deck[1] == board:
                self._selected_deck = None
                self._update_card_odds()
            self._update_comparison_bar()
            return
        try:
            focused = tv.focus()
        except tk.TclError:
            focused = ""
        primary = focused if focused in sel else sel[0]
        new_selection = (primary, board)
        same_selection = (self._selected_deck == new_selection)
        self._selected_deck = new_selection
        move_button = getattr(self, "_deck_move_btn", None)
        if move_button is not None:
            move_button.configure(
                text="Move to Sideboard" if board == "main" else "Move to Mainboard")

        # The focused row is the preview/odds card; all highlighted rows remain
        # available to batch quantity/remove/move actions.
        if not same_selection:
            card = self.db.get_card(primary) or self._deck_card(primary, board)
            if card:
                self._show_card(card)
            self._update_card_odds()

        self._update_comparison_bar()

    def _restore_deck_selection(self, card_id, board):
        """Compatibility helper for restoring one primary deck row."""
        return bool(self._restore_deck_selections((card_id,), board, card_id))

    def _neighbor_after_removal(self, board, old_ids, removed_id):
        """Choose the visible row now occupying the primary removed row's position."""
        try:
            old_index = list(old_ids).index(removed_id)
        except ValueError:
            old_index = 0
        tv = self.main_tv if board == "main" else self.side_tv
        try:
            new_ids = list(tv.get_children(""))
        except tk.TclError:
            new_ids = []
        if not new_ids:
            return None
        return new_ids[min(old_index, len(new_ids) - 1)]

    def _deck_card(self, card_id, board):
        for e in self.deck.entries(board):
            if e["card"]["id"] == card_id:
                return e["card"]
        return None

    def _deck_qty(self, delta):
        active = self._active_deck_selection()
        if active is None:
            self._selected_deck = None
            self._update_card_odds()
            return
        board, _tv, selected, primary = active
        changed = False
        for cid in selected:
            entry = next((
                item for item in self.deck.entries(board)
                if item["card"]["id"] == cid), None)
            if entry is None:
                continue
            # Minus adjusts quantities but deliberately does not delete the last
            # copy; the Remove button owns one-copy-at-a-time deletion.
            if delta < 0 and int(entry.get("qty", 0)) <= 1:
                continue
            self.deck.change_qty(cid, board, delta)
            changed = True
        if not changed:
            self._restore_deck_selections(selected, board, primary)
            return
        self._mark_deck_dirty()
        self._refresh_changed_deck_views(board)
        self._restore_deck_selections(selected, board, primary)
        self._update_card_odds()

    def _deck_remove(self):
        active = self._active_deck_selection()
        if active is None:
            self._selected_deck = None
            self._update_card_odds()
            return
        board, tv, selected, primary = active
        try:
            old_visible = list(tv.get_children(""))
        except tk.TclError:
            old_visible = list(selected)

        changed = False
        for cid in selected:
            if self._deck_card(cid, board) is None:
                continue
            # Remove means remove each highlighted deck entry completely.  The
            # minus button remains the quantity-decrement action.
            self.deck.remove(cid, board)
            changed = True
        if not changed:
            return

        self._mark_deck_dirty()
        self._selected_deck = None
        self._refresh_changed_deck_views(board)
        next_id = self._neighbor_after_removal(board, old_visible, primary)
        if next_id and self._restore_deck_selections((next_id,), board, next_id):
            card = self.db.get_card(next_id) or self._deck_card(next_id, board)
            if card:
                self._show_card(card)
        self._update_card_odds()

    def _deck_move(self):
        active = self._active_deck_selection()
        if active is None:
            self._selected_deck = None
            self._update_card_odds()
            return
        board, _tv, selected, primary = active
        target = "side" if board == "main" else "main"
        moved = []
        for cid in selected:
            if self._deck_card(cid, board) is None:
                continue
            self.deck.move(cid, board, target)
            moved.append(cid)
        if not moved:
            return
        self._mark_deck_dirty()
        self._selected_deck = None
        self._refresh_changed_deck_views("main", "side")
        restored = self._restore_deck_selections(moved, target, primary)
        if restored:
            card = self.db.get_card(self._selected_deck[0]) or self._deck_card(
                self._selected_deck[0], target)
            if card:
                self._show_card(card)
        self._update_card_odds()

    def _refresh_single_deck_view(self, view):
        """Refresh one deck table after sort/filter changes without touching preview."""
        board = "main" if view == "main" else "side"
        tv = self.main_tv if view == "main" else self.side_tv
        selected = tuple(tv.selection())

        entries = list(self.deck.entries(board))
        sort_col, sort_desc = self._deck_sorts[view]
        if sort_col:
            entries.sort(
                key=lambda e, c=sort_col: self._table_sort_key(
                    e["card"], c, qty=e["qty"]),
                reverse=sort_desc)
        entries = [
            e for e in entries
            if self._row_passes_table_filters(
                view, e["card"], qty=e["qty"])
        ]

        wanted_ids = {e["card"]["id"] for e in entries}
        existing = set(tv.get_children())

        # Remove only rows no longer visible instead of rebuilding the table.
        # Deck contents can change independently, so unlike immutable Search
        # results we delete hidden deck rows rather than retaining detached stale
        # items across later quantity/card edits.
        for iid in existing - wanted_ids:
            try:
                tv.delete(iid)
            except tk.TclError:
                pass

        ordinary = [
            c for c in TABLE_COLUMN_ORDER
            if c != "cost" and view in TABLE_COLUMNS[c]["views"]]
        self._update_table_headings(view, tv)

        for row_i, entry in enumerate(entries):
            card = entry["card"]
            iid = card["id"]
            try:
                costimg = self._cost_image(card.get("mana_cost", ""))
                values = tuple(
                    self._table_value(card, c, qty=entry["qty"])
                    for c in ordinary)
                if tv.exists(iid):
                    tv.move(iid, "", "end")
                    tv.item(
                        iid, image=(costimg or ""), values=values,
                        tags=("odd" if row_i % 2 else "even",))
                else:
                    tv.insert(
                        "", "end", iid=iid, text="", image=(costimg or ""),
                        values=values,
                        tags=("odd" if row_i % 2 else "even",))
            except tk.TclError:
                pass

        # Keep every surviving highlighted row selected through quantity edits,
        # sorting, and filtering; the focused row remains the preview primary.
        if selected:
            surviving = tuple(
                card_id for card_id in selected
                if card_id in wanted_ids and tv.exists(card_id))
            focus_id = self._selected_deck[0] if (
                self._selected_deck and self._selected_deck[1] == board) else None
            self._restore_deck_selections(surviving, board, focus_id)
            if not surviving:
                self._update_card_odds()

    def _refresh_changed_deck_views(self, *views):
        """Reconcile only boards changed by a deck mutation, then shared stats."""
        for view in dict.fromkeys(views):
            self._refresh_single_deck_view(view)
        self._update_board_headers()
        self._refresh_stats()
        self._refresh_comparison_window_only()

    def _refresh_deck_views(self):
        wanted = self._selected_deck

        for tv, board, view in ((self.main_tv, "main", "main"),
                                (self.side_tv, "side", "side")):
            tv.delete(*tv.get_children())
            ordinary = [c for c in TABLE_COLUMN_ORDER
                        if c != "cost" and view in TABLE_COLUMNS[c]["views"]]

            entries = list(self.deck.entries(board))
            sort_col, sort_desc = self._deck_sorts[view]
            if sort_col:
                entries.sort(
                    key=lambda e, c=sort_col: self._table_sort_key(
                        e["card"], c, qty=e["qty"]),
                    reverse=sort_desc,
                )

            entries = [
                e for e in entries
                if self._row_passes_table_filters(
                    view, e["card"], qty=e["qty"])
            ]

            self._update_table_headings(view, tv)

            for i, e in enumerate(entries):
                card = e["card"]
                costimg = self._cost_image(card.get("mana_cost", ""))
                values = tuple(
                    self._table_value(card, c, qty=e["qty"]) for c in ordinary)
                tv.insert("", "end", iid=card["id"], text="", image=(costimg or ""),
                          values=values, tags=("odd" if i % 2 else "even",))

        self._update_board_headers()
        self._refresh_stats()

        # Rebuilding the Treeviews destroys their native selection state.
        # Restore the user's active row whenever it still exists.
        if wanted:
            cid, board = wanted
            if not self._restore_deck_selection(cid, board):
                self._selected_deck = None
                self._update_card_odds()

    def _refresh_deck_format_button(self):
        value = str(self.deck_format.get() or "").strip()
        text = value.replace("_", " ").capitalize() if value else "Choose format…"
        self._deck_format_btn.configure(text=text)

    def _choose_deck_format(self):
        """Choose the deck format from the same trusted catalog Search uses."""
        current = str(self.deck_format.get() or "").strip()
        selected = {current} if current in self._format_catalog else set()

        def apply(chosen):
            value = next(iter(chosen), "")
            if not value:
                return
            self.deck_format.set(value)
            self._refresh_deck_format_button()
            self._on_deck_format_selected()

        open_search_checklist(
            self, title="Choose Deck Format",
            values=[
                (fmt, fmt.replace("_", " ").capitalize())
                for fmt in self._format_catalog
            ],
            selected=selected, apply_callback=apply,
            help_text=("Choose a format from the same Scryfall legality catalog "
                       "used by Search."),
            single_select=True)

    def _on_deck_format_selected(self, _event=None):
        """Apply the deck format selected by the shared checklist picker."""
        self._sync_deck_meta()

    def _sync_deck_meta(self):
        new_name = self.deck_name.get().strip() or "Untitled Deck"
        new_fmt = self.deck_format.get()
        changed = (new_name != self.deck.name or new_fmt != self.deck.fmt)
        format_changed = new_fmt != self.deck.fmt
        self.deck.name = new_name
        self.deck.fmt = new_fmt
        if changed:
            self._mark_deck_dirty()
            self._render_deck_tabs()
        if format_changed:
            self._refresh_stats()   # legality depends on the chosen format

    def _new_deck(self):
        """Create a fresh deck in a new browser-style tab."""
        self._append_deck_session(Deck(), path=None, dirty=False)
