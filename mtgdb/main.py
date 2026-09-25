r"""
MTG Deck Builder — entry point.

Run from source:   python -m mtgdb
Or build a Windows .exe with build_windows.bat / MTGDeckBuilder.spec.

Card data and images are provided by Scryfall (https://scryfall.com). This app
caches Scryfall's bulk data locally and searches it offline, per Scryfall's
guidelines. It is not affiliated with Scryfall or Wizards of the Coast.

Where data is stored
--------------------
MTG Deck Builder is intentionally portable. Runtime files are stored only
inside the program folder:

    MTGDeckBuilder\MTGDeckBuilder.exe
    MTGDeckBuilder\data\cards.db
    MTGDeckBuilder\data\card_images\
    MTGDeckBuilder\data\card_images\print_png\
    MTGDeckBuilder\data\ui_preferences.json
    MTGDeckBuilder\data\mtg_deckbuilder.log

The program does not fall back to AppData, the user profile, or Windows Temp.
If its own folder is not writable, startup fails instead of relocating data.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

from mtgdb.core.self_update import note_started
from mtgdb.core.version import app_version
from mtgdb.database.db import CardDB
from mtgdb.ui.app import DeckBuilderApp

LOG_NAME = "mtg"
LOG_FILE = "mtg_deckbuilder.log"

# Keep the Windows mutex handle alive for the lifetime of the process.
_SINGLE_INSTANCE_HANDLE = None
_SINGLE_INSTANCE_NAME = r"Local\MTGDeckBuilder_SingleInstance_v1"


def acquire_single_instance():
    """Allow only one MTG Deck Builder process per Windows login session.

    A named Windows mutex is kernel-managed, needs no cleanup file, survives
    crashes safely, and avoids races where two processes start simultaneously.
    Non-Windows source/test runs intentionally skip this Windows-only guard.

    Detection only: this returns False for a second instance and never shows
    UI. The user-facing notice lives in ``notify_already_running`` and is
    raised by ``main``. Keeping them apart matters because a modal dialog
    blocks until someone dismisses it, which no automated, headless, or
    packaged-smoke caller can do -- such a caller would hang here forever
    instead of observing the refusal.
    """
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (
        wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    ERROR_ALREADY_EXISTS = 183
    already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    if already_running:
        kernel32.CloseHandle(handle)
        return False

    _SINGLE_INSTANCE_HANDLE = handle
    return True


def notify_already_running():
    """Tell the user why a second launch exited immediately.

    Called only from the interactive startup path. This blocks until the
    dialog is dismissed, which is correct for a person double-clicking the
    application and is exactly why it must stay out of the detection above.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MessageBoxW(
            None,
            "MTG Deck Builder is already running.\n\n"
            "Only one instance can be open at a time.",
            "MTG Deck Builder",
            0x00000040,  # MB_ICONINFORMATION
        )
    except Exception:
        pass


def release_single_instance():
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform == "win32" and _SINGLE_INSTANCE_HANDLE:
        try:
            import ctypes
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                _SINGLE_INSTANCE_HANDLE)
        except Exception:
            pass
        _SINGLE_INSTANCE_HANDLE = None


def _program_dir():
    """The folder the program lives in (the .exe when frozen, else this file)."""
    if getattr(sys, "frozen", False):          # running as a PyInstaller .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent      # running from source


def resolve_data_dir():
    """Return the local data folder beside the executable/source tree.

    This app is intentionally portable: there is no AppData/home-directory
    fallback and no environment-variable override. If this folder cannot be
    written, fail clearly rather than silently writing somewhere else.
    """
    base = _program_dir() / "data"
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise RuntimeError(
            "MTG Deck Builder must be run from a writable folder. "
            f"Could not write to: {base}"
        ) from exc
    return base

def setup_logging(data_dir):
    """Configure the 'mtg' logger to write to a rotating file (and console)."""
    log_path = data_dir / LOG_FILE
    logger = logging.getLogger(LOG_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    # Include the source module so every line says where it came from, which is
    # what makes the log useful for tracing a report back to code.
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s %(module)-14s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    # Keep the log bounded: ~1 MB per file, 2 old copies retained.
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Echo to console when there is one (i.e. running from source, not the
    # windowed .exe where sys.stderr is None).
    if sys.stderr is not None:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return log_path


def main():
    if not acquire_single_instance():
        notify_already_running()
        return

    data_dir = resolve_data_dir()
    log_path = setup_logging(data_dir)
    log = logging.getLogger(LOG_NAME)
    log.info("=== MTG Deck Builder starting ===")
    log.info("version: %s", app_version())
    log.info("data folder: %s", data_dir)
    log.info("python: %s  frozen: %s", sys.version.split()[0],
             getattr(sys, "frozen", False))

    # Log any exception that escapes everything else, too.
    def excepthook(exc_type, exc, tb):
        log.error("Uncaught exception", exc_info=(exc_type, exc, tb))
        if sys.__excepthook__:
            sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = excepthook

    db = CardDB(str(data_dir / "cards.db"))
    try:
        app = DeckBuilderApp(db, str(data_dir / "card_images"),
                             str(data_dir), str(log_path))
        # Signal a healthy launch only once the database and main window are
        # both built successfully: if the running process is a freshly swapped
        # build, this drops the flag the update helper waits on, so it does not
        # roll back a version that actually starts. Signalling before
        # construction reported success even when CardDB(...) or
        # DeckBuilderApp(...) then raised moments later -- a build that never
        # actually started looked "installed," leaving the helper no reason to
        # roll back a genuinely broken build and no backup left once it deleted
        # one as no longer needed.
        try:
            note_started(str(data_dir))
        except Exception:
            pass
        app.mainloop()
    except Exception:
        log.exception("Fatal error")
        raise
    finally:
        db.close()
        log.info("=== shut down ===")
        release_single_instance()


if __name__ == "__main__":
    main()
