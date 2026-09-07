"""Regression gates for state integrity, schema rollback, and cache identity."""

from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.core import cache_names
from mtgdb.database import schema
from mtgdb.deck.model import Deck
from mtgdb.core.atomic_files import TEMP_SUFFIX, sweep_abandoned_writes
from mtgdb.deck.io import save_deck_text
from mtgdb.preferences.repository import UIPreferencesRepository
from mtgdb.workspace.repository import WORKSPACE_VERSION, WorkspaceRepository


def _card(card_id, *, collector="1"):
    return {
        "id": card_id,
        "oracle_id": f"oracle-{card_id}",
        "name": "Same Card",
        "set_code": "tst",
        "collector_number": collector,
    }


def _deck_mutation_checks():
    deck = Deck()
    card = _card("printing-a")
    deck.add(card, "main", 2)

    invalid_move_raised = False
    try:
        deck.move(card["id"], "main", "bogus")
    except ValueError:
        invalid_move_raised = True

    preserved_after_invalid_move = (
        deck.total("main") == 2 and deck.total("side") == 0)

    invalid_quantity_raised = False
    try:
        deck.add(_card("printing-b"), "main", -3)
    except ValueError:
        invalid_quantity_raised = True

    deck.set_qty(card["id"], "main", "3")
    quantity_stays_integer = (
        deck.entries("main")[0]["qty"] == 3
        and isinstance(deck.entries("main")[0]["qty"], int))

    invalid_board_raised = False
    try:
        deck.remove(card["id"], "bogus")
    except ValueError:
        invalid_board_raised = True

    deck.move(card["id"], "main", "main")
    same_board_noop = deck.total("main") == 3

    return (
        invalid_move_raised and preserved_after_invalid_move,
        invalid_quantity_raised and deck.unique("main") == 1,
        quantity_stays_integer, invalid_board_raised, same_board_noop,
    )


def _schema_rollback_check():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', '999')")
    connection.execute("CREATE TABLE cards (marker TEXT)")
    connection.execute("INSERT INTO cards (marker) VALUES ('preserve-me')")
    connection.commit()

    original_schema = schema._SCHEMA
    schema._SCHEMA = (
        "CREATE TABLE should_rollback (id INTEGER);\n"
        "THIS IS INTENTIONALLY INVALID SQL;"
    )
    failed = False
    try:
        schema.initialize_schema(connection)
    except sqlite3.DatabaseError:
        failed = True
    finally:
        schema._SCHEMA = original_schema

    cards = connection.execute("SELECT marker FROM cards").fetchall()
    version = connection.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    temporary = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='should_rollback'").fetchone()
    connection.close()
    return failed and cards == [("preserve-me",)] and version == "999" and temporary is None


def _workspace_checks(tmp):
    future_rejected = not WorkspaceRepository._valid_payload({
        "version": WORKSPACE_VERSION + 1,
        "decks": [],
    })
    current_accepted = WorkspaceRepository._valid_payload({
        "version": WORKSPACE_VERSION,
        "decks": [],
    })

    fixed_time = 1_800_000_000.125
    repository = WorkspaceRepository(
        tmp / "workspace", recovery_interval=0, recovery_keep=8,
        clock=lambda: fixed_time)
    payload_a = {"version": WORKSPACE_VERSION, "decks": [], "marker": "a"}
    payload_b = {"version": WORKSPACE_VERSION, "decks": [], "marker": "b"}
    first = repository._write_recovery_snapshot(payload_a, force=True)
    second = repository._write_recovery_snapshot(payload_b, force=True)
    snapshots = list(repository.recovery_dir.glob("session_*.json"))
    collision_safe = first and second and len(snapshots) == 2
    return future_rejected and current_accepted, collision_safe


def _cache_identity_check(tmp):
    cache_dir = tmp / "cache"
    card_a = _card("aaaaaaaa-1111", collector="7")
    card_b = _card("bbbbbbbb-2222", collector="8")

    cache_names._INDEX_CACHE.clear()
    path_a = Path(cache_names.cache_path(card_a, cache_dir, ".jpg"))
    path_a.write_bytes(b"printing-a")

    index = cache_dir / ".cache_index.json"
    index.unlink()
    cache_names._INDEX_CACHE.clear()

    path_b = Path(cache_names.cache_path(card_b, cache_dir, ".jpg"))
    result = path_b != path_a and not path_b.exists()
    cache_names._INDEX_CACHE.clear()
    return result


