"""Window and platform services: geometry, dark title bars, DPI/maximize,
icon, global mouse-wheel routing, popup centering, and dark menus.

Composed into DeckBuilderApp as a mixin. Presentation/window behavior only.
"""

import logging
import sys
import tkinter as tk

from mtgdb.ui.assets import APP_ICON_FILE, HAVE_PIL, Image, ImageTk, _asset_path
from mtgdb.ui.tokens import FONT_HELPER, PALETTE

log = logging.getLogger("mtg")


class WindowServicesMixin:
    """Popup geometry, dark title bars, scrolling, icons, DPI, window behavior."""

    def _maximize_window(self):
        """Open maximized while retaining the normal native Windows frame."""
        try:
            self.state("zoomed")
            return
        except tk.TclError:
            pass
        try:
            self.attributes("-zoomed", True)
            return
        except tk.TclError:
            pass
        self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
    def _set_dark_titlebar(self):
        """Force the native Windows title bar to match the Dark MTG palette."""
        self._set_dark_titlebar_for(self, frame_changed=True)
    def _set_window_icon(self):
        if not HAVE_PIL:
            return
        try:
            img = Image.open(_asset_path(APP_ICON_FILE)).convert("RGBA")
            img.thumbnail((64, 64), Image.LANCZOS)
            self._window_icon = ImageTk.PhotoImage(img)  # keep a reference
            self.iconphoto(True, self._window_icon)
        except Exception:
            log.warning("Could not set window icon")
    def _register_scrollable(self, widget, target=None):
        key = str(widget)
        self._scroll_targets[key] = target or widget

        def unregister(event, *, registered_widget=widget, registered_key=key):
            if event.widget is not registered_widget:
                return
            self._scroll_targets.pop(registered_key, None)

        widget.bind("<Destroy>", unregister, add="+")
    def _scroll_target_under_pointer(self, event):
        """Return our registered scroll target beneath the pointer.

        ttk combobox dropdowns are native Tcl popdown windows. On Windows they
        are not represented in Tkinter's Python widget registry, so calling
        winfo_containing() over one can raise KeyError("popdown"). Detect those
        Tcl-only windows first and deliberately leave their wheel handling to
        ttk itself.
        """
        try:
            path = str(self.tk.call(
                "winfo", "containing", event.x_root, event.y_root) or "")
        except tk.TclError:
            return None

        if not path:
            return None

        # ttk::combobox creates an internal window such as
        # .!autocompletecombobox.popdown. It is scrollable already, but cannot
        # safely be passed through Tkinter's nametowidget()/winfo_containing().
        if ".popdown" in path:
            return None

        try:
            widget = self.nametowidget(path)
        except (KeyError, tk.TclError):
            return None

        while widget is not None:
            target = self._scroll_targets.get(str(widget))
            if target is not None:
                return target
            widget = getattr(widget, "master", None)
        return None
    def _wheel_units(self, event):
        if getattr(event, "num", None) == 4:
            return -1
        if getattr(event, "num", None) == 5:
            return 1
        delta = getattr(event, "delta", 0)
        if not delta:
            return 0
        units = int(-delta / 120)
        return units if units else (-1 if delta > 0 else 1)
    def _on_global_mousewheel(self, event):
        try:
            target = self._scroll_target_under_pointer(event)
        except (KeyError, tk.TclError):
            return
        if target is None:
            return
        units = self._wheel_units(event)
        if not units:
            return
        try:
            target.yview_scroll(units, "units")
            return "break"
        except (tk.TclError, AttributeError):
            return
    def _on_global_shift_mousewheel(self, event):
        try:
            target = self._scroll_target_under_pointer(event)
        except (KeyError, tk.TclError):
            return
        if target is None:
            return
        units = self._wheel_units(event)
        if not units:
            return
        try:
            target.xview_scroll(units, "units")
            return "break"
        except (tk.TclError, AttributeError):
            try:
                target.yview_scroll(units, "units")
                return "break"
            except (tk.TclError, AttributeError):
                return

    def _work_area_for_widget(self, widget):
        """Return the visible work area containing ``widget`` in root coordinates.

        On Windows this uses the monitor nearest the anchor widget so transient
        popups stay on the same physical display as the app/control instead of
        being clamped against the combined virtual desktop. Other platforms fall
        back to Tk's virtual-root bounds.
        """
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG),
                    ]

                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                x = int(widget.winfo_rootx() + max(1, widget.winfo_width()) / 2)
                y = int(widget.winfo_rooty() + max(1, widget.winfo_height()) / 2)
                point = wintypes.POINT(x, y)
                user32 = ctypes.windll.user32
                monitor_from_point = user32.MonitorFromPoint
                monitor_from_point.argtypes = [wintypes.POINT, wintypes.DWORD]
                monitor_from_point.restype = wintypes.HANDLE
                get_monitor_info = user32.GetMonitorInfoW
                get_monitor_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
                get_monitor_info.restype = wintypes.BOOL
                monitor = monitor_from_point(point, 2)  # MONITOR_DEFAULTTONEAREST
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if monitor and get_monitor_info(monitor, ctypes.byref(info)):
                    work = info.rcWork
                    return (
                        int(work.left), int(work.top),
                        max(1, int(work.right - work.left)),
                        max(1, int(work.bottom - work.top)),
                    )
            except Exception:
                log.debug("Could not resolve Windows monitor work area", exc_info=True)

        try:
            return (
                int(widget.winfo_vrootx()), int(widget.winfo_vrooty()),
                max(1, int(widget.winfo_vrootwidth() or widget.winfo_screenwidth())),
                max(1, int(widget.winfo_vrootheight() or widget.winfo_screenheight())),
            )
        except tk.TclError:
            return (0, 0, max(1, int(self.winfo_screenwidth())),
                    max(1, int(self.winfo_screenheight())))

    def _center_popup_on_screen(self, popup, width=None, height=None,
                                min_width=320, min_height=220):
        """Size and center an app-owned dialog on the current Tk screen."""
        popup.update_idletasks()
        screen_w = max(1, popup.winfo_screenwidth())
        screen_h = max(1, popup.winfo_screenheight())
        req_w = popup.winfo_reqwidth()
        req_h = popup.winfo_reqheight()
        w = int(width or req_w)
        h = int(height or req_h)
        # Leave breathing room for taskbars/docks and window decorations.
        max_w = max(min_width, screen_w - 100)
        max_h = max(min_height, screen_h - 120)
        w = max(min_width, min(w, max_w))
        h = max(min_height, min(h, max_h))
        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")
        self._set_dark_titlebar_for(popup, frame_changed=False)
    def _center_popup_with_visible_actions(
            self, popup, preferred_width, preferred_height,
            min_width=320, min_height=220, lock_size=False,
            screen_margin_x=80, screen_margin_y=90):
        """Center a dialog while keeping its footer visible and optionally fixed."""
        popup.update_idletasks()
        work_x, work_y, work_w, work_h = self._work_area_for_widget(self)
        # Constrain against the current monitor work area, not the full screen.
        # That keeps action footers above the Windows taskbar at common 768px
        # desktop heights and on secondary monitors with different work areas.
        max_w = max(min_width, work_w - int(screen_margin_x))
        max_h = max(min_height, work_h - int(screen_margin_y))
        w = max(min_width, min(int(preferred_width), max_w))
        h = max(min_height, min(int(preferred_height), max_h))
        x = work_x + max(0, (work_w - w) // 2)
        y = work_y + max(0, (work_h - h) // 2)
        popup.geometry(f"{w}x{h}+{x}+{y}")
        if lock_size:
            popup.resizable(False, False)
            try:
                popup.minsize(w, h)
                popup.maxsize(w, h)
            except tk.TclError:
                pass
        self._set_dark_titlebar_for(popup, frame_changed=False)
        return w, h
    def _bind_autohide_canvas_scrollbar(
            self, canvas, inner, window, scrollbar, axis="vertical"):
        """Keep scrollbar geometry stable; enable it only when content overflows."""
        state = {"after": None}

        def apply():
            state["after"] = None
            try:
                if axis == "vertical":
                    viewport = max(1, canvas.winfo_height())
                    content = max(1, inner.winfo_reqheight())
                    width = max(1, canvas.winfo_width())
                    canvas.itemconfigure(window, width=width)
                    canvas.configure(scrollregion=(0, 0, width, max(viewport, content)))
                    if content <= viewport:
                        canvas.yview_moveto(0)
                        scrollbar.state(["disabled"])
                    else:
                        scrollbar.state(["!disabled"])
            except tk.TclError:
                pass

        def update(_event=None):
            if state["after"] is not None:
                return
            try:
                state["after"] = canvas.after_idle(apply)
            except tk.TclError:
                state["after"] = None

        inner.bind("<Configure>", update, add="+")
        canvas.bind("<Configure>", update, add="+")
        return update

    def _set_dark_titlebar_for(self, window, *, frame_changed=False):
        """Apply the dark native Windows frame to the real top-level HWND.

        Tk exposes an inner child HWND from ``winfo_id()`` on Windows.  Native
        non-client styling belongs to its top-level wrapper, and ctypes must use
        pointer-sized HWND return types on 64-bit Windows or the handle can be
        truncated.  Apply to the wrapper plus the Tk child for compatibility
        across Tk/Windows builds.
        """
        if sys.platform != "win32":
            return
        try:
            # Resolve the Tk handle before touching Windows-only ctypes symbols.
            # This keeps the platform guard directly testable on non-Windows
            # hosts and is also the first native datum the real path needs.
            child_id = int(window.winfo_id())
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            dwm = ctypes.windll.dwmapi
            get_parent = user32.GetParent
            get_parent.argtypes = [wintypes.HWND]
            get_parent.restype = wintypes.HWND
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
            get_ancestor.restype = wintypes.HWND

            child_hwnd = wintypes.HWND(child_id)
            wrapper_hwnd = get_ancestor(child_hwnd, 2)  # GA_ROOT
            parent_hwnd = get_parent(child_hwnd)
            hwnds = []
            for candidate in (wrapper_hwnd, parent_hwnd, child_hwnd):
                value = int(getattr(candidate, "value", candidate) or 0)
                if value and value not in hwnds:
                    hwnds.append(value)

            def colorref(color):
                h = color.lstrip("#")
                r, g, b = int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return r | (g << 8) | (b << 16)

            for hwnd_value in hwnds:
                hwnd = wintypes.HWND(hwnd_value)

                def set_dword(attr, value):
                    data = ctypes.c_int(value)
                    return dwm.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(data), ctypes.sizeof(data))

                # DWMWA_USE_IMMERSIVE_DARK_MODE moved from 19 to 20 across
                # Windows 10 builds. Prefer 20, then fall back to 19.
                for attr in (20, 19):
                    try:
                        if set_dword(attr, 1) == 0:
                            break
                    except Exception:
                        continue

                # Windows 11 exposes explicit non-client colors. Older Windows
                # versions safely ignore unsupported attributes.
                for attr, color in ((35, PALETTE["bg"]),
                                    (36, PALETTE["text"]),
                                    (34, PALETTE["border"])):
                    try:
                        set_dword(attr, colorref(color))
                    except Exception:
                        pass

            if frame_changed and hwnds:
                root_hwnd = wintypes.HWND(hwnds[0])
                set_window_pos = user32.SetWindowPos
                set_window_pos.argtypes = [
                    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
                set_window_pos.restype = wintypes.BOOL
                flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
                set_window_pos(root_hwnd, None, 0, 0, 0, 0, flags)
                try:
                    # Force only the non-client frame to repaint after a zoom/map
                    # transition; this avoids waiting for the next user resize.
                    user32.RedrawWindow(root_hwnd, None, None,
                                        0x0001 | 0x0100 | 0x0400)
                except Exception:
                    pass
        except Exception:
            log.debug("Could not style secondary Windows frame", exc_info=True)

    def _schedule_dark_titlebar_refresh(self, window):
        """Re-apply DWM colors across the short native map/maximize transition."""
        if sys.platform != "win32":
            return

        def apply(frame_changed=False):
            try:
                if window.winfo_exists():
                    self._set_dark_titlebar_for(
                        window, frame_changed=frame_changed)
            except tk.TclError:
                pass

        # Tk/Windows can recreate the wrapper frame just after mapping or zooming.
        # Three bounded passes cover that transition without an ongoing timer.
        self.after_idle(lambda: apply(False))
        self.after(80, lambda: apply(True))
        self.after(260, lambda: apply(True))

    def _initialize_popup_performance(self):
        self._popup_performance = {
            "created": 0, "presented": 0, "destroyed": 0,
            "last_prepare_ms": 0.0, "max_prepare_ms": 0.0,
        }

    def _create_hidden_popup(self, title="", *, transient=None, resizable=True):
        """Create an app-owned Toplevel that can never expose an unfinished frame."""
        import time
        started = time.perf_counter()
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.configure(bg=PALETTE["surface2"])
        if title:
            popup.title(title)
        if transient is not False:
            try:
                popup.transient(transient or self)
            except tk.TclError:
                pass
        if not resizable:
            popup.resizable(False, False)
        self._set_dark_titlebar_for(popup, frame_changed=False)
        stats = getattr(self, "_popup_performance", None)
        if stats is not None:
            stats["created"] += 1
            elapsed = (time.perf_counter() - started) * 1000.0
            stats["last_prepare_ms"] = elapsed
            stats["max_prepare_ms"] = max(stats["max_prepare_ms"], elapsed)
        return popup

    def _present_hidden_popup(
            self, popup, *, preferred_width=None, preferred_height=None,
            min_width=320, min_height=220, lock_size=False, focus=None, grab=False,
            center=True):
        """Finish hidden geometry/style first, then map exactly once."""
        if center:
            self._center_popup_with_visible_actions(
                popup, preferred_width or popup.winfo_reqwidth(),
                preferred_height or popup.winfo_reqheight(),
                min_width=min_width, min_height=min_height, lock_size=lock_size)
        else:
            # A hidden layout flush is safe; no intermediate pixels are visible.
            popup.update_idletasks()
            self._set_dark_titlebar_for(popup, frame_changed=False)
        popup.deiconify()
        popup.lift()
        if focus is not None:
            try:
                focus.focus_set()
            except tk.TclError:
                pass
        else:
            try:
                popup.focus_set()
            except tk.TclError:
                pass
        if grab:
            try:
                popup.grab_set()
            except tk.TclError:
                pass
        stats = getattr(self, "_popup_performance", None)
        if stats is not None:
            stats["presented"] += 1
        return popup

    def _record_popup_destroyed(self, _popup=None):
        stats = getattr(self, "_popup_performance", None)
        if stats is not None:
            stats["destroyed"] += 1

    def _initialize_layout_motion(self):
        self._layout_motion_depth = 0
        self._layout_motion_after = None
        self._pane_clamp_after = None
        self._layout_settle_callbacks = []
        self._layout_motion_stats = {
            "begins": 0, "settles": 0, "configure_events": 0,
        }

    def _register_layout_settle_callback(self, callback):
        callbacks = getattr(self, "_layout_settle_callbacks", None)
        if callbacks is not None and callback not in callbacks:
            callbacks.append(callback)

    def _begin_layout_motion(self):
        self._layout_motion_depth = max(1, getattr(self, "_layout_motion_depth", 0) + 1)
        self._window_in_motion = True
        stats = getattr(self, "_layout_motion_stats", None)
        if stats is not None:
            stats["begins"] += 1
        pending = getattr(self, "_layout_motion_after", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
            self._layout_motion_after = None

    def _touch_layout_motion(self):
        self._window_in_motion = True
        stats = getattr(self, "_layout_motion_stats", None)
        if stats is not None:
            stats["configure_events"] += 1
        pending = getattr(self, "_layout_motion_after", None)
        if pending is not None:
            try:
                self.after_cancel(pending)
            except tk.TclError:
                pass
        try:
            self._layout_motion_after = self.after(100, self._settle_layout_motion)
        except tk.TclError:
            self._layout_motion_after = None

    def _settle_layout_motion(self):
        self._layout_motion_after = None
        self._layout_motion_depth = 0
        self._window_in_motion = False
        stats = getattr(self, "_layout_motion_stats", None)
        if stats is not None:
            stats["settles"] += 1
        for callback in tuple(getattr(self, "_layout_settle_callbacks", ())):
            try:
                callback()
            except (tk.TclError, AttributeError):
                pass

    def _end_layout_motion(self, _event=None):
        self._layout_motion_depth = 0
        self._touch_layout_motion()

    def _schedule_pane_clamp(self):
        """Coalesce sash safety clamps to at most one idle callback."""
        if getattr(self, "_pane_clamp_after", None) is not None:
            return

        def run():
            self._pane_clamp_after = None
            clamp = getattr(self, "_clamp_saved_pane_geometry", None)
            if clamp is None:
                return
            try:
                clamp()
            except (tk.TclError, AttributeError):
                pass

        try:
            self._pane_clamp_after = self.after_idle(run)
        except tk.TclError:
            self._pane_clamp_after = None

    def _register_paned_motion(self, paned):
        """Treat every sash drag as UI motion so child reflows wait for settle."""
        def press(_event):
            self._begin_layout_motion()

        def drag(_event):
            self._touch_layout_motion()
            # The ttk class binding moves the sash after this instance binding.
            # Clamp on idle so no pane can spend a visible frame below its
            # action-safe minimum while the pointer is still down.
            self._schedule_pane_clamp()

        paned.bind("<ButtonPress-1>", press, add="+")
        paned.bind("<B1-Motion>", drag, add="+")
        paned.bind("<ButtonRelease-1>", self._end_layout_motion, add="+")
        return paned

    def ui_performance_info(self):
        return {
            "popup": dict(getattr(self, "_popup_performance", {})),
            "layout_motion": dict(getattr(self, "_layout_motion_stats", {})),
        }

    def _dark_menu(self, parent=None):
        """Create an app-owned popup/context menu using the MTG dark palette."""
        p = PALETTE
        return tk.Menu(
            parent or self,
            tearoff=False,
            bg=p["surface2"],
            fg=p["text"],
            activebackground=p["accent"],
            activeforeground=p["on_accent"],
            disabledforeground=p["muted"],
            selectcolor=p["accent"],
            relief="flat",
            bd=1,
            activeborderwidth=0,
            font=FONT_HELPER,
        )
