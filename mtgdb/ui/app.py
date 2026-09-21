"""
Tkinter desktop GUI for the deck builder.

Layout is three deterministic columns:
  left    search controls + results
  middle  fixed card preview above a scrollable deck-statistics dashboard
  right   current deck with the one retained Mainboard/Sideboard splitter

Network work (bulk sync, image downloads) runs on background threads and posts
results back to the UI thread via self.after(...), so the window never freezes.
"""

from collections import OrderedDict
import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

from mtgdb.comparison.models import ComparisonCollection
from mtgdb.database.sync import DatabaseSyncController, DatabaseSyncService
from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession, DeckSessionManager
from mtgdb.images.service import CardImageService
from mtgdb.printing.service import PrintController, PrintTemplateService
from mtgdb.search.catalogs import SearchCatalogController
from mtgdb.search.context import SearchContextController
from mtgdb.search.controller import SearchController
from mtgdb.search.repository import SearchRepository
from mtgdb.ui.card_detail import CardDetailMixin
from mtgdb.ui.comparison_controls import ComparisonFeatureMixin
from mtgdb.ui.components import AppMenubutton, ToolTip
from mtgdb.ui.database_sync import DatabaseSyncMixin
from mtgdb.ui.deck import DeckEditorMixin
from mtgdb.ui.deck_files import DeckFileWorkflowMixin
from mtgdb.ui.deck_stats import DeckStatsMixin
from mtgdb.ui.table_filters import TableFilterMixin
from mtgdb.ui.mana import ManaSymbolsMixin
from mtgdb.ui.printing import PrintingMixin
from mtgdb.ui.results import SearchResultsMixin
from mtgdb.ui.search import SearchFeatureMixin
from mtgdb.ui.set_filters import SetFilterSupportMixin
from mtgdb.ui.styles import install_ui_styles
from mtgdb.ui.tables import TableInfrastructureMixin
from mtgdb.ui.tokens import (
    MAIN_CARD_PREVIEW_HEIGHT, MAIN_CENTER_COLUMN_WIDTH, MAIN_DECK_WEIGHT,
    MAIN_SEARCH_WEIGHT, MAIN_SIDE_MIN_WIDTH, PANEL_PADDING, WINDOW_GUTTER,
)
from mtgdb.ui.window import WindowServicesMixin
from mtgdb.ui.workspace import WorkspaceMixin

log = logging.getLogger("mtg")

