"""Tk-free print-image preparation, workflow, and worker lifecycle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import logging
import os

from mtgdb.core.background_jobs import (
    GenerationalWorker, JobCancelled, check_cancel,
)

from PIL import Image

import mtgdb.core.cache_names as cache_names

import mtgdb.core.net as net

from mtgdb.printing.renderer import page_count, render_print_template


log = logging.getLogger("mtg")


class PrintCancelled(JobCancelled):
    """Raised when application shutdown cancels an incomplete print job."""


@dataclass(frozen=True)
class PrintJob:
    deck_name: str
    cards: tuple[dict, ...]
    output_path: str
    cache_dir: str

    @classmethod
    def from_deck(cls, deck, output_path, cache_dir):
        """Capture a stable exact-printing snapshot of one deck."""
        return cls(
            deck_name=str(getattr(deck, "name", "MTG Deck") or "MTG Deck"),
            cards=tuple(copy.deepcopy(expanded_cards(deck))),
            output_path=os.fspath(output_path),
            cache_dir=os.fspath(cache_dir),
        )


@dataclass(frozen=True)
class PrintResult:
    output_path: str
    total_cards: int
    pages: int


@dataclass(frozen=True)
class PrintEvent:
    kind: str
    generation: int
    stage: str = ""
    payload: object = None


@dataclass(frozen=True)
class PrintStart:
    status: str
    generation: int
    total_cards: int


@dataclass(frozen=True)
class PrintPoll:
    progress: PrintEvent | None
    terminal: PrintEvent | None


def expanded_cards(deck):
    """Return card dicts repeated by quantity, mainboard then sideboard."""
    cards = []
    for board in ("main", "side"):
        for entry in deck.entries(board):
            cards.extend([entry["card"]] * int(entry.get("qty", 0)))
    return cards


def face_image_urls(card):
    """Ordered high-resolution PNG URLs to print for one card.

    A double-faced card (transform / modal DFC / battle / reversible /
    double-faced token) carries a separate ``image_uris`` PNG on each face, so
    both sides are printed. Single-image cards -- including split, adventure and
    flip, whose faces share one image -- print their one ``image_png``.
    """
    faces = card.get("card_faces")
    if isinstance(faces, str):
        try:
            faces = json.loads(faces or "[]")
        except (TypeError, ValueError):
            faces = []
    face_urls = []
    if isinstance(faces, list):
        for face in faces:
            if not isinstance(face, dict):
                continue
            image_uris = face.get("image_uris")
            png = image_uris.get("png") if isinstance(image_uris, dict) else None
            if png:
                face_urls.append(png)
    if len(face_urls) >= 2:
        return face_urls
    single = card.get("image_png")
    return [single] if single else []


def expand_faces(cards):
    """Flatten card copies into per-face print units ``(card, face_index, url)``.

    Each face becomes its own proxy so both sides of a double-faced card print.
    A card with no printable image still yields one unit so the download step can
    raise a clear per-card error instead of silently dropping it.
    """
    units = []
    for card in cards:
        urls = face_image_urls(card)
        if not urls:
            units.append((card, 0, card.get("image_png")))
            continue
        for face_index, url in enumerate(urls):
            units.append((card, face_index, url))
    return units


def _safe_id(card):
    value = card.get("id") or card.get("oracle_id") or card.get("name") or "card"
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value)


def cached_png_path(card, cache_dir):
    legacy = os.path.join(cache_dir, f"{_safe_id(card)}.png")
    return cache_names.cache_path(card, cache_dir, ".png", legacy)


def _face_cache_path(front_path, face_index):
    """Distinct cache path for a non-front face beside the front image."""
    stem, ext = os.path.splitext(front_path)
    return f"{stem}.face{int(face_index)}{ext}"


def _check_cancel(cancel_event):
    check_cancel(
        cancel_event, "Print-template creation was cancelled.", PrintCancelled)



def _valid_png(path):
    if not (os.path.exists(path) and os.path.getsize(path) > 0):
        return False
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _download_png(url, path, name, http=net, cancel_event=None):
    """Return a validated high-resolution PNG at ``path``, replacing corrupt data.

    Downloads ``url`` (one card face or single image) to ``path`` and validates
    it. A path already holding a valid PNG is reused so repeated copies -- and
    both faces of a double-faced card -- download at most once each.
    """
    _check_cancel(cancel_event)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    if os.path.exists(path):
        if _valid_png(path):
            return path
        try:
            os.remove(path)
        except OSError:
            pass

    if not url:
        raise ValueError(
            f"No high-resolution PNG is available for {name}")

    temporary_path = path + ".download"
    try:
        _check_cancel(cancel_event)
        http.download(url, temporary_path)
        _check_cancel(cancel_event)
        if not _valid_png(temporary_path):
            raise ValueError(f"Downloaded image for {name} is invalid")
        os.replace(temporary_path, path)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
    return path


def ensure_png(card, cache_dir, http=net, cancel_event=None):
    """Return a validated high-resolution PNG for a card's single/front image."""
    os.makedirs(cache_dir, exist_ok=True)
    return _download_png(
        card.get("image_png"), cached_png_path(card, cache_dir),
        card.get("name", "this card"), http=http, cancel_event=cancel_event)


