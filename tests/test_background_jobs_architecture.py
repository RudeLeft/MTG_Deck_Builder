"""Background-job infrastructure architecture gates (BGJ-*)."""

import ast
import inspect
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core.background_jobs import (
    GenerationalWorker, JobCancelled, check_cancel, spawn_daemon)
from mtgdb.printing.service import PrintController, PrintCancelled, PrintEvent
from mtgdb.database.sync import DatabaseSyncController, DatabaseSyncCancelled


def _import_roots(source):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def main():
    bg_source = (ROOT / "mtgdb/core/background_jobs.py").read_text(encoding="utf-8")
    bg_imports = _import_roots(bg_source)
    service_source = (ROOT / "mtgdb/images/service.py").read_text(encoding="utf-8")
    print_source = (ROOT / "mtgdb/printing/service.py").read_text(encoding="utf-8")
    sync_source = (ROOT / "mtgdb/database/sync.py").read_text(encoding="utf-8")

    # Behavioral: generation tagging, busy guard, terminal drain, cancel/shutdown.
    class FakeJob:
        cards = list(range(9))

    class DoneService:
        def create(self, job, progress_cb=None, cancel_event=None):
            progress_cb("render", 1, 9, "a")
            progress_cb("done", 9, 9, "z")
            class R:
                total_cards = 9
                output_path = "/tmp/out.pdf"
            return R()

    controller = PrintController(DoneService())
    started = controller.start(FakeJob())
    busy = controller.start(FakeJob())
    controller._thread.join(2)
    poll = controller.poll()
    controller.events.put(PrintEvent("progress", 999, "stale", (0, 0, "")))
    stale_dropped = controller.poll().progress is None

    cancel_event = threading.Event()
    check_cancel(cancel_event)  # unset -> no raise
    cancel_event.set()
    raised = False
    try:
        check_cancel(cancel_event, "x")
    except JobCancelled:
        raised = True

    checks = {
        "core module exposes the shared job primitives": (
            "class JobCancelled" in bg_source
            and "def check_cancel(" in bg_source
            and "def spawn_daemon(" in bg_source
            and "class GenerationalWorker" in bg_source),
        "background_jobs is Tk-free and imports no feature package": (
            "tkinter" not in bg_imports
            and not (bg_imports & {
                "search", "deck", "database", "comparison", "images",
                "printing", "workspace", "preferences"})),
        "print and sync controllers derive from GenerationalWorker": (
            issubclass(PrintController, GenerationalWorker)
            and issubclass(DatabaseSyncController, GenerationalWorker)
            and "class PrintController(GenerationalWorker)" in print_source
            and "class DatabaseSyncController(GenerationalWorker)" in sync_source),
        "subsystem cancellations subclass the shared exception": (
            issubclass(PrintCancelled, JobCancelled)
            and issubclass(DatabaseSyncCancelled, JobCancelled)),
        "image, print, and sync spawn workers via the shared factory": (
            "spawn_daemon(" in service_source
            and "self._spawn(" in print_source
            and "self._spawn(" in sync_source
            and callable(spawn_daemon)),
        "single-worker limit, generations, and terminals are preserved": (
            started.status == "started" and busy.status == "busy"
            and poll.terminal is not None and poll.terminal.kind == "done"
            and controller.running is False and stale_dropped),
        "cooperative cancel check raises the shared exception": (
            raised
            and inspect.signature(
                DatabaseSyncController.shutdown).parameters["timeout"].default
            == 3.0),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nBACKGROUND JOBS ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
