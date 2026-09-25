"""Contract for the in-app self-update: asset selection, staging, and the swap.

Exercises the pure ``core/self_update`` logic against real temp zips, and checks
that ``ui/updates.py`` wires the download/verify/stage/restart flow through it and
keeps the swap frozen-only. No network and no process spawning happen here.
"""

import atexit
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
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
    main_source = (ROOT / "mtgdb/main.py").read_text(encoding="utf-8")

    release = {"assets": [
        {"name": "MTG_Deck_Builder-v1.2.0-source.zip",
         "browser_download_url": "https://example/src.zip", "size": 10},
        {"name": "MTG_Deck_Builder-v1.2.0-windows.zip",
         "browser_download_url": "https://example/win.zip", "size": 20,
         "digest": "sha256:abc"},
        {"name": "SHA256SUMS.txt",
         "browser_download_url": "https://example/SHA256SUMS.txt", "size": 5},
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

    checks["swap script backs up the program before overwriting it"] = (
        su.backup_dir(data_dir) in script
        and script.index(su.backup_dir(data_dir)) < script.index(
            su.staged_program_dir(data_dir)))
    checks["swap script health-gates on the started flag"] = (
        su.started_flag_path(data_dir) in script
        and "if exist" in script and ":wait" in script)
    checks["swap script rolls back by restoring the backup on failure"] = (
        ":rollback" in script and "taskkill" in script
        and su.PROGRAM_EXE in script
        # the rollback copies backup -> program (source precedes dest)
        and script.rfind(su.backup_dir(data_dir))
        < script.rfind(su.program_dir_for(data_dir)))
    # The backup's own exit code is checked BEFORE the swap robocopy runs (and
    # overwrites ERRORLEVEL); a failed backup aborts without touching the
    # install, since there would be nothing safe to roll back to.
    checks["swap script aborts before swapping when the backup itself fails"] = (
        ":backupfail" in script
        and script.index("goto backupfail")
        < script.index(su.staged_program_dir(data_dir)))
    # The backup runs right after the just-exited process releases its files --
    # exactly when a slow save-on-close or an AV/indexer lock is most likely
    # still settling -- so it must not be given a stingier retry budget than
    # the swap copy moments later gets.
    backup_line = next(
        line for line in script.splitlines() if su.backup_dir(data_dir) in line
        and "robocopy" in line)
    swap_line = next(
        line for line in script.splitlines()
        if su.staged_program_dir(data_dir) in line and "robocopy" in line)
    checks["backup copy gets the same retry budget as the swap copy"] = (
        "/R:30" in backup_line and "/R:30" in swap_line)
    # Rollback restores from a known-clean backup snapshot, so it must remove
    # any file the failed new build added that the backup does not have --
    # /MIR (mirror), not the additive /E used for the backup/swap copies. /MIR
    # is unique to the rollback line; the backup/swap lines both use /E and
    # both mention the backup/program paths, so filtering on /MIR itself (not
    # the paths, which appear in more than one line) is what actually isolates
    # the rollback's own robocopy call.
    mir_lines = [line for line in script.splitlines() if "/MIR" in line]
    # program_dir_for(data_dir) is a path-prefix of backup_dir(data_dir) (the
    # backup lives under data\_update\backup), so an unquoted substring search
    # for program_dir_for matches inside backup_dir's own text at the same
    # position -- anchor on the quoted robocopy arguments instead, which
    # exactly bound where each path argument starts and ends.
    quoted_backup = f'"{su.backup_dir(data_dir)}"'
    quoted_program = f'"{su.program_dir_for(data_dir)}"'
    checks["rollback restore mirrors the backup instead of only adding to it"] = (
        len(mir_lines) == 1
        and quoted_backup in mir_lines[0]
        and quoted_program in mir_lines[0]
        # source (backup) precedes dest (program) in the robocopy argument order
        and mir_lines[0].index(quoted_backup) < mir_lines[0].index(quoted_program))
    # The helper runs from %TEMP% and must remove itself as its final action so
    # no stray mtgupdate-*.bat accumulates per update. Every exit path funnels
    # to :done, whose (goto) idiom ends the batch context so the file can be
    # deleted; the delete must come after every scratch rmdir.
    checks["swap script deletes itself as its final action on every path"] = (
        script.rstrip().endswith('& exit %RC%')
        and 'del "%~f0"' in script
        and "(goto) 2>nul" in script
        and script.index('del "%~f0"') > script.rindex("rmdir")
        and script.count("goto done") == 3)

    # checksums (A2): SHA256SUMS asset selection + parsing
    checks["select_checksums_url finds the SHA256SUMS asset"] = (
        su.select_checksums_url(release) == "https://example/SHA256SUMS.txt"
        and su.select_checksums_url({"assets": []}) is None)
    sums = (
        "0" * 64 + "  MTG_Deck_Builder-v1.2.0-source.zip\n"
        + "a" * 64 + " *MTG_Deck_Builder-v1.2.0-windows.zip\n")
    checks["expected_sha256 reads the entry for the right asset"] = (
        su.expected_sha256(sums, "MTG_Deck_Builder-v1.2.0-windows.zip") == "a" * 64
        and su.expected_sha256(sums, "missing.zip") is None
        and su.expected_sha256("", "x") is None)

    # apply-in-progress marker (A1/A4): recency window drives who owns cleanup
    checks["verify_is_recent is False with no marker, True right after writing"] = (
        su.verify_is_recent(data_dir) is False)
    su.write_verify(data_dir, "v1.2.0")
    checks["verify_is_recent True for a fresh marker, False once it is old"] = (
        su.verify_is_recent(data_dir) is True
        and su.verify_is_recent(data_dir, max_age_seconds=0) is False
        and (su.read_verify(data_dir) or {}).get("version") == "v1.2.0")
    # A backward wall-clock jump leaves the marker timestamp in the future
    # (negative elapsed). That must read as recent, never orphaned: the safe
    # failure is to leave a possibly-live apply alone.
    with open(su.verify_marker_path(data_dir), "w", encoding="utf-8") as handle:
        json.dump({"version": "v1.2.0", "started_at": time.time() + 3600}, handle)
    checks["verify_is_recent treats a backward clock jump as recent"] = (
        su.verify_is_recent(data_dir) is True)
    # note_started drops the health flag only on a post-update launch
    checks["note_started signals a healthy launch when an apply is pending"] = (
        su.note_started(data_dir) is True
        and os.path.exists(su.started_flag_path(data_dir)))
    su.clear_update(data_dir)
    checks["note_started is a no-op when no apply is pending"] = (
        su.note_started(data_dir) is False
        and not os.path.exists(su.started_flag_path(data_dir)))

    # A persistent (non-transient) write failure must be reported honestly as
    # False rather than silently claimed as success: the swap helper's health
    # gate only sees the flag file on disk, so a caller that gets True back
    # without a real write on disk has no way to tell a signal was lost.
    su.write_verify(data_dir, "v1.2.0")
    blocked_flag = su.started_flag_path(data_dir)
    os.makedirs(blocked_flag, exist_ok=True)  # a directory where the flag file
    # would go: writing to it as a file raises OSError on every attempt, unlike
    # a transient lock that would clear within the retry window.
    started = time.perf_counter()
    result = su.note_started(data_dir)
    elapsed = time.perf_counter() - started
    checks["note_started reports False (not True) when the flag write always fails"] = (
        result is False)
    checks["note_started retries a failing write before giving up"] = (
        elapsed >= 0.5)  # 4 retries * 0.2s between attempts
    os.rmdir(blocked_flag)
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
    checks["updates.py verifies against SHA256SUMS and marks the apply in-flight"] = all(
        token in updates_source for token in (
            "self_update.select_checksums_url(",
            "self_update.expected_sha256(",
            "fetch_bytes(",
            "self_update.write_verify(",
            "self_update.verify_is_recent(",
            "self_update.clear_update(",
        ))
    # write_verify must be written only once the helper script/temp file exist
    # and launch is imminent -- not before build_swap_script/mkstemp, which can
    # still fail -- and cleared if launching the helper itself then fails, so a
    # dead marker can never make a later launch believe an apply is in flight.
    checks["write_verify runs only right before launching the helper, and is undone on failure"] = (
        updates_source.index("build_swap_script(")
        < updates_source.index("tempfile.mkstemp(")
        < updates_source.index("self_update.write_verify(")
        < updates_source.index("subprocess.Popen(")
        and "self_update.clear_verify(" in updates_source)
    # write_pending must record the tag of the release actually downloaded and
    # verified (re-fetched fresh in _download_and_stage), not the stale tag
    # captured by the earlier background check -- a newer release publishing
    # between the check and the click must not mislabel what got installed.
    checks["write_pending records the freshly fetched tag, not a stale one"] = (
        'fresh_tag = data.get("tag_name")' in updates_source
        and "self._update_tag = str(fresh_tag)" in updates_source
        and updates_source.index("self._update_tag = str(fresh_tag)")
        < updates_source.index("self_update.write_pending("))
    checks["updates.py self-update is frozen-only and detaches the helper"] = all(
        token in updates_source for token in (
            'getattr(sys, "frozen", False)',
            "subprocess.Popen(",
            "creationflags=_DETACHED_FLAGS",
            "self._on_app_close()",
        ))
    # note_started must run only after DeckBuilderApp(...) is built successfully,
    # not before CardDB(...)/DeckBuilderApp(...) are even constructed -- signalling
    # early reported a healthy launch even when construction then raised moments
    # later, so a build that never actually started looked "installed" to the
    # swap helper's health gate.
    checks["main.py signals a healthy launch only after the app window is built"] = (
        "from mtgdb.core.self_update import note_started" in main_source
        and "note_started(str(data_dir))" in main_source
        and main_source.index("DeckBuilderApp(db,")
        < main_source.index("note_started(str(data_dir))")
        < main_source.index("app.mainloop()"))

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nSELF UPDATE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
