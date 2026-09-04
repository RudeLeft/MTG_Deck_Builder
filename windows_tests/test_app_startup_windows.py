"""Windows pre-build smoke test for full DeckBuilderApp construction.

This intentionally constructs the real application against an empty temporary
portable data directory, then destroys it before mainloop.  It catches startup
ordering regressions that source/geometry contracts cannot see (for example a
feature querying a Treeview before the deck pane has created it).
"""

from pathlib import Path
import sys
import tempfile

if sys.platform != "win32":
    print("SKIP: full Tk startup smoke test requires Windows.")
    raise SystemExit(0)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.ui.app import DeckBuilderApp


def main():
    with tempfile.TemporaryDirectory(prefix="mtg-startup-smoke-") as tmp:
        data_dir = Path(tmp) / "data"
        image_dir = data_dir / "card_images"
        data_dir.mkdir(parents=True, exist_ok=True)
        db = CardDB(str(data_dir / "cards.db"))
        app = None
        try:
            app = DeckBuilderApp(
                db,
                str(image_dir),
                str(data_dir),
                str(data_dir / "startup-smoke.log"),
            )
            # Constructor completion is the contract.  Do not enter mainloop or
            # let scheduled database maintenance/network work begin.
            if app._workspace_loaded:
                raise AssertionError(
                    "workspace must remain unloaded until async restore completes")
            if app._workspace_autosave_after is not None:
                raise AssertionError(
                    "autosave must not start before async workspace restore")
            app.withdraw()
            print("  [PASS] full DeckBuilderApp constructs before mainloop")
            return 0
        finally:
            if app is not None:
                try:
                    app.destroy()
                except Exception:
                    pass
            db.close()


if __name__ == "__main__":
    raise SystemExit(main())