class PrintTemplateService:
    """Prepare exact-printing PNGs and render one complete proxy PDF."""

    def __init__(self, http=net):
        self.http = http

    def create(self, job, progress_cb=None, cancel_event=None):
        # Expand every copy into its printable faces (both sides of a
        # double-faced card), then map each face to a distinct cache path: the
        # front reuses the card's cache entry, back faces get a per-face path
        # beside it, so each unique image downloads at most once.
        units = expand_faces(job.cards)
        if not units:
            raise ValueError("The deck is empty.")

        placements = []
        downloads = {}
        for card, face_index, url in units:
            front_path = cached_png_path(card, job.cache_dir)
            path = (front_path if face_index == 0
                    else _face_cache_path(front_path, face_index))
            placements.append((card, path))
            if path not in downloads:
                downloads[path] = (url, card.get("name") or "Card")

        items = list(downloads.items())
        for index, (path, (url, name)) in enumerate(items, 1):
            _check_cancel(cancel_event)
            if progress_cb:
                progress_cb("download", index - 1, len(items), name)
            _download_png(
                url, path, name, http=self.http, cancel_event=cancel_event)
            if progress_cb:
                progress_cb("download", index, len(items), name)

        placements = tuple(placements)
        render_print_template(
            placements, job.output_path, deck_name=job.deck_name,
            progress_cb=progress_cb,
            cancel_cb=lambda: _check_cancel(cancel_event))
        result = PrintResult(
            job.output_path, len(placements), page_count(len(placements)))
        if progress_cb:
            progress_cb(
                "done", result.total_cards, result.total_cards,
                result.output_path)
        return result


class PrintController(GenerationalWorker):
    """Own one print worker and its generation-tagged event queue."""

    def __init__(self, service):
        super().__init__()
        self.service = service

    def start(self, job):
        generation = self._begin()
        if generation is None:
            return PrintStart("busy", self.generation, len(job.cards))

        def progress(stage, current, total, detail):
            if stage != "done":
                self.events.put(PrintEvent(
                    "progress", generation, stage,
                    (current, total, detail)))

        def worker():
            try:
                log.info(
                    "Print template starting: %s cards", len(job.cards))
                result = self.service.create(
                    job, progress_cb=progress,
                    cancel_event=self._cancel_event)
                log.info(
                    "Print template finished: %s", result.output_path)
                event = PrintEvent("done", generation, payload=result)
            except JobCancelled:
                log.info("Print template cancelled during shutdown")
                event = PrintEvent("cancelled", generation)
            except Exception as exc:
                log.exception("Could not create print template")
                event = PrintEvent("error", generation, payload=str(exc))
            self.events.put(event)

        self._spawn(worker, "print-template")
        return PrintStart("started", generation, len(job.cards))

    def poll(self):
        latest_progress = None
        terminal = None
        for event in self._drain():
            if event.kind == "progress":
                latest_progress = event
            else:
                terminal = event
        if terminal is not None:
            self.running = False
        return PrintPoll(latest_progress, terminal)

def create_print_template(deck, output_path, cache_dir, progress_cb=None):
    """Synchronous print-template entry point composing a job and the service."""
    job = PrintJob.from_deck(deck, output_path, cache_dir)
    result = PrintTemplateService().create(job, progress_cb=progress_cb)
    return result.output_path
