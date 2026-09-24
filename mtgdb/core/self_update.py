"""Stage a downloaded release and build the on-restart swap that installs it.

The in-app updater downloads the built Windows release zip, verifies and unpacks
it beside the app's data, and -- because a running ``.exe`` cannot overwrite
itself on Windows -- hands the actual file swap to a small batch helper that runs
after the app exits. This module owns the pure, testable half of that: choosing
the right release asset, verifying and extracting the download into a staging
area, recording and reading the pending-update marker, and constructing the swap
script. It is Tk-free and imports only the standard library; the UI adapter
performs the network download, spawns the helper, and closes the app.

The swap deliberately preserves ``data\\`` (decks, database, images, prefs): it
copies the new program files over the install folder and excludes that one
subtree, so an update never costs the user a multi-hundred-megabyte re-sync.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import zipfile

# The built portable app ships as ``MTG_Deck_Builder-<tag>-windows.zip`` whose
# single top-level folder is the program directory (the ``.exe`` plus
# ``_internal``). The source zip ends ``-source.zip`` and is not installable, so
# the asset match must be this exact suffix, never a bare ``.zip``.
WINDOWS_ASSET_SUFFIX = "-windows.zip"
# The release also carries a checksums file so a download can be verified even
# when GitHub does not attach an asset ``digest``.
SUMS_ASSET_NAME = "SHA256SUMS.txt"
PROGRAM_EXE = "MTGDeckBuilder.exe"
PROGRAM_DIRNAME = "MTGDeckBuilder"
DATA_DIRNAME = "data"

_UPDATE_DIRNAME = "_update"
_DOWNLOAD_NAME = "download.zip"
_STAGED_NAME = "staged"
_BACKUP_NAME = "backup"
_MARKER_NAME = "pending.json"
_VERIFY_NAME = "verifying.json"
_STARTED_NAME = "started.ok"


def update_dir(data_dir):
    """The scratch folder (under ``data\\``) that holds an in-progress update."""
    return os.path.join(data_dir, _UPDATE_DIRNAME)


def download_path(data_dir):
    """Where the release zip is streamed to disk before verification."""
    return os.path.join(update_dir(data_dir), _DOWNLOAD_NAME)


def staged_dir(data_dir):
    """The folder the verified zip is extracted into."""
    return os.path.join(update_dir(data_dir), _STAGED_NAME)


def staged_program_dir(data_dir):
    """The extracted program folder (``.../staged/MTGDeckBuilder``)."""
    return os.path.join(staged_dir(data_dir), PROGRAM_DIRNAME)


def backup_dir(data_dir):
    """Where the current program files are copied before the swap, for rollback."""
    return os.path.join(update_dir(data_dir), _BACKUP_NAME)


def marker_path(data_dir):
    """The JSON marker recording that a verified update is staged and ready."""
    return os.path.join(update_dir(data_dir), _MARKER_NAME)


def verify_marker_path(data_dir):
    """The marker recording that an update is being applied right now."""
    return os.path.join(update_dir(data_dir), _VERIFY_NAME)


def started_flag_path(data_dir):
    """The flag a freshly swapped build drops to prove it launched (health gate)."""
    return os.path.join(update_dir(data_dir), _STARTED_NAME)


def program_dir_for(data_dir):
    """The install folder the swap replaces: the parent of ``data\\``.

    The portable app stores its data beside the executable, so the program
    directory is always ``data``'s parent regardless of where the app is run.
    """
    return os.path.dirname(os.path.abspath(data_dir))


def select_release_asset(release_json):
    """Return ``(url, size, digest)`` for the built Windows zip, else all ``None``.

    ``digest`` is GitHub's ``sha256:<hex>`` string when the API supplies one,
    which lets the download be integrity-checked even though the app is not
    code-signed. A release that carries only the source zip yields no asset.
    """
    if not isinstance(release_json, dict):
        return (None, None, None)
    for asset in release_json.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = asset.get("browser_download_url")
        if url and name.endswith(WINDOWS_ASSET_SUFFIX):
            return (str(url), asset.get("size"), asset.get("digest"))
    return (None, None, None)


def select_checksums_url(release_json):
    """Return the URL of the release's ``SHA256SUMS.txt`` asset, or ``None``."""
    if not isinstance(release_json, dict):
        return None
    for asset in release_json.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        url = asset.get("browser_download_url")
        if url and str(asset.get("name") or "") == SUMS_ASSET_NAME:
            return str(url)
    return None


