"""Modeless fixed-size card-comparison window presentation.

Compared printings are shown as larger images in a scrollbar-free grid with
comparison/source-board actions. The same fixed grid can present a read-only
sample hand without mutating comparison state. Card attribute data and disclosure
controls are intentionally not part of this view.
"""

import tkinter as tk
from tkinter import ttk

from mtgdb.comparison.models import (
    # MAX_COMPARISON_CARDS is re-exported for the UI component contract test,
    # which asserts the comparison limits through this module.
    MAX_COMPARISON_CARDS,  # noqa: F401
    MIN_COMPARISON_CARDS,
    comparison_json_list as _comparison_json_list,
)
from mtgdb.images.service import (
    card_face_image_url,
    card_viewable_faces,
)
from mtgdb.ui.components import AppButton, deck_board_label
from mtgdb.ui.tokens import (
    COMPARISON_IMAGE_MAX_HEIGHT,
    COMPARISON_IMAGE_MAX_WIDTH,
    COMPARISON_MAX_COLUMNS,
    COMPARISON_WINDOW_MIN_SIZE,
    COMPARISON_WINDOW_SIZE,
    FONT_BODY,
    FONT_HELPER,
    FONT_MICRO,
    PALETTE,
)

try:
    from PIL import ImageTk
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False


