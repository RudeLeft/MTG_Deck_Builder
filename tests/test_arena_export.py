"""Proxic Arena export contract: art naming, bundle contents, durability (ARN-*)."""

import ast
import json
import pathlib
import re
import sys
import tempfile
import threading
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.arena.export import (
    REAL_ART_DIRECTORY, ArenaExportCancelled, ArenaExportJob, art_filename,
    build_export, distinct_art_cards, front_face_name,
)
from mtgdb.core.background_jobs import JobCancelled
from mtgdb.deck.io import deck_to_text
from mtgdb.deck.model import Deck

# A minimal valid-looking PNG header. These tests never reach the network:
# the downloader is injected (ARN-003), so the stub stands in for it.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _card(identifier, name, set_code="tst", png="https://example/art.png"):
    return {
        "id": identifier, "name": name, "set_code": set_code,
        "collector_number": "1", "image_png": png,
    }


def _stub_downloader(calls=None):
    """Stand in for printing.service.ensure_png without a network."""
    def ensure(card, cache_dir):
        if calls is not None:
            calls.append(card.get("id"))
        if not card.get("image_png"):
            return None
        path = pathlib.Path(cache_dir) / f"{card['id']}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PNG_BYTES)
        return str(path)
    return ensure


def _arena_index_key(filename):
    """Arena's own rule, from `_real_art_path` in its client.

    Written out here rather than imported: Proxic Arena is a separate project
    this repository cannot reach, so the rule the names must satisfy is
    restated as an independent expectation (VER-010).
    """
    stem = pathlib.Path(filename).stem
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", stem).strip().lower()


def _art_naming_check():
    """ARN-002: every name resolves back to the card Arena looks up."""
    cards = [
        _card("a", "Lightning Bolt", "lea"),
        _card("b", "Delver of Secrets // Insectile Aberration", "isd"),
        _card("c", "Éowyn, Lady of Rohan", "ltr"),
        _card("d", "Ach! Hans, Run!", "unh"),
        # A name carrying characters no filesystem accepts.
        _card("e", 'Ko:tar? "Quote"/Slash', "tst"),
    ]
    names = {card["id"]: art_filename(card) for card in cards}
    return (
        names["a"] == "Lightning Bolt [LEA].png"
        # Filed under the front face alone.
        and names["b"] == "Delver of Secrets [ISD].png"
        # Accents survive: Arena case-folds but does not strip them.
        and names["c"] == "Éowyn, Lady of Rohan [LTR].png"
        and names["d"] == "Ach! Hans, Run! [UNH].png"
        and not any(
            character in names["e"] for character in '<>:"/\\|?*'[:-1])
        # Every one of them is found by Arena's own index rule.
        and all(
            _arena_index_key(names[card["id"]])
            == front_face_name(card["name"]).lower()
            for card in cards[:4])
        # A card with no usable name cannot be filed.
        and art_filename({"name": ""}) is None
        and art_filename({"name": "///"}) is None
        and art_filename(None) is None
        # No set code still yields a name Arena can match.
        and art_filename({"name": "Nameless Set"}) == "Nameless Set.png")


def _one_image_per_name_check():
    """ARN-002: Arena's name-keyed index gets exactly one image per name."""
    deck = Deck("Collision", "commander")
    first = _card("bolt-lea", "Lightning Bolt", "lea")
    second = _card("bolt-m10", "Lightning Bolt", "m10")
    deck.add(first, "main", 1)
    deck.add(second, "main", 1)
    deck.add(_card("side", "Subgoyf", "mb2"), "side", 2)
    # Same name again in the other board.
    deck.add(_card("bolt-side", "Lightning Bolt", "4ed"), "side", 1)
    chosen = distinct_art_cards(deck)
    names = [front_face_name(card["name"]) for card in chosen]
    return (
        names == ["Lightning Bolt", "Subgoyf"]
        # The deck's own first printing of the name wins.
        and chosen[0]["id"] == "bolt-lea"
        and len(chosen) == 2)


