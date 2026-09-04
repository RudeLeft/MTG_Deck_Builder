"""Window-services runtime regression gates (WIN-*).

Guards against the extraction hazard where a method moved into
``WindowServicesMixin`` references a module-level name that did not travel with
it. Each display-safe method is actually invoked on a composed Tk root so an
undefined global surfaces as a failure here instead of at application startup.
"""

import os
import sys
import symtable
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.ui.assets import APP_ICON_FILE, HAVE_PIL, _asset_path
from mtgdb.ui.window import WindowServicesMixin


def _free_module_globals(module_path):
    """Return names referenced as globals but never bound at module scope.

    Uses the interpreter's own symbol table so nested functions, ``for``
    targets, ``with ... as`` names, and comprehension scopes are resolved
    correctly. A genuinely missing module-level dependency (an extracted method
    that still reaches for a name left behind) shows up as a global symbol that
    is neither a builtin nor bound anywhere at module scope.
    """
    source = module_path.read_text(encoding="utf-8")
    table = symtable.symtable(source, module_path.name, "exec")
    module_bound = {
        sym.get_name() for sym in table.get_symbols()
        if sym.is_local() or sym.is_imported() or sym.is_namespace()
    }
    builtins = set(dir(__builtins__)) if not isinstance(__builtins__, dict) \
        else set(__builtins__)

    free = set()

    def walk(scope):
        for sym in scope.get_symbols():
            if sym.is_global() and not sym.is_local():
                name = sym.get_name()
                if name.startswith("__") and name.endswith("__"):
                    continue
                if name not in module_bound and name not in builtins:
                    free.add(name)
        for child in scope.get_children():
            walk(child)

    for child in table.get_children():
        walk(child)
    return free


class _Window(WindowServicesMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self._scroll_targets = {}


def main():
    free_globals = _free_module_globals(ROOT / "mtgdb/ui/window.py")

    runtime_checks = {}
    runtime_skip = None
    try:
        window = _Window()
    except tk.TclError as exc:
        # Headless Linux source gates have no display server. Static checks still
        # run there; the live-root behavior is covered by the Windows gates.
        runtime_skip = str(exc)
    else:
        try:
            # The method that previously crashed on a missing HAVE_PIL global.
            window._set_window_icon()
            icon_ok = (not HAVE_PIL) or getattr(
                window, "_window_icon", None) is not None
            window._register_scrollable(window)
            registered = str(window) in window._scroll_targets
            menu = window._dark_menu(window)
            runtime_checks = {
                "window methods run without NameError on a live root": True,
                "set_window_icon keeps an icon reference": icon_ok,
                "register_scrollable and dark_menu operate on the root": (
                    registered and menu is not None),
            }
        except Exception as exc:  # pragma: no cover - failure path
            print(f"  window method raised: {exc!r}")
            runtime_checks = {
                "window methods run without NameError on a live root": False,
                "set_window_icon keeps an icon reference": False,
                "register_scrollable and dark_menu operate on the root": False,
            }
        finally:
            window.destroy()

    window_source = (ROOT / "mtgdb/ui/window.py").read_text(encoding="utf-8")
    app_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")

    # The dark title bar is a Windows-only native call. Inverting the platform
    # guard would both skip the styling on Windows and attempt ctypes calls on
    # platforms that have no dwmapi, so pin both directions.
    import sys as _sys
    from unittest import mock as _mock
    import mtgdb.ui.window as _window

    class _HwndProbe:
        def __init__(self):
            self.id_reads = 0

        def winfo_id(self):
            self.id_reads += 1
            raise RuntimeError("stop before any native call")

    def _attempts_native_styling(platform):
        probe = _HwndProbe()
        with _mock.patch.object(_sys, "platform", platform):
            try:
                _window.WindowServicesMixin._set_dark_titlebar_for(
                    object(), probe, frame_changed=False)
            except Exception:
                pass
        return probe.id_reads > 0

    platform_guard_ok = (
        _attempts_native_styling("win32")
        and not _attempts_native_styling("linux")
        and not _attempts_native_styling("darwin"))

    checks = {
        "dark title bar styling is attempted only on Windows": platform_guard_ok,
        "window mixin has no undefined module-level names": not free_globals,
        "bundled window icon asset resolves from source": (
            os.path.exists(_asset_path(APP_ICON_FILE))),
        "monitor work-area service uses Windows rcWork with Tk fallback": all(
            marker in window_source for marker in (
                "def _work_area_for_widget", "MonitorFromPoint",
                "GetMonitorInfoW", "rcWork", "winfo_vrootwidth")),
        "popup geometry clamps to the current monitor work area": (
            'work_x, work_y, work_w, work_h = self._work_area_for_widget(self)' in window_source
            and 'max_h = max(min_height, work_h - int(screen_margin_y))' in window_source
            and 'y = work_y + max(0, (work_h - h) // 2)' in window_source),
        "root dark title bar resolves the 64-bit top-level HWND and reapplies after mapping": (
            "get_ancestor.restype = wintypes.HWND" in window_source
            and "get_parent.restype = wintypes.HWND" in window_source
            and "get_ancestor(child_hwnd, 2)" in window_source
            and "def _schedule_dark_titlebar_refresh" in window_source
            and "self.after(260" in window_source
            and "def finalize_native_frame():" in app_source
            and "self._schedule_dark_titlebar_refresh(self)" in app_source),
        "valid sash positions are not rewritten during motion": (
            "def set_if_changed(" in app_source
            and "if int(current) != int(desired):" in app_source),
        **runtime_checks,
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    if free_globals:
        print("  undefined module-level names:", sorted(free_globals))
    if runtime_skip is not None:
        print(f"  [SKIP] live Tk runtime checks: {runtime_skip}")
    print("\nWINDOW SERVICES:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
