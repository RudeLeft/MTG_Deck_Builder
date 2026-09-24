#!/usr/bin/env python
"""Run the cross-platform test suite in parallel for fast local iteration.

The suite is dozens of independent standalone scripts, each returning 0/1 from
its own ``main()`` and sharing nothing with the others. Running them one after
another (as ``build_windows.bat`` and the CI loop do) wastes wall-clock time on
a multi-core machine. This runner launches them as concurrent subprocesses,
then prints a name-sorted PASS/FAIL summary and the full captured output of any
failure so a red run is still easy to read.

It mirrors the cross-platform loop's scope (``tests/test_*.py`` in name order).
CI keeps its own sequential loop on purpose -- deterministic ordering and
early-exit per-file attribution matter more there than wall-clock time -- so
this runner is a developer convenience and nothing depends on it.

    python tests/run_tests.py                       # every tests/test_*.py
    python tests/run_tests.py -j 4                   # cap concurrent workers
    python tests/run_tests.py test_deck_architecture.py test_search_models.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent


def _discover(names):
    """Resolve explicit file names, or every ``test_*.py`` in name order."""
    if names:
        return [(TESTS_DIR / name).resolve() for name in names]
    return sorted(TESTS_DIR.glob("test_*.py"))


def _run_one(path):
    """Run one test file as a subprocess; return its outcome and duration."""
    start = time.monotonic()
    completed = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    return path, completed.returncode, output, time.monotonic() - start


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the test suite in parallel.")
    parser.add_argument(
        "names", nargs="*", help="specific test_*.py file names to run")
    parser.add_argument(
        "-j", "--jobs", type=int, default=0,
        help="maximum concurrent workers (default: CPU count)")
    args = parser.parse_args(argv)

    paths = _discover(args.names)
    missing = [path for path in paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"  [MISSING] {path.name}")
        return 1
    if not paths:
        print("no tests found")
        return 1

    # Each test is I/O- and CPU-bound in its own process, so oversubscribing a
    # little past the core count keeps every core busy without thrashing.
    workers = args.jobs or (os.cpu_count() or 4)
    workers = max(1, min(workers, len(paths)))

    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_run_one, paths))

    results.sort(key=lambda result: result[0].name)
    failures = [result for result in results if result[1] != 0]

    for path, code, _output, seconds in results:
        mark = "PASS" if code == 0 else "FAIL"
        print(f"  [{mark}] {path.name}  ({seconds:.1f}s)")

    for path, code, output, _seconds in failures:
        print(f"\n===== {path.name} (exit {code}) =====")
        print(output.rstrip())

    elapsed = time.monotonic() - start
    print(f"\n{len(results)} files, {len(failures)} failed, "
          f"{workers} workers, {elapsed:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
