"""Main card-detail presentation backed by the shared image service."""

from __future__ import annotations

import logging
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from mtgdb.database.constants import PLAYABLE_LEGALITY_STATUSES
from mtgdb.deck.legality import normalize_legalities
from mtgdb.core.scryfall_json import card_faces as scryfall_card_faces
from mtgdb.images.service import (
    card_display_rotation_degrees,
    card_face_image_url,
    card_viewable_faces,
)
from mtgdb.ui.components import AppButton
from mtgdb.ui.tokens import (
    CARD_PREVIEW_PORTRAIT_SIZE,
    CARD_ZOOM_BASE_PORTRAIT_SIZE,
    CARD_ZOOM_LEVELS,
    CARD_ZOOM_WINDOW_MIN_SIZE,
    CARD_ZOOM_WINDOW_SIZE,
    FONT_BODY,
    FONT_DIALOG_TITLE,
    FONT_HELPER,
    PALETTE,
)

try:
    from PIL import ImageTk
except Exception:
    ImageTk = None


log = logging.getLogger("mtg")


def _card_image_url(card):
    """Return the interactive-display image URL using the established priority."""
    return (
        card.get("image_normal") or card.get("image_small")
        or card.get("image_png")
    )


def _target_for_rotation(portrait_size, rotation_degrees):
    """Swap portrait bounds when a quarter-turn yields landscape posture."""
    width, height = (max(1, int(value)) for value in portrait_size)
    return (height, width) if int(rotation_degrees) % 180 else (width, height)


