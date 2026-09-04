"""Search-layer ownership, projection, controller, and responsiveness contracts."""

import os
import tempfile
import time
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.search.controller import SearchController
from mtgdb.search.models import SearchCriteria
from mtgdb.search.repository import SEARCH_RESULT_COLUMNS, SearchRepository
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.results import SearchResultsMixin
from mtgdb.ui.search import SearchFeatureMixin


def _card(card_id, name, keyword="Flying"):
    return {
        "id": card_id,
        "name": name,
        "type_line": "Creature — Bird",
        "mana_cost": "{1}{W}",
        "cmc": 2,
        "colors": ["W"],
        "color_identity": ["W"],
        "power": "2",
        "toughness": "2",
        "rarity": "common",
        "set": "tst",
        "set_name": "Test Set",
        "set_type": "expansion",
        "collector_number": card_id,
        "lang": "en",
        "released_at": "2026-01-01",
        "games": ["paper"],
        "keywords": [keyword],
        "oracle_text": "Flying",
        "legalities": {"modern": "legal"},
        "image_uris": {"normal": "normal", "png": "png"},
    }


class _ResultOwner(SearchResultsMixin):
    def __init__(self, repository, rows):
        self.search_repository = repository
        self._result_store = SearchResultStore.from_rows(rows)


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _LoadingSearchOwner:
    def __init__(self):
        self._search_catalog_loading = True
        self._pending_search_request = False
        self.results_count_lbl = _FakeLabel()
        self.status = ""

    def _update_search_filter_summary(self):
        pass

    def _status(self, text):
        self.status = text


class _PendingSearchOwner:
    def __init__(self, *, catalog_loading=False, running=False):
        self._pending_search_request = True
        self._search_catalog_loading = catalog_loading
        self.search_controller = type("Controller", (), {"running": running})()
        self.calls = 0
        self.count_restores = 0

    def _set_result_count(self):
        self.count_restores += 1

    def _do_search(self):
        self.calls += 1


