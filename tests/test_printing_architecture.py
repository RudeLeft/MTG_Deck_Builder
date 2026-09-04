"""Printing renderer, service, controller, UI, and façade contracts."""

import ast
from pathlib import Path
import sys
import tempfile
import threading
import time

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.model import Deck
from mtgdb.printing.renderer import CARDS_PER_PAGE, page_count
from mtgdb.printing.service import (
    PrintCancelled, PrintController, PrintJob, PrintResult,
    PrintTemplateService, cached_png_path,
)
import mtgdb.printing.service as print_template



class FakeHttp:
    def __init__(self):
        self.downloads = []

    def download(self, url, destination, progress_cb=None):
        self.downloads.append(url)
        color = (180, 40, 40) if url.endswith("a") else (40, 40, 180)
        Image.new("RGB", (250, 350), color).save(destination, format="PNG")
        return destination


class BlockingPrintService:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def create(self, job, progress_cb=None, cancel_event=None):
        if progress_cb:
            progress_cb("download", 1, 2, "Card")
        self.entered.set()
        while not self.release.wait(0.005):
            if cancel_event is not None and cancel_event.is_set():
                raise PrintCancelled("cancelled")
        return PrintResult(job.output_path, len(job.cards), page_count(len(job.cards)))


def _methods(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


def _bases(source, class_name):
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {base.id for base in cls.bases if isinstance(base, ast.Name)}


def _wait_for_terminal(controller, timeout=3.0):
    deadline = time.monotonic() + timeout
    progress = None
    while time.monotonic() < deadline:
        poll = controller.poll()
        progress = poll.progress or progress
        if poll.terminal is not None:
            return progress, poll.terminal
        time.sleep(0.005)
    return progress, None


def main():
    card_a = {
        "id": "printing-a", "oracle_id": "oracle-shared",
        "name": "Shared Name", "set_code": "tst",
        "collector_number": "1", "image_png": "https://image/a",
    }
    card_b = {
        "id": "printing-b", "oracle_id": "oracle-shared",
        "name": "Shared Name", "set_code": "tst",
        "collector_number": "2", "image_png": "https://image/b",
    }
    deck = Deck("Snapshot Deck")
    deck.add(card_a, "main", 8)
    deck.add(card_b, "side", 2)

    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        output = temporary / "deck.pdf"
        cache_dir = temporary / "print_png"
        job = PrintJob.from_deck(deck, output, cache_dir)
        snapshot_ids = [card["id"] for card in job.cards]
        deck.clear()

        corrupt_path = Path(cached_png_path(card_a, cache_dir))
        corrupt_path.write_bytes(b"not a png")
        http = FakeHttp()
        stages = []
        result = PrintTemplateService(http=http).create(
            job,
            progress_cb=lambda stage, current, total, detail: stages.append(
                (stage, current, total, detail)))
        pdf_bytes = output.read_bytes()
        cache_a = Path(cached_png_path(card_a, cache_dir))
        cache_b = Path(cached_png_path(card_b, cache_dir))

        compatibility_output = temporary / "compatibility.pdf"
        compatibility_deck = Deck("Compatibility Deck")
        compatibility_deck.add(card_a, "main", 1)
        compatibility_result = print_template.create_print_template(
            compatibility_deck, compatibility_output, cache_dir)

        functional = {
            "job snapshot preserves exact printing quantities": (
                len(job.cards) == 10
                and snapshot_ids.count("printing-a") == 8
                and snapshot_ids.count("printing-b") == 2),
            "renderer preserves nine-card US Letter pagination": (
                CARDS_PER_PAGE == 9 and page_count(0) == 0
                and page_count(9) == 1 and result.pages == 2),
            "service downloads each exact printing once": (
                http.downloads == ["https://image/a", "https://image/b"]),
            "corrupt cache is replaced and printing paths stay distinct": (
                cache_a != cache_b and cache_a.is_file() and cache_b.is_file()
                and cache_a.read_bytes().startswith(b"\x89PNG")
                and cache_b.read_bytes().startswith(b"\x89PNG")),
            "PDF output is complete and atomically replaces its part file": (
                result.output_path == str(output)
                and pdf_bytes.startswith(b"%PDF-")
                and not Path(str(output) + ".part").exists()),
            "download layout and completion progress remain available": (
                {stage for stage, *_rest in stages}
                == {"download", "layout", "done"}
                and sum(1 for stage, *_rest in stages if stage == "layout") == 10),
            "legacy print_template entry point delegates successfully": (
                compatibility_result == str(compatibility_output)
                and compatibility_output.read_bytes().startswith(b"%PDF-")),
        }

    blocking_service = BlockingPrintService()
    controller = PrintController(blocking_service)
    blocking_job = PrintJob("Deck", (card_a,), "output.pdf", "cache")
    started = controller.start(blocking_job)
    blocking_service.entered.wait(1.0)
    busy = controller.start(blocking_job)
    initial_poll = controller.poll()
    blocking_service.release.set()
    progress, terminal = _wait_for_terminal(controller)

    cancelling_service = BlockingPrintService()
    cancelling_controller = PrintController(cancelling_service)
    cancelling_controller.start(blocking_job)
    cancelling_service.entered.wait(1.0)
    cancelled_stopped = cancelling_controller.shutdown(timeout=1.0)
    _cancel_progress, cancelled_terminal = _wait_for_terminal(
        cancelling_controller)

    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/printing/service.py", "mtgdb/printing/renderer.py",
            "mtgdb/printing/service.py", "mtgdb/ui/printing.py",
        )
    }
    ui_methods = _methods(sources["mtgdb/ui/printing.py"], "PrintingMixin")
    gui_methods = _methods(sources["mtgdb/ui/app.py"], "DeckBuilderApp")
    print_methods = {
        "_initialize_printing", "_show_print_popup", "_close_print_popup",
        "_create_print_template", "_start_print_event_pump",
        "_poll_print_events", "_update_print_progress", "_print_done",
        "_print_failed", "_shutdown_printing",
    }

    checks = {
        **functional,
        "controller allows one worker and coalesces typed progress": (
            started.status == "started" and busy.status == "busy"
            and (initial_poll.progress is not None or progress is not None)
            and terminal is not None and terminal.kind == "done"),
        "controller cooperatively cancels during shutdown": (
            cancelled_stopped and cancelled_terminal is not None
            and cancelled_terminal.kind == "cancelled"),
        "renderer exclusively owns physical PDF construction": (
            "def render_print_template(" in sources["mtgdb/printing/renderer.py"]
            and "canvas.Canvas(" in sources["mtgdb/printing/renderer.py"]
            and all(
                marker not in sources["mtgdb/printing/renderer.py"]
                for marker in (
                    "import mtgdb.core.net", "import mtgdb.core.cache_names", "threading",
                    "tkinter", "def expanded_cards("))),
        "service owns snapshots image preparation and worker lifecycle": (
            all(
                marker in sources["mtgdb/printing/service.py"]
                for marker in (
                    "class PrintJob", "class PrintTemplateService",
                    "class PrintController(GenerationalWorker)", "def ensure_png(",
                    "self._spawn(",
                    "from mtgdb.core.background_jobs import"))
            and "tkinter" not in sources["mtgdb/printing/service.py"]
            and "reportlab" not in sources["mtgdb/printing/service.py"]),
        "print UI owns every extracted Tk workflow method": (
            print_methods <= ui_methods and not (print_methods & gui_methods)
            and "PrintingMixin" in _bases(sources["mtgdb/ui/app.py"], "DeckBuilderApp")),
        "print UI owns no network cache or worker implementation": all(
            marker not in sources["mtgdb/ui/printing.py"]
            for marker in (
                "import mtgdb.core.net", "import mtgdb.core.cache_names", "threading.Thread",
                "queue.Queue", "reportlab")),
        "print progress popup uses a DPI-safe explicit minimum width": (
            "preferred_width=700" in sources["mtgdb/ui/printing.py"]
            and "min_width=640" in sources["mtgdb/ui/printing.py"]
            and "lock_size=True" in sources["mtgdb/ui/printing.py"]),
        "gui only composes print services and retains the menu command": (
            "PrintController(PrintTemplateService())" in sources["mtgdb/ui/app.py"]
            and 'command=self._create_print_template' in sources["mtgdb/ui/app.py"]
            and "self._shutdown_printing()" in sources["mtgdb/ui/app.py"]
            and all(
                marker not in sources["mtgdb/ui/app.py"]
                for marker in (
                    "import mtgdb.printing.service", "threading.Thread",
                    "_print_event_queue", "def _create_print_template("))),
        "print entry point create_print_template lives in the service": (
            "def create_print_template(" in sources["mtgdb/printing/service.py"]
            and "PrintTemplateService().create(" in sources["mtgdb/printing/service.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nPRINTING ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