class _CardZoomWindow:
    """Modeless dark card-image viewer with bounded zoom and panning."""

    def __init__(self, owner, card, rotation_turns, face_index=0):
        self.owner = owner
        self.top = owner._create_hidden_popup(
            "Card Zoom", transient=owner, resizable=True)
        self.top.configure(bg=PALETTE["bg"])
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.close())
        self.top.bind("<KeyPress-r>", lambda _event: self._rotate())
        self.top.bind("<KeyPress-R>", lambda _event: self._rotate())
        self.top.bind("<KeyPress-plus>", lambda _event: self._zoom_by(1))
        self.top.bind("<KeyPress-minus>", lambda _event: self._zoom_by(-1))
        self.top.bind("<KeyPress-0>", lambda _event: self._reset_zoom())

        self._card = None
        self._card_identity = None
        self._display_key = None
        self._rotation_turns = int(rotation_turns) % 4
        self._face_index = max(0, int(face_index))
        self._zoom_index = CARD_ZOOM_LEVELS.index(100)
        self._request_token = 0
        self._request_after = None
        self._poll_after = None
        self._future = None
        self._future_token = None
        self._photo = None

        self._build_shell()
        self.owner._center_popup_on_screen(
            self.top,
            width=CARD_ZOOM_WINDOW_SIZE[0],
            height=CARD_ZOOM_WINDOW_SIZE[1],
            min_width=CARD_ZOOM_WINDOW_MIN_SIZE[0],
            min_height=CARD_ZOOM_WINDOW_MIN_SIZE[1],
        )
        self.top.minsize(*CARD_ZOOM_WINDOW_MIN_SIZE)
        self.top.resizable(True, True)
        self.show_card(card, rotation_turns, self._face_index)
        self.owner._present_hidden_popup(self.top, center=False)

    def _build_shell(self):
        p = PALETTE
        header = tk.Frame(self.top, bg=p["surface"], padx=12, pady=9)
        header.pack(fill="x")
        # Fixed-width controls are packed before the title so Tk squeezes the
        # title rather than pushing the controls out of the header. A long
        # double-faced card name otherwise overruns the zoom percentage.
        AppButton(
            header, text="Close", role="compact_primary", command=self.close,
        ).pack(side="right")
        AppButton(
            header, text="Rotate", role="compact", command=self._rotate,
        ).pack(side="right", padx=(0, 7))
        self.flip_btn = AppButton(
            header, text="Flip", role="compact", command=self._flip,
        )
        self.flip_btn.pack(side="right", padx=(0, 7))
        AppButton(
            header, text="+", role="compact", width=2,
            command=lambda: self._zoom_by(1),
        ).pack(side="right", padx=(4, 0))
        self.zoom_label = tk.Label(
            header, text="100%", width=6, anchor="center",
            bg=p["surface"], fg=p["muted"], font=FONT_HELPER,
        )
        self.zoom_label.pack(side="right", padx=4)
        AppButton(
            header, text="−", role="compact", width=2,
            command=lambda: self._zoom_by(-1),
        ).pack(side="right")

        self.title_label = tk.Label(
            header, text="CARD ZOOM", bg=p["surface"], fg=p["text"],
            font=FONT_DIALOG_TITLE, anchor="w",
        )
        self.title_label.pack(side="left", fill="x", expand=True)
        self._header_title_full = "CARD ZOOM"
        header.bind("<Configure>", self._fit_header_title, add="+")

        holder = tk.Frame(self.top, bg=p["bg"])
        holder.pack(fill="both", expand=True, padx=10, pady=10)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            holder, bg=p["input"], bd=0, highlightthickness=1,
            highlightbackground=p["border"],
        )
        vbar = ttk.Scrollbar(
            holder, orient="vertical", command=self.canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        hbar = ttk.Scrollbar(
            holder, orient="horizontal", command=self.canvas.xview,
            style="Dark.Horizontal.TScrollbar",
        )
        self.canvas.configure(
            yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self.image_item = self.canvas.create_image(0, 0, anchor="nw")
        self.text_item = self.canvas.create_text(
            0, 0, text="Loading image…", fill=p["muted"],
            font=FONT_BODY, justify="center", width=420,
        )
        self.canvas.bind("<Configure>", self._canvas_configure, add="+")
        self.canvas.bind("<Control-MouseWheel>", self._control_wheel, add="+")
        self.canvas.bind("<Control-Button-4>", self._control_wheel, add="+")
        self.canvas.bind("<Control-Button-5>", self._control_wheel, add="+")

    @staticmethod
    def _shorten_to_width(text, measure, available):
        """Return ``text`` shortened so ``measure`` reports it within ``available``."""
        if available <= 0 or measure(text) <= available:
            return text
        # Prefer breaking at the double-faced separator so the visible half is a
        # whole face name rather than a mid-word cut.
        if " // " in text:
            front = text.split(" // ", 1)[0].strip() + " //…"
            if measure(front) <= available:
                return front
        ellipsis = "…"
        if measure(ellipsis) > available:
            return ""
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if measure(text[:middle].rstrip() + ellipsis) <= available:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + ellipsis

    def _fit_header_title(self, _event=None):
        """Trim the header title to the space the fixed controls leave behind."""
        label = getattr(self, "title_label", None)
        if label is None:
            return
        header = label.master
        try:
            header.update_idletasks()
            width = header.winfo_width()
            if width <= 1:
                return
            controls = sum(
                child.winfo_reqwidth() for child in header.pack_slaves()
                if child is not label)
            padding = int(str(header.cget("padx")) or 0) * 2
            available = width - controls - padding - 8
            font = tkfont.Font(font=label.cget("font"))
            label.configure(
                text=self._shorten_to_width(
                    self._header_title_full, font.measure, available))
        except tk.TclError:
            return

    def _flip(self):
        """Advance the zoom view to the next separately imaged face."""
        card = self._card or {}
        faces = card_viewable_faces(card)
        if len(faces) < 2:
            return
        order = [index for index, _name, _url in faces]
        try:
            position = order.index(self._face_index)
        except ValueError:
            position = 0
        self._face_index = order[(position + 1) % len(order)]
        self.owner._preview_face_index = self._face_index
        self.owner._preview_key = None
        self._display_key = None
        self._schedule_image()
        owner_card = self.owner._preview_card
        if owner_card is not None:
            self.owner._show_card(owner_card)

    def show_card(self, card, rotation_turns, face_index=0):
        card = dict(card or {})
        identity = str(
            card.get("id") or card.get("oracle_id") or card.get("name") or "")
        if identity != self._card_identity:
            self._zoom_index = CARD_ZOOM_LEVELS.index(100)
            self._card_identity = identity
        self._card = card
        self._rotation_turns = int(rotation_turns) % 4
        self._face_index = max(0, int(face_index))
        name = str(card.get("name") or "Card")
        self.top.title(f"Card Zoom — {name}")
        # The window title bar keeps the untruncated name; the in-header label
        # degrades so a long double-faced name cannot crowd the zoom controls.
        self._header_title_full = name.upper()
        self.title_label.configure(text=self._header_title_full)
        self._fit_header_title()
        self._sync_zoom_label()
        if getattr(self, "flip_btn", None) is not None:
            self.flip_btn.state(
                ["!disabled"] if len(card_viewable_faces(card)) > 1
                else ["disabled"])
        display_key = (
            identity,
            card_face_image_url(card, self._face_index) or _card_image_url(card),
            self._rotation_turns,
            self._face_index,
        )
        if display_key != self._display_key:
            self._display_key = display_key
            self._schedule_image()

    def lift(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
        except tk.TclError:
            pass

    def close(self):
        for attr in ("_request_after", "_poll_after"):
            after_id = getattr(self, attr)
            if after_id is not None:
                try:
                    self.top.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._request_token += 1
        self._future = None
        self._future_token = None
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if getattr(self.owner, "_preview_zoom_window", None) is self:
            self.owner._preview_zoom_window = None

    def _rotate(self):
        self.owner._rotate_card_preview()

    def _zoom_by(self, direction):
        new_index = max(
            0, min(len(CARD_ZOOM_LEVELS) - 1,
                   self._zoom_index + int(direction)))
        if new_index == self._zoom_index:
            return "break"
        self._zoom_index = new_index
        self._sync_zoom_label()
        self._schedule_image()
        return "break"

    def _reset_zoom(self):
        new_index = CARD_ZOOM_LEVELS.index(100)
        if self._zoom_index != new_index:
            self._zoom_index = new_index
            self._sync_zoom_label()
            self._schedule_image()
        return "break"

    def _sync_zoom_label(self):
        self.zoom_label.configure(text=f"{CARD_ZOOM_LEVELS[self._zoom_index]}%")

    def _control_wheel(self, event):
        if getattr(event, "num", None) == 4:
            direction = 1
        elif getattr(event, "num", None) == 5:
            direction = -1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if not delta:
                return "break"
            direction = 1 if delta > 0 else -1
        return self._zoom_by(direction)

    def _schedule_image(self):
        self._request_token += 1
        token = self._request_token
        if self._request_after is not None:
            try:
                self.top.after_cancel(self._request_after)
            except tk.TclError:
                pass
        self._request_after = self.top.after(
            70, lambda t=token: self._start_image_request(t))

    def _start_image_request(self, token):
        self._request_after = None
        if token != self._request_token:
            return
        card = self._card or {}
        face_index = self._face_index
        url = card_face_image_url(card, face_index) or _card_image_url(card)
        if not url or not self.owner.card_image_service.available or ImageTk is None:
            self._show_fallback(self.owner._card_summary_text(card))
            return

        rotation = card_display_rotation_degrees(
            card, self._rotation_turns, face_index=face_index)
        zoom = CARD_ZOOM_LEVELS[self._zoom_index] / 100.0
        portrait = tuple(
            max(1, int(round(value * zoom)))
            for value in CARD_ZOOM_BASE_PORTRAIT_SIZE
        )
        target = _target_for_rotation(portrait, rotation)
        self.canvas.itemconfigure(self.text_item, text="Loading image…")
        try:
            self._future = self.owner.card_image_service.request(
                card, url, face_index=face_index, target_size=target,
                rotation_degrees=rotation, allow_upscale=True,
            )
            self._future_token = token
        except Exception:
            self._show_fallback("Image unavailable")
            return
        self._start_polling()

    def _start_polling(self):
        if self._poll_after is None:
            future = self._future
            delay = 0 if future is not None and future.done() else 25
            self._poll_after = self.top.after_idle(
                self._poll_image) if delay == 0 else self.top.after(delay, self._poll_image)

    def _poll_image(self):
        self._poll_after = None
        future = self._future
        if future is None:
            return
        if not future.done():
            self._poll_after = self.top.after(25, self._poll_image)
            return
        token = self._future_token
        self._future = None
        self._future_token = None
        if token != self._request_token:
            return
        try:
            image = future.result()
            photo = ImageTk.PhotoImage(image)
        except Exception:
            self._show_fallback("Image unavailable")
            return
        self._photo = photo
        self.canvas.itemconfigure(self.image_item, image=photo)
        self.canvas.itemconfigure(self.text_item, text="")
        self._reposition_canvas_items()

    def _show_fallback(self, text):
        self._photo = None
        self.canvas.itemconfigure(self.image_item, image="")
        self.canvas.itemconfigure(
            self.text_item, text=text or "(image unavailable)")
        self._reposition_canvas_items()

    def _canvas_configure(self, _event=None):
        self._reposition_canvas_items()

    def _reposition_canvas_items(self):
        try:
            viewport_w = max(1, self.canvas.winfo_width())
            viewport_h = max(1, self.canvas.winfo_height())
            if self._photo is None:
                self.canvas.coords(
                    self.text_item, viewport_w // 2, viewport_h // 2)
                self.canvas.configure(
                    scrollregion=(0, 0, viewport_w, viewport_h))
                return
            image_w = max(1, self._photo.width())
            image_h = max(1, self._photo.height())
            x = max(0, (viewport_w - image_w) // 2)
            y = max(0, (viewport_h - image_h) // 2)
            self.canvas.coords(self.image_item, x, y)
            self.canvas.configure(scrollregion=(
                0, 0, max(viewport_w, image_w), max(viewport_h, image_h)))
        except tk.TclError:
            pass


class CardDetailMixin:
    """Own the main preview widgets, text fallback, and Tk-side image polling."""

    def _initialize_card_detail(self):
        self._img_ref = None
        self._image_token = 0
        self._image_load_after = None
        self._image_poll_after = None
        self._image_ready_after = None
        self._preview_future = None
        self._preview_future_token = None
        self._preview_key = None
        self._preview_loading = False
        self._card_fallback_text = ""
        self._preview_card = None
        self._preview_card_identity = None
        self._preview_rotation_turns = 0
        self._preview_face_index = 0
        self._preview_zoom_window = None
        self._preview_legality_popup = None
        self._preview_legality_list = None
        self._preview_legality_name_lbl = None
        self.card_rotate_btn = None
        self.card_flip_btn = None
        self.card_zoom_btn = None
        self.card_legality_btn = None

    def _build_card_pane(self, parent):
        header = ttk.Frame(parent, style="Preview.TFrame")
        header.pack(fill="x", pady=(0, 8))
        self.card_zoom_btn = AppButton(
            header, text="Zoom", role="compact", command=self._open_card_zoom)
        self.card_zoom_btn.pack(side="right")
        self.card_rotate_btn = AppButton(
            header, text="Rotate", role="compact",
            command=self._rotate_card_preview)
        self.card_rotate_btn.pack(side="right", padx=(0, 6))
        self.card_flip_btn = AppButton(
            header, text="Flip", role="compact",
            command=self._flip_card_preview)
        self.card_flip_btn.pack(side="right", padx=(0, 6))
        self.card_legality_btn = AppButton(
            header, text="Legality", role="compact",
            command=self._open_card_legality)
        self.card_legality_btn.pack(side="right", padx=(0, 6))
        self._set_card_preview_actions(False)
        self.card_legality_btn.state(["disabled"])

        self.card_image = ttk.Label(
            parent, anchor="center", justify="center",
            style="PreviewCard.TLabel")
        self.card_image.pack(side="top", fill="both", expand=True, pady=(4, 0))

    def _set_card_preview_actions(self, enabled, flippable=False):
        state = ["!disabled"] if enabled else ["disabled"]
        for button in (self.card_rotate_btn, self.card_zoom_btn):
            if button is not None:
                button.state(state)
        if self.card_flip_btn is not None:
            self.card_flip_btn.state(
                ["!disabled"] if (enabled and flippable) else ["disabled"])

    def _show_card(self, card):
        card = dict(card or {})
        identity = str(
            card.get("id") or card.get("oracle_id") or card.get("name") or "")
        if identity != self._preview_card_identity:
            self._preview_rotation_turns = 0
            self._preview_face_index = 0
            self._preview_card_identity = identity
        self._preview_card = card

        viewable_faces = card_viewable_faces(card)
        if self._preview_face_index >= max(1, len(viewable_faces)):
            self._preview_face_index = 0
        face_index = self._preview_face_index

        self._card_fallback_text = self._card_summary_text(card)
        url = card_face_image_url(card, face_index) or _card_image_url(card)
        interactive_image = bool(
            url and self.card_image_service.available and ImageTk is not None)
        self._set_card_preview_actions(
            interactive_image, flippable=bool(viewable_faces))
        if self.card_legality_btn is not None:
            self.card_legality_btn.state(["!disabled"] if card else ["disabled"])
        self._sync_preview_legality()

        rotation = card_display_rotation_degrees(
            card, self._preview_rotation_turns, face_index=face_index)
        preview_key = (identity, url, rotation, face_index)
        self._sync_preview_zoom()
        if (preview_key == self._preview_key and
                (self._preview_loading or self._img_ref is not None)):
            return

        self._image_token += 1
        token = self._image_token
        self._cancel_image_ready_retry()
        self._preview_key = preview_key
        self._preview_loading = True
        if self._img_ref is None:
            self.card_image.configure(image="", text="Loading image…")
        if not interactive_image:
            self._preview_loading = False
            self._preview_future = None
            self._preview_future_token = None
            self._img_ref = None
            self.card_image.configure(image="", text=self._card_fallback_text)
            return
        if self._image_load_after is not None:
            try:
                self.after_cancel(self._image_load_after)
            except tk.TclError:
                pass

        def start_load():
            self._image_load_after = None
            if token != self._image_token:
                return
            # The main preview lives in a fixed portrait-size display box.
            # A rotated landscape card is fitted *inside* that same box so the
            # full card remains visible instead of widening/clipping the pane.
            target = CARD_PREVIEW_PORTRAIT_SIZE
            try:
                self._preview_future = self.card_image_service.request(
                    card, url, face_index=face_index, target_size=target,
                    rotation_degrees=rotation, channel="main-preview")
                self._preview_future_token = token
            except Exception as exc:
                self._image_failed(token, str(exc))
                return
            self._start_image_event_pump()

        self._image_load_after = self.after(55, start_load)

    def _rotate_card_preview(self):
        if not self._preview_card:
            return
        self._preview_rotation_turns = (self._preview_rotation_turns + 1) % 4
        self._preview_key = None
        self._show_card(self._preview_card)

    def _flip_card_preview(self):
        """Advance the main preview to the next separately imaged face."""
        card = self._preview_card
        if not card:
            return
        faces = card_viewable_faces(card)
        if len(faces) < 2:
            return
        order = [index for index, _name, _url in faces]
        try:
            position = order.index(self._preview_face_index)
        except ValueError:
            position = 0
        self._preview_face_index = order[(position + 1) % len(order)]
        self._preview_key = None
        self._show_card(card)

    def _open_card_zoom(self):
        if not self._preview_card or not _card_image_url(self._preview_card):
            return
        window = self._preview_zoom_window
        if window is not None:
            try:
                if window.top.winfo_exists():
                    window.show_card(
                        self._preview_card, self._preview_rotation_turns,
                        self._preview_face_index)
                    window.lift()
                    return
            except tk.TclError:
                pass
        self._preview_zoom_window = _CardZoomWindow(
            self, self._preview_card, self._preview_rotation_turns,
            self._preview_face_index)


    def _open_card_legality(self):
        """Show the previewed card's playable format legalities in a small dark window."""
        if not self._preview_card:
            return
        popup = self._preview_legality_popup
        if popup is not None:
            try:
                if popup.winfo_exists():
                    self._sync_preview_legality()
                    popup.deiconify()
                    popup.lift()
                    popup.focus_set()
                    return
            except tk.TclError:
                pass

        p = PALETTE
        popup = self._create_hidden_popup(
            "Card Legality", transient=self, resizable=False)
        popup.configure(bg=p["bg"])
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)
        popup.bind("<Escape>", lambda _event: popup.destroy())
        popup.bind(
            "<Destroy>", lambda _event: self._clear_preview_legality_popup(popup),
            add="+")

        shell = tk.Frame(popup, bg=p["surface"], padx=14, pady=12)
        shell.pack(fill="both", expand=True)
        tk.Label(
            shell, text="CARD LEGALITY", bg=p["surface"], fg=p["accent"],
            font=FONT_DIALOG_TITLE, anchor="w").pack(fill="x")
        self._preview_legality_name_lbl = tk.Label(
            shell, text="", bg=p["surface"], fg=p["muted"],
            font=FONT_HELPER, anchor="w")
        self._preview_legality_name_lbl.pack(fill="x", pady=(2, 8))

        list_shell = tk.Frame(
            shell, bg=p["border"], highlightthickness=1,
            highlightbackground=p["border"])
        list_shell.pack(fill="both", expand=True)
        list_shell.rowconfigure(0, weight=1)
        list_shell.columnconfigure(0, weight=1)
        formats = tk.Listbox(
            list_shell, activestyle="none", exportselection=False,
            background=p["input"], foreground=p["text"],
            selectbackground=p["accent"], selectforeground=p["on_accent"],
            highlightthickness=0, relief="flat", bd=0, font=FONT_BODY)
        scroll = ttk.Scrollbar(
            list_shell, orient="vertical", command=formats.yview,
            style="Dark.Vertical.TScrollbar")
        formats.configure(yscrollcommand=scroll.set)
        formats.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self._register_scrollable(formats)
        self._preview_legality_list = formats

        foot = tk.Frame(shell, bg=p["surface"])
        foot.pack(fill="x", pady=(10, 0))
        AppButton(
            foot, text="Close", role="compact_primary",
            command=popup.destroy).pack(side="right")

        self._preview_legality_popup = popup
        self._sync_preview_legality()
        self._present_hidden_popup(
            popup, preferred_width=390, preferred_height=360,
            min_width=330, min_height=260, lock_size=True)

    def _clear_preview_legality_popup(self, popup):
        if self._preview_legality_popup is popup:
            self._preview_legality_popup = None
            self._preview_legality_list = None
            self._preview_legality_name_lbl = None

    def _sync_preview_legality(self):
        popup = self._preview_legality_popup
        formats = self._preview_legality_list
        card = self._preview_card or {}
        if popup is None or formats is None:
            return
        try:
            if not popup.winfo_exists():
                self._clear_preview_legality_popup(popup)
                return
            name = str(card.get("name") or "Card")
            popup.title(f"Card Legality — {name}")
            if self._preview_legality_name_lbl is not None:
                self._preview_legality_name_lbl.configure(text=name)
            formats.delete(0, "end")
            rows = self._legal_format_rows(card)
            if rows:
                for label in rows:
                    formats.insert("end", label)
            else:
                formats.insert("end", "No playable formats listed.")
        except tk.TclError:
            self._clear_preview_legality_popup(popup)

    def _sync_preview_zoom(self):
        window = self._preview_zoom_window
        if window is None or not self._preview_card:
            return
        try:
            if window.top.winfo_exists():
                window.show_card(
                    self._preview_card, self._preview_rotation_turns,
                    self._preview_face_index)
            else:
                self._preview_zoom_window = None
        except tk.TclError:
            self._preview_zoom_window = None

    @staticmethod
    def _face_summary_lines(face):
        """Return the printed characteristics block for one card face."""
        lines = []
        head = str(face.get("name") or "").strip()
        if face.get("mana_cost"):
            head = f"{head}   {face['mana_cost']}" if head else str(
                face["mana_cost"])
        if head:
            lines.append(head)
        if face.get("type_line"):
            lines.append(str(face["type_line"]))
        if face.get("power") is not None and face.get("toughness") is not None:
            lines.append(f"{face['power']}/{face['toughness']}")
        elif face.get("loyalty"):
            lines.append(f"Loyalty {face['loyalty']}")
        if face.get("oracle_text"):
            lines.append(str(face["oracle_text"]))
        return lines

    @staticmethod
    def _card_summary_text(card):
        """Return the preview text panel, including every face's printed data.

        Card-level ``power``/``toughness``/``oracle_text`` carry only the first
        face, so a multi-face card's remaining faces would otherwise be
        unreadable anywhere in the app even though Search indexes their rules
        text. Showing all faces also keeps the panel truthful while the image
        is flipped to a back face.
        """
        set_line = (
            f"{card.get('set_name', '')} "
            f"({(card.get('set_code') or '').upper()})").strip()
        faces = [face for face in scryfall_card_faces(card)
                 if face.get("oracle_text") or face.get("type_line")]
        if len(faces) > 1:
            parts = [str(card.get("name") or "")]
            for face in faces:
                block = CardDetailMixin._face_summary_lines(face)
                if block:
                    parts.append("")
                    parts.extend(block)
            parts.extend(("", set_line))
            return "\n".join(parts)

        head = card["name"]
        if card.get("mana_cost"):
            head += "   " + card["mana_cost"]
        parts = [head]
        if card.get("type_line"):
            parts.append(card["type_line"])
        if card.get("power") is not None and card.get("toughness") is not None:
            parts.append(f"{card['power']}/{card['toughness']}")
        elif card.get("loyalty"):
            parts.append(f"Loyalty {card['loyalty']}")
        if card.get("oracle_text"):
            parts.extend(("", card["oracle_text"]))
        parts.extend(("", set_line))
        return "\n".join(parts)

    def _legal_format_rows(self, card):
        legal = normalize_legalities(card.get("legalities"))
        preferred = [str(value).casefold() for value in
                     getattr(self, "_format_catalog", ())]
        ordered = [
            value for value in preferred
            if legal.get(value) in PLAYABLE_LEGALITY_STATUSES
        ]
        seen = set(ordered)
        ordered.extend(sorted(
            (value for value, status in legal.items()
             if status in PLAYABLE_LEGALITY_STATUSES and value not in seen),
            key=str.casefold))
        labels = []
        for value in ordered:
            label = value.replace("_", " ").title()
            if legal.get(value) == "restricted":
                label += " (Restricted)"
            labels.append(label)
        return labels

    def _legal_summary(self, card):
        return ", ".join(CardDetailMixin._legal_format_rows(self, card))

    def _start_image_event_pump(self):
        if self._image_poll_after is None:
            future = getattr(self, "_preview_future", None)
            if future is not None and future.done():
                self._image_poll_after = self.after_idle(self._poll_image_events)
            else:
                self._image_poll_after = self.after(20, self._poll_image_events)

    def _poll_image_events(self):
        self._image_poll_after = None
        future = self._preview_future
        if future is None:
            return
        if not future.done():
            if self._preview_loading:
                self._image_poll_after = self.after(20, self._poll_image_events)
            return
        token = self._preview_future_token
        self._preview_future = None
        self._preview_future_token = None
        try:
            image = future.result()
        except Exception as exc:
            self._image_failed(token, str(exc))
            return
        self._image_ready(token, image)

    def _cancel_image_ready_retry(self):
        pending = self._image_ready_after
        self._image_ready_after = None
        if pending is None:
            return
        try:
            self.after_cancel(pending)
        except tk.TclError:
            pass

    def _defer_image_ready(self, token, image):
        """Retry one ready image after window motion without blocking polling."""
        self._cancel_image_ready_retry()

        def resume():
            self._image_ready_after = None
            self._image_ready(token, image)

        self._image_ready_after = self.after(100, resume)

    def _image_ready(self, token, image):
        if token != self._image_token:
            return
        try:
            if self._window_in_motion or self.state() == "iconic":
                self._defer_image_ready(token, image)
                return
        except tk.TclError:
            return
        try:
            photo = ImageTk.PhotoImage(image)
        except Exception as exc:
            log.warning("Could not create card image: %s", exc)
            self._image_failed(token, str(exc))
            return
        self._img_ref = photo
        self._preview_loading = False
        self.card_image.configure(image=photo, text="")

    def _image_failed(self, token, _message):
        if token != self._image_token:
            return
        self._preview_loading = False
        self._preview_future = None
        self._preview_future_token = None
        self._img_ref = None
        self.card_image.configure(
            image="", text=self._card_fallback_text or "(image unavailable)")
