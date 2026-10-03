"""Bundle one deck as a Proxic Arena import: its decklist plus its card art.

Proxic Arena renders a card from real art only when it already has the file:
its client indexes `client/assets/real_cards/*.png` by card name, so a deck
that arrives as a bare decklist plays through the generated fallback art. This
module packages the two together -- the decklist Arena loads and the PNGs its
client indexes -- as one zip:

    <DeckName>.txt
    real_cards/<Card Name> [SET].png

The names follow Arena's own index rule (`_real_art_path` in its client): the
trailing `[SET]` is stripped and what remains is matched against the card name,
lowercased, with no accent folding. So accents are kept exactly as printed, and
only characters a filesystem refuses are removed. That index is keyed by name
alone, which means two printings of one name would collide in it, so this
bundles ONE image per distinct card name rather than per printing.

The PNGs come from `printing/service.py`, which already downloads, validates,
caches and atomically replaces Scryfall's high-quality PNG per exact printing,
including a separate image per face of a double-faced card. Nothing here
re-downloads what printing has already cached.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import os
import tempfile
import zipfile

from mtgdb.core.atomic_files import (
    TEMP_SUFFIX, sweep_abandoned_writes, temp_prefix,
)
from mtgdb.core.background_jobs import JobCancelled, check_cancel


class ArenaExportCancelled(JobCancelled):
    """Raised when the user cancels an export (BGJ-004: one catchable base)."""


# Where Arena's client looks for real art, and so the folder this zip carries
# so the whole directory can be dropped into `client/assets/`.
REAL_ART_DIRECTORY = "real_cards"

# Characters a Windows or POSIX filename cannot hold. Accents and spaces are
# deliberately NOT touched: Arena matches the name as printed, case-folded
# only, so stripping accents here would make Eowyn's art unfindable.
_ILLEGAL_FILENAME_CHARACTERS = '<>:"/\\|?*'


@dataclass(frozen=True)
class ArenaExportJob:
    """A stable snapshot of one deck's export, safe to hand to a worker."""

    deck_name: str
    deck_text: str
    cards: tuple[dict, ...]
    output_path: str
    cache_dir: str

    @classmethod
    def from_deck(cls, deck, deck_text, output_path, cache_dir):
        return cls(
            deck_name=str(getattr(deck, "name", "Deck") or "Deck"),
            deck_text=str(deck_text),
            cards=tuple(copy.deepcopy(distinct_art_cards(deck))),
            output_path=os.fspath(output_path),
            cache_dir=os.fspath(cache_dir),
        )


@dataclass(frozen=True)
class ArenaExportResult:
    """What one completed export wrote, and what it could not."""

    output_path: str
    image_count: int
    card_count: int
    missing_art: tuple[str, ...]


def front_face_name(name):
    """Return the face Arena indexes, which is the one before any ` // `."""
    return str(name or "").split(" // ", 1)[0].strip()


def art_filename(card):
    """Return `<Card Name> [SET].png` for one card, or None without a name.

    The set code is upper-cased for legibility and plays no part in Arena's
    lookup, which strips it. A card with no stored set still gets a usable
    name, because the suffix is what Arena discards anyway.
    """
    name = front_face_name((card or {}).get("name"))
    if not name:
        return None
    cleaned = "".join(
        character for character in name
        if character not in _ILLEGAL_FILENAME_CHARACTERS).strip()
    # A name of nothing but illegal characters cannot be filed.
    if not cleaned:
        return None
    set_code = str((card or {}).get("set_code") or "").strip().upper()
    return f"{cleaned} [{set_code}].png" if set_code else f"{cleaned}.png"


def distinct_art_cards(deck):
    """Return one card per distinct name, mainboard first, in deck order.

    Arena's art index is keyed by card name, so a second printing of the same
    name could only overwrite the first. The deck's own first printing of a
    name wins, which is the copy the player is holding.
    """
    seen = {}
    for board in ("main", "side"):
        for entry in deck.entries(board):
            card = entry.get("card") or {}
            name = front_face_name(card.get("name")).casefold()
            if name and name not in seen:
                seen[name] = card
    return list(seen.values())


def write_export(job, image_paths, cancel_event=None):
    """Write the zip through one durable replace (PORT-007).

    `image_paths` maps each published `real_cards/...` name to a local PNG. The
    zip is built beside its destination and renamed onto it, so an interrupted
    or cancelled export never leaves a half-written archive where a complete
    one is expected.
    """
    target = os.fspath(job.output_path)
    parent = os.path.dirname(target) or "."
    os.makedirs(parent, exist_ok=True)
    sweep_abandoned_writes(parent, os.path.basename(target))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=temp_prefix(os.path.basename(target)), suffix=TEMP_SUFFIX,
        dir=parent)
    written = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            with zipfile.ZipFile(
                    handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    f"{_safe_member(job.deck_name)}.txt", job.deck_text)
                for member, source in image_paths.items():
                    check_cancel(
                        cancel_event, "Export was cancelled.",
                        ArenaExportCancelled)
                    archive.write(source, member)
                    written += 1
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary_name, target)
    finally:
        # A failed or cancelled export leaves no temporary behind, and never
        # replaces the archive already at the destination.
        if os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
    return written


def _safe_member(name):
    """Return a zip-safe stem for the decklist member."""
    cleaned = "".join(
        character for character in str(name or "Deck")
        if character not in _ILLEGAL_FILENAME_CHARACTERS).strip()
    return cleaned or "Deck"


def build_export(job, ensure_image, progress_cb=None, cancel_event=None):
    """Download each card's art, then write the bundle.

    `ensure_image` is injected rather than imported so this module states what
    it needs -- a validated local PNG for one card -- without owning the
    download, the cache, or the HTTP policy, all of which belong to
    `printing/service.py`.
    """
    total = len(job.cards)
    image_paths = {}
    missing = []
    for index, card in enumerate(job.cards, 1):
        check_cancel(
            cancel_event, "Export was cancelled.", ArenaExportCancelled)
        member_name = art_filename(card)
        if progress_cb:
            progress_cb("download", index - 1, total, member_name or "")
        path = None
        if member_name:
            try:
                path = ensure_image(card, job.cache_dir)
            except Exception:
                path = None
        if path and os.path.exists(path):
            image_paths[f"{REAL_ART_DIRECTORY}/{member_name}"] = path
        else:
            # A card whose art cannot be fetched MUST NOT fail the export: the
            # decklist is still worth having, and Arena falls back to its
            # generated art for exactly this case.
            missing.append(front_face_name(card.get("name")) or "(unnamed)")
        if progress_cb:
            progress_cb("download", index, total, member_name or "")

    if progress_cb:
        progress_cb("package", total, total, os.path.basename(job.output_path))
    written = write_export(job, image_paths, cancel_event=cancel_event)
    return ArenaExportResult(
        output_path=job.output_path, image_count=written, card_count=total,
        missing_art=tuple(missing))
