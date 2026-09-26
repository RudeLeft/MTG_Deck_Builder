"""Virtualized Search Results table and complete logical-selection ownership."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from mtgdb.search.results import (
    CompactResultSelection, ResultPreparationWorker, SearchResultStore,
)
from mtgdb.ui.card_detail import _ResultsGalleryWindow
from mtgdb.ui.components import card_count_text
from mtgdb.ui.tables import TABLE_COLUMNS, TABLE_COLUMN_ORDER


log = logging.getLogger("mtg")
RESULT_LIVE_ROW_LIMIT = 128
RESULT_PREP_POLL_MS = 12


class _ResultScrollAdapter:
    """Route global wheel handling to logical Results scrolling."""

    def __init__(self, owner, tree):
        self.owner = owner
        self.tree = tree

    def yview_scroll(self, number, what):
        self.owner._result_scroll(number, what)

    def xview_scroll(self, number, what):
        self.tree.xview_scroll(number, what)


class SearchResultsMixin:
    """Own the compact Results store, async view index, and bounded Tk viewport."""

    def _initialize_search_results(self):
        self._result_store = SearchResultStore()
        self._result_view_worker = ResultPreparationWorker("search-result-view")
        self._result_vocab_worker = ResultPreparationWorker("search-result-vocabulary")
        self._result_prepare_after = None
        self._result_vocab_after = None
        self._result_vocab_callback = None
        self._result_top = 0
        self._result_window_start = 0
        self._result_slot_sources = {}
        self._result_live_slots = []
        self._result_selected_ids = CompactResultSelection(self._result_store)
        self._result_focus_id = None
        self._result_selection_anchor_id = None
        self._result_selection_sync = False
        self._result_selection_sync_after = None
        self._result_tree_configure_after = None
        self._last_result_preview_selection = None
        self._results_gallery_window = None
        self._result_diagnostics = {
            "view_preparations": 0,
            "view_prepare_seconds": 0.0,
            "vocabulary_preparations": 0,
            "vocabulary_prepare_seconds": 0.0,
            "live_tk_rows": 0,
            "logical_rows": 0,
            "rows_rebound": 0,
            "full_refills": 0,
            "ring_scrolls": 0,
        }

    def _shutdown_search_results(self):
        gallery = getattr(self, "_results_gallery_window", None)
        if gallery is not None:
            gallery.close()
        for attr in (
                "_result_prepare_after", "_result_vocab_after",
                "_result_selection_sync_after", "_result_tree_configure_after"):
            pending = getattr(self, attr, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        for worker in (
                getattr(self, "_result_view_worker", None),
                getattr(self, "_result_vocab_worker", None)):
            if worker is not None:
                worker.shutdown()

    def _install_result_tree_virtualization(self, vertical_scrollbar):
        """Attach logical scrolling/key navigation to the Results Treeview."""
        self._results_vsb = vertical_scrollbar
        self._result_scroll_adapter = _ResultScrollAdapter(self, self.results_tv)
        self._register_scrollable(
            self.results_tv, target=self._result_scroll_adapter)
        self.results_tv.bind("<Prior>", lambda event: self._result_page_key(event, -1))
        self.results_tv.bind("<Next>", lambda event: self._result_page_key(event, 1))
        self.results_tv.bind("<Home>", lambda event: self._result_absolute_key(event, 0))
        self.results_tv.bind("<End>", lambda event: self._result_absolute_key(event, -1))
        self.results_tv.bind("<Up>", lambda event: self._result_arrow_key(event, -1))
        self.results_tv.bind("<Down>", lambda event: self._result_arrow_key(event, 1))
        # Stop the Treeview class binding from scrolling its small physical
        # slot set. Pointer-wheel movement must always address the complete
        # logical Results index.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.results_tv.bind(sequence, self._on_result_mousewheel, add="+")
        for sequence in (
                "<Shift-MouseWheel>", "<Shift-Button-4>",
                "<Shift-Button-5>"):
            self.results_tv.bind(sequence, self._on_result_shift_mousewheel, add="+")
        self.results_tv.bind("<Configure>", self._on_result_tree_configure, add="+")
        self._update_result_scrollbar()

    def _reset_results_viewport(self):
        """Scroll Results back to its first row without touching the data.

        Search Clear leaves the logical result set alone but shortens the form
        above it, so the viewport has to be told to return to the top.
        """
        self._result_top = 0
        self._result_window_start = 0
        try:
            self._render_results()
        except (AttributeError, tk.TclError):
            pass

    def _set_result_store(self, store, signature=None):
        """Atomically replace the complete logical result set."""
        if not isinstance(store, SearchResultStore):
            store = SearchResultStore.from_rows(store or ())
        self._result_store = store
        self._active_search_signature = signature
        self._result_top = 0
        self._result_window_start = 0
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            selection.bind_store(store)
        else:
            selection.clear()
        self._result_focus_id = None
        self._result_selection_anchor_id = None
        self._last_result_preview_selection = None
        self._result_diagnostics["logical_rows"] = store.logical_count
        self._sync_results_gallery()

    def _render_results(self):
        """Prepare the complete Results view off Tk, keeping the old index usable."""
        self._update_table_headings("results", self.results_tv)
        store = self._result_store
        total = store.logical_count
        self._result_diagnostics["logical_rows"] = total
        if not self._table_filters["results"] and not self._sort_col:
            store.reset_view()
            self._apply_prepared_result_view()
            return

        self._render_results_count()
        status = getattr(self, "_results_status", None)
        if status is not None:
            status.start(f"{self._results_count_text()} · Updating…")
        self._result_view_worker.submit_view(
            store, self._table_filters["results"],
            sort_col=self._sort_col, sort_desc=self._sort_desc)
        self._start_result_prepare_pump()

    def _start_result_prepare_pump(self):
        if self._result_prepare_after is not None:
            try:
                self.after_cancel(self._result_prepare_after)
            except tk.TclError:
                pass
        self._result_prepare_after = self.after(
            RESULT_PREP_POLL_MS, self._poll_result_prepare)

    def _poll_result_prepare(self):
        self._result_prepare_after = None
        event = self._result_view_worker.poll_latest()
        if event is None:
            self._result_prepare_after = self.after(
                RESULT_PREP_POLL_MS, self._poll_result_prepare)
            return
        if event.store is not self._result_store:
            return
        if event.kind == "error":
            log.error("Results preparation failed: %s", event.payload)
            self._set_result_count()
            return
        self._result_diagnostics["view_preparations"] += 1
        self._result_diagnostics["view_prepare_seconds"] = float(event.elapsed)
        self._result_store.swap_view_index(event.payload)
        self._apply_prepared_result_view()

    def _apply_prepared_result_view(self):
        store = self._result_store
        focus_position = store.view_position_for_id(self._result_focus_id)
        capacity = self._result_visible_capacity()
        max_top = max(0, store.visible_count - capacity)
        if focus_position is not None:
            if focus_position < self._result_top:
                self._result_top = focus_position
            elif focus_position >= self._result_top + capacity:
                self._result_top = focus_position - capacity + 1
        self._result_top = max(0, min(self._result_top, max_top))
        self._populate_result_window(force=True)
        self._set_result_count()
        self._restore_pending_result_selection()
        # Table filters can hide or reveal selected logical Results without a
        # Treeview selection event. Refresh the comparison source count after
        # every accepted logical-view swap so Cards Selected matches the current
        # visible Results selection plus deck-board highlights.
        self._update_comparison_bar()
        self._sync_results_gallery()

    def _results_count_text(self):
        total = self._result_store.logical_count
        visible = self._result_store.visible_count
        filtered = bool(self._table_filters.get("results"))
        count = visible if filtered else total
        return f"RESULTS | {card_count_text(count)}"

    def _render_results_count(self):
        """Idle RESULTS header paint (the PulseStatus restore target)."""
        try:
            self.results_count_lbl.configure(
                text=self._results_count_text(), style="Section.TLabel")
        except (tk.TclError, AttributeError):
            pass
        self._sync_results_gallery_button()

    def _set_result_count(self):
        # Showing the count means "not working": end any working cue (honouring
        # its minimum dwell) and paint the idle header.
        status = getattr(self, "_results_status", None)
        if status is not None:
            status.stop()
        else:
            self._render_results_count()
        # ...and the centered "Searching…" cue ends with it: the results this
        # Search was producing are now on screen.
        search_status = getattr(self, "_search_status", None)
        if search_status is not None:
            search_status.stop()

    def _result_gallery_count(self):
        return self._result_store.visible_count

    def _result_gallery_card_at(self, position):
        try:
            source = self._result_store.source_index_at_view(position)
        except (IndexError, TypeError, ValueError):
            return None
        return self._full_result_at(source)

    def _sync_results_gallery_button(self):
        button = getattr(self, "card_gallery_btn", None)
        if button is None:
            return
        try:
            button.state(
                ["!disabled"] if self._result_store.visible_count else ["disabled"])
        except tk.TclError:
            pass

    def _sync_results_gallery(self):
        self._sync_results_gallery_button()
        gallery = getattr(self, "_results_gallery_window", None)
        if gallery is not None:
            gallery.refresh_results()

    def _open_results_gallery(self):
        if not self._result_store.visible_count:
            return
        gallery = getattr(self, "_results_gallery_window", None)
        if gallery is not None:
            try:
                if gallery.top.winfo_exists():
                    gallery.refresh_results()
                    gallery.lift()
                    return
            except tk.TclError:
                pass
        self._results_gallery_window = _ResultsGalleryWindow(
            self, count_fn=self._result_gallery_count,
            card_at_fn=self._result_gallery_card_at,
            context_menu_fn=self._show_gallery_card_context_menu)

    def _result_visible_capacity(self):
        tv = self.results_tv
        # Before the table is laid out its height option is only the minimum
        # request (LAY-011); the row pool starts from the size it grows to.
        fallback = 16
        try:
            height = int(tv.winfo_height())
            rowheight = int(ttk.Style(tv).lookup("Treeview", "rowheight") or 20)
            if height > rowheight * 2:
                return max(1, min(RESULT_LIVE_ROW_LIMIT, (height - rowheight) // rowheight))
        except (tk.TclError, TypeError, ValueError):
            pass
        return min(RESULT_LIVE_ROW_LIMIT, fallback)

    def _desired_result_slot_count(self):
        visible = self._result_store.visible_count
        if not visible:
            return 0
        capacity = self._result_visible_capacity()
        # Keep a generous reusable buffer without ever exceeding the hard ceiling.
        return min(visible, RESULT_LIVE_ROW_LIMIT, max(75, capacity + 32))

    def _result_ordinary_columns(self):
        return [
            column for column in TABLE_COLUMN_ORDER
            if column != "cost" and "results" in TABLE_COLUMNS[column]["views"]
        ]

    def _bind_result_slot(self, slot, view_position, ordinary):
        """Show one result in one pooled row, or empty the row past the end.

        The pool is larger than the viewport, so near the end of a result set
        some slots have no row to show. Returning early and leaving those
        slots as they were made the ring carry stale rows back to the top of
        the viewport on the next scroll: the rows nearest the end of a long
        result set stopped moving while the rest scrolled past them.
        """
        store = self._result_store
        if view_position < 0 or view_position >= store.visible_count:
            self._result_slot_sources.pop(slot, None)
            try:
                self.results_tv.item(
                    slot, text="", image="",
                    values=tuple("" for _ in ordinary), tags=())
            except tk.TclError:
                pass
            return False
        source = store.source_index_at_view(view_position)
        row = store.row_at_source(source)
        self._result_slot_sources[slot] = source
        cost_image = self._cost_image(row.get("mana_cost", ""))
        values = tuple(self._table_value(row, column) for column in ordinary)
        try:
            self.results_tv.item(
                slot, text="", image=(cost_image or ""), values=values,
                tags=("odd" if view_position % 2 else "even",))
        except tk.TclError:
            return False
        self._result_diagnostics["rows_rebound"] = self._result_diagnostics.get("rows_rebound", 0) + 1
        return True

    def _populate_result_window(self, force=False):
        """Render a bounded viewport; small scrolls rotate/rebind only exposed rows."""
        tv = self.results_tv
        store = self._result_store
        visible_total = store.visible_count
        capacity = self._result_visible_capacity()
        max_top = max(0, visible_total - capacity)
        self._result_top = max(0, min(int(self._result_top), max_top))
        desired = self._desired_result_slot_count()
        start = self._result_top
        if (not force and start == self._result_window_start
                and len(self._result_live_slots) == desired):
            self._update_result_scrollbar()
            return

        ordinary = self._result_ordinary_columns()
        self._begin_result_selection_sync()
        try:
            # Allocate the physical pool only when capacity changes.
            while len(self._result_live_slots) < desired:
                slot = f"vr{len(self._result_live_slots):03d}"
                try:
                    tv.insert("", "end", iid=slot, text="", values=())
                except tk.TclError:
                    break
                self._result_live_slots.append(slot)
            while len(self._result_live_slots) > desired:
                slot = self._result_live_slots.pop()
                self._result_slot_sources.pop(slot, None)
                try:
                    if tv.exists(slot):
                        tv.delete(slot)
                except tk.TclError:
                    pass

            delta = start - self._result_window_start
            can_rotate = (
                not force and desired and len(self._result_live_slots) == desired
                and self._result_slot_sources
                and 0 < abs(delta) <= min(16, max(1, desired // 3))
            )
            if can_rotate:
                self._result_diagnostics["ring_scrolls"] = self._result_diagnostics.get("ring_scrolls", 0) + 1
                if delta > 0:
                    moved = self._result_live_slots[:delta]
                    self._result_live_slots[:] = self._result_live_slots[delta:] + moved
                    # Rows leaving the top go to the bottom, and they go there
                    # one at a time. Moving each to its final index instead --
                    # 72, then 73, then 74 of 75 -- lands every one of them
                    # ahead of rows that had not moved yet, because each move
                    # is a remove and an insert into a list that is one short.
                    # The pool came out interleaved, and the damage stayed
                    # below the viewport until enough scrolling brought it back
                    # to the top as rows that repeated or sat still.
                    rotated = [(slot, "end", desired - delta + index)
                               for index, slot in enumerate(moved)]
                else:
                    count = -delta
                    moved = self._result_live_slots[-count:]
                    self._result_live_slots[:] = moved + self._result_live_slots[:-count]
                    # Rows entering at the top do land on their final index:
                    # each is removed from behind the ones already placed.
                    rotated = [(slot, index, index)
                               for index, slot in enumerate(moved)]
                for slot, target, offset in rotated:
                    # The move happens whether or not the slot had a row to
                    # show: the ring has already been rotated, so a slot left
                    # where it was puts the tree out of step with the order the
                    # next scroll assumes.
                    self._bind_result_slot(slot, start + offset, ordinary)
                    try:
                        tv.move(slot, "", target)
                    except tk.TclError:
                        self._result_window_start = -1
                        return
            else:
                self._result_diagnostics["full_refills"] = self._result_diagnostics.get("full_refills", 0) + 1
                self._result_slot_sources.clear()
                for offset, slot in enumerate(self._result_live_slots):
                    self._bind_result_slot(slot, start + offset, ordinary)
                    try:
                        tv.move(slot, "", offset)
                    except tk.TclError:
                        self._result_window_start = -1
                        return
            try:
                tv.yview_moveto(0)
            except tk.TclError:
                pass
            self._apply_result_native_selection()
        finally:
            self._end_result_selection_sync_later()
        self._result_window_start = start
        self._result_diagnostics["live_tk_rows"] = len(self._result_live_slots)
        self._update_result_scrollbar()

    def _begin_result_selection_sync(self):
        self._result_selection_sync = True
        pending = self._result_selection_sync_after
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
            self._result_selection_sync_after = None

    def _end_result_selection_sync_later(self):
        try:
            self._result_selection_sync_after = self.after_idle(
                self._end_result_selection_sync)
        except tk.TclError:
            self._result_selection_sync = False

    def _end_result_selection_sync(self):
        self._result_selection_sync_after = None
        self._result_selection_sync = False

    def _apply_result_native_selection(self):
        tv = self.results_tv
        selected_slots = []
        focus_slot = None
        for slot, source in self._result_slot_sources.items():
            card_id = self._result_store.row_at_source(source).get("id")
            card_id = str(card_id) if card_id else ""
            if card_id in self._result_selected_ids:
                selected_slots.append(slot)
            if card_id and card_id == self._result_focus_id:
                focus_slot = slot
        try:
            if selected_slots:
                tv.selection_set(*selected_slots)
            else:
                current = tv.selection()
                if current:
                    tv.selection_remove(*current)
            if focus_slot is not None:
                tv.focus(focus_slot)
        except tk.TclError:
            pass

    def _result_source_for_iid(self, iid):
        return self._result_slot_sources.get(str(iid))

    def _result_id_for_iid(self, iid):
        source = self._result_source_for_iid(iid)
        if source is None:
            return None
        card_id = self._result_store.row_at_source(source).get("id")
        return str(card_id) if card_id else None

    def _selected_result_iids(self):
        """Return live slot IDs currently highlighted in visible logical order."""
        try:
            selected = set(self.results_tv.selection())
        except tk.TclError:
            return ()
        return tuple(slot for slot in self._result_live_slots if slot in selected)

    def _primary_result_iid(self):
        selected = set(self._selected_result_iids())
        if not selected:
            return None
        try:
            focused = self.results_tv.focus()
        except tk.TclError:
            focused = ""
        return focused if focused in selected else next(
            (slot for slot in self._result_live_slots if slot in selected), None)

    def _selected_result_ids_in_view_order(self, limit=None):
        """Return selected exact IDs in logical view order without hydration."""
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            return selection.ids_in_view_order(limit=limit)
        ids = self._result_store.ids_in_view_order(selection)
        if limit is None:
            return ids
        return ids[:max(0, int(limit))]

    def _replace_result_selection(self, card_ids):
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            selection.replace(card_ids)
        else:
            self._result_selected_ids = set(card_ids)

    def _select_result_view_range(self, first, last, *, additive=False):
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            selection.select_view_range(first, last, additive=additive)
            return
        chosen = {
            str(self._result_store.row_at_view(position).get("id") or "")
            for position in range(min(first, last), max(first, last) + 1)
        }
        chosen.discard("")
        if additive:
            selection.update(chosen)
        else:
            self._result_selected_ids = chosen

    def _selected_result_visible_count(self):
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            return selection.visible_count()
        return len(self._result_store.ids_in_view_order(selection))

    def _result_selection_contains_visible(self, card_id):
        selection = self._result_selected_ids
        if isinstance(selection, CompactResultSelection):
            return selection.contains_visible_id(card_id)
        return (card_id in selection
                and self._result_store.view_position_for_id(card_id) is not None)

    def _selected_results(self, limit=None):
        """Hydrate selected cards still present in the complete current view."""
        cards = []
        for card_id in self._selected_result_ids_in_view_order(limit=limit):
            source = self._result_store.source_index_for_id(card_id)
            if source is None:
                continue
            card = self._full_result_at(source)
            if card:
                cards.append(card)
        return cards

    def _selected_result(self):
        card_id = self._result_focus_id
        if not card_id:
            iid = self._primary_result_iid()
            card_id = self._result_id_for_iid(iid) if iid else None
        source = self._result_store.source_index_for_id(card_id)
        return self._full_result_at(source) if source is not None else None

    def _full_result_at(self, index):
        """Hydrate one compact result row by exact printing ID only when needed."""
        try:
            return self._result_store.hydrate(index, self.search_repository)
        except (IndexError, TypeError, ValueError):
            return None

    def _on_result_select(self, _event=None):
        if self._result_selection_sync:
            return
        tv = self.results_tv
        try:
            selection_order = tv.selection()
            selected_slots = set(selection_order)
            focused = tv.focus()
        except tk.TclError:
            return
        live_ids = {
            card_id for slot in self._result_live_slots
            for card_id in [self._result_id_for_iid(slot)] if card_id
        }
        self._result_selected_ids.difference_update(live_ids)
        for slot in selected_slots:
            card_id = self._result_id_for_iid(slot)
            if card_id:
                self._result_selected_ids.add(card_id)
        focused_id = self._result_id_for_iid(focused)
        if focused_id and focused_id in self._result_selected_ids:
            self._result_focus_id = focused_id
            self._result_selection_anchor_id = focused_id
        elif not self._result_selected_ids:
            self._result_focus_id = None
        else:
            # Ctrl-click can deselect the previously-focused row while other
            # rows remain selected: Tk's own focus() still names that now-
            # deselected row (neither branch above fires), so the tracked
            # focus stayed pointed at a card no longer part of the selection
            # and the preview kept showing it. Re-anchor to whichever
            # selected row is now first in on-screen order instead.
            for slot in selection_order:
                candidate = self._result_id_for_iid(slot)
                if candidate and candidate in self._result_selected_ids:
                    self._result_focus_id = candidate
                    self._result_selection_anchor_id = candidate
                    break
        self._result_selection_changed()

    def _result_selection_changed(self):
        self._update_comparison_bar()
        card = self._selected_result()
        if not card:
            return
        selection_key = (self._active_search_signature, self._result_focus_id)
        if selection_key != self._last_result_preview_selection:
            self._last_result_preview_selection = selection_key
            self._show_card(card)
        set_name = card.get("set_name") or (card.get("set_code") or "").upper()
        type_line = card.get("type_line") or ""
        bits = [card.get("name") or "(unnamed card)"]
        if type_line:
            bits.append(type_line)
        if set_name:
            bits.append(set_name)
        self._status("  •  ".join(bits))

    def _result_select_card_id(self, card_id, *, additive=False, ensure_visible=True):
        card_id = str(card_id or "")
        position = self._result_store.view_position_for_id(card_id)
        if position is None:
            return False
        if not additive:
            self._result_selected_ids.clear()
        self._result_selected_ids.add(card_id)
        self._result_focus_id = card_id
        self._result_selection_anchor_id = card_id
        if ensure_visible:
            capacity = self._result_visible_capacity()
            if position < self._result_top:
                self._result_top = position
            elif position >= self._result_top + capacity:
                self._result_top = max(0, position - capacity + 1)
        self._populate_result_window(force=True)
        return True

    def _clear_result_selection(self):
        self._result_selected_ids.clear()
        self._result_focus_id = None
        self._begin_result_selection_sync()
        try:
            self.results_tv.selection_remove(*self.results_tv.selection())
        except tk.TclError:
            pass
        finally:
            self._end_result_selection_sync_later()
        self._update_comparison_bar()

    def _on_result_mousewheel(self, event):
        units = self._wheel_units(event)
        if units:
            self._result_scroll(units, "units")
        return "break"

    def _on_result_shift_mousewheel(self, event):
        units = self._wheel_units(event)
        if units:
            try:
                self.results_tv.xview_scroll(units, "units")
            except tk.TclError:
                pass
        return "break"

    def _result_scroll(self, number, what="units"):
        amount = int(number)
        if str(what) == "pages":
            amount *= max(1, self._result_visible_capacity() - 1)
        self._set_result_top(self._result_top + amount)

    def _result_yview(self, *args):
        if not args:
            total = self._result_store.visible_count
            capacity = self._result_visible_capacity()
            if not total:
                return (0.0, 1.0)
            return (
                self._result_top / total,
                min(1.0, (self._result_top + capacity) / total),
            )
        if args[0] == "moveto" and len(args) >= 2:
            try:
                fraction = float(args[1])
            except (TypeError, ValueError):
                return
            total = self._result_store.visible_count
            self._set_result_top(round(max(0.0, min(1.0, fraction)) * total))
        elif args[0] == "scroll" and len(args) >= 3:
            self._result_scroll(args[1], args[2])

    def _set_result_top(self, top):
        total = self._result_store.visible_count
        capacity = self._result_visible_capacity()
        max_top = max(0, total - capacity)
        top = max(0, min(int(top), max_top))
        if top == self._result_top and self._result_window_start == top:
            self._update_result_scrollbar()
            return
        self._result_top = top
        self._populate_result_window(force=False)

    def _update_result_scrollbar(self):
        scrollbar = getattr(self, "_results_vsb", None)
        if scrollbar is None:
            return
        total = self._result_store.visible_count
        capacity = self._result_visible_capacity()
        if total <= 0:
            first, last = 0.0, 1.0
        else:
            first = min(1.0, self._result_top / total)
            last = min(1.0, (self._result_top + capacity) / total)
        try:
            scrollbar.set(first, last)
        except tk.TclError:
            pass

    def _on_result_tree_configure(self, _event=None):
        if getattr(self, "_window_in_motion", False):
            return
        pending = self._result_tree_configure_after
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        try:
            self._result_tree_configure_after = self.after(
                45, self._result_tree_configured)
        except tk.TclError:
            pass

    def _result_tree_configured(self):
        self._result_tree_configure_after = None
        if getattr(self, "_window_in_motion", False):
            return
        self._populate_result_window(force=True)

    def _result_navigation_target(self, delta=None, absolute=None):
        total = self._result_store.visible_count
        if not total:
            return None
        current = self._result_store.view_position_for_id(self._result_focus_id)
        if current is None:
            current = self._result_top
        if absolute is not None:
            return total - 1 if absolute < 0 else max(0, min(absolute, total - 1))
        return max(0, min(current + int(delta or 0), total - 1))

    def _result_apply_keyboard_target(self, event, target):
        if target is None:
            return "break"
        row = self._result_store.row_at_view(target)
        card_id = str(row.get("id") or "")
        if not card_id:
            return "break"
        shift = bool(getattr(event, "state", 0) & 0x0001)
        control = bool(getattr(event, "state", 0) & 0x0004)
        if shift:
            anchor_id = self._result_selection_anchor_id or self._result_focus_id or card_id
            anchor = self._result_store.view_position_for_id(anchor_id)
            if anchor is None:
                anchor = target
            self._select_result_view_range(
                anchor, target, additive=control)
        elif not control:
            self._replace_result_selection((card_id,))
            self._result_selection_anchor_id = card_id
        else:
            self._result_selected_ids.add(card_id)
        self._result_focus_id = card_id
        capacity = self._result_visible_capacity()
        if target < self._result_top:
            self._result_top = target
        elif target >= self._result_top + capacity:
            self._result_top = target - capacity + 1
        self._populate_result_window(force=True)
        self._result_selection_changed()
        return "break"

    def _result_arrow_key(self, event, delta):
        return self._result_apply_keyboard_target(
            event, self._result_navigation_target(delta=delta))

    def _result_page_key(self, event, direction):
        step = max(1, self._result_visible_capacity() - 1)
        return self._result_apply_keyboard_target(
            event, self._result_navigation_target(delta=direction * step))

    def _result_absolute_key(self, event, absolute):
        return self._result_apply_keyboard_target(
            event, self._result_navigation_target(absolute=absolute))

    def _request_result_vocabulary(self, key, callback):
        """Prepare one complete-result-set values vocabulary off the Tk thread."""
        self._result_vocab_callback = callback
        self._result_vocab_worker.submit_vocabulary(
            self._result_store, key, self._table_filters["results"])
        if self._result_vocab_after is not None:
            try:
                self.after_cancel(self._result_vocab_after)
            except tk.TclError:
                pass
        self._result_vocab_after = self.after(
            RESULT_PREP_POLL_MS, self._poll_result_vocabulary)

    def _poll_result_vocabulary(self):
        self._result_vocab_after = None
        event = self._result_vocab_worker.poll_latest()
        if event is None:
            self._result_vocab_after = self.after(
                RESULT_PREP_POLL_MS, self._poll_result_vocabulary)
            return
        if event.store is not self._result_store:
            return
        callback = self._result_vocab_callback
        self._result_vocab_callback = None
        if event.kind == "error":
            log.error("Results vocabulary preparation failed: %s", event.payload)
            values = ()
        else:
            self._result_diagnostics["vocabulary_preparations"] += 1
            self._result_diagnostics["vocabulary_prepare_seconds"] = float(event.elapsed)
            values = event.payload
        if callback is not None:
            callback(tuple(values))

    def result_performance_info(self):
        return {
            **self._result_diagnostics,
            **self._result_store.diagnostics(),
            "live_tk_rows": len(self._result_live_slots),
            "viewport_top": self._result_top,
            "selected_ids": len(self._result_selected_ids),
            "selection_bytes": (
                self._result_selected_ids.diagnostics().get("bytes", 0)
                if isinstance(self._result_selected_ids, CompactResultSelection)
                else 0),
        }