def main():
    criteria = SearchCriteria.from_mapping({
        "name": "Bird", "colors": ["W"], "card_types": ["Creature"],
        "set_codes": {"tst"}, "content_types": ["card"],
    })
    same = SearchCriteria.from_mapping({
        "name": "Bird", "colors": ("W",), "card_types": ("Creature",),
        "set_codes": ("tst",), "content_types": ("card",),
    })

    with tempfile.TemporaryDirectory() as temporary_directory:
        db = CardDB(os.path.join(temporary_directory, "cards.db"))
        db.load_cards([
            _card("1", "First Bird"),
            _card("2", "Second Bird", "Vigilance"),
        ])
        repository = SearchRepository(db)
        reader = repository.open_reader()
        try:
            summaries = repository.search(criteria, reader)
        finally:
            reader.close()
        full_ids = [row["id"] for row in db.search(**criteria.query_arguments())]
        summary_ids = [row["id"] for row in summaries]
        exact_batch = SearchCriteria.from_mapping({
            "names": ["First Bird", "Second Bird"],
            "content_types": ["card"],
        })
        batch_ids = [row["id"] for row in db.search(**exact_batch.query_arguments())]

        owner = _ResultOwner(repository, list(summaries))
        full = owner._full_result_at(0)

        controller = SearchController(repository)
        started = controller.start(criteria)
        deadline = time.monotonic() + 2.0
        event = None
        while event is None and time.monotonic() < deadline:
            event = controller.poll_latest()
            if event is None:
                time.sleep(0.005)
        accepted = bool(event and controller.accept(event))
        cached = controller.start(criteria)
        db.close()

    checklist_source = (ROOT / "mtgdb/ui/search_checklist.py").read_text(encoding="utf-8")
    table_filter_source = (ROOT / "mtgdb/ui/table_filters.py").read_text(encoding="utf-8")
    results_source = (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8")
    search_source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    printings_source = (
        (ROOT / "mtgdb/ui/search_printings.py").read_text(encoding="utf-8")
        + (ROOT / "mtgdb/ui/set_filters.py").read_text(encoding="utf-8"))
    mechanic_ten_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Mechanic {index}" for index in range(10)}, "Any", "mechanics",
        max_visible=10)
    mechanic_eleven_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Mechanic {index}" for index in range(11)}, "Any", "mechanics",
        max_visible=10)
    rarity_summary = SearchFeatureMixin._picker_button_text(
        None, {"common", "uncommon", "rare", "mythic"}, "Any", "rarities")
    subtype_ten_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(10)},
        "Any", "subtypes", max_visible=10, single_line=True)
    subtype_eleven_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(11)},
        "Any", "subtypes", max_visible=10, single_line=True)

    loading_owner = _LoadingSearchOwner()
    SearchFeatureMixin._do_search(loading_owner)
    ready_owner = _PendingSearchOwner()
    resumed = SearchFeatureMixin._resume_pending_search_request(ready_owner)
    blocked_owner = _PendingSearchOwner(catalog_loading=True)
    blocked = SearchFeatureMixin._resume_pending_search_request(blocked_owner)

    checks = {
        "criteria signatures normalize equivalent snapshots": (
            criteria.signature() == same.signature()),
        "repository projection is intentionally narrow": (
            set(summaries[0]) == set(SEARCH_RESULT_COLUMNS)
            and "legalities" not in summaries[0]
            and "image_normal" not in summaries[0]),
        "narrow and full searches return identical printing IDs": (
            summary_ids == full_ids == ["1", "2"]),
        "exact-name batches return every selected deck-card name": (
            batch_ids == ["1", "2"]),
        "result action hydrates by exact printing ID without inflating compact rows": (
            full["id"] == "1" and "legalities" in full
            and owner._result_store.row_at_source(0).get("id") == "1"
            and not hasattr(owner._result_store.row_at_source(0), "__dict__")),
        "controller delivers and caches a Tk-free result": (
            started.kind == "started" and accepted
            and cached.kind == "unchanged"),
        "search checklist maps only through hidden-first popup presenter": (
            "owner._create_hidden_popup(" in checklist_source
            and "owner._present_hidden_popup(" in checklist_source
            and "VirtualChecklistView" in checklist_source),
        "table filter maps only after withdrawal and geometry": (
            table_filter_source.index("pop.withdraw()")
            < table_filter_source.rindex("pop.geometry(")
            < table_filter_source.rindex("pop.deiconify()")),
        "large UI collections use fixed row pools and Results virtualization": (
            "ROW_POOL = 32" in checklist_source
            and 'style="ListChoice.TRadiobutton"' in checklist_source
            and "VirtualChecklistView" in table_filter_source
            and "RESULT_LIVE_ROW_LIMIT = 128" in results_source
            and "SearchResultStore" in results_source
            and "BooleanVar" not in table_filter_source[
                table_filter_source.index("def _build_values_filter_editor"):
                table_filter_source.index("def _build_numeric_filter_editor")]),
        "result count is independent of physical viewport rows": (
            'text=f"0 /' not in results_source
            and "live_tk_rows" in results_source
            and "logical_rows" in results_source),
        "Search Clear resets subtype mechanic and rarity picker presentation": all(
            marker in search_source for marker in (
                'self._selected_subtypes.clear()',
                'self._selected_keywords.clear()',
                'self._subtype_btn.configure(text="Any")',
                'self._keyword_btn.configure(text="Any")',
                'self._rarity_btn.configure(text="Any")',
            )),
        "Search Clear also releases highlights and every Results column filter": (
            'clear_highlights = getattr(self, "_clear_source_highlights", None)'
                in search_source
            and 'if callable(clear_highlights):' in search_source
            and 'clear_highlights()' in search_source
            and 'self._clear_table_filter("results")' in search_source),
        "Search requested during trusted-filter loading is queued and resumes": (
            loading_owner._pending_search_request
            and loading_owner.results_count_lbl.text == "RESULTS | Trusted filters are loading…"
            and "automatically" in loading_owner.status
            and resumed and ready_owner.calls == 1
            and ready_owner.count_restores == 1
            and not ready_owner._pending_search_request
            and not blocked and blocked_owner.calls == 0
            and blocked_owner._pending_search_request
            and 'self._resume_pending_search_request()' in search_source
            and 'if start.kind == "unchanged":\n            self._set_result_count()'
                in search_source),
        "Search Clear cancels a queued trusted-filter Search": (
            search_source.index('def _clear_search(self):')
            < search_source.index('self._pending_search_request = False',
                                  search_source.index('def _clear_search(self):'))
            < search_source.index('def _apply_cards_search_preset',
                                  search_source.index('def _clear_search(self):'))),
        "trusted-filter failure cannot strand the Results loading label": (
            'self._pending_search_request = False' in search_source
            and 'set_count = getattr(self, "_set_result_count", None)' in search_source
            and 'text="RESULTS | Trusted filters unavailable"' in search_source),
        "Rules Text Subtype Format Rarity and Printings share the Advanced grid": (
            'text="Active Filters"' not in search_source
            and 'self._build_advanced_filters(parent)' in search_source
            and 'self._build_rules_text_filter(\n            self._advanced_filters_frame, row=1, advanced=True)' in search_source
            and 'text="Subtype").grid(\n            row=2' in search_source
            and 'self._build_format_rarity_filters(\n            self._advanced_filters_frame, format_row=3, rarity_row=4)'
                in search_source
            and 'self._build_printing_filter(self._advanced_filters_frame, row=5)'
                in search_source
            and 'printing = ttk.Frame(self._advanced_filters_frame)' not in search_source
            and 'row=row, column=1, sticky="ew", pady=2' in printings_source),
        "picker summaries expose ten values before remainder count": (
            'max_visible=10' in search_source
            and 'PICKER_SUMMARY_PER_LINE = 5' in search_source
            and 'f" · +{len(vals) - max_visible}"' in search_source),
        "Mechanics summaries show ten then count the remainder": (
            "+" not in mechanic_ten_summary
            and mechanic_eleven_summary.endswith("+1")
            and len(mechanic_ten_summary.replace("\n", " · ").split(" · ")) == 10),
        "Rarity summary lists every selected rarity": (
            all(value in rarity_summary for value in (
                "common", "uncommon", "rare", "mythic"))
            and "+" not in rarity_summary),
        "Subtype summary stays horizontal through ten selections": (
            "\n" not in subtype_ten_summary
            and "\n" not in subtype_eleven_summary
            and len(subtype_ten_summary.split(" · ")) == 10
            and subtype_eleven_summary.endswith("+1")
            and "single_line=True" in search_source),
        "Card Name spans the primary row and English only lives in Printings": (
            'text="Card Name"' in search_source
            and 'row=0, column=1, columnspan=3, sticky="ew"' in search_source
            and 'text="English only"' not in search_source
            and 'text="English only"' in printings_source
            and 'english_variable=owner.english_only' in printings_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nSEARCH ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
