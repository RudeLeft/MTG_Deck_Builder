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
import zipfile

# The built portable app ships as ``MTG_Deck_Builder-<tag>-windows.zip`` whose
# single top-level folder is the program directory (the ``.exe`` plus
# ``_internal``). The source zip ends ``-source.zip`` and is not installable, so
# the asset match must be this exact suffix, never a bare ``.zip``.
WINDOWS_ASSET_SUFFIX = "-windows.zip"
PROGRAM_EXE = "MTGDeckBuilder.exe"
PROGRAM_DIRNAME = "MTGDeckBuilder"
DATA_DIRNAME = "data"

_UPDATE_DIRNAME = "_update"
_DOWNLOAD_NAME = "download.zip"
_STAGED_NAME = "staged"
_MARKER_NAME = "pending.json"


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


def marker_path(data_dir):
    """The JSON marker recording that a verified update is staged and ready."""
    return os.path.join(update_dir(data_dir), _MARKER_NAME)


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


def clear_update(data_dir):
    """Remove all staged-update scratch (download, staging, and marker)."""
    shutil.rmtree(update_dir(data_dir), ignore_errors=True)


def build_swap_script(data_dir):
    """Return the text of a Windows ``.bat`` that installs the staged update.

    It runs after the app exits: a short grace wait lets the old process release
    its ``.exe``, ``robocopy /E`` copies the staged program files over the
    install folder, ``/XD`` excludes ``data\\`` so decks/database/images/prefs
    survive, the new ``.exe`` is relaunched, and the script removes the staging
    area and returns robocopy's code. ``/E`` (copy, never purge) is deliberate:
    a failed copy can never delete the working install, so the worst outcome is
    an unchanged, still-runnable app rather than a broken one.
    """
    program = program_dir_for(data_dir)
    data = os.path.abspath(data_dir)
    staged = staged_program_dir(data_dir)
    executable = os.path.join(program, PROGRAM_EXE)
    scratch = update_dir(data_dir)
    lines = [
        "@echo off",
        "setlocal",
        # ~2 seconds of grace for the exiting app to unlock its files; the
        # robocopy retries below absorb any remaining lock.
        "ping 127.0.0.1 -n 3 >nul",
        f'robocopy "{staged}" "{program}" /E /XD "{data}" /R:30 /W:1 >nul',
        # robocopy uses exit codes 0-7 for success; 8 and above is a real error.
        "set RC=%ERRORLEVEL%",
        f'rmdir /s /q "{scratch}" >nul 2>&1',
        f'start "" "{executable}"',
        "endlocal",
        "exit /b %RC%",
    ]
    return "\r\n".join(lines) + "\r\n"
