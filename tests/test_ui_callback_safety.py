"""Headless regressions for Tk callback and transient-popup lifecycle safety."""

from __future__ import annotations

import ast
import gc
import sys
import weakref
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mtgdb.ui.app as gui
from mtgdb.ui.database_sync import DatabaseSyncMixin
from mtgdb.ui.printing import PrintingMixin
from mtgdb.ui.search import SearchFeatureMixin
from mtgdb.ui.search_checklist import SearchChecklistDialog
from mtgdb.ui.search_printings import SearchPrintingFilter
from mtgdb.ui.window import WindowServicesMixin


class FakeEntry:
    def __init__(self, popup=None):
        self._suggest_popup = popup
        self.cancelled = 0
        self.hidden = 0

    def __str__(self):
        return ".search.name"

    def _cancel_pending_refresh(self):
        self.cancelled += 1

    def _hide_suggestions(self):
        self.hidden += 1


class FakeTk:
    def call(self, *_args):
        return ".search.name"


class FakeApp:
    def __init__(self, popup=None):
        self.q_name = FakeEntry(popup)
        self.tk = FakeTk()
        self.root_focus = 0
        self.selection_clears = 0

    def _clear_editable_selection(self, _entry):
        self.selection_clears += 1

    def after_idle(self, callback):
        callback()

    def focus_set(self):
        self.root_focus += 1


class Event:
    def __init__(self, widget):
        self.widget = widget


class FakeSelectionWidget:
    def __init__(self, name):
        self.name = name
        self.clears = 0
        self.host = None

    def __str__(self):
        return self.name

    def winfo_exists(self):
        return True

    def focus_set(self):
        if self.host is not None:
            self.host.focus_path = self.name


class FakeSearchTk:
    def __init__(self, host):
        self.host = host

    def call(self, command):
        assert command == "focus"
        return self.host.focus_path


class FakeSearchSelectionHost:
    def __init__(self, widgets):
        self._search_blur_clear_widgets = tuple(widgets)
        self.focus_path = str(widgets[0]) if widgets else ""
        self.root_focus = 0
        self.tk = FakeSearchTk(self)
        for widget in widgets:
            widget.host = self

    def _clear_editable_selection(self, widget):
        widget.clears += 1

    def after_idle(self, callback):
        callback()

    def focus_set(self):
        self.root_focus += 1
        self.focus_path = "."


class FakeLifecycleWidget:
    _counter = 0

    def __init__(self, name=None):
        type(self)._counter += 1
        self.name = name or f".fake{type(self)._counter}"
        self._bindings = {}

    def __str__(self):
        return self.name

    def bind(self, sequence, callback, add=None):
        self._bindings.setdefault(sequence, []).append(callback)
        return f"bind-{len(self._bindings[sequence])}"

    def trigger_destroy(self):
        event = Event(self)
        callbacks = list(self._bindings.get("<Destroy>", ()))
        for callback in callbacks:
            callback(event)
        # Tk discards widget-specific Tcl bindings with the destroyed widget.
        self._bindings.clear()

    def after(self, _delay, _callback):
        return "widget-after"

    def after_cancel(self, _after_id):
        return None


class FakeVariable:
    def __init__(self):
        self._traces = {}
        self._next = 0

    def trace_add(self, mode, callback):
        assert mode == "write"
        self._next += 1
        trace_id = f"trace-{self._next}"
        self._traces[trace_id] = callback
        return trace_id

    def trace_remove(self, mode, trace_id):
        assert mode == "write"
        self._traces.pop(trace_id, None)

    def fire_write(self):
        for callback in list(self._traces.values()):
            callback(None, None, None)


class FakeScheduler:
    def __init__(self):
        self._scheduled = {}
        self._next_after = 0

    def after(self, _delay, callback):
        self._next_after += 1
        after_id = f"after-{self._next_after}"
        self._scheduled[after_id] = callback
        return after_id

    def after_cancel(self, after_id):
        self._scheduled.pop(after_id, None)

    def run_scheduled(self):
        while self._scheduled:
            after_id = next(iter(self._scheduled))
            callback = self._scheduled.pop(after_id)
            callback()


class FakePopup:
    def __init__(self):
        self.exists = True
        self.destroyed = 0

    def winfo_exists(self):
        return int(self.exists)

    def grab_release(self):
        return None

    def destroy(self):
        self.destroyed += 1
        self.exists = False


class _CallbackOwner:
    def __init__(self, calls):
        self.calls = calls

    def redraw(self):
        self.calls.append("redraw")