class DeckBuilderApp(
        WorkspaceMixin, DatabaseSyncMixin, PrintingMixin,
        DeckEditorMixin, DeckFileWorkflowMixin, DeckStatsMixin, ComparisonFeatureMixin,
        ManaSymbolsMixin, SetFilterSupportMixin,
        SearchFeatureMixin, TableFilterMixin, SearchResultsMixin,
        TableInfrastructureMixin, CardDetailMixin,
        WindowServicesMixin, tk.Tk):
    def __init__(self, db, image_cache_dir, data_dir, log_path=None):
        super().__init__()
        # Construct the complete styled root while hidden so Windows never shows
        # Tk's default light client area as an intermediate startup frame.
        self.withdraw()
        self.db = db
        self.search_repository = SearchRepository(db)
        self.search_controller = SearchController(self.search_repository)
        self.search_catalog_controller = SearchCatalogController(self.search_repository)
        self.search_context_controller = SearchContextController(self.search_repository)
        self.database_sync_controller = DatabaseSyncController(
            DatabaseSyncService(db))
        self.print_controller = PrintController(PrintTemplateService())
        self.deck = Deck()
        # Multi-deck workspace.  The physical Mainboard/Sideboard widgets are
        # shared; each session owns its Deck plus deck-specific view state.
        self.deck_sessions = DeckSessionManager([DeckSession(self.deck)])
        self._deck_tab_widgets = []
        self._deck_tab_bar = None
        self._deck_plus_btn = None
        self.image_cache_dir = image_cache_dir
        self.data_dir = data_dir
        self.log_path = log_path
        os.makedirs(image_cache_dir, exist_ok=True)
        self.card_image_service = CardImageService(image_cache_dir)
        self._initialize_card_detail()
        self._initialize_printing()
        self.comparison = ComparisonCollection()
        self._initialize_comparison()
        self._initialize_search_results()
        self._selected_deck = None    # (card_id, board) selected in deck views
        self._sort_col = None         # active result-sort column
        self._sort_desc = False       # result sort direction
        self._deck_sorts = {
            "main": [None, False],
            "side": [None, False],
        }                             # independent deck-table sort states
        self._sym_keys = set()        # available mana-symbol keys
        self._sym_scaled = OrderedDict()  # bounded (token, height) -> PIL image
        self._cost_cache = OrderedDict()  # bounded (cost_str, height) -> PhotoImage
        self._table_filters = {"results": {}, "main": {}, "side": {}}
        # Pointer-aware mouse-wheel routing for every registered scrollable pane.
        self._scroll_targets = {}
        self.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.bind_all("<Shift-MouseWheel>", self._on_global_shift_mousewheel, add="+")
        self.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.bind_all("<Button-5>", self._on_global_mousewheel, add="+")
        self._filter_popup = None
        self._filter_popup_view = None
        self._filter_popup_col = None
        # Coalesce high-frequency resize/redraw work and long result-table paints.
        self._curve_resize_after = None
        self._search_poll_after = None
        self._search_catalog_poll_after = None
        self._search_catalog_loading = False
        self._search_catalog_scope = None
        self._pending_catalog_filter_state = None
        self._pending_search_request = False
        self._format_catalog = []
        self._format_catalog_by_status = {}
        self._rarity_catalog = []
        self._card_type_catalog = []
        self._keyword_catalog = []
        self._subtype_catalog = []
        self._active_search_signature = None
        self._window_in_motion = False
        self._tooltips = []
        self._initialize_popup_performance()
        self._initialize_layout_motion()
        self._initialize_database_sync()
        self._initialize_workspace(self.data_dir)
        self._initialize_table_infrastructure(
            os.path.join(self.data_dir, "ui_preferences.json"))

        self.title("MTG Deck Builder")
        self.geometry("1380x860")
        self.minsize(1170, 700)

        self._setup_style()
        self._make_pips()
        self._load_symbol_keys()
        self._set_window_icon()
        self._build_menu()
        self._build_body()
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self._refresh_deck_views()
        # Establish the final root geometry while the native window is still
        # hidden.  A visible 1380x860 frame followed by one or more delayed
        # maximize calls is particularly noticeable on Windows and defeats the
        # hidden-first/no-flash startup contract.
        self._maximize_window()
        self._set_dark_titlebar()
        # First visible root frame is already fully styled and populated.
        self.deiconify()
        # Some Windows window managers only honour ``zoomed`` after mapping;
        # one idle retry is enough. Re-apply the DWM frame colors after mapping
        # because Windows can recreate the non-client frame when zoomed.
        def finalize_native_frame():
            self._maximize_window()
            self._set_dark_titlebar_for(self, frame_changed=True)
            self._schedule_dark_titlebar_refresh(self)
        self.after_idle(finalize_native_frame)
        # Trusted taxonomy and workspace restoration start after first paint and
        # remain background work.
        self.after_idle(self._start_post_paint_initialization)

        # Automatic database maintenance: first launch always downloads;
        # subsequent launches refresh only after 48 hours.
        self.bind("<Configure>", self._on_root_configure, add="+")
        self.after(350, self._maybe_auto_sync)

    # ======================================================================
    # look & feel
    # ======================================================================


    def _setup_style(self):
        """Install the centralized role-based application component system."""
        install_ui_styles(self)


    # ---- mana symbols in casting costs -----------------------------------


    # ======================================================================
    # layout
    # ======================================================================

    def _add_tooltip(self, widget, text, delay=450, wraplength=360):
        """Attach a dark tooltip and release it when its owning control dies."""
        tip = ToolTip(widget, text, delay=delay, wraplength=wraplength)
        self._tooltips.append(tip)
        # Marked so a caller adding tooltips in bulk can tell which controls
        # already explain themselves; two tooltips on one widget both fire.
        try:
            widget._mtg_tooltip = tip
        except AttributeError:
            pass

        def release(event, *, control=widget, tooltip=tip):
            if event.widget is not control:
                return
            try:
                self._tooltips.remove(tooltip)
            except ValueError:
                pass

        widget.bind("<Destroy>", release, add="+")
        return tip


    def _build_menu(self):
        """Build a dark in-window menu bar instead of Tk's light native bar."""
        bar = ttk.Frame(self, style="MenuBar.TFrame", padding=(4, 2))
        bar.pack(fill="x", side="top")

        file_btn = AppMenubutton(bar, text="File", role="menu")
        filemenu = self._dark_menu(file_btn)
        filemenu.add_command(label="New Deck", command=self._new_deck)
        filemenu.add_command(label="Open Deck...", command=self._open_deck)
        filemenu.add_command(label="Save Deck As...", command=self._save_deck)
        filemenu.add_command(label="Export JSON...", command=self._export_all_decks_json)
        filemenu.add_separator()
        filemenu.add_command(label="Close Deck", command=self._close_active_deck)
        file_btn.configure(menu=filemenu)
        file_btn.pack(side="left")

        db_btn = AppMenubutton(bar, text="Database", role="menu")
        dbmenu = self._dark_menu(db_btn)
        dbmenu.add_command(label="Update Database", command=lambda: self._sync_db(reason="manual"))
        db_btn.configure(menu=dbmenu)
        db_btn.pack(side="left", padx=(2, 0))

        print_btn = AppMenubutton(bar, text="Print Deck", role="menu")
        printmenu = self._dark_menu(print_btn)
        printmenu.add_command(label="Create Print Template...", command=self._create_print_template)
        print_btn.configure(menu=printmenu)
        print_btn.pack(side="left", padx=(2, 0))


    def _bind_debounced_wrap(self, label, min_width=140, padding=8,
                              delay_ms=35):
        """Update label wrapping after resize bursts instead of on every pixel."""
        def schedule(event):
            pending = getattr(label, "_wrap_after", None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
            width = max(min_width, int(event.width) - padding)
            def apply():
                try:
                    if label.winfo_exists():
                        label.configure(wraplength=width)
                except tk.TclError:
                    pass
                label._wrap_after = None
            label._wrap_after = self.after(delay_ms, apply)
        label.bind("<Configure>", schedule, add="+")

    def _trace_write_debounced(
            self, variable, callback, delay_ms=70, *, lifecycle_widget):
        """Debounce a variable trace only for the lifetime of one Tk widget."""
        state = {
            "active": True,
            "after": None,
            "trace": None,
            "callback": callback,
        }

        def cancel_pending():
            pending = state["after"]
            state["after"] = None
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass

        def cleanup(event=None):
            if event is not None and event.widget is not lifecycle_widget:
                return
            if not state["active"]:
                return
            state["active"] = False
            cancel_pending()
            trace_id = state["trace"]
            state["trace"] = None
            if trace_id is not None:
                try:
                    variable.trace_remove("write", trace_id)
                except (tk.TclError, ValueError):
                    pass
            # A bound callback commonly owns the transient dialog. Drop that
            # final reference even if Tcl has already discarded the trace.
            state["callback"] = None

        def schedule(*_args):
            if not state["active"]:
                return
            cancel_pending()

            def fire():
                state["after"] = None
                if not state["active"]:
                    return
                current_callback = state["callback"]
                if current_callback is None:
                    return
                try:
                    current_callback()
                except tk.TclError:
                    pass

            try:
                state["after"] = self.after(delay_ms, fire)
            except tk.TclError:
                cleanup()

        state["trace"] = variable.trace_add("write", schedule)
        try:
            lifecycle_widget.bind("<Destroy>", cleanup, add="+")
        except tk.TclError:
            cleanup()
        return cleanup

    def _on_root_configure(self, event):
        """Route root movement through the same motion controller as sash drags."""
        if event.widget is not self:
            return
        self._touch_layout_motion()

    def _build_body(self):
        outer = ttk.Frame(self, padding=WINDOW_GUTTER, style="Bg.TFrame")
        outer.pack(fill="both", expand=True)

        # The main shell intentionally has no draggable sashes.  The center
        # column is fixed so the complete card preview and its action row never
        # get squeezed; Search and Deck share every remaining horizontal pixel.
        main = ttk.Frame(outer, style="Bg.TFrame")
        main.pack(fill="both", expand=True)
        main.rowconfigure(0, weight=1)
        main.columnconfigure(
            0, weight=MAIN_SEARCH_WEIGHT, minsize=MAIN_SIDE_MIN_WIDTH,
            uniform="main_side")
        main.columnconfigure(1, weight=0, minsize=1)
        main.columnconfigure(2, weight=0, minsize=MAIN_CENTER_COLUMN_WIDTH)
        main.columnconfigure(3, weight=0, minsize=1)
        main.columnconfigure(
            4, weight=MAIN_DECK_WEIGHT, minsize=MAIN_SIDE_MIN_WIDTH,
            uniform="main_side")
        left = ttk.Frame(main, padding=PANEL_PADDING, style="Workspace.TFrame")
        middle = ttk.Frame(
            main, width=MAIN_CENTER_COLUMN_WIDTH, style="Bg.TFrame")
        right = ttk.Frame(main, padding=PANEL_PADDING, style="Workspace.TFrame")
        left.grid(row=0, column=0, sticky="nsew")
        ttk.Separator(
            main, orient="vertical", style="PaneDivider.TSeparator").grid(
                row=0, column=1, sticky="ns")
        middle.grid(row=0, column=2, sticky="ns")
        ttk.Separator(
            main, orient="vertical", style="PaneDivider.TSeparator").grid(
                row=0, column=3, sticky="ns")
        right.grid(row=0, column=4, sticky="nsew")
        # Keep the center column at its determined width even when child content
        # requests more; feature content must adapt inside this stable envelope.
        middle.grid_propagate(False)
        middle.columnconfigure(0, weight=1)
        middle.rowconfigure(0, weight=0, minsize=MAIN_CARD_PREVIEW_HEIGHT)
        middle.rowconfigure(1, weight=0, minsize=1)
        middle.rowconfigure(2, weight=1, minsize=1)
        mid_top = ttk.Frame(
            middle, padding=PANEL_PADDING, style="Preview.TFrame",
            width=MAIN_CENTER_COLUMN_WIDTH, height=MAIN_CARD_PREVIEW_HEIGHT)
        mid_bottom = ttk.Frame(
            middle, padding=PANEL_PADDING, style="Workspace.TFrame")
        mid_top.grid(row=0, column=0, sticky="nsew")
        ttk.Separator(
            middle, orient="horizontal", style="PaneDivider.TSeparator").grid(
                row=1, column=0, sticky="ew")
        mid_bottom.grid(row=2, column=0, sticky="nsew")
        # CardDetail uses pack internally; disable propagation so image/button
        # requests cannot enlarge the fixed preview region.
        mid_top.pack_propagate(False)

        self._build_search_pane(left)
        self._build_card_pane(mid_top)
        self._build_stats_panel(mid_bottom)
        self._build_deck_pane(right)
        self._register_layout_settle_callback(self._commit_settled_layout)

    def _commit_settled_layout(self):
        """One coherent high-quality layout pass after root/board resizing settles."""
        try:
            self._clamp_saved_pane_geometry()
        except (tk.TclError, AttributeError):
            pass
        for callback_name in (
                "_layout_card_type_chips", "_result_tree_configured",
                "_settle_stats_canvas_layout", "_schedule_curve_redraw",
                "_layout_comparison_actions", "_layout_deck_actions"):
            callback = getattr(self, callback_name, None)
            if callback is None:
                continue
            try:
                callback()
            except (tk.TclError, TypeError):
                pass

    def _clamp_saved_pane_geometry(self):
        """Keep the one retained Mainboard/Sideboard sash inside safe minima."""
        def set_if_changed(paned, index, current, desired):
            if int(current) != int(desired):
                paned.sashpos(index, int(desired))

        boards = getattr(self, "_boards_panes", None)
        if boards is not None and len(boards.panes()) >= 2:
            height = max(1, boards.winfo_height())
            board_min = 160
            current = int(boards.sashpos(0))
            desired = max(board_min, min(current, height - board_min))
            set_if_changed(boards, 0, current, desired)

    # ---- search pane ------------------------------------------------------

    def _autocomplete_return(self, combo):
        """Commit an explicitly highlighted suggestion, then run the search."""
        try:
            combo.commit_highlighted_suggestion()
        except AttributeError:
            pass
        self._do_search()

    def _clear_editable_selection(self, widget):
        """Remove text-selection highlight after a typed field loses focus."""
        if widget is None:
            return
        try:
            if not widget.winfo_exists():
                return
            widget.selection_clear()
            widget.selection_range(0, 0)
        except (tk.TclError, AttributeError):
            pass

    def _bind_editable_focus_behavior(self, widget):
        """Gold focus ring while active; no lingering selection after blur."""
        if widget is None:
            return
        def clear_later(_event=None):
            # ttk Spinbox can restore its selection in its class FocusOut
            # binding, so clear once immediately after idle and once again after
            # that native/class processing has completed.
            self.after_idle(
                lambda w=widget: self._clear_editable_selection(w))
            self.after(
                20, lambda w=widget: self._clear_editable_selection(w))
        widget.bind("<FocusOut>", clear_later, add="+")

    def _dismiss_name_autocomplete_on_outside_click(self, event):
        """Close Name suggestions and clear its highlight on an outside click."""
        entry = getattr(self, "q_name", None)
        target = getattr(event, "widget", None)
        target_path = str(target or "")
        if entry is None or target is entry or target_path == str(entry):
            return

        # A click in the suggestion popup must still be allowed to commit the
        # selected card name before the popup closes. Compare Tcl widget paths
        # rather than calling winfo_toplevel(): bind_all runs after the clicked
        # tab's own callback, and that callback can rebuild/destroy the tab. In
        # that case Tkinter correctly leaves event.widget as a path string.
        popup = getattr(entry, "_suggest_popup", None)
        if popup is not None:
            popup_path = str(popup)
            if (target_path == popup_path or
                    target_path.startswith(popup_path + ".")):
                return

        entry._cancel_pending_refresh()
        entry._hide_suggestions()
        self._clear_editable_selection(entry)

        def finish_blur():
            try:
                # If native bindings did not move focus, do it explicitly so
                # the Name entry also loses its active focus ring.
                if str(self.tk.call("focus") or "") == str(entry):
                    focus_target = getattr(target, "focus_set", None)
                    if callable(focus_target):
                        focus_target()
                    else:
                        self.focus_set()
            except (tk.TclError, AttributeError):
                try:
                    self.focus_set()
                except tk.TclError:
                    pass
            self._clear_editable_selection(entry)

        self.after_idle(finish_blur)


    # ---- card pane --------------------------------------------------------


    # ======================================================================
    # search
    # ======================================================================


    # ======================================================================
    # multi-deck workspace
    # ======================================================================


    # ======================================================================
    def _start_post_paint_initialization(self):
        self._refresh_search_catalogs()
        # Pre-load the other content/platform scopes in the background so the
        # first switch to Tokens/Emblems/Art (or paper-only) is instant.
        self._warm_common_search_catalogs()
        self._restore_workspace_session_async()
        # Build the bitset facet index in the background now, so the first live
        # filter pick is instant instead of paying the one-time build cost.
        self.search_context_controller.warm_facet_index()

    # workspace autosave / crash recovery
    # ======================================================================

    def _on_app_close(self):
        """Persist the live workspace and exit without forcing file-save prompts.

        Closing an individual deck tab still asks about unsaved deck-file changes.
        Closing the application itself is seamless because the private workspace
        autosave restores those dirty/unsaved tabs on the next launch.
        """
        self._capture_active_session_state()
        self._save_workspace_session(force=True, recovery=True)
        self._save_ui_preferences()
        for attr in (
                "_curve_resize_after", "_image_load_after",
                "_image_poll_after", "_image_ready_after",
                "_pane_clamp_after", "_workspace_load_after",
                "_context_debounce_after"):
            pending = getattr(self, attr, None)
            if pending is not None:
                try:
                    self.after_cancel(pending)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        if self._search_poll_after is not None:
            try:
                self.after_cancel(self._search_poll_after)
            except tk.TclError:
                pass
            self._search_poll_after = None
        if self._search_catalog_poll_after is not None:
            try:
                self.after_cancel(self._search_catalog_poll_after)
            except tk.TclError:
                pass
            self._search_catalog_poll_after = None
        if getattr(self, "_context_poll_after", None) is not None:
            try:
                self.after_cancel(self._context_poll_after)
            except tk.TclError:
                pass
            self._context_poll_after = None
        self._shutdown_database_sync()
        self._shutdown_printing()
        if self._workspace_autosave_after is not None:
            try:
                self.after_cancel(self._workspace_autosave_after)
            except tk.TclError:
                pass
            self._workspace_autosave_after = None
        self.search_catalog_controller.shutdown()
        self.search_context_controller.shutdown()
        self._shutdown_search_results()
        self._shutdown_workspace(timeout=None)
        self.card_image_service.shutdown()
        self.destroy()

    # ======================================================================
    # deck editing
    # ======================================================================


    # ======================================================================
    # deck files
    # ======================================================================


    # ======================================================================
    # JSON export
    # ======================================================================


    # ======================================================================
    # misc
    # ======================================================================

    def _status(self, msg):
        """Record a transient user-facing status line.

        The persistent bottom status bar was removed, but several callers use
        this as their only report of a non-fatal outcome (an invalid search
        filter, a trusted-catalog load failure). Route it to the log file the
        error dialogs already point users at rather than discarding it.
        """
        text = str(msg or "").strip()
        if text:
            log.info("status: %s", text)

    def report_callback_exception(self, exc, val, tb):
        """
        Tkinter calls this for any exception raised inside a widget callback
        (button clicks, menu commands, etc). By default these only print to a
        console -- which doesn't exist in the windowed .exe -- so we log the
        full traceback to the file and show a dialog pointing at it.
        """
        log.error("Unhandled UI error", exc_info=(exc, val, tb))
        where = f"\n\nFull details were written to the error log:\n{self.log_path}" \
            if self.log_path else ""
        messagebox.showerror(
            "Unexpected error",
            f"Something went wrong:\n\n{val}{where}\n\n"
            "Check the error-log path shown above for the full details.")
