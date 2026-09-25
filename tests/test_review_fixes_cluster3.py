"""Regression coverage for four cloud-review findings with no single shared home.

- queries.py: the DFC/split front-face import fallback escapes LIKE metacharacters.
- search/results.py: filter_signature() includes the Cost/Colors "groups" field.
- search/catalogs.py: shutdown() clears the warm-load queue, not just _pending.
- search/models.py: SearchCriteria.from_mapping() preserves an absent field's
  dataclass default instead of coercing it to an empty tuple.
"""

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.search.catalogs import SearchCatalogController
from mtgdb.search.models import SearchCriteria
from mtgdb.search.repository import SearchRepository
from mtgdb.search.results import filter_signature


def _card(card_id, name, **extra):
    row = {
        "object": "card", "id": card_id, "name": name, "type_line": "Instant",
        "lang": "en", "games": ["paper"], "set": "tst", "set_name": "Test",
        "set_type": "expansion", "collector_number": card_id, "rarity": "common",
        "released_at": "2020-01-01", "legalities": {"modern": "legal"},
    }
    row.update(extra)
    return row


def _like_escape_check():
    """A literal '%'/'_' in an imported decklist name must not act as a wildcard.

    get_by_name's DFC/split front-face fallback used to build its LIKE pattern
    from unescaped user text. Querying with just the front face ("100% Bear",
    no "// Certified") skips the first (exact-match) query entirely -- no
    card is literally named that -- and reaches the vulnerable second query,
    whose pattern is "<front> //%". Unescaped, the "%" in "100% Bear" is a
    wildcard matching "100" + anything + " Bear //" + anything, which would
    also match a completely different card like "100000% Bear // Certified".
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        db = CardDB(os.path.join(temp_dir, "cards.db"))
        db.load_cards([
            _card("wanted", "100% Bear // Certified", type_line="Creature // Land"),
            # Matches the front-face LIKE pattern only if % is treated as a
            # wildcard: "100" + anything + "% Bear //" would match this name.
            _card("decoy", "100000% Bear // Certified",
                  type_line="Creature // Land"),
        ])
        try:
            found = db.get_by_name("100% Bear")
            found_id = found["id"] if found else None
        finally:
            db.close()
        return found_id == "wanted"


def _filter_signature_groups_check():
    """filter_signature() must key on Cost/Colors' "groups", not ignore it.

    Two rules that differ only by which symbol groups are checked must not
    hash to the same cache signature, or a picker opened after changing
    Cost/Colors reuses the previous selection's stale cached vocabulary.
    """
    rule_white = {"kind": "cost", "mode": "Any", "groups": {"W"}}
    rule_blue = {"kind": "cost", "mode": "Any", "groups": {"U"}}
    rule_no_groups = {"kind": "text", "mode": "Any", "value": "foo"}
    return (
        filter_signature({"cost": rule_white})
        != filter_signature({"cost": rule_blue})
        # A rule with no "groups" key at all must still hash consistently
        # (regression guard: must not raise on a missing key).
        and filter_signature({"other": rule_no_groups})
        == filter_signature({"other": rule_no_groups}))


def _catalog_shutdown_check():
    """shutdown() must stop a still-queued warm-load, not just clear _pending.

    Before the fix, _run()'s exit guard required BOTH _pending and
    _warm_queue empty; shutdown() cleared only _pending, so a warm() queued at
    startup was still popped and processed (a full DB scan) during/after the
    rest of teardown instead of the worker exiting immediately. A functional
    race test against a tiny in-memory DB is not discriminating on its own --
    the worker drains a one-item queue so fast that it empties itself well
    within any reasonable join() timeout whether or not shutdown() also
    clears it, which was verified by running this same scenario against the
    pre-fix source and seeing it pass regardless. The source check is what
    actually proves the fix; the functional half only guards against a
    hang/exception, which source inspection alone would not catch.
    """
    catalogs_source = (ROOT / "mtgdb/search/catalogs.py").read_text(encoding="utf-8")
    shutdown_body = catalogs_source[catalogs_source.index("def shutdown("):]
    shutdown_body = shutdown_body[:shutdown_body.index("\n\n    def ")]
    structural_fix = "self._warm_queue.clear()" in shutdown_body

    with tempfile.TemporaryDirectory() as temp_dir:
        db = CardDB(os.path.join(temp_dir, "cards.db"))
        db.load_cards([_card("c1", "Test Card")])
        try:
            controller = SearchCatalogController(SearchRepository(db))
            controller.warm(("card",), False)
            stopped = controller.shutdown(timeout=2.0)
            warm_queue_cleared = not controller._warm_queue
        finally:
            db.close()
        functional_sane = stopped and warm_queue_cleared

    return structural_fix and functional_sane


def _from_mapping_default_check():
    """from_mapping() must preserve an omitted field's dataclass default.

    Applying _tuple(None) -> () to every tuple field unconditionally,
    including ones absent from the input mapping, silently overrode
    content_types' actual default of ("card",) with an empty tuple. An empty
    content_types makes add_content_filter(()) short-circuit the whole search
    to zero results rather than "every ordinary card".
    """
    default_criteria = SearchCriteria.from_mapping({})
    explicit_empty = SearchCriteria.from_mapping({"content_types": ()})
    explicit_value = SearchCriteria.from_mapping({"content_types": ("token",)})
    return (
        default_criteria.content_types == ("card",)
        # An explicitly empty tuple is still honoured (caller's real intent),
        # only an ABSENT key falls back to the dataclass default.
        and explicit_empty.content_types == ()
        and explicit_value.content_types == ("token",))


def main():
    checks = {
        "get_by_name's DFC front-face fallback escapes LIKE metacharacters":
            _like_escape_check(),
        "filter_signature distinguishes Cost/Colors rules by their groups":
            _filter_signature_groups_check(),
        "catalog controller shutdown stops a still-queued warm-load":
            _catalog_shutdown_check(),
        "SearchCriteria.from_mapping preserves an omitted field's default":
            _from_mapping_default_check(),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nREVIEW FIXES (CLUSTER 3):", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