def expected_sha256(sums_text, asset_name):
    """Return the hex sha256 recorded for ``asset_name`` in a SHA256SUMS file.

    Accepts the usual ``<hex>  <name>`` lines, tolerating the binary ``*``
    marker some tools prefix to the name. Returns ``None`` when the file or the
    entry is absent or malformed, so the caller falls back to GitHub's digest.
    """
    for line in str(sums_text or "").splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        digest, name = parts[0].lower(), parts[-1].lstrip("*")
        if name == asset_name and len(digest) == 64 and all(
                character in "0123456789abcdef" for character in digest):
            return digest
    return None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_matches(path, expected):
    """True when ``path`` hashes to ``expected`` (``sha256:<hex>`` or ``<hex>``).

    An absent or unrecognized digest returns True: GitHub does not always attach
    one, and the download is HTTPS from GitHub, so a missing digest must not
    block an otherwise valid update. A present, well-formed sha256 that does not
    match returns False so a tampered or truncated file is rejected.
    """
    if not expected:
        return True
    text = str(expected).strip().lower()
    if text.startswith("sha256:"):
        text = text[len("sha256:"):]
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        return True
    return _sha256(path) == text


def verify_zip(path, expected_digest=None):
    """True when the file is an intact zip with the right hash and program exe.

    Rejects a wrong digest, a corrupt archive, or one that does not contain the
    expected ``MTGDeckBuilder/MTGDeckBuilder.exe``, so an unexpected or damaged
    asset can never be staged as if it were a real build.
    """
    if not digest_matches(path, expected_digest):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                return False
            wanted = f"{PROGRAM_DIRNAME}/{PROGRAM_EXE}"
            names = {name.replace("\\", "/") for name in archive.namelist()}
            return wanted in names
    except (zipfile.BadZipFile, OSError):
        return False


def extract_staged(zip_path, data_dir):
    """Replace the staging area with the zip's contents; return the program dir.

    Raises when the archive lacks the expected program executable, so a
    malformed or unexpected asset never leaves a half-staged update behind.
    """
    target = staged_dir(data_dir)
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)
    program = staged_program_dir(data_dir)
    if not os.path.isfile(os.path.join(program, PROGRAM_EXE)):
        raise RuntimeError("staged update is missing the program executable")
    return program