def _bundle_layout_check():
    """ARN-001/ARN-004: contents, tolerated missing art, nothing left behind."""
    deck = Deck("Layout Probe", "commander")
    deck.add(_card("a", "Lightning Bolt", "lea"), "main", 4)
    deck.add(_card("c", "Éowyn, Lady of Rohan", "ltr"), "main", 1)
    # No upstream art for this one.
    deck.add(_card("x", "Subgoyf", "mb2", png=None), "side", 1)

    with tempfile.TemporaryDirectory() as directory:
        base = pathlib.Path(directory)
        output = base / "Layout Probe.zip"
        job = ArenaExportJob.from_deck(
            deck, deck_to_text(deck), output, base / "cache")
        events = []
        result = build_export(
            job, _stub_downloader(),
            progress_cb=lambda *args: events.append(args))
        with zipfile.ZipFile(output) as archive:
            members = sorted(archive.namelist())
            decklist = archive.read("Layout Probe.txt").decode("utf-8")
        leftovers = [
            item.name for item in base.iterdir()
            if item.is_file() and item.suffix != ".zip"]
        return (
            output.is_file()
            # Decklist at the root, art under real_cards/.
            and members == [
                "Layout Probe.txt",
                f"{REAL_ART_DIRECTORY}/Lightning Bolt [LEA].png",
                f"{REAL_ART_DIRECTORY}/Éowyn, Lady of Rohan [LTR].png",
            ]
            and decklist.startswith("// Layout Probe (commander)")
            and "Lightning Bolt" in decklist
            # The art-less card is reported, and did not fail the bundle.
            and result.missing_art == ("Subgoyf",)
            and result.image_count == 2
            and result.card_count == 3
            # Progress is reported per card, plus the packaging step.
            and events
            and events[-1][0] == "package"
            and leftovers == [])


def _durability_check():
    """ARN-004: a cancelled export leaves the destination and folder alone."""
    deck = Deck("Cancelled", "commander")
    for index in range(4):
        deck.add(_card(f"c{index}", f"Card {index}"), "main", 1)

    with tempfile.TemporaryDirectory() as directory:
        base = pathlib.Path(directory)
        output = base / "Cancelled.zip"
        output.write_bytes(b"an archive that was already here")
        original = output.read_bytes()
        job = ArenaExportJob.from_deck(
            deck, deck_to_text(deck), output, base / "cache")
        cancel = threading.Event()
        cancel.set()
        raised = None
        try:
            build_export(job, _stub_downloader(), cancel_event=cancel)
        except JobCancelled as exc:
            raised = exc
        leftovers = [
            item.name for item in base.iterdir()
            if item.is_file() and item.suffix != ".zip"]
        return (
            # Cancellation is catchable as the shared base (BGJ-004).
            isinstance(raised, ArenaExportCancelled)
            and isinstance(raised, JobCancelled)
            # The archive already at the destination is untouched.
            and output.read_bytes() == original
            and leftovers == [])