class _SyncHost(DatabaseSyncMixin, FakeScheduler):
    def __init__(self, popup):
        FakeScheduler.__init__(self)
        self._sync_close_after = None
        self._sync_popup = popup
        self._sync_stage_label = object()
        self._sync_detail_label = object()
        self._sync_progressbar = object()
        self._sync_percent_label = object()


class _PrintHost(PrintingMixin, FakeScheduler):
    def __init__(self, popup):
        FakeScheduler.__init__(self)
        self._print_close_after = None
        self._print_popup = popup
        self._print_stage_label = object()
        self._print_detail_label = object()
        self._print_progressbar = object()
        self._print_percent_label = object()


def _all_debounced_trace_calls_have_lifecycle_owner():
    found = 0
    missing = []
    for path in (ROOT / "mtgdb" / "ui").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "_trace_write_debounced":
                continue
            found += 1
            if not any(keyword.arg == "lifecycle_widget" for keyword in node.keywords):
                missing.append(path.name)
    return found == 3 and not missing, found, missing


def _printing_filter_fixture(popup):
    printing = SearchPrintingFilter.__new__(SearchPrintingFilter)
    printing._popup = popup
    printing._set_type_frame = object()
    printing._status_label = object()
    printing._set_search_var = object()
    printing._visible_set_codes = ["abc"]
    printing._set_checklist = None
    printing._set_checklist_catalog = ()
    return printing


