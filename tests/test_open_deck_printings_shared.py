"""Open Deck must compose the same authoritative Printings component as Search."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mtgdb.ui.deck_files as deck_files
from mtgdb.deck.io import deck_from_text
from mtgdb.ui.deck_files import DeckFileWorkflowMixin
from mtgdb.ui.set_filters import PrintingFilter
from mtgdb.ui.search_printings import SearchPrintingFilter


class _Var:
    def __init__(self, value=False):
        self.value = bool(value)

    def get(self):
        return self.value


class _FakePicker:
    last = None

    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.kwargs = kwargs
        self.paper_only = _Var(False)
        self.refreshed = 0
        self.done_text = None
        _FakePicker.last = self

    def refresh_catalog(self):
        self.refreshed += 1

    def run_modal(self, *, done_text="Done"):
        self.done_text = done_text
        return True

    def selected_set_types(self):
        return {"commander"}

    def selected_set_codes(self):
        return {"cmd"}


class _Owner(DeckFileWorkflowMixin):
    def __init__(self):
        self.search_repository = object()


class _Resolver:
    def __init__(self):
        self.calls = []
        self.counter = 0

    def get_by_name(self, name, **options):
        self.calls.append((name, options))
        self.counter += 1
        return {
            "id": f"card-{self.counter}", "name": name,
            "set_code": next(iter(options.get("allowed_set_codes") or {"cmd"})),
        }


def main():
    source = (ROOT / "mtgdb/ui/deck_files.py").read_text(encoding="utf-8")
    set_source = (ROOT / "mtgdb/ui/set_filters.py").read_text(encoding="utf-8")

    original_picker = deck_files._DeckImportPrintingFilter
    original_boolean_var = deck_files.tk.BooleanVar
    deck_files._DeckImportPrintingFilter = _FakePicker
    deck_files.tk.BooleanVar = lambda master=None, value=False: _Var(value)
    try:
        owner = _Owner()
        result = owner._choose_import_sets()
        picker = _FakePicker.last
    finally:
        deck_files._DeckImportPrintingFilter = original_picker
        deck_files.tk.BooleanVar = original_boolean_var

    resolver = _Resolver()
    imported, missing = deck_from_text(
        "1 Untagged Card\n1 Tagged Card [TST:7]\n",
        resolver,
        allowed_set_types={"commander"},
        allowed_set_codes={"cmd"},
        paper_only=True,
        lang="en",
    )
    untagged_options = resolver.calls[0][1]
    tagged_options = resolver.calls[1][1]

    checks = {
        "Search adapter subclasses the shared PrintingFilter": (
            issubclass(SearchPrintingFilter, PrintingFilter)),
        "Open Deck has no second bespoke Printings dialog": (
            "class _DeckImportSetsDialog" not in source
            and "PrintingFilter(" in source
            and "class PrintingFilter" in set_source),
        "Open Deck remains Cards-only while Search may expose Art Series": (
            result == ({"commander"}, {"cmd"}, False, "en")
            and picker.refreshed == 1
            and picker.done_text == "Open Deck"
            and picker.kwargs["content_types_getter"]() == ("card",)
            and picker.kwargs["english_variable"].get() is True),
        "Open Deck Printings keeps only the requested resolver guidance": (
            picker.kwargs["intro_text"]
            == "Choose which printings may resolve untagged cards."
            and "Explicit [SET]" not in source),
        "untagged TXT cards honor the shared Printings scope": (
            untagged_options.get("allowed_set_types") == {"commander"}
            and untagged_options.get("allowed_set_codes") == {"cmd"}
            and untagged_options.get("paper_only") is True
            and untagged_options.get("lang") == "en"),
        "explicit printing tags remain authoritative over picker scope": (
            tagged_options.get("allowed_set_types") is None
            and tagged_options.get("allowed_set_codes") == {"tst"}
            and tagged_options.get("allowed_collector_numbers") == {"7"}
            and tagged_options.get("paper_only") is False
            and tagged_options.get("lang") is None),
        "shared-scope import still resolves both rows": (
            not missing and imported.total("main") == 2),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nOPEN DECK SHARED PRINTINGS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