def write_pending(data_dir, version):
    """Record that a verified update for ``version`` is staged and ready."""
    os.makedirs(update_dir(data_dir), exist_ok=True)
    payload = {"version": str(version), "program": PROGRAM_DIRNAME}
    with open(marker_path(data_dir), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def read_pending(data_dir):
    """Return the pending-update marker dict, or ``None`` if absent/unreadable."""
    try:
        with open(marker_path(data_dir), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def pending_version(data_dir):
    """Return the staged version only if its program files are actually present.

    A marker whose staged executable is gone (partial cleanup, manual delete) is
    treated as no pending update, so the UI never offers to restart into nothing.
    """
    marker = read_pending(data_dir)
    if not marker:
        return None
    if not os.path.isfile(os.path.join(staged_program_dir(data_dir), PROGRAM_EXE)):
        return None
    version = marker.get("version")
    return str(version) if version else None


def write_verify(data_dir, version):
    """Record that an update to ``version`` is being applied right now.

    Timestamped so a later launch can tell an apply that may still be in flight
    (its helper running) from an orphaned marker left by a helper that died.
    """
    os.makedirs(update_dir(data_dir), exist_ok=True)
    payload = {"version": str(version), "started_at": time.time()}
    with open(verify_marker_path(data_dir), "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


def read_verify(data_dir):
    """Return the apply-in-progress marker dict, or ``None`` if absent/unreadable."""
    try:
        with open(verify_marker_path(data_dir), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def verify_is_recent(data_dir, max_age_seconds=120):
    """True when an apply marker exists and is new enough that its helper may
    still be running. The whole swap lifecycle is well under a minute, so an
    older marker is an orphan the app may safely clean up rather than a live
    apply it must leave alone.
    """
    marker = read_verify(data_dir)
    if not marker:
        return False
    try:
        started_at = float(marker.get("started_at", 0))
    except (TypeError, ValueError):
        return False
    return 0 <= (time.time() - started_at) < max_age_seconds


def note_started(data_dir):
    """Drop the started flag when this is a freshly swapped build's first launch.

    Called as early as possible in startup. When an apply marker is present the
    running process is the newly swapped build proving it can start, so create
    the flag the swap helper is waiting on; without it the helper rolls back.
    Returns True when this was a post-update launch. On any ordinary launch
    there is no marker and this is a cheap no-op.
    """
    if read_verify(data_dir) is None:
        return False
    try:
        os.makedirs(update_dir(data_dir), exist_ok=True)
        with open(started_flag_path(data_dir), "w", encoding="utf-8") as handle:
            handle.write("ok")
    except OSError:
        pass
    return True


def clear_update(data_dir):
    """Remove all staged-update scratch (download, staging, backup, markers)."""
    shutil.rmtree(update_dir(data_dir), ignore_errors=True)


def build_swap_script(data_dir):
    """Return a Windows ``.bat`` that backs up, swaps, health-checks, rolls back.

    It runs after the app exits. In order: a short grace wait for the old
    process to release its ``.exe``; back up the current program files; copy the
    staged files over the install folder; relaunch; then wait for the new build
    to prove it launched by creating the started flag. On success the whole
    scratch area (backup, staging, markers) is removed. If the new build never
    signals within the timeout -- it failed to launch -- the old files are
    restored from the backup and relaunched, so a broken build can never strand
    the user. Every copy is ``robocopy /E`` (additive, never purges) and
    excludes ``data\\`` with ``/XD``, so decks/database/images/prefs always
    survive and a failed copy can never delete the working install.
    """
    program = program_dir_for(data_dir)
    data = os.path.abspath(data_dir)
    staged = staged_program_dir(data_dir)
    backup = backup_dir(data_dir)
    scratch = update_dir(data_dir)
    started = started_flag_path(data_dir)
    executable = os.path.join(program, PROGRAM_EXE)
    lines = [
        "@echo off",
        "setlocal enableextensions",
        # ~2 seconds of grace for the exiting app to unlock its files; the
        # robocopy retries below absorb any remaining lock.
        "ping 127.0.0.1 -n 3 >nul",
        f'robocopy "{program}" "{backup}" /E /XD "{data}" /R:3 /W:1 >nul',
        f'robocopy "{staged}" "{program}" /E /XD "{data}" /R:30 /W:1 >nul',
        # robocopy uses exit codes 0-7 for success; 8 and above is a real error.
        "if %ERRORLEVEL% GEQ 8 goto rollback",
        f'del /q "{started}" >nul 2>&1',
        f'start "" "{executable}"',
        "set /a N=0",
        ":wait",
        f'if exist "{started}" goto ok',
        "set /a N+=1",
        "if %N% GEQ 30 goto rollback",
        "ping 127.0.0.1 -n 2 >nul",
        "goto wait",
        ":ok",
        f'rmdir /s /q "{scratch}" >nul 2>&1',
        "exit /b 0",
        ":rollback",
        f'taskkill /IM "{PROGRAM_EXE}" /F >nul 2>&1',
        f'robocopy "{backup}" "{program}" /E /XD "{data}" /R:10 /W:1 >nul',
        f'rmdir /s /q "{scratch}" >nul 2>&1',
        f'start "" "{executable}"',
        "exit /b 1",
    ]
    return "\r\n".join(lines) + "\r\n"