def _abandoned_temporary_checks(root):
    """Every durable writer cleans up after a process killed mid-write.

    The atomic write shape -- temporary file, fsync, replace -- cannot clean up
    after itself when the process is killed between the two steps, and autosave
    enters that window constantly. Bulk downloads learned this first; these are
    the same sweep for the three writers that persist the user's own work.
    """
    def temporaries(folder):
        return sorted(path.name for path in Path(folder).iterdir()
                      if path.name.endswith(TEMP_SUFFIX))

    workspace_dir = Path(root) / "swept-workspace"
    (workspace_dir / "workspace_recovery").mkdir(parents=True)
    (workspace_dir / ".session.json.dead.tmp").write_text("half", encoding="utf-8")
    (workspace_dir / "workspace_recovery"
     / ".session_20260101_000000_000.json.dead.tmp").write_text(
        "half", encoding="utf-8")
    WorkspaceRepository(workspace_dir)
    workspace_clean = (
        not temporaries(workspace_dir)
        and not temporaries(workspace_dir / "workspace_recovery"))

    preferences_dir = Path(root) / "swept-preferences"
    preferences_dir.mkdir()
    (preferences_dir / ".ui_preferences.json.dead.tmp").write_text(
        "half", encoding="utf-8")
    UIPreferencesRepository(preferences_dir / "ui_preferences.json")
    preferences_clean = not temporaries(preferences_dir)

    deck_dir = Path(root) / "swept-decks"
    deck_dir.mkdir()
    (deck_dir / ".My Deck.txt.dead.tmp").write_text("half", encoding="utf-8")
    (deck_dir / ".Another Deck.txt.dead.tmp").write_text("half", encoding="utf-8")
    save_deck_text(deck_dir / "My Deck.txt", Deck(name="My Deck"))
    deck_clean = (
        ".My Deck.txt.dead.tmp" not in temporaries(deck_dir)
        # Writing one deck must not delete a different deck's temporary.
        and ".Another Deck.txt.dead.tmp" in temporaries(deck_dir))

    narrow_dir = Path(root) / "swept-narrow"
    narrow_dir.mkdir()
    (narrow_dir / ".session.json.aaa.tmp").write_text("ours", encoding="utf-8")
    (narrow_dir / ".other-app.dat.bbb.tmp").write_text("theirs", encoding="utf-8")
    (narrow_dir / "notes.tmp").write_text("a real file", encoding="utf-8")
    removed = sweep_abandoned_writes(narrow_dir, "session.json")
    remaining = set(temporaries(narrow_dir))
    narrow = (
        removed == 1
        and remaining == {".other-app.dat.bbb.tmp", "notes.tmp"}
        # Naming no prefix must never mean "everything in this folder".
        and sweep_abandoned_writes(narrow_dir) == 0
        and sweep_abandoned_writes(narrow_dir / "missing", "session.json") == 0)

    return (workspace_clean and preferences_clean and deck_clean), narrow


def main():
    (invalid_move_safe, invalid_quantity_safe, quantity_integer,
     invalid_board_safe, same_board_noop) = _deck_mutation_checks()
    schema_atomic = _schema_rollback_check()
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        workspace_version_safe, snapshot_collision_safe = _workspace_checks(tmp)
        cache_identity_safe = _cache_identity_check(tmp)
        abandoned_swept, sweep_is_narrow = _abandoned_temporary_checks(tmp)

    checks = {
        "an interrupted write leaves nothing behind for good": abandoned_swept,
        "a sweep only removes this application's own temporaries": sweep_is_narrow,
        "invalid deck move preserves source state": invalid_move_safe,
        "deck rejects non-positive additions": invalid_quantity_safe,
        "deck stores quantities as integers": quantity_integer,
        "deck mutation API rejects invalid boards": invalid_board_safe,
        "same-board deck move is a no-op": same_board_noop,
        "failed schema migration rolls back every DDL change": schema_atomic,
        "workspace rejects unsupported future schema versions": workspace_version_safe,
        "same-timestamp recovery snapshots do not overwrite": snapshot_collision_safe,
        "missing cache index cannot reuse an unknown printing": cache_identity_safe,
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nINTEGRITY REGRESSIONS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
