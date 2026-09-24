"""Contract for the in-app self-update: asset selection, staging, and the swap.

Exercises the pure ``core/self_update`` logic against real temp zips, and checks
that ``ui/updates.py`` wires the download/verify/stage/restart flow through it and
keeps the swap frozen-only. No network and no process spawning happen here.
"""

import atexit
import hashlib
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core import self_update as su


def _make_build_zip(path):
    """Write a minimal but structurally valid built-app zip."""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{su.PROGRAM_DIRNAME}/{su.PROGRAM_EXE}", b"MZ fake exe")
        archive.writestr(f"{su.PROGRAM_DIRNAME}/_internal/base_library.zip", b"stub")


def main():
    updates_source = (ROOT / "mtgdb/ui/updates.py").read_text(encoding="utf-8")

    release = {"assets": [
        {"name": "MTG_Deck_Builder-v1.2.0-source.zip",
         "browser_download_url": "https://example/src.zip", "size": 10},
        {"name": "MTG_Deck_Builder-v1.2.0-windows.zip",
         "browser_download_url": "https://example/win.zip", "size": 20,
         "digest": "sha256:abc"},
    ]}

    tmp = Path(tempfile.mkdtemp(prefix="mtg-selfupdate-"))
    atexit.register(shutil.rmtree, tmp, True)
    data_dir = str(tmp / "data")
    os.makedirs(data_dir)

    good = tmp / "good.zip"
    _make_build_zip(good)
    good_digest = "sha256:" + hashlib.sha256(good.read_bytes()).hexdigest()

    noexe = tmp / "noexe.zip"
    with zipfile.ZipFile(noexe, "w") as archive:
        archive.writestr(f"{su.PROGRAM_DIRNAME}/readme.txt", b"hi")

    corrupt = tmp / "corrupt.zip"
    corrupt.write_bytes(b"this is not a zip file")

    checks = {}

    url, size, digest = su.select_release_asset(release)
    checks["select_release_asset picks the windows zip, not the source zip"] = (
        url == "https://example/win.zip" and size == 20 and digest == "sha256:abc")
    checks["select_release_asset returns Nones when no windows asset exists"] = (
        su.select_release_asset({"assets": [
            {"name": "x-source.zip", "browser_download_url": "https://x"}]})
        == (None, None, None)
        and su.select_release_asset({}) == (None, None, None)
        and su.select_release_asset(None) == (None, None, None))

    checks["digest_matches accepts the right sha256 with or without prefix"] = (
        su.digest_matches(good, good_digest)
        and su.digest_matches(good, good_digest.split(":", 1)[1]))
    checks["digest_matches rejects a wrong sha256"] = (
        not su.digest_matches(good, "sha256:" + "0" * 64))
    checks["digest_matches is lenient when no or garbled digest is supplied"] = (
        su.digest_matches(good, None) and su.digest_matches(good, "")
        and su.digest_matches(good, "not-a-digest"))

    checks["verify_zip accepts an intact build zip (matching digest or none)"] = (
        su.verify_zip(good, good_digest) and su.verify_zip(good, None))
    checks["verify_zip rejects a wrong digest"] = (
        not su.verify_zip(good, "sha256:" + "1" * 64))
    checks["verify_zip rejects a zip missing the program exe"] = (
        not su.verify_zip(noexe))
    checks["verify_zip rejects a corrupt file"] = (not su.verify_zip(corrupt))

    program = su.extract_staged(good, data_dir)
    checks["extract_staged returns the staged program dir holding the exe"] = (
        os.path.isfile(os.path.join(program, su.PROGRAM_EXE))
        and program == su.staged_program_dir(data_dir))

    checks["pending_version is None before any marker is written"] = (
        su.pending_version(data_dir) is None)
    su.write_pending(data_dir, "v1.2.0")
    checks["pending_version reports the staged version"] = (
        su.pending_version(data_dir) == "v1.2.0")
    os.remove(os.path.join(program, su.PROGRAM_EXE))
    checks["pending_version is None when the staged exe is gone"] = (
        su.pending_version(data_dir) is None)

    raised = False
    try:
        su.extract_staged(noexe, data_dir)
    except RuntimeError:
        raised = True
    checks["extract_staged raises on an archive missing the exe"] = raised

    script = su.build_swap_script(data_dir)
    program_dir = su.program_dir_for(data_dir)
    checks["program_dir_for is the parent of the data folder"] = (
        program_dir == os.path.dirname(os.path.abspath(data_dir)))
    checks["swap script copies staged over the program dir, additively"] = (
        "robocopy" in script and "/E" in script
        and su.staged_program_dir(data_dir) in script
        and program_dir in script)
    checks["swap script excludes the data folder from the swap"] = (
        '/XD "' + os.path.abspath(data_dir) + '"' in script)
    checks["swap script relaunches the new exe"] = (
        os.path.join(program_dir, su.PROGRAM_EXE) in script)

    su.clear_update(data_dir)
    checks["clear_update removes the staging scratch"] = (
        not os.path.exists(su.update_dir(data_dir)))

    checks["updates.py drives download/verify/stage/restart through self_update"] = all(
        token in updates_source for token in (
            "from mtgdb.core import self_update",
            "self_update.select_release_asset(",
            "self_update.verify_zip(",
            "self_update.extract_staged(",
            "self_update.write_pending(",
            "self_update.pending_version(",
            "self_update.build_swap_script(",
            "download(url, destination",
        ))
    checks["updates.py self-update is frozen-only and detaches the helper"] = all(
        token in updates_source for token in (
            'getattr(sys, "frozen", False)',
            "subprocess.Popen(",
            "creationflags=_DETACHED_FLAGS",
            "self._on_app_close()",
        ))

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nSELF UPDATE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