def main():
    # Existing global-click regression: a bind_all callback can receive a Tcl
    # path string when the clicked deck tab rebuilt earlier in the same event.
    app = FakeApp()
    gui.DeckBuilderApp._dismiss_name_autocomplete_on_outside_click(
        app, Event(".deck.tabs.tab2"))

    popup_app = FakeApp(popup=".search.popup")
    gui.DeckBuilderApp._dismiss_name_autocomplete_on_outside_click(
        popup_app, Event(".search.popup.list"))

    # Search text editors must clear selection when clicking elsewhere, even if
    # the clicked control does not take focus. The clicked editor itself remains
    # untouched.
    first_editor = FakeSelectionWidget(".search.rules")
    second_editor = FakeSelectionWidget(".search.power")
    selection_host = FakeSearchSelectionHost((first_editor, second_editor))
    SearchFeatureMixin._clear_search_selections_on_outside_click(
        selection_host, Event(first_editor))
    own_editor_preserved = first_editor.clears == 0
    other_editor_cleared = second_editor.clears == 2
    SearchFeatureMixin._clear_search_selections_on_outside_click(
        selection_host, Event(".search.button"))
    outside_click_clears_all = (
        first_editor.clears == 2 and second_editor.clears == 4
        and selection_host.root_focus == 1
        and selection_host.focus_path == ".")

    # Debounced transient traces must remove both the Tcl trace and any queued
    # callback when the owning popup dies. The callback owner should then be
    # collectible rather than retained by Tcl/Python closure state.
    scheduler = FakeScheduler()
    variable = FakeVariable()
    lifecycle = FakeLifecycleWidget(".popup")
    redraw_calls = []
    callback_owner = _CallbackOwner(redraw_calls)
    callback_ref = weakref.ref(callback_owner)
    gui.DeckBuilderApp._trace_write_debounced(
        scheduler, variable, callback_owner.redraw,
        lifecycle_widget=lifecycle)
    variable.fire_write()
    queued_before_destroy = len(scheduler._scheduled) == 1
    del callback_owner
    gc.collect()
    retained_while_live = callback_ref() is not None
    lifecycle.trigger_destroy()
    scheduler.run_scheduled()
    gc.collect()
    released_after_destroy = callback_ref() is None

    # Scroll routing is a global registry; entries for temporary popup widgets
    # must disappear on that widget's Destroy event.
    scroll_host = SimpleNamespace(_scroll_targets={})
    scroll_widget = FakeLifecycleWidget(".popup.canvas")
    WindowServicesMixin._register_scrollable(scroll_host, scroll_widget)
    scroll_registered = str(scroll_widget) in scroll_host._scroll_targets
    scroll_widget.trigger_destroy()
    scroll_released = str(scroll_widget) not in scroll_host._scroll_targets

    # Tooltips are retained by the app while their controls live, then both the
    # app registry and ToolTip's widget reference must be released on Destroy.
    tooltip_host = SimpleNamespace(_tooltips=[])
    tooltip_widget = FakeLifecycleWidget(".popup.radio")
    tip = gui.DeckBuilderApp._add_tooltip(
        tooltip_host, tooltip_widget, "Any means one match")
    tooltip_retained_while_live = tip in tooltip_host._tooltips
    tooltip_widget.trigger_destroy()
    tooltip_released = (
        tip not in tooltip_host._tooltips and tip.widget is None
        and tip._popup is None)

    # Search Printings owns a persistent controller, so destroying one popup
    # must release every widget reference owned by that popup instance.
    old_popup = FakePopup()
    printing = _printing_filter_fixture(old_popup)
    SearchPrintingFilter._destroy_popup(printing)
    printing_cleanup = (
        old_popup.destroyed == 1
        and printing._popup is None
        and printing._set_type_frame is None
        and printing._status_label is None
        and printing._set_search_var is None
        and printing._visible_set_codes == [])

    externally_destroyed = FakePopup()
    printing_external = _printing_filter_fixture(externally_destroyed)
    SearchPrintingFilter._on_popup_destroy(
        printing_external, Event(externally_destroyed), externally_destroyed)
    printing_external_cleanup = (
        printing_external._popup is None
        and printing_external._set_type_frame is None
        and printing_external._status_label is None)

    # Search checklist's app-level retention is cleared even when destruction
    # arrives from outside its own Close button.
    checklist_popup = FakePopup()
    checklist = SearchChecklistDialog.__new__(SearchChecklistDialog)
    checklist.owner = SimpleNamespace()
    checklist.owner._active_search_checklist = checklist
    checklist.popup = checklist_popup
    SearchChecklistDialog._on_popup_destroy(checklist, Event(checklist_popup))
    checklist_cleanup = (
        checklist.popup is None
        and checklist.owner._active_search_checklist is None)

    trace_calls_ok, trace_call_count, missing_trace_owners = (
        _all_debounced_trace_calls_have_lifecycle_owner())

    # Delayed success-close callbacks may close only the popup instance that
    # scheduled them; a replacement popup must survive an older timer.
    sync_old, sync_new = FakePopup(), FakePopup()
    sync_host = _SyncHost(sync_old)
    sync_host._schedule_sync_popup_close(sync_old, delay_ms=1)
    sync_host._sync_popup = sync_new
    sync_host.run_scheduled()
    sync_replacement_safe = sync_new.destroyed == 0 and sync_host._sync_popup is sync_new

    print_old, print_new = FakePopup(), FakePopup()
    print_host = _PrintHost(print_old)
    print_host._schedule_print_popup_close(print_old, delay_ms=1)
    print_host._print_popup = print_new
    print_host.run_scheduled()
    print_replacement_safe = (
        print_new.destroyed == 0 and print_host._print_popup is print_new)

    agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    lifecycle_contract_present = (
        "BEH-006" in agents_text
        and "replacement popup MUST NOT reuse a widget object" in agents_text)

    checks = {
        "destroyed/rebuilt tab path is safe": (
            app.q_name.cancelled == 1 and app.q_name.hidden == 1),
        "string target uses root focus fallback": app.root_focus == 1,
        "suggestion-popup child click is preserved": (
            popup_app.q_name.cancelled == 0 and popup_app.q_name.hidden == 0),
        "clicked Search editor keeps its own selection": own_editor_preserved,
        "outside clicks clear other Search editor selections": (
            other_editor_cleared and outside_click_clears_all),
        "debounced popup trace queues while live": queued_before_destroy,
        "debounced popup callback stays reachable while popup lives": retained_while_live,
        "destroy removes trace and queued callback": (
            not variable._traces and not scheduler._scheduled and not redraw_calls),
        "destroy releases trace callback owner": released_after_destroy,
        "temporary scroll target registers": scroll_registered,
        "temporary scroll target unregisters on destroy": scroll_released,
        "tooltip is retained only while control lives": (
            tooltip_retained_while_live and tooltip_released),
        "Search Printings destroy clears popup widget caches": printing_cleanup,
        "Search Printings external destroy clears popup widget caches": (
            printing_external_cleanup),
        "Search Checklist destroy clears app retention and row caches": checklist_cleanup,
        "every debounced popup trace declares a lifecycle widget": trace_calls_ok,
        "sync delayed close cannot destroy a replacement popup": sync_replacement_safe,
        "print delayed close cannot destroy a replacement popup": print_replacement_safe,
        "AGENTS defines the popup lifecycle invariant": lifecycle_contract_present,
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    if not trace_calls_ok:
        print(
            "  debounced trace calls:", trace_call_count,
            "missing lifecycle owners:", missing_trace_owners)
    print("\nUI CALLBACK REGRESSION:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
