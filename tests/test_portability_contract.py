"""Portable storage, user-path, and ONEDIR build contracts."""

from pathlib import Path
import tempfile
from unittest import mock
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mtgdb.main as app_main


def main():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory) / "portable-app"
        base.mkdir()
        with mock.patch.object(app_main, "_program_dir", return_value=base):
            data_dir = app_main.resolve_data_dir()
        local_data_only = data_dir == base / "data" and data_dir.is_dir()

        with mock.patch.object(app_main, "_program_dir", return_value=base), \
                mock.patch.object(Path, "write_text", side_effect=OSError("readonly")):
            try:
                app_main.resolve_data_dir()
            except RuntimeError as exc:
                unwritable_fails_clearly = (
                    "writable folder" in str(exc) and str(base / "data") in str(exc))
            else:
                unwritable_fails_clearly = False

    spec = (ROOT / "MTGDeckBuilder.spec").read_text(encoding="utf-8")
    deck_files = (ROOT / "mtgdb/ui/deck_files.py").read_text(encoding="utf-8")
    printing_ui = (ROOT / "mtgdb/ui/printing.py").read_text(encoding="utf-8")
    onedir = (
        "exclude_binaries=True" in spec
        and "COLLECT(" in spec
        and "one-file" in spec.lower())
    selected_paths = (
        "filedialog.askopenfilename(" in deck_files
        and "filedialog.asksaveasfilename(" in deck_files
        and "filedialog.askdirectory(" in deck_files
        and "filedialog.asksaveasfilename(" in printing_ui)

    # PORT-006: a modal dialog inside the mutex claim blocks any caller that
    # cannot click OK, so the guard must detect without notifying.
    main_source = (ROOT / "mtgdb/main.py").read_text(encoding="utf-8")
    claim = main_source.split("def acquire_single_instance(", 1)[1].split(
        "\ndef ", 1)[0]
    silent_single_instance_claim = (
        "MessageBoxW" not in claim
        and "user32" not in claim
        and "def notify_already_running(" in main_source
        and "notify_already_running()" in main_source.split(
            "def main(", 1)[1])

    # PORT-007: one durable replace pattern for every persisted file. Asserted
    # behaviourally (a real save leaves no temp and preserves unrelated keys)
    # and structurally (no writer may reintroduce a fixed temp name or drop
    # fsync, which a behavioural test alone cannot detect).
    from mtgdb.preferences.repository import UIPreferencesRepository
    import json as _json
    with tempfile.TemporaryDirectory() as _prefs_dir:
        _prefs_path = Path(_prefs_dir) / "ui_preferences.json"
        _prefs_path.write_text(
            _json.dumps({"unrelated": "keep"}), encoding="utf-8")
        UIPreferencesRepository(_prefs_path).save_table_columns(
            {"results": ["name", "cost"]})
        _saved = _json.loads(_prefs_path.read_text(encoding="utf-8"))
        durable_preference_save = (
            _saved.get("unrelated") == "keep"
            and _saved["table_columns"]["results"] == ["name", "cost"]
            and not list(Path(_prefs_dir).glob("*.tmp"))
            and not (Path(_prefs_dir) / "ui_preferences.json.tmp").exists())

    _writers = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("mtgdb/preferences/repository.py",
                     "mtgdb/workspace/repository.py",
                     "mtgdb/deck/io.py")
    }
    durable_write_pattern = all(
        "tempfile.mkstemp(" in source
        and "os.fsync(" in source
        # os.replace() and Path.replace() are the same atomic rename.
        and ("os.replace(" in source or "temporary_path.replace(" in source)
        # A fixed temp name lets two writers interleave into one path.
        and '.with_name(self.path.name + ".tmp")' not in source
        for source in _writers.values())

    checks = {
        "PORT-007 preference save is atomic and preserves unrelated keys": (
            durable_preference_save),
        "PORT-007 every persisted writer uses unique temp plus fsync": (
            durable_write_pattern),
        "single-instance claim detects without showing a blocking dialog": (
            silent_single_instance_claim),
        "automatic data stays beside the portable application": local_data_only,
        "unwritable portable storage fails with a clear error": unwritable_fails_clearly,
        "PyInstaller specification remains ONEDIR": onedir,
        "Open Save Export and Print use user-selected paths": selected_paths,
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nPORTABILITY CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
