"""Main card-detail presentation backed by the shared image service."""

from __future__ import annotations

import logging
import math
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
from mtgdb.ui.components import AppButton, format_display_name
from mtgdb.ui.tokens import (
    CARD_PREVIEW_PORTRAIT_SIZE,
    CARD_ZOOM_BASE_PORTRAIT_SIZE,
    CARD_ZOOM_LEVELS,
    CARD_ZOOM_WINDOW_MIN_SIZE,
    CARD_ZOOM_WINDOW_SIZE,
    FONT_BODY,
    FONT_HELPER,
    PALETTE,
    RESULT_GALLERY_CARD_ASPECT,
    RESULT_GALLERY_CARD_MAX_WIDTH,
    RESULT_GALLERY_CARD_MIN_WIDTH,
    RESULT_GALLERY_CARD_TARGET_WIDTH,
    RESULT_GALLERY_CARD_SLIDER_STEP,
    RESULT_GALLERY_GAP,
    RESULT_GALLERY_MAX_COLUMNS,
    RESULT_GALLERY_MAX_VISIBLE_ROWS,
    RESULT_GALLERY_WINDOW_MIN_SIZE,
    RESULT_GALLERY_WINDOW_SIZE,
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


def results_gallery_layout_metrics(width, height, target_width=None):
    """Return a bounded responsive layout for a continuously scrolling gallery.

    ``rows`` intentionally includes enough reusable slot rows to cover partially
    clipped cards above/below the viewport.  The logical gallery itself is not
    paged by rows; vertical position is tracked in pixels.
    """
    viewport_width = max(320, int(width))
    viewport_height = max(260, int(height))
    gap = RESULT_GALLERY_GAP
    target = RESULT_GALLERY_CARD_TARGET_WIDTH if target_width is None else target_width
    image_width = max(
        RESULT_GALLERY_CARD_MIN_WIDTH,
        min(RESULT_GALLERY_CARD_MAX_WIDTH, int(round(float(target)))),
    )
    columns = max(1, min(
        RESULT_GALLERY_MAX_COLUMNS,
        max(1, (viewport_width + gap) // (image_width + gap)),
    ))
    image_height = max(
        1, int(round(image_width * RESULT_GALLERY_CARD_ASPECT)))
    cell_height = image_height
    row_stride = cell_height + gap
    # One leading partial row plus one trailing partial row keeps the viewport
    # completely populated while scrolling at arbitrary pixel offsets.
    rows = max(1, min(
        RESULT_GALLERY_MAX_VISIBLE_ROWS,
        max(1, int(math.ceil(viewport_height / row_stride)) + 2),
    ))
    return {
        "columns": columns,
        "rows": rows,
        "slot_count": columns * rows,
        "image_w": image_width,
        "image_h": image_height,
        "cell_h": cell_height,
        "row_stride": row_stride,
        "gap": gap,
    }


class _GalleryCardPeekWindow:
    """Temporary enlarged card image that dismisses when focus leaves it."""

    IMAGE_POLL_MS = 25

    def __init__(self, owner, parent, card):
        self.owner = owner
        self.parent = parent
        self.card = dict(card or {})
        self._future = None
        self._poll_after = None
        self._photo = None
        self._closed = False
        name = str(self.card.get("name") or "Card")
        self.top = owner._create_hidden_popup(
            name, transient=parent, resizable=False)
        self.top.configure(bg=PALETTE["bg"])
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.close())
        self.top.bind("<FocusOut>", self._focus_out, add="+")
        self.image = tk.Label(
            self.top, text="Loading image…", bg=PALETTE["input"],
            fg=PALETTE["muted"], font=FONT_BODY, bd=0,
            highlightthickness=1, highlightbackground=PALETTE["border"],
        )
        self.image.pack(fill="both", expand=True, padx=3, pady=3)
        rotation = card_display_rotation_degrees(self.card, 0, face_index=0)
        target = _target_for_rotation(CARD_ZOOM_BASE_PORTRAIT_SIZE, rotation)
        owner._center_popup_with_visible_actions(
            self.top, preferred_width=target[0] + 10,
            preferred_height=target[1] + 10, min_width=360, min_height=500,
            lock_size=True, screen_margin_x=24, screen_margin_y=24)
        owner._present_hidden_popup(self.top, center=False, focus=self.image)
        self._request_image(target, rotation)

    def _request_image(self, target, rotation):
        url = card_face_image_url(self.card, 0) or _card_image_url(self.card)
        if ImageTk is None or not self.owner.card_image_service.available or not url:
            self.image.configure(text="Image unavailable")
            return
        try:
            self._future = self.owner.card_image_service.request(
                self.card, url, face_index=0, target_size=target,
                rotation_degrees=rotation, allow_upscale=True,
                channel="results-gallery-peek")
        except Exception:
            self.image.configure(text="Image unavailable")
            return
        self._poll_after = self.top.after(
            0 if self._future.done() else self.IMAGE_POLL_MS, self._poll_image)

    def _poll_image(self):
        self._poll_after = None
        future = self._future
        if self._closed or future is None:
            return
        if not future.done():
            try:
                self._poll_after = self.top.after(self.IMAGE_POLL_MS, self._poll_image)
            except tk.TclError:
                pass
            return
        try:
            self._photo = ImageTk.PhotoImage(future.result())
            self.image.configure(image=self._photo, text="", width=0, height=0)
        except Exception:
            try:
                self.image.configure(text="Image unavailable", image="")
            except tk.TclError:
                pass

    def _focus_out(self, _event=None):
        try:
            self.top.after(40, self._close_if_focus_left)
        except tk.TclError:
            pass

    def _close_if_focus_left(self):
        if self._closed:
            return
        try:
            focused = self.top.focus_get()
            if focused is not None and focused.winfo_toplevel() is self.top:
                return
        except tk.TclError:
            pass
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_after is not None:
            try:
                self.top.after_cancel(self._poll_after)
            except tk.TclError:
                pass
            self._poll_after = None
        self._future = None
        try:
            self.top.destroy()
        except tk.TclError:
            pass


class _ResultsGalleryWindow:
    """Modeless virtualized image grid over the current visible Search Results."""

    RESIZE_DEBOUNCE_MS = 70
    IMAGE_POLL_MS = 30

    def __init__(self, owner, *, count_fn, card_at_fn, context_menu_fn=None):
        self.owner = owner
        self._count_fn = count_fn
        self._card_at_fn = card_at_fn
        self._context_menu_fn = context_menu_fn
        # Keep the gallery as an independent resizable top-level so Windows
        # exposes normal resize/maximize chrome instead of transient-dialog
        # restrictions.
        self.top = owner._create_hidden_popup(
            "Results Gallery", transient=False, resizable=True)
        self.top.resizable(True, True)
        self.top.configure(bg=PALETTE["bg"])
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _event: self.close())

        self._scroll_y = 0.0
        self._card_size = RESULT_GALLERY_CARD_TARGET_WIDTH
        self._layout = results_gallery_layout_metrics(
            *RESULT_GALLERY_WINDOW_SIZE, target_width=self._card_size)
        self._image_generation = 0
        self._image_requests = {}
        self._photos = {}
        self._slots = []
        self._empty_label = None
        self._image_after = None
        self._resize_after = None
        self._size_after = None
        self._peek_window = None
        self._owner_click_binding = None

        self._build_shell()
        self.top.bind("<Button-1>", self._dismiss_peek_from_outside, add="+")
        try:
            self._owner_click_binding = owner.bind(
                "<Button-1>", self._dismiss_peek_from_outside, add="+")
        except tk.TclError:
            self._owner_click_binding = None
        owner._center_popup_with_visible_actions(
            self.top,
            preferred_width=RESULT_GALLERY_WINDOW_SIZE[0],
            preferred_height=RESULT_GALLERY_WINDOW_SIZE[1],
            min_width=RESULT_GALLERY_WINDOW_MIN_SIZE[0],
            min_height=RESULT_GALLERY_WINDOW_MIN_SIZE[1],
            lock_size=False,
            screen_margin_x=30,
            screen_margin_y=30,
        )
        try:
            self.top.minsize(*RESULT_GALLERY_WINDOW_MIN_SIZE)
        except tk.TclError:
            pass
        self.top.update_idletasks()
        self._sync_layout(force=True)
        self.refresh_results()
        self.viewport.bind("<Configure>", self._on_viewport_configure, add="+")
        owner._register_scrollable(self.viewport, target=self)
        owner._present_hidden_popup(self.top, center=False)

    def _build_shell(self):
        header = tk.Frame(self.top, bg=PALETTE["surface"], padx=8, pady=6)
        header.pack(fill="x")
        self.title_label = ttk.Label(
            header, text="RESULTS GALLERY | 0 CARDS",
            style="DialogTitle.TLabel",
        )
        self.title_label.pack(side="left")
        AppButton(
            header, text="Close", role="compact", command=self.close,
        ).pack(side="right")
        self.card_size_scale = ttk.Scale(
            header, from_=RESULT_GALLERY_CARD_MIN_WIDTH,
            to=RESULT_GALLERY_CARD_MAX_WIDTH, orient="horizontal",
            length=190, style="Gallery.Horizontal.TScale",
            command=self._on_card_size_change,
        )
        self.card_size_scale.set(self._card_size)
        self.card_size_scale.pack(side="right", padx=(5, 15))
        tk.Label(
            header, text="Card Size", bg=PALETTE["surface"],
            fg=PALETTE["text"], font=FONT_HELPER,
        ).pack(side="right")

        body = tk.Frame(self.top, bg=PALETTE["bg"], padx=3, pady=3)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.viewport = tk.Frame(body, bg=PALETTE["bg"])
        self.viewport.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(
            body, orient="vertical", command=self._yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(2, 0))

    def lift(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
        except tk.TclError:
            pass

    def close(self):
        for attr in ("_image_after", "_resize_after", "_size_after"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.top.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._image_generation += 1
        self._image_requests.clear()
        self._photos.clear()
        if self._peek_window is not None:
            self._peek_window.close()
            self._peek_window = None
        if self._owner_click_binding is not None:
            try:
                self.owner.unbind("<Button-1>", self._owner_click_binding)
            except tk.TclError:
                pass
            self._owner_click_binding = None
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if getattr(self.owner, "_results_gallery_window", None) is self:
            self.owner._results_gallery_window = None

    def _on_card_size_change(self, value):
        try:
            size = int(round(float(value) / RESULT_GALLERY_CARD_SLIDER_STEP)
                       * RESULT_GALLERY_CARD_SLIDER_STEP)
        except (TypeError, ValueError):
            return
        size = max(RESULT_GALLERY_CARD_MIN_WIDTH,
                   min(RESULT_GALLERY_CARD_MAX_WIDTH, size))
        self._card_size = size
        if self._size_after is not None:
            try:
                self.top.after_cancel(self._size_after)
            except tk.TclError:
                pass
        try:
            self._size_after = self.top.after(45, self._finish_card_size_change)
        except tk.TclError:
            self._size_after = None

    def _finish_card_size_change(self):
        self._size_after = None
        if self._sync_layout(force=True):
            self._render()

    def _on_viewport_configure(self, _event=None):
        if self._resize_after is not None:
            try:
                self.top.after_cancel(self._resize_after)
            except tk.TclError:
                pass
        try:
            self._resize_after = self.top.after(
                self.RESIZE_DEBOUNCE_MS, self._finish_resize)
        except tk.TclError:
            self._resize_after = None

    def _finish_resize(self):
        self._resize_after = None
        if self._sync_layout(force=False):
            self._render()

    def _sync_layout(self, *, force=False):
        try:
            width = max(1, self.viewport.winfo_width())
            height = max(1, self.viewport.winfo_height())
        except tk.TclError:
            return False
        if width <= 1 or height <= 1:
            width, height = RESULT_GALLERY_WINDOW_SIZE
        new_layout = results_gallery_layout_metrics(
            width, height, target_width=self._card_size)
        old_layout = self._layout
        if not force and new_layout == old_layout:
            return False

        old_columns = max(1, old_layout.get("columns", 1))
        old_stride = max(1, old_layout.get(
            "row_stride", old_layout.get("cell_h", 1) + old_layout.get("gap", 0)))
        old_first_row = int(max(0.0, self._scroll_y) // old_stride)
        old_row_fraction = (max(0.0, self._scroll_y) % old_stride) / old_stride
        first_position = old_first_row * old_columns

        self._layout = new_layout
        new_columns = max(1, new_layout["columns"])
        new_stride = max(1, new_layout["row_stride"])
        new_first_row = first_position // new_columns
        self._scroll_y = (
            new_first_row * new_stride + old_row_fraction * new_stride)
        self._clamp_scroll_y()
        return True

    def _total_count(self):
        try:
            return max(0, int(self._count_fn() or 0))
        except Exception:
            return 0

    def _total_rows(self):
        count = self._total_count()
        columns = max(1, self._layout["columns"])
        return int(math.ceil(count / columns)) if count else 0

    def _viewport_height(self):
        try:
            height = max(1, int(self.viewport.winfo_height()))
        except tk.TclError:
            height = RESULT_GALLERY_WINDOW_SIZE[1]
        return height if height > 1 else RESULT_GALLERY_WINDOW_SIZE[1]

    def _total_scroll_height(self):
        rows = self._total_rows()
        if rows <= 0:
            return 0
        return max(0, rows * self._layout["row_stride"] - self._layout["gap"])

    def _max_scroll_y(self):
        return max(0.0, float(self._total_scroll_height() - self._viewport_height()))

    def _clamp_scroll_y(self):
        self._scroll_y = max(0.0, min(float(self._scroll_y), self._max_scroll_y()))

    def _first_visible_row(self):
        stride = max(1, self._layout["row_stride"])
        return int(max(0.0, self._scroll_y) // stride)

    def _position_bound_slots(self):
        """Slide already-bound cells without rebinding/re-requesting images."""
        stride = max(1, self._layout["row_stride"])
        first_row = self._first_visible_row()
        row_offset = int(round(self._scroll_y - first_row * stride))
        columns = max(1, self._layout["columns"])
        gap = self._layout["gap"]
        for slot in self._slots:
            position = slot.get("position")
            if position is None:
                continue
            absolute_row, column = divmod(int(position), columns)
            relative_row = absolute_row - first_row
            x = column * (self._layout["image_w"] + gap)
            y = relative_row * stride - row_offset
            try:
                slot["cell"].place_configure(x=x, y=y)
            except tk.TclError:
                pass

    def _scroll_to_y(self, value):
        old_first_row = self._first_visible_row()
        self._scroll_y = float(value)
        self._clamp_scroll_y()
        if self._first_visible_row() == old_first_row:
            self._position_bound_slots()
            self._update_scrollbar()
        else:
            self._render()

    def _update_scrollbar(self):
        total_height = self._total_scroll_height()
        viewport_height = self._viewport_height()
        self._clamp_scroll_y()
        if total_height <= 0 or total_height <= viewport_height:
            first, last = 0.0, 1.0
            self.scrollbar.state(["disabled"])
        else:
            first = self._scroll_y / total_height
            last = min(1.0, (self._scroll_y + viewport_height) / total_height)
            self.scrollbar.state(["!disabled"])
        try:
            self.scrollbar.set(first, last)
        except tk.TclError:
            pass

    def _yview(self, *args):
        if not args:
            return
        if args[0] == "moveto" and len(args) >= 2:
            try:
                fraction = max(0.0, min(1.0, float(args[1])))
            except (TypeError, ValueError):
                return
            self._scroll_to_y(fraction * self._total_scroll_height())
        elif args[0] == "scroll" and len(args) >= 3:
            self.yview_scroll(args[1], args[2])

    def yview_scroll(self, number, what="units"):
        try:
            amount = int(number)
        except (TypeError, ValueError):
            return
        if not amount:
            return
        if str(what) == "pages":
            pixels = amount * max(80, int(self._viewport_height() * 0.88))
        else:
            # Wheel movement is intentionally smaller than a card row. This is
            # what makes the gallery feel continuous instead of row-snapped.
            pixels = amount * max(28, min(72, self._layout["image_h"] // 5))
        self._scroll_to_y(self._scroll_y + pixels)

    def refresh_results(self):
        try:
            if not self.top.winfo_exists():
                return
        except tk.TclError:
            return
        self._sync_layout(force=False)
        self._clamp_scroll_y()
        self._render()

    def _render(self):
        count = self._total_count()
        title = f"RESULTS GALLERY | {count:,} CARDS"
        self.title_label.configure(text=title)
        try:
            self.top.title(title)
        except tk.TclError:
            pass
        self._update_scrollbar()
        self._image_generation += 1
        self._image_requests.clear()
        self._photos.clear()

        if self._empty_label is not None:
            try:
                self._empty_label.place_forget()
            except tk.TclError:
                pass

        self._ensure_gallery_slots(self._layout["slot_count"])
        for slot in self._slots:
            try:
                slot["cell"].place_forget()
                slot["image"].configure(image="", text="")
            except tk.TclError:
                pass

        if not count:
            if self._empty_label is None:
                self._empty_label = tk.Label(
                    self.viewport,
                    text="No cards are currently listed in Results.",
                    bg=PALETTE["bg"], fg=PALETTE["muted"], font=FONT_BODY,
                    padx=24, pady=36,
                )
            self._empty_label.place(relx=0.5, rely=0.5, anchor="center")
            return

        columns = self._layout["columns"]
        stride = self._layout["row_stride"]
        self._clamp_scroll_y()
        first_row = self._first_visible_row()
        row_offset = int(round(self._scroll_y - first_row * stride))
        start = first_row * columns
        end = min(count, start + self._layout["slot_count"])
        generation = self._image_generation
        for position in range(start, end):
            slot_index = position - start
            absolute_row, column = divmod(position, columns)
            relative_row = absolute_row - first_row
            try:
                card = dict(self._card_at_fn(position) or {})
            except Exception:
                card = {}
            slot = self._slots[slot_index]
            cell = slot["cell"]
            image_box = slot["image_box"]
            image_label = slot["image"]
            gap = self._layout["gap"]
            x = column * (self._layout["image_w"] + gap)
            y = relative_row * stride - row_offset
            cell.place(
                x=x, y=y, width=self._layout["image_w"],
                height=self._layout["image_h"], anchor="nw",
            )
            image_box.configure(
                width=self._layout["image_w"], height=self._layout["image_h"])
            image_label.configure(image="", text="Loading image…", width=0, height=0)
            slot["card"] = card
            slot["position"] = position
            self._queue_gallery_image(
                slot_index, card, image_label, generation=generation)

    def _ensure_gallery_slots(self, required):
        """Create only the bounded live slot pool; scrolling rebinds these cells."""
        while len(self._slots) < max(0, int(required)):
            # Image-only cells eliminate decorative padding/captions so adjacent
            # cards sit nearly edge-to-edge and viewport space goes to card art.
            cell = tk.Frame(self.viewport, bg=PALETTE["bg"], padx=0, pady=0)
            image_box = tk.Frame(
                cell, width=self._layout["image_w"],
                height=self._layout["image_h"], bg=PALETTE["input"],
            )
            image_box.pack(anchor="center")
            image_box.pack_propagate(False)
            image_label = tk.Label(
                image_box, text="", bg=PALETTE["input"],
                fg=PALETTE["muted"], font=FONT_HELPER, justify="center",
            )
            image_label.pack(fill="both", expand=True)
            slot = {
                "cell": cell, "image_box": image_box, "image": image_label,
                "card": {}, "position": None,
            }
            slot_index = len(self._slots)
            image_label.bind(
                "<Button-1>",
                lambda event, index=slot_index: self._open_card_peek(index, event),
                add="+")
            image_label.bind(
                "<Button-3>",
                lambda event, index=slot_index: self._open_card_context(index, event),
                add="+")
            self._slots.append(slot)

    def _dismiss_peek_from_outside(self, _event=None):
        if self._peek_window is not None:
            self._peek_window.close()
            self._peek_window = None

    def _card_for_slot(self, slot_index):
        try:
            slot = self._slots[int(slot_index)]
        except (IndexError, TypeError, ValueError):
            return {}
        return dict(slot.get("card") or {})

    def _open_card_context(self, slot_index, event):
        card = self._card_for_slot(slot_index)
        if not card or self._context_menu_fn is None:
            return "break"
        try:
            self._context_menu_fn(card, event.x_root, event.y_root)
        except Exception:
            log.exception("Could not open Results Gallery card context menu")
        return "break"

    def _open_card_peek(self, slot_index, _event=None):
        card = self._card_for_slot(slot_index)
        if not card:
            return "break"
        if self._peek_window is not None:
            self._peek_window.close()
            self._peek_window = None
        try:
            self._peek_window = _GalleryCardPeekWindow(
                self.owner, self.top, card)
        except Exception:
            self._peek_window = None
            log.exception("Could not open Results Gallery card preview")
        return "break"

    def _queue_gallery_image(self, slot, card, label, *, generation):
        if ImageTk is None or not self.owner.card_image_service.available:
            label.configure(text="Image unavailable")
            return
        url = card_face_image_url(card, 0) or _card_image_url(card)
        if not url:
            label.configure(text="Image unavailable")
            return
        try:
            future = self.owner.card_image_service.request(
                card, url, face_index=0,
                target_size=(self._layout["image_w"], self._layout["image_h"]),
                channel=f"results-gallery-slot-{slot}",
            )
        except Exception:
            label.configure(text="Image unavailable")
            return
        self._image_requests[slot] = (generation, label, future)
        if self._image_after is None:
            self._image_after = (
                self.top.after_idle(self._poll_gallery_images)
                if future.done() else
                self.top.after(self.IMAGE_POLL_MS, self._poll_gallery_images))

    def _poll_gallery_images(self):
        self._image_after = None
        for slot, request in tuple(self._image_requests.items()):
            generation, label, future = request
            if not future.done():
                continue
            self._image_requests.pop(slot, None)
            if generation != self._image_generation:
                continue
            try:
                photo = ImageTk.PhotoImage(future.result())
                self._photos[slot] = photo
                label.configure(image=photo, text="", width=0, height=0)
            except Exception:
                try:
                    label.configure(text="Image unavailable", image="")
                except tk.TclError:
                    return
        if self._image_requests:
            try:
                self._image_after = self.top.after(
                    self.IMAGE_POLL_MS, self._poll_gallery_images)
            except tk.TclError:
                self._image_after = None


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
            header, text="Close", role="compact", command=self.close,
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

        self.title_label = ttk.Label(
            header, text="CARD ZOOM", style="DialogTitle.TLabel", anchor="w",
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
        self.card_gallery_btn = None

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
        self.card_gallery_btn = AppButton(
            header, text="Gallery", role="compact_primary",
            command=self._request_results_gallery)
        self.card_gallery_btn.pack(side="right", padx=(0, 6))
        self._set_card_preview_actions(False)
        self.card_legality_btn.state(["disabled"])
        self.card_gallery_btn.state(["disabled"])

        self.card_image = ttk.Label(
            parent, anchor="center", justify="center",
            style="PreviewCard.TLabel")
        self.card_image.pack(side="top", fill="both", expand=True, pady=(4, 0))

    def _request_results_gallery(self):
        callback = getattr(self, "_open_results_gallery", None)
        if callable(callback):
            callback()

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
        ttk.Label(
            shell, text="CARD LEGALITY", style="DialogTitle.TLabel",
            anchor="w").pack(fill="x")
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
            foot, text="Close", role="compact",
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
            label = format_display_name(value)
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
