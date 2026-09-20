r"""
Windows-only smoke test for the packaged ONEDIR build.
After `pyinstaller MTGDeckBuilder.spec --noconfirm`, run:
    python windows_tests\smoke_packaged_windows.py

Verifies the portable-storage guarantee: the packaged app must create and use
only its own folder and must touch no AppData/LocalAppData/Temp location.

The test is deliberately hermetic. It seeds a minimal, already-current card
database before launching so the app's first-launch auto-sync has nothing to
do. Without that seed this test downloads the full Scryfall bulk export on
every build -- minutes of build time, a hard dependency on network access, and
a ~380 MB data folder left inside dist/ that then ships with the artifact.

It also removes the data folder it created, so the build output stays exactly
the program and never accumulates a database or a developer's saved session.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WATCHED_LOCATIONS = ("APPDATA", "LOCALAPPDATA", "TEMP")
APP_NAME_MARKERS = ("mtgdeckbuilder", "mtg_deck")
STARTUP_TIMEOUT_SECONDS = 60
SETTLE_SECONDS = 5


def seed_current_database(data_dir):
    """Write a minimal database the sync service considers already current.

    due_reason() must return None or the app starts a full network refresh.
    That needs cards present, every trusted catalog stored, the Comprehensive
    Rules supertypes recorded, and a recent successful-sync timestamp.
    """
    from mtgdb.database.authorities import SCRYFALL_CATALOGS
    from mtgdb.database.bulk_import import UNIVERSES_BEYOND_RULE
    from mtgdb.database.db import CardDB
    from mtgdb.database.schema import (
        RULES_SUPERTYPES_META_KEY, UNIVERSES_BEYOND_META_KEY)
    from mtgdb.database.sync import DatabaseSyncService

    os.makedirs(data_dir, exist_ok=True)
    db = CardDB(os.path.join(data_dir, "cards.db"))
    try:
        db.load_cards(iter([{
            "id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()),
            "name": "Smoke Test Card", "lang": "en", "layout": "normal",
            "mana_cost": "{G}", "cmc": 1.0,
            "type_line": "Creature - Bear", "oracle_text": "",
            "colors": ["G"], "color_identity": ["G"], "keywords": [],
            "set": "smk", "set_name": "Smoke Set", "set_type": "core",
            "collector_number": "1", "rarity": "common",
            "legalities": {"commander": "legal"}, "games": ["paper"],
            "released_at": "2020-01-01", "image_uris": {}, "prices": {},
        }]))
        db.store_catalogs({name: ["Placeholder"] for name in SCRYFALL_CATALOGS})
        db.set_meta_many({
            RULES_SUPERTYPES_META_KEY: json.dumps(["Basic", "Legendary"]),
            # due_reason() forces a classification_refresh until this marker
            # matches the current rule, so a genuinely-current seed must set it.
            UNIVERSES_BEYOND_META_KEY: UNIVERSES_BEYOND_RULE,
            "last_successful_sync_epoch": str(time.time()),
        })
        return DatabaseSyncService(db).due_reason()
    finally:
        db.close()


def snapshot(location):
    root = os.environ.get(location, "")
    if not root:
        return set()
    # Only entries named after this application can indicate a leak, so match
    # by name instead of walking the whole tree twice. A full recursive glob of
    # %TEMP% is slow and races with every other process on the machine.
    found = set()
    for marker in APP_NAME_MARKERS:
        found.update(glob.glob(os.path.join(root, "*" + marker + "*")))
        found.update(glob.glob(os.path.join(root, "*", "*" + marker + "*")))
    return found


def wait_for_startup(proc, log_path):
    """Wait until the app has written its log, or it exits early."""
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    while time.time() < deadline:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
            return True
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def main():
    if sys.platform != "win32":
        print("SKIP: packaged smoke test is Windows-only.")
        return 0
    exe = os.path.join(ROOT, "dist", "MTGDeckBuilder", "MTGDeckBuilder.exe")
    if not os.path.exists(exe):
        print(f"FAIL: build not found at {exe}. Build first with PyInstaller.")
        return 1

    app_dir = os.path.dirname(exe)
    data_dir = os.path.join(app_dir, "data")
    log_path = os.path.join(data_dir, "mtg_deckbuilder.log")
    shutil.rmtree(data_dir, ignore_errors=True)

    due = seed_current_database(data_dir)
    if due is not None:
        print(f"FAIL: seeded database still reports a sync as due ({due!r}); "
              "this test would download the full Scryfall export.")
        shutil.rmtree(data_dir, ignore_errors=True)
        return 1

    before = {name: snapshot(name) for name in WATCHED_LOCATIONS}
    proc = subprocess.Popen([exe])
    started = wait_for_startup(proc, log_path)
    time.sleep(SETTLE_SECONDS)
    proc.terminate()
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

    used_portable_data = os.path.isdir(data_dir) and os.path.exists(log_path)
    leaks = []
    for name, previous in before.items():
        leaks += sorted(snapshot(name) - previous)
    downloaded = glob.glob(os.path.join(data_dir, "*.download"))

    print(f"  started within {STARTUP_TIMEOUT_SECONDS}s: {started}")
    print(f"  used portable data folder: {used_portable_data} ({data_dir})")
    print(f"  machine-specific writes detected: {leaks or 'none'}")
    print(f"  network sync started: {bool(downloaded)}")

    ok = started and used_portable_data and not leaks and not downloaded

    # Leave the build output as just the program: no database, no saved session.
    shutil.rmtree(data_dir, ignore_errors=True)
    print(f"  build output cleaned of runtime data: "
          f"{not os.path.exists(data_dir)}")

    print("PACKAGED SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