def _injected_downloader_check():
    """ARN-003: the export package reaches neither printing nor the network."""
    source = (ROOT / "mtgdb/arena/export.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    calls = []
    deck = Deck("Injected", "commander")
    deck.add(_card("a", "Lightning Bolt"), "main", 1)
    with tempfile.TemporaryDirectory() as directory:
        base = pathlib.Path(directory)
        job = ArenaExportJob.from_deck(
            deck, deck_to_text(deck), base / "out.zip", base / "cache")
        build_export(job, _stub_downloader(calls))
    return (
        # Naming and packaging only: no printing, no net, no Tk, no sqlite.
        not any(
            name.startswith(("mtgdb.printing", "mtgdb.ui", "tkinter", "sqlite3"))
            or name == "mtgdb.core.net"
            for name in imported)
        and {"mtgdb.core.atomic_files", "mtgdb.core.background_jobs"} <= imported
        # The downloader it was handed is the one it used.
        and calls == ["a"])


def _export_ui_check():
    """ARN-005/ARN-006: off Tk, cancellable, one app-owned dark report."""
    source = (ROOT / "mtgdb/ui/arena_export.py").read_text(encoding="utf-8")
    app_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
    deck_files = (ROOT / "mtgdb/ui/deck_files.py").read_text(encoding="utf-8")
    report = source.split("def _report_arena_export")[1].split("\n    def ")[0]
    return (
        # Runs on a deck-file worker, not on Tk.
        'submit_deck_file_job(run_export, name="mtg-arena-export")' in source
        and "ensure_png" in source
        # Progress crosses threads through a queue.
        and "queue.Queue()" in source
        and "progress.put(" in source
        and "progress.get_nowait()" in source
        # Cancel exists, and closing the popup means the same thing.
        and "threading.Event()" in source
        and 'text="Cancel"' in source
        and 'popup.protocol("WM_DELETE_WINDOW", cancel_event.set)' in source
        # The popup comes down however the job ends.
        and "on_error=self._close_arena_export_progress" in source
        and "def _close_arena_export_progress" in source
        and "after_cancel" in source
        # One app-owned dark dialog, never a native one. The prohibition is on
        # calls (`messagebox.`) rather than the word, which a docstring names
        # to say what this dialog is not.
        and "messagebox." not in source
        and "from tkinter import filedialog, ttk" in source
        and 'style="DialogTitle.TLabel"' in source
        and "PALETTE" in source
        and "_show_arena_list_dialog(" in report
        # Composed onto the app, and Save Deck As says nothing about Arena.
        and "ArenaExportMixin" in app_source
        and 'label="Proxic Arena Export..."' in app_source
        and "_export_arena" not in deck_files
        and "arena_support" not in deck_files
        and "save_deck_text, path, detached" in deck_files)


def _arena_warning_sentences_check():
    """DECK-012: the notice's sentences, composed apart from its widgets.

    Checked here without a Tk root. The lead carries no count, so it reads the
    same however many cards are listed and needs no subject/verb agreement --
    the defect an earlier count-carrying lead could produce was "1 card ...
    are not", which lives in the sentence rather than in any value a
    structural check can see.
    """
    from mtgdb.ui.arena_export import (
        ARENA_WARNING_NAME_LIMIT, arena_warning_text,
    )

    expected_lead = (
        "The following cards in your deck are not in the Proxic Arena card "
        "pool and will not play properly")
    one_lead, one_shown, one_rest, one_closing = arena_warning_text(["Subgoyf"])
    many = ["Subgoyf", "Cecily, Haunted Mage", "Sly Spy"]
    many_lead, many_shown, many_rest, _closing = arena_warning_text(many)
    overflowing = [
        f"Card {index}" for index in range(ARENA_WARNING_NAME_LIMIT + 5)]
    over_lead, over_shown, over_rest, _over_closing = arena_warning_text(
        overflowing)
    return (
        one_lead == expected_lead
        and one_shown == ["Subgoyf"]
        and one_rest == 0
        and one_closing == "The rest of the deck will play without issue."
        # One sentence at every count, so no agreement can drift.
        and many_lead == expected_lead
        and over_lead == expected_lead
        and many_shown == many
        and many_rest == 0
        # The heading says the deck was saved; the lead does not repeat it.
        and not one_lead.startswith("Deck Saved")
        # The listing is capped and the remainder counted, not dropped.
        and len(over_shown) == ARENA_WARNING_NAME_LIMIT
        and over_rest == 5)


def main():
    checks = {
        "art filenames resolve through Arena's own index rule":
            _art_naming_check(),
        "one image per distinct card name reaches the bundle":
            _one_image_per_name_check(),
        "the bundle holds the decklist beside real_cards art":
            _bundle_layout_check(),
        "a cancelled export preserves the destination and leaves no temporary":
            _durability_check(),
        "the export package owns naming only and is handed its downloader":
            _injected_downloader_check(),
        "the export runs off Tk, can be cancelled, and reports once":
            _export_ui_check(),
        "the notice reads the same at every count":
            _arena_warning_sentences_check(),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nARENA EXPORT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
