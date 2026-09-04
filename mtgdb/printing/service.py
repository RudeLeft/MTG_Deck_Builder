"""Tk-free print-image preparation, workflow, and worker lifecycle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
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


def _safe_id(card):
    value = card.get("id") or card.get("oracle_id") or card.get("name") or "card"
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value)


def _card_key(card):
    return card.get("id") or card.get("name")


def cached_png_path(card, cache_dir):
    legacy = os.path.join(cache_dir, f"{_safe_id(card)}.png")
    return cache_names.cache_path(card, cache_dir, ".png", legacy)


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


def ensure_png(card, cache_dir, http=net, cancel_event=None):
    """Return a validated high-resolution PNG, replacing corrupt cache data."""
    _check_cancel(cancel_event)
    os.makedirs(cache_dir, exist_ok=True)
    path = cached_png_path(card, cache_dir)

    if os.path.exists(path):
        if _valid_png(path):
            return path
        try:
            os.remove(path)
        except OSError:
            pass

    url = card.get("image_png")
    if not url:
        raise ValueError(
            "No high-resolution PNG is available for "
            f"{card.get('name', 'this card')}")

    temporary_path = path + ".download"
    try:
        _check_cancel(cancel_event)
        http.download(url, temporary_path)
        _check_cancel(cancel_event)
        if not _valid_png(temporary_path):
            raise ValueError(
                f"Downloaded image for {card.get('name', 'this card')} "
                "is invalid")
        os.replace(temporary_path, path)
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
    return path


class PrintTemplateService:
    """Prepare exact-printing PNGs and render one complete proxy PDF."""

    def __init__(self, http=net):
        self.http = http

    def create(self, job, progress_cb=None, cancel_event=None):
        cards = tuple(job.cards)
        if not cards:
            raise ValueError("The deck is empty.")

        unique = {}
        for card in cards:
            unique[_card_key(card)] = card

        local_paths = {}
        unique_cards = tuple(unique.values())
        for index, card in enumerate(unique_cards, 1):
            _check_cancel(cancel_event)
            detail = card.get("name") or "Card"
            if progress_cb:
                progress_cb(
                    "download", index - 1, len(unique_cards), detail)
            local_paths[_card_key(card)] = ensure_png(
                card, job.cache_dir, http=self.http,
                cancel_event=cancel_event)
            if progress_cb:
                progress_cb("download", index, len(unique_cards), detail)

        placements = tuple(
            (card, local_paths[_card_key(card)]) for card in cards)
        render_print_template(
            placements, job.output_path, deck_name=job.deck_name,
            progress_cb=progress_cb,
            cancel_cb=lambda: _check_cancel(cancel_event))
        result = PrintResult(
            job.output_path, len(cards), page_count(len(cards)))
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
