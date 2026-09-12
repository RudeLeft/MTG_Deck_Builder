"""FacetIndex must stay byte-identical to the SQLite context worker.

The in-memory bitset index powers near-real-time contextual Search.  These
checks build a small crafted database that exercises the semantics that differ
between the search filter and the predictive counts -- top_heavy (GLOB/CAST vs
float), the "Urza's Saga" apostrophe subtype (regex boundary vs whitespace
trie), colour within/exact, and property Any/All/None -- and assert the index
matches ``count_search`` and the worker's ``_prepare`` exactly, and that it
declines (fallback) whatever it cannot represent.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.search.context import SearchContextController
from mtgdb.search.facet_index import FacetIndex
from mtgdb.search.models import SearchCriteria
from mtgdb.search.repository import SearchRepository


def _card(cid, name, type_line, **extra):
    row = {"object": "card", "id": cid, "name": name, "type_line": type_line,
           "lang": "en", "games": ["paper"], "set": "tst", "set_name": "Test",
           "set_type": "expansion", "collector_number": cid, "rarity": "common",
           "released_at": "2020-01-01", "legalities": {"modern": "legal"},
           "image_uris": {"png": "p"}}
    row.update(extra)
    return row


CARDS = [
    _card("bear", "Grizzly Bear", "Creature — Bear", cmc=2.0, colors=["G"],
          color_identity=["G"], power="2", toughness="1", keywords=["Trample"]),
    _card("saga", "Urza's Saga", "Enchantment Land — Urza's Saga", cmc=0.0,
          colors=[], color_identity=[], rarity="rare", layout="normal"),
    _card("diverge", "Odd Stats", "Creature — Construct", cmc=1.0, colors=[],
          color_identity=[], power="1-2", toughness="0"),
    _card("ub", "Beyond Bear", "Legendary Creature — Bear", cmc=3.0, colors=["G"],
          color_identity=["G"], power="3", toughness="4", rarity="mythic",
          color_indicator=["G"]),
    _card("hybrid", "Hybrid Wizard", "Creature — Wizard", cmc=1.0,
          colors=["W", "U"], color_identity=["W", "U"], power="1", toughness="3",
          mana_cost="{W/U}", produced_mana=["W", "U"], rarity="uncommon"),
    _card("var", "Star Creature", "Creature — Elemental", cmc=4.0, colors=["R"],
          color_identity=["R"], power="*", toughness="*", rarity="rare"),
    _card("walker", "Planar Judge", "Legendary Planeswalker — Judge", cmc=4.0,
          colors=["W"], color_identity=["W"], loyalty="3", rarity="mythic"),
    _card("token", "Servo", "Token Artifact Creature — Servo", colors=[],
          color_identity=[], power="1", toughness="1", layout="token"),
    _card("art", "Bear Art", "Card", layout="art_series", colors=[],
          color_identity=[]),
]


def _fixture():
    workspace = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)
    db = CardDB(os.path.join(workspace, "cards.db"))
    db.load_cards(CARDS)
    repo = SearchRepository(db)
    reader = db.open_reader()
    rows = [dict(r) for r in reader.execute(
        "SELECT " + ", ".join(FacetIndex.INDEX_COLUMNS) + " FROM cards")]
    index = FacetIndex(rows)
    ctrl = SearchContextController(repo)
    raw_vocab = dict(
        card_types=list(repo.card_types(["card"])),
        supertypes=list(repo.supertypes(["card"])),
        subtypes=repo.subtype_catalog(["card"]),
        keywords=repo.keyword_catalog(["card"]),
        layouts=[v for v, _ in repo.layouts(["card"])],
        rarities=list(repo.rarities(["card"])),
        formats=list((repo.formats_by_status(["card"]) or {}).get("playable") or ()),
        set_types=[s[0] for s in repo.set_types(["card"])],
        sets=repo.sets(content_types=["card"]))
    vocab = ctrl._vocabulary_payload(**raw_vocab)
    return db, repo, reader, index, ctrl, vocab


def main():
    db, repo, reader, index, ctrl, vocab = _fixture()

    CONTEXT_FIELDS = [
        "result_count", "card_type_counts", "supertype_counts", "subtype_counts",
        "keyword_counts", "color_counts", "produces_counts", "layout_counts",
        "rarity_counts", "numeric_ranges", "numeric_applicability", "release_years",
        "release_year_counts", "trait_counts", "mana_feature_counts",
        "special_property_counts", "status_property_counts", "pip_counts",
        "content_counts", "game_counts", "set_type_counts", "set_counts",
        "format_counts", "english_count", "all_language_count"]

    def worker(crit):
        ctrl._facet_index = None
        ctrl._facet_index_disabled = True   # force SQLite oracle
        ctrl._generation += 1
        return ctrl._prepare(ctrl._generation, crit, vocab)

    drafts = {
        "empty": SearchCriteria(content_types=("card",)),
        "green_within": SearchCriteria(content_types=("card",), colors=("G",),
                                       color_mode="within"),
        "wu_exact": SearchCriteria(content_types=("card",), colors=("W", "U"),
                                   color_mode="exact"),
        "creature": SearchCriteria(content_types=("card",), card_types=("Creature",)),
        "subtype_urza": SearchCriteria(content_types=("card",), subtypes=("Urza",),
                                       subtype_mode="any"),
        "topheavy_none": SearchCriteria(content_types=("card",),
            special_properties=("top_heavy",), special_property_mode="none"),
        "topheavy_any": SearchCriteria(content_types=("card",),
            special_properties=("top_heavy",), special_property_mode="any"),
        "ub_all": SearchCriteria(content_types=("card",),
            status_properties=("universes_beyond",), status_property_mode="all"),
        "all_content": SearchCriteria(content_types=("card", "token", "emblem", "art")),
        # Format legality and numeric ranges are now bitset-representable, so
        # they must match count_search and the worker exactly rather than
        # falling back.
        "format_modern": SearchCriteria(content_types=("card",), fmt="modern"),
        "format_modern_creatures": SearchCriteria(content_types=("card",),
            fmt="modern", card_types=("Creature",)),
        "cmc_range": SearchCriteria(content_types=("card",),
            cmc_min=1.0, cmc_max=3.0),
        "cmc_min_only": SearchCriteria(content_types=("card",), cmc_min=3.0),
        "power_range": SearchCriteria(content_types=("card",),
            power_min=1.0, power_max=3.0),
        "toughness_max": SearchCriteria(content_types=("card",), toughness_max=1.0),
    }

    checks = {}

    # 1. filter_bitset popcount == count_search for every draft.
    filter_ok = True
    for name, crit in drafts.items():
        got = index.popcount(index.filter_bitset(crit))
        want = repo.count(crit, reader)
        if got != want:
            filter_ok = False
            print("    filter mismatch %s: %d != %d" % (name, got, want))
    checks["filter_bitset popcount matches count_search"] = filter_ok

    # 2. context_counts matches the worker for every representable draft.
    context_ok = True
    for name, crit in drafts.items():
        got = index.context_counts(crit, vocab)
        snap = worker(crit)
        for field in CONTEXT_FIELDS:
            a = got[field]
            b = getattr(snap, field)
            if field == "release_years":
                a, b = tuple(a), tuple(b)
            aa = dict(a) if isinstance(a, dict) else a
            bb = dict(b) if isinstance(b, dict) else b
            if aa != bb:
                context_ok = False
                print("    context mismatch %s.%s: %s != %s" % (name, field, aa, bb))
    checks["context_counts matches the worker per field"] = context_ok

    # 3. top_heavy count equals its filter: both bear (2/1) and Odd Stats
    #    ("1-2"/"0", CAST 1>0) satisfy the GLOB/CAST rule the count now shares
    #    with the filter, so selecting top_heavy returns exactly what the count
    #    predicts (no gap).
    th_filter = SearchCriteria(content_types=("card",),
        special_properties=("top_heavy",), special_property_mode="any")
    filter_count = index.popcount(index.filter_bitset(th_filter))
    predictive = index.context_counts(
        SearchCriteria(content_types=("card",)), vocab)["special_property_counts"]
    checks["top_heavy count matches its filter (GLOB/CAST)"] = (
        filter_count == 2 and predictive.get("top_heavy") == 2)

    # 4. Subtype matching is whitespace-bounded: "Urza" must NOT match
    #    "Urza's Saga" (they are different subtypes), while the full subtype
    #    does -- and the count equals the selectable result (no gap).
    urza_partial = index.popcount(index.filter_bitset(
        SearchCriteria(content_types=("card",), subtypes=("Urza",),
                       subtype_mode="any")))
    saga_full = index.popcount(index.filter_bitset(
        SearchCriteria(content_types=("card",), subtypes=("Urza's Saga",),
                       subtype_mode="any")))
    saga_word = index.popcount(index.filter_bitset(
        SearchCriteria(content_types=("card",), subtypes=("Saga",),
                       subtype_mode="any")))
    checks["subtype match is whitespace-bounded (Urza != Urza's Saga)"] = (
        urza_partial == 0 and saga_full == 1 and saga_word == 1)

    # 5. Free-text and mana-symbol minimums still decline so the caller keeps
    #    the SQLite worker; format and numeric ranges no longer fall back.
    checks["text/pip criteria fall back (None)"] = all(
        index.filter_bitset(c) is None and index.context_counts(c, vocab) is None
        for c in (
            SearchCriteria(content_types=("card",), text=("bear",)),
            SearchCriteria(content_types=("card",), pips=("G",)),
            SearchCriteria(content_types=("card",), pip_min=2)))
    checks["format and numeric ranges are represented (no fallback)"] = all(
        index.filter_bitset(c) is not None and index.context_counts(c, vocab) is not None
        for c in (
            SearchCriteria(content_types=("card",), fmt="modern"),
            SearchCriteria(content_types=("card",), cmc_min=1.0),
            SearchCriteria(content_types=("card",), power_min=1.0, power_max=3.0)))

    ok = True
    for label, passed in checks.items():
        print("  [%s] %s" % ("PASS" if passed else "FAIL", label))
        ok &= bool(passed)
    print("\nFACET INDEX:", "ALL PASS" if ok else "FAILURES")
    ctrl.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
