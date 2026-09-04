"""Physical MTG proxy-sheet layout and atomic PDF rendering."""

from __future__ import annotations

import os
import tempfile

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


INCH = 72.0
CARD_W = 2.5 * INCH
CARD_H = 3.5 * INCH
PAGE_W, PAGE_H = letter
COLS = 3
ROWS = 3
CARDS_PER_PAGE = COLS * ROWS

# A small physical gap helps optical cutters distinguish adjacent cards while
# retaining nine full-size cards on US Letter paper.
CARD_GAP = 0.04 * INCH
CARD_CORNER_RADIUS = 0.12 * INCH
BORDER_WIDTH = 0.20

GRID_W = COLS * CARD_W + (COLS - 1) * CARD_GAP
GRID_H = ROWS * CARD_H + (ROWS - 1) * CARD_GAP
MARGIN_X = (PAGE_W - GRID_W) / 2.0
MARGIN_Y = (PAGE_H - GRID_H) / 2.0


def page_count(card_count):
    """Return the number of nine-card sheets required for a card count."""
    count = max(0, int(card_count or 0))
    return (count + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE


def _draw_card(pdf, png_path, x, y):
    """Draw one card at exact physical size while preserving aspect ratio."""
    image_reader = ImageReader(png_path)
    image_width, image_height = image_reader.getSize()

    target_ratio = CARD_W / CARD_H
    image_ratio = (
        image_width / image_height if image_height else target_ratio)
    if image_ratio > target_ratio:
        draw_width = CARD_W
        draw_height = CARD_W / image_ratio
    else:
        draw_height = CARD_H
        draw_width = CARD_H * image_ratio

    draw_x = x + (CARD_W - draw_width) / 2.0
    draw_y = y + (CARD_H - draw_height) / 2.0
    pdf.drawImage(
        image_reader, draw_x, draw_y,
        width=draw_width, height=draw_height,
        preserveAspectRatio=True, anchor="c", mask="auto")

    pdf.saveState()
    pdf.setStrokeColorRGB(0, 0, 0)
    pdf.setLineWidth(BORDER_WIDTH)
    pdf.roundRect(
        x, y, CARD_W, CARD_H, CARD_CORNER_RADIUS, stroke=1, fill=0)
    pdf.restoreState()


def render_print_template(placements, output_path, deck_name="MTG Deck",
                          progress_cb=None, cancel_cb=None):
    """Render ``(card, png_path)`` placements to an atomic US Letter PDF."""
    placements = tuple(placements)
    if not placements:
        raise ValueError("The deck is empty.")

    output_path = os.fspath(output_path)
    directory = os.path.dirname(os.path.abspath(output_path)) or "."
    os.makedirs(directory, exist_ok=True)
    # PORT-007: a unique temporary name, so two renders to the same destination
    # cannot write through one another, and an fsync below so the rename cannot
    # publish a PDF whose bytes have not reached disk.
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(output_path)}.", suffix=".part",
        dir=directory)
    os.close(descriptor)
    pdf = canvas.Canvas(temporary_path, pagesize=letter, pageCompression=1)
    pdf.setTitle(f"{deck_name or 'MTG Deck'} - Print Template")
    pdf.setAuthor("MTG Deck Builder")

    try:
        total = len(placements)
        for index, (card, png_path) in enumerate(placements):
            if cancel_cb:
                cancel_cb()
            slot = index % CARDS_PER_PAGE
            if slot == 0 and index > 0:
                pdf.showPage()

            column = slot % COLS
            row_from_top = slot // COLS
            x = MARGIN_X + column * (CARD_W + CARD_GAP)
            y = (
                PAGE_H - MARGIN_Y - (row_from_top + 1) * CARD_H
                - row_from_top * CARD_GAP)
            _draw_card(pdf, png_path, x, y)

            if progress_cb:
                progress_cb(
                    "layout", index + 1, total,
                    card.get("name") or "Card")

        if cancel_cb:
            cancel_cb()
        pdf.save()
        with open(temporary_path, "r+b") as handle:
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary_path, output_path)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass

    return output_path
