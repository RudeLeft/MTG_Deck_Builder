"""Search adaptation of the shared authoritative printing filter."""

from __future__ import annotations

from tkinter import ttk

from mtgdb.ui.components import AppButton
from mtgdb.ui.set_filters import PrintingFilter, set_type_label
from mtgdb.ui.tokens import PALETTE


class SearchPrintingFilter(PrintingFilter):
    """Bind shared printing controls to the asynchronous Search taxonomy path."""

    def __init__(self, owner, parent, *, row=0):
        super().__init__(
            owner,
            repository=owner.search_repository,
            content_types_getter=owner._selected_content_types,
            english_variable=owner.english_only,
            english_change_callback=owner._update_search_filter_summary,
            change_callback=owner._update_search_filter_summary,
            scope_change_callback=None,
            popup_title="Search Printings",
            header_text="PRINTINGS",
            intro_text="",
        )
        self._pending_restore_types = None
        self._pending_restore_codes = None
        self._context_snapshot = None
        printings_label = ttk.Label(parent, text="Printings")
        printings_label.grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
        owner._add_standard_filter_tooltip(printings_label, "printings")
        self.button = AppButton(
            parent, text="Paper only · Any set type · Any set", role="picker",
            command=self.toggle_popup)
        self.button.grid(row=row, column=1, sticky="ew", pady=2)
        # The button is what a user clicks; the label beside it is not.
        owner._add_standard_filter_tooltip(self.button, "printings")

    def refresh_catalog(self):
        """Request current printing vocabulary without querying on the Tk thread."""
        self.owner._refresh_search_catalogs(
            selected_set_types=self.selected_set_types())

    def _refresh_exact_set_catalog(self, selected_codes=None):
        """Request an Exact Set cascade without synchronous database access."""
        if selected_codes is not None:
            self._pending_restore_codes = set(selected_codes)
        self.owner._refresh_search_catalogs(
            selected_set_types=self.selected_set_types())

    def _on_set_type_change(self):
        self._pending_restore_codes = self.selected_set_codes()
        self.owner._refresh_search_catalogs(
            selected_set_types=self.selected_set_types())
        self._update_summary()
        self._notify_change()

    def clear(self):
        self.paper_only.set(True)
        self._pending_restore_types = set()
        self._pending_restore_codes = set()
        for variable in self.set_type_vars.values():
            variable.set(False)
        for variable in self._set_vars.values():
            variable.set(False)
        self.refresh_catalog()

    def restore_selection(self, set_types, set_codes=None, *, paper_only=True,
                          games=None):
        """Stage saved selections until trusted asynchronous vocabulary arrives.

        Search resolves its vocabulary asynchronously, so this stages rather
        than queries -- but the platform checkboxes are plain UI state and are
        applied immediately, because the scope request below is keyed by them.
        """
        if games is not None:
            wanted = {str(value) for value in games}
            for key, variable in self.game_vars.items():
                variable.set(key in wanted)
            self._sync_paper_only_from_games()
        else:
            self.paper_only.set(bool(paper_only))
            # A workspace saved before platforms were recorded only knows the
            # paper flag; widen to every platform when it was off.
            for key, variable in self.game_vars.items():
                variable.set(key == "paper" or not paper_only)
        self._pending_restore_types = {
            str(value) for value in (set_types or ()) if str(value)
        }
        self._pending_restore_codes = {
            str(value) for value in (set_codes or ()) if str(value)
        }
        self.owner._refresh_search_catalogs(
            selected_set_types=self._pending_restore_types)

    def select_all_present_types_and_sets(self):
        """Empty set selections mean any observed printing in the current scope."""
        self._pending_restore_types = set()
        self._pending_restore_codes = set()
        for variable in self.set_type_vars.values():
            variable.set(False)
        for variable in self._set_vars.values():
            variable.set(False)
        self.refresh_catalog()
        self._update_summary()

    def begin_scope_loading(self):
        """Keep stable catalog geometry visible but disabled until trusted data arrives."""
        if self._pending_restore_types is None:
            self._pending_restore_types = set(self.selected_set_types())
        if self._pending_restore_codes is None:
            self._pending_restore_codes = set(self.selected_set_codes())
        self._set_catalog_controls_enabled(False)
        try:
            self.button.configure(text="Loading trusted printings…")
        except AttributeError:
            pass

    def begin_sets_loading(self):
        """Keep the current Exact Set viewport stable during a cold cascade."""
        if self._pending_restore_codes is None:
            self._pending_restore_codes = set(self.selected_set_codes())
        checklist = getattr(self, "_set_checklist", None)
        if checklist is not None:
            try:
                checklist.set_enabled(False)
            except Exception:
                pass

    def cancel_loading(self):
        """Restore the last stable controls after an async catalog failure."""
        self._pending_restore_types = None
        self._pending_restore_codes = None
        self._set_catalog_controls_enabled(True)
        self._update_summary()

    def apply_snapshot(self, snapshot):
        """Apply one generation-checked catalog snapshot on Tk."""
        selected_types = (
            set(self._pending_restore_types)
            if self._pending_restore_types is not None
            else self.selected_set_types()
        )
        selected_codes = (
            set(self._pending_restore_codes)
            if self._pending_restore_codes is not None
            else self.selected_set_codes()
        )
        self._pending_restore_types = None
        self._pending_restore_codes = None
        self.apply_catalog_data(
            snapshot.set_types, snapshot.sets,
            selected_types=selected_types, selected_codes=selected_codes)
        self._set_catalog_controls_enabled(True)
        return self.selected_set_types()

    def _create_popup(self):
        super()._create_popup()
        self._apply_context_to_popup()

    def apply_context_snapshot(self, snapshot):
        """Decorate existing printing controls with predictive counts only."""
        self._context_snapshot = snapshot
        self._apply_context_to_popup()
        self._update_summary()

    def _apply_context_to_popup(self):
        snapshot = getattr(self, "_context_snapshot", None)
        if snapshot is None:
            return
        frame = getattr(self, "_set_type_frame", None)
        if frame is not None:
            try:
                values = sorted(self._present_set_types, key=str.casefold)
                children = list(frame.winfo_children())
                for value, child in zip(values, children):
                    count = int((snapshot.set_type_counts or {}).get(value, 0))
                    label = f"{set_type_label(value)} · {count:,}"
                    availability = getattr(child, "set_context_availability", None)
                    if callable(availability):
                        availability(
                            count > 0, display_text=label,
                            allow_selected_clear=True)
                    else:
                        selected = bool(
                            self.set_type_vars.get(value)
                            and self.set_type_vars[value].get())
                        child.configure(
                            text=(label if count > 0 else f"✕ {label}"),
                            state=("normal" if count > 0 or selected else "disabled"),
                            fg=(PALETTE["text"] if count > 0 else PALETTE["bad"]),
                            activeforeground=(
                                PALETTE["text"] if count > 0 else PALETTE["bad"]),
                            disabledforeground=PALETTE["bad"],
                        )
            except Exception:
                pass
        checklist = getattr(self, "_set_checklist", None)
        if checklist is not None:
            try:
                selected = self.selected_set_codes()
                checklist.set_values(
                    [
                        (code, f"{name} ({code.upper()}) · "
                               f"{int((snapshot.set_counts or {}).get(code, 0)):,}",
                         {"zero_count": int((snapshot.set_counts or {}).get(code, 0)) <= 0})
                        for code, name in self._eligible_sets
                    ],
                    selected=selected)
                query = self._set_search_var.get().strip() if self._set_search_var is not None else ""
                checklist.filter(query)
                self._visible_set_codes = list(checklist.visible_keys)
            except Exception:
                pass

    def _update_summary(self):
        text = super()._update_summary()
        snapshot = getattr(self, "_context_snapshot", None)
        try:
            self.button.configure(text=text)
        except AttributeError:
            pass
        label = getattr(self, "_status_label", None)
        if snapshot is not None and label is not None:
            try:
                games = " · ".join(
                    f"{key.title()} {int((snapshot.game_counts or {}).get(key, 0)):,}"
                    for key in ("paper", "arena", "mtgo"))
                language = (
                    f"English {int(snapshot.english_count):,} · "
                    f"All languages {int(snapshot.all_language_count):,}")
                label.configure(
                    text=(f"{len(self.selected_set_codes())} exact sets selected; "
                          f"{len(self.selected_set_types())} set types selected. "
                          f"{games} · {language}"))
            except Exception:
                pass
        return text