def comparison_layout_metrics(window_width, window_height, card_count, *, tk_scaling=96 / 72):
    """Return a count-aware grid that fits inside one fixed comparison viewport."""
    count = max(1, int(card_count or 1))
    columns = min(COMPARISON_MAX_COLUMNS, count)
    rows = max(1, (count + COMPARISON_MAX_COLUMNS - 1) // COMPARISON_MAX_COLUMNS)
    content_width = max(320, int(window_width) - 20)
    content_height = max(320, int(window_height) - 54)
    card_width = max(120, int(content_width / columns) - 8)
    row_height = max(180, int(content_height / rows) - 4)
    # Metadata/actions live beside the image rather than below it. Reserve
    # enough rail width for compact action buttons at the active Tk scaling;
    # higher display scaling shrinks card art before it clips button labels.
    scaled_action_width = max(100, int(round(72 * float(tk_scaling))) + 4)
    minimum_meta_width = scaled_action_width if columns >= 4 else max(120, scaled_action_width)
    # This matches the actual padx between image and side rail below.
    side_rail_reserve = 6
    available_image_width = max(90, card_width - minimum_meta_width - side_rail_reserve)
    available_image_height = max(126, row_height - 12)
    scale = min(
        1.0,
        available_image_width / COMPARISON_IMAGE_MAX_WIDTH,
        available_image_height / COMPARISON_IMAGE_MAX_HEIGHT,
    )
    image_width = max(90, int(COMPARISON_IMAGE_MAX_WIDTH * scale))
    image_height = max(126, int(COMPARISON_IMAGE_MAX_HEIGHT * scale))
    meta_width = max(90, min(180, card_width - image_width - side_rail_reserve))
    return {
        "columns": columns,
        "rows": rows,
        "card_width": card_width,
        "row_height": row_height,
        "meta_width": meta_width,
        "cell_wrap": max(64, meta_width - 4),
        "image_w": image_width,
        "image_h": image_height,
    }


class CardComparisonWindow:
    """Fixed modeless card grid used by comparison and read-only hand viewing."""

    def __init__(self, app, *, cards=None, title="Card Comparison", on_close=None):
        self.app = app
        self._static_cards = None if cards is None else list(cards)
        self._view_title = str(title or "Card Comparison")
        self._on_close = on_close
        self.top = app._create_hidden_popup(
            self._view_title, transient=app, resizable=False)
        self.top.configure(bg=PALETTE["bg"])
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.top.bind("<Escape>", lambda _e: self.close())

        self._image_generation = 0
        self._image_after = None
        self._image_requests = {}
        self._image_labels = {}
        self._photos = {}
        self._face_indexes = {}
        self._fixed_size = COMPARISON_WINDOW_SIZE
        self._layout = comparison_layout_metrics(
            COMPARISON_WINDOW_SIZE[0], COMPARISON_WINDOW_SIZE[1], 2)

        self._build_shell()
        self._configure_fixed_geometry()
        self.refresh()
        self.app._present_hidden_popup(self.top, center=False)


    def _build_shell(self):
        p = PALETTE
        header = tk.Frame(self.top, bg=p["surface"], padx=12, pady=9)
        header.pack(fill="x")
        self.title_label = ttk.Label(
            header, text=self._view_title.upper(), style="DialogTitle.TLabel"
        )
        self.title_label.pack(side="left")
        AppButton(
            header, text="Close", role="compact", command=self.close
        ).pack(side="right")
        self.clear_button = None
        if self._static_cards is None:
            self.clear_button = AppButton(
                header, text="Clear", role="compact",
                command=self.app._clear_comparison
            )
            self.clear_button.pack(side="right", padx=(0, 7))

        self.content = tk.Frame(self.top, bg=p["bg"], padx=10, pady=10)
        self.content.pack(fill="both", expand=True)

    def _configure_fixed_geometry(self):
        configured = self.app._center_popup_with_visible_actions(
            self.top,
            preferred_width=COMPARISON_WINDOW_SIZE[0],
            preferred_height=COMPARISON_WINDOW_SIZE[1],
            min_width=COMPARISON_WINDOW_MIN_SIZE[0],
            min_height=COMPARISON_WINDOW_MIN_SIZE[1],
            lock_size=True,
            screen_margin_x=20,
            screen_margin_y=5,
        )
        if (isinstance(configured, (tuple, list)) and len(configured) == 2):
            self._fixed_size = tuple(max(1, int(value)) for value in configured)
        self.top.resizable(False, False)

    def _layout_metrics(self, card_count):
        try:
            width = max(1, self.top.winfo_width())
            height = max(1, self.top.winfo_height())
            if width <= 1 or height <= 1:
                width, height = self._fixed_size
        except tk.TclError:
            width, height = self._fixed_size
        try:
            tk_scaling = float(self.top.tk.call("tk", "scaling"))
        except (tk.TclError, TypeError, ValueError):
            tk_scaling = 96 / 72
        return comparison_layout_metrics(
            width, height, card_count, tk_scaling=tk_scaling)

    def close(self):
        if self._image_after is not None:
            try:
                self.top.after_cancel(self._image_after)
            except tk.TclError:
                pass
            self._image_after = None
        self._image_requests.clear()
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if getattr(self.app, "_comparison_window", None) is self:
            self.app._comparison_window = None
        callback = self._on_close
        self._on_close = None
        if callback is not None:
            try:
                callback(self)
            except Exception:
                pass

    def lift(self):
        try:
            self.top.deiconify()
            self.top.lift()
            self.top.focus_force()
        except tk.TclError:
            pass

    def _cards(self):
        if self._static_cards is not None:
            return list(self._static_cards)
        return list(self.app.comparison.cards())

    def show_cards(self, cards, *, title=None):
        """Replace a read-only card-grid snapshot without touching comparison state."""
        if self._static_cards is None:
            raise RuntimeError("show_cards is only valid for a read-only card grid")
        self._static_cards = list(cards or ())
        if title is not None:
            self._view_title = str(title or "Card View")
            self.top.title(self._view_title)
            self.title_label.configure(text=self._view_title.upper())
        self.refresh()

    def refresh(self):
        try:
            if not self.top.winfo_exists():
                return
        except tk.TclError:
            return

        cards = self._cards()
        count = len(cards)
        self._layout = self._layout_metrics(max(1, count))
        active_card_ids = {
            str(card.get("id") or index)
            for index, card in enumerate(cards, 1)}
        self._photos = {
            key: photo for key, photo in self._photos.items()
            if key[0] in active_card_ids}
        self._face_indexes = {
            cid: index for cid, index in self._face_indexes.items()
            if cid in active_card_ids}
        for child in self.content.winfo_children():
            child.destroy()
        self._image_labels.clear()
        self._image_generation += 1
        self._image_requests.clear()

        if not cards:
            tk.Label(
                self.content,
                text="Add cards from Search Results or a deck to begin comparing.",
                bg=PALETTE["bg"], fg=PALETTE["muted"],
                font=FONT_BODY, padx=30, pady=40
            ).pack(fill="both", expand=True)
            return

        self._build_cards(cards)

        if self._static_cards is None and count < MIN_COMPARISON_CARDS:
            notice = tk.Frame(
                self.content, bg=PALETTE["surface2"], padx=10, pady=8,
                highlightthickness=1, highlightbackground=PALETTE["border"])
            notice.pack(fill="x", pady=(6, 0))
            tk.Label(
                notice,
                text="Add at least one more card to compare.",
                bg=PALETTE["surface2"], fg=PALETTE["muted"], font=FONT_HELPER
            ).pack(anchor="w")

    def _build_cards(self, cards):
        p = PALETTE
        m = self._layout
        columns = m["columns"]
        rows = m["rows"]
        for row_index in range(rows):
            first = row_index * columns
            row_cards = cards[first:first + columns]
            if not row_cards:
                continue
            row = tk.Frame(self.content, bg=p["bg"])
            row.pack(fill="both", expand=True, pady=(0, 4 if row_index + 1 < rows else 0))
            row.rowconfigure(0, weight=1)
            for column in range(len(row_cards)):
                row.columnconfigure(column, weight=1, uniform="comparison_card")

            for offset, card in enumerate(row_cards):
                card_index = first + offset
                cid = str(card.get("id") or (card_index + 1))
                cardbox = tk.Frame(
                    row, bg=p["surface"], padx=6, pady=6,
                    highlightthickness=1, highlightbackground=p["border"])
                cardbox.grid(
                    row=0, column=offset, sticky="nsew", padx=4, pady=2)

                body = tk.Frame(cardbox, bg=p["surface"])
                body.pack(expand=True)
                image_box = tk.Frame(
                    body, width=m["image_w"], height=m["image_h"],
                    bg=p["input"], highlightthickness=0)
                image_box.pack(side="left", anchor="n")
                image_box.pack_propagate(False)
                image = tk.Label(
                    image_box, text="Loading image…",
                    bg=p["input"], fg=p["muted"], anchor="center",
                    justify="center", font=FONT_MICRO)
                image.pack(fill="both", expand=True)
                instance_key = f"{card_index}:{cid}"
                self._image_labels[instance_key] = image
                if card_viewable_faces(card):
                    # Flipping is bound to the existing image rather than a new
                    # control so the fixed comparison geometry is unchanged.
                    image.configure(cursor="hand2")
                    image.bind(
                        "<Button-1>",
                        lambda _event, c=card, k=instance_key:
                        self._flip_comparison_face(c, k))

                meta = tk.Frame(
                    body, width=m["meta_width"], bg=p["surface"])
                meta.pack(side="left", fill="y", padx=(6, 0))
                meta.pack_propagate(False)
                if card_viewable_faces(card):
                    # Sample hands are read-only, so the flip control is the one
                    # meta action both modes share.
                    AppButton(
                        meta, text="Flip", role="compact",
                        command=lambda c=card, k=instance_key:
                        self._flip_comparison_face(c, k)
                    ).pack(fill="x", pady=(0, 7))
                if self._static_cards is None:
                    source = self.app._comparison_source_info(cid)
                    if source is not None:
                        board = source["board"]
                        qty = int(source.get("qty", 0))
                        board_label = deck_board_label(board)
                        copy_label = "copy" if qty == 1 else "copies"
                        tk.Label(
                            meta,
                            text=f"From {board_label}\n{qty} {copy_label}",
                            bg=p["surface"], fg=p["muted"], font=FONT_MICRO,
                            wraplength=m["cell_wrap"], justify="center",
                        ).pack(fill="x", pady=(0, 7))

                    actions = tk.Frame(meta, bg=p["surface"])
                    actions.pack(fill="x")
                    if source is not None:
                        board = source["board"]
                        qty = int(source.get("qty", 0))
                        if qty > 0:
                            board_label = deck_board_label(board)
                            AppButton(
                                actions, text=f"Remove from\n{board_label}", role="compact",
                                command=lambda c=cid, b=board:
                                self.app._remove_comparison_source_from_board(c, b)
                            ).pack(fill="x")
                    else:
                        AppButton(
                            actions, text="Add to\nMainboard", role="compact",
                            command=lambda c=cid:
                            self.app._add_comparison_card_to_board(c, "main")
                        ).pack(fill="x", pady=(0, 5))
                        AppButton(
                            actions, text="Add to\nSideboard", role="compact",
                            command=lambda c=cid:
                            self.app._add_comparison_card_to_board(c, "side")
                        ).pack(fill="x")
                self._queue_image(card, instance_key)

    def _queue_image(self, card, instance_key):
        cid = str(card.get("id") or "")
        label = self._image_labels.get(instance_key)
        if label is None:
            return
        if not HAVE_PIL:
            label.configure(text="Image unavailable")
            return

        viewable = card_viewable_faces(card)
        face_index = self._face_indexes.get(cid, 0)
        if face_index >= max(1, len(viewable)):
            face_index = 0
            self._face_indexes.pop(cid, None)
        faces = _comparison_json_list(card.get("card_faces"))
        face = faces[0] if faces else {}
        face_img = face.get("image_uris") if isinstance(face, dict) else None
        url = (card_face_image_url(card, face_index) or
               (face_img or {}).get("normal") or (face_img or {}).get("small") or
               card.get("image_normal") or card.get("image_small") or
               card.get("image_png"))
        if not url:
            label.configure(text="Image unavailable")
            return

        generation = self._image_generation
        target_size = (self._layout["image_w"], self._layout["image_h"])
        photo_key = (cid, target_size, face_index)
        cached_photo = self._photos.get(photo_key)
        if cached_photo is not None:
            label.configure(image=cached_photo, text="", width=0, height=0)
            return
        label.configure(text="Loading image…", image="")
        try:
            future = self.app.card_image_service.request(
                card, url, face_index=face_index, target_size=target_size)
        except Exception:
            label.configure(text="Image unavailable", image="")
            return
        self._image_requests[instance_key] = (
            cid, generation, target_size, face_index, future)
        if self._image_after is None:
            self._image_after = (
                self.top.after_idle(self._poll_images) if future.done()
                else self.top.after(30, self._poll_images))

    def _flip_comparison_face(self, card, instance_key):
        """Advance one compared card to its next separately imaged face."""
        cid = str(card.get("id") or "")
        faces = card_viewable_faces(card)
        if len(faces) < 2:
            return
        order = [index for index, _name, _url in faces]
        try:
            position = order.index(self._face_indexes.get(cid, 0))
        except ValueError:
            position = 0
        self._face_indexes[cid] = order[(position + 1) % len(order)]
        self._image_requests.pop(instance_key, None)
        self._queue_image(card, instance_key)

    def _poll_images(self):
        self._image_after = None
        for instance_key, request in tuple(self._image_requests.items()):
            cid, generation, target_size, face_index, future = request
            if not future.done():
                continue
            self._image_requests.pop(instance_key, None)
            if generation != self._image_generation:
                continue
            label = self._image_labels.get(instance_key)
            if label is None:
                continue
            try:
                photo_key = (cid, target_size, face_index)
                photo = self._photos.get(photo_key)
                if photo is None:
                    image = future.result()
                    photo = ImageTk.PhotoImage(image)
                    self._photos[photo_key] = photo
                else:
                    # Duplicate cards can share one completed image request; keep
                    # one retained PhotoImage for every label using that printing.
                    future.result()
                label.configure(image=photo, text="", width=0, height=0)
            except Exception:
                try:
                    label.configure(text="Image unavailable", image="")
                except tk.TclError:
                    return
        if self._image_requests:
            self._image_after = self.top.after(35, self._poll_images)
