"""Open Deck TXT resolution must be independent of interactive filters."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mtgdb.ui.deck_files as deck_files
from mtgdb.deck.io import deck_from_text
from mtgdb.ui.deck_files import DeckFileWorkflowMixin


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


class _Owner(DeckFileWorkflowMixin):
    def __init__(self):
        self.db = object()
        self.statuses = []
        self.opened = []

    def _status(self, text):
        self.statuses.append(text)

    def _append_deck_session(self, deck, *, path=None, dirty=False):
        self.opened.append((deck, path, dirty))

    def _poll_deck_file_job(self, future, on_success, *, error_title):
        on_success(future.result())


class _Resolver:
    def __init__(self):
        self.calls = []
        self.counter = 0

    def get_by_name(self, name, **options):
        self.calls.append((name, options))
        self.counter += 1
        allowed = options.get("allowed_set_codes")
        return {
            "id": f"card-{self.counter}",
            "name": name,
            "set_code": next(iter(allowed)) if allowed else "any",
        }


def _token_tag_resolution():
    """DUI-015: an explicit tag may resolve a token; an untagged name may not.

    Decks this app exports carry their own tokens as tagged entries, so a
    round-trip must not silently drop them -- but a bare untagged name must stay
    Cards-only and never resolve to a token/emblem/art printing.
    """
    import atexit
    import os
    import shutil
    import tempfile

    from mtgdb.database.db import CardDB

    workspace = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)
    db = CardDB(os.path.join(workspace, "cards.db"))
    db.load_cards([{
        "id": "tok1", "oracle_id": "gob", "name": "Goblin",
        "type_line": "Token Creature — Goblin", "layout": "token",
        "set": "tst", "set_name": "Token Set", "set_type": "token",
        "collector_number": "1", "lang": "en", "released_at": "2012-01-01",
        "games": ["paper"], "colors": ["R"], "power": "1", "toughness": "1",
    }])

    tagged = db.get_by_name(
        "Goblin", allowed_set_codes={"tst"}, allowed_collector_numbers={"1"},
        paper_only=False, lang=None, allow_non_card=True)
    untagged = db.get_by_name("Goblin", paper_only=False, lang=None)

    tagged_deck, tagged_missing = deck_from_text(
        "1 Goblin [TST:1]\n", db, name="t", fmt="commander")
    untagged_deck, untagged_missing = deck_from_text(
        "1 Goblin\n", db, name="t", fmt="commander")
    db.close()

    return {
        "an explicit tag resolves a token printing (tokens survive a round-trip)": (
            tagged is not None and str(tagged.get("layout")) == "token"
            and str(tagged.get("collector_number")) == "1"
            and not tagged_missing and tagged_deck.total("main") == 1),
        "an untagged name stays Cards-only (never resolves to a token)": (
            untagged is None
            and untagged_missing == ["Goblin"]
            and untagged_deck.total("main") == 0),
    }


def _paste_deck_check():
    """Paste Decklist imports clipboard text via the Open Deck resolver."""
    captured = {}
    original_parse = deck_files.deck_from_text
    original_submit = deck_files.submit_deck_file_job
    try:
        def fake_parse(raw, resolver, **kwargs):
            captured["raw"] = raw
            captured["resolver"] = resolver
            captured["kwargs"] = kwargs
            return object(), []

        deck_files.deck_from_text = fake_parse
        deck_files.submit_deck_file_job = (
            lambda fn, *args, **_kwargs: _ImmediateFuture(fn(*args)))
        owner = _Owner()
        owner.clipboard_get = lambda: "// Pasted (modern)\n1 Some Card\n"
        owner._paste_deck()
    finally:
        deck_files.deck_from_text = original_parse
        deck_files.submit_deck_file_job = original_submit

    return {
        "Paste Decklist imports clipboard text unrestricted into a new session": (
            captured.get("raw") == "// Pasted (modern)\n1 Some Card\n"
            and captured.get("resolver") is owner.db
            and captured.get("kwargs", {}).get("paper_only") is False
            and captured.get("kwargs", {}).get("allowed_set_types") is None
            and captured.get("kwargs", {}).get("allowed_set_codes") is None
            and len(owner.opened) == 1
            and owner.opened[0][1] is None
            and owner.opened[0][2] is True),
    }


def main():
    source = (ROOT / "mtgdb/ui/deck_files.py").read_text(encoding="utf-8")
    app_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
    deck_source = (ROOT / "mtgdb/ui/deck.py").read_text(encoding="utf-8")

    captured = {}
    original_dialog = deck_files.filedialog.askopenfilename
    original_read = deck_files.read_deck_text
    original_parse = deck_files.deck_from_text
    original_submit = deck_files.submit_deck_file_job
    try:
        deck_files.filedialog.askopenfilename = lambda **_kwargs: "/tmp/example.txt"
        deck_files.read_deck_text = lambda _path: "1 Any Card\n"

        def fake_parse(raw, resolver, **kwargs):
            captured["raw"] = raw
            captured["resolver"] = resolver
            captured["kwargs"] = kwargs
            return object(), []

        deck_files.deck_from_text = fake_parse
        deck_files.submit_deck_file_job = (
            lambda fn, *args, **_kwargs: _ImmediateFuture(fn(*args)))

        owner = _Owner()
        owner._open_deck()
    finally:
        deck_files.filedialog.askopenfilename = original_dialog
        deck_files.read_deck_text = original_read
        deck_files.deck_from_text = original_parse
        deck_files.submit_deck_file_job = original_submit

    resolver = _Resolver()
    imported, missing = deck_from_text(
        "1 Untagged Card\n1 Tagged Card [TST:7]\n",
        resolver,
        allowed_set_types=None,
        allowed_set_codes=None,
        paper_only=False,
        lang=None,
    )
    untagged_options = resolver.calls[0][1]
    tagged_options = resolver.calls[1][1]

    checks = {
        "File menu and deck + menu share the same Open Deck command": (
            'label="Open Deck...", command=self._open_deck' in app_source
            and 'label="Open Deck TXT...", command=self._open_deck' in deck_source),
        "Open Deck no longer opens or depends on a Printings scope picker": (
            "_choose_import_sets" not in source
            and "_DeckImportPrintingFilter" not in source
            and "PrintingFilter" not in source),
        "Open Deck passes an unrestricted resolver scope": (
            captured.get("raw") == "1 Any Card\n"
            and captured.get("resolver") is owner.db
            and captured.get("kwargs", {}).get("allowed_set_types") is None
            and captured.get("kwargs", {}).get("allowed_set_codes") is None
            and captured.get("kwargs", {}).get("paper_only") is False
            and captured.get("kwargs", {}).get("lang") is None),
        "untagged TXT names resolve against the complete local card database": (
            untagged_options.get("allowed_set_types") is None
            and untagged_options.get("allowed_set_codes") is None
            and untagged_options.get("paper_only") is False
            and untagged_options.get("lang") is None),
        "explicit printing tags remain authoritative during unrestricted import": (
            tagged_options.get("allowed_set_types") is None
            and tagged_options.get("allowed_set_codes") == {"tst"}
            and tagged_options.get("allowed_collector_numbers") == {"7"}
            and tagged_options.get("paper_only") is False
            and tagged_options.get("lang") is None),
        "unrestricted import still resolves both rows": (
            not missing and imported.total("main") == 2),
        **_token_tag_resolution(),
        **_paste_deck_check(),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nOPEN DECK FULL DATABASE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
