"""Windows integration test: the update swap replaces files and preserves data.

Runs the actual robocopy commands the swap script composes -- the pre-swap
backup and the staged-over-install copy -- against a temporary install tree on
real Windows robocopy, then verifies that program files are replaced, new files
are added, the user's ``data\\`` survives untouched, and the backup captured the
old program (so rollback has something to restore).

The relaunch / health-gate / rollback branches are process-driven (they start
the exe, wait for its health flag, and taskkill on failure); those are covered
by the script-structure assertions in ``tests/test_self_update.py``. This gate
exercises the one thing only real Windows can prove: that ``robocopy /E /XD``
truly replaces the program while leaving ``data\\`` alone.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core import self_update as su


def _robocopy_lines(script):
    return [line for line in script.splitlines()
            if line.strip().startswith("robocopy")]


def main():
    if sys.platform != "win32":
        print("SKIP: Windows-only integration test")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="mtg-swap-"))
    try:
        program = tmp / su.PROGRAM_DIRNAME
        data = program / su.DATA_DIRNAME
        (program / "_internal").mkdir(parents=True)
        data.mkdir(parents=True)
        # Old program files that the update should replace.  robocopy skips a
        # file whose size and timestamp match the one already there, so a fixture
        # of same-length files written milliseconds apart is skipped about half
        # the time -- which is not what an update looks like.  An installed
        # program is older than the build that replaces it, and a new build is a
        # different size, so the fixture is too.
        (program / su.PROGRAM_EXE).write_text("OLD EXE", encoding="utf-8")
        (program / "_internal" / "lib.txt").write_text("OLD LIB", encoding="utf-8")
        a_day_ago = time.time() - 24 * 3600
        for old_file in (program / su.PROGRAM_EXE, program / "_internal" / "lib.txt"):
            os.utime(old_file, (a_day_ago, a_day_ago))
        # User data that MUST survive the swap untouched.
        (data / "cards.db").write_text("USER DB", encoding="utf-8")
        (data / "decks.json").write_text("USER DECKS", encoding="utf-8")
        # The staged new version.
        staged = Path(su.staged_program_dir(str(data)))
        (staged / "_internal").mkdir(parents=True)
        (staged / su.PROGRAM_EXE).write_text("NEW EXE, REBUILT", encoding="utf-8")
        (staged / "_internal" / "lib.txt").write_text("NEW LIB, REBUILT", encoding="utf-8")
        (staged / "_internal" / "new.txt").write_text("ADDED", encoding="utf-8")

        script = su.build_swap_script(str(data))
        robocopy = _robocopy_lines(script)

        checks = {"script composes a backup then a swap copy": len(robocopy) >= 2}
        # Run the backup copy and the staged-over-install copy exactly as
        # composed (skip the rollback restore, which is the third robocopy).
        for command in robocopy[:2]:
            subprocess.run(command, shell=True, cwd=str(tmp),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        backup = Path(su.backup_dir(str(data)))
        checks["program exe replaced with the new build"] = (
            (program / su.PROGRAM_EXE).read_text(encoding="utf-8") == "NEW EXE, REBUILT")
        checks["an added program file is copied in"] = (
            (program / "_internal" / "new.txt").read_text(encoding="utf-8") == "ADDED")
        checks["user database is preserved"] = (
            (data / "cards.db").read_text(encoding="utf-8") == "USER DB")
        checks["user decks are preserved"] = (
            (data / "decks.json").read_text(encoding="utf-8") == "USER DECKS")
        checks["backup captured the old exe for rollback"] = (
            (backup / su.PROGRAM_EXE).read_text(encoding="utf-8") == "OLD EXE")
        checks["backup excluded the data folder"] = not (backup / su.DATA_DIRNAME).exists()

        ok = True
        for label, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
            ok &= bool(passed)
        print("\nSELF-UPDATE SWAP (WINDOWS):", "ALL PASS" if ok else "FAILURES")
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
