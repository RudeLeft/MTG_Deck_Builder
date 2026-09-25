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
from mtgdb.database.semantics import pip_minimum_threshold
from mtgdb.search.context import SearchContextController, _predict_pips
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
    # Costs whose "/" is NOT a hybrid symbol (split and adventure halves are
    # joined by " // "), and a compleated {G/U/P} that IS one.
    _card("split", "Fire // Ice", "Instant // Instant", cmc=4.0,
          colors=["R", "U"], color_identity=["R", "U"],
          mana_cost="{1}{R} // {1}{U}", layout="split", rarity="uncommon"),
    _card("adventure", "Giant // Stomp",
          "Creature \u2014 Giant // Instant \u2014 Adventure",
          cmc=3.0, colors=["R"], color_identity=["R"],
          mana_cost="{2}{R} // {1}{R}", layout="adventure"),
    _card("compleated", "Compleated Sage",
          "Legendary Planeswalker \u2014 Tamiyo",
          cmc=5.0, colors=["G", "U"], color_identity=["G", "U"],
          mana_cost="{2}{G}{G/U/P}{U}", loyalty="5", rarity="mythic"),
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
        # Mana-symbol color presence (no explicit minimum) is representable.
        "pips_any_wu": SearchCriteria(content_types=("card",),
            pips=("W", "U"), pip_mode="any"),
        "pips_all_wu": SearchCriteria(content_types=("card",),
            pips=("W", "U"), pip_mode="all"),
        "pips_none_g": SearchCriteria(content_types=("card",),
            pips=("G",), pip_mode="none"),
        # The Mana Symbols Minimum box always carries a value, and its untouched
        # default of 1 is the same query as no minimum.  The real UI sends
        # pip_min=1.0 on EVERY request, so these must be represented and agree
        # with count_search and the worker exactly.
        "pips_any_wu_min1": SearchCriteria(content_types=("card",),
            pips=("W", "U"), pip_mode="any", pip_min=1.0),
        "pips_all_wu_min1": SearchCriteria(content_types=("card",),
            pips=("W", "U"), pip_mode="all", pip_min=1.0),
        "pips_none_g_min1": SearchCriteria(content_types=("card",),
            pips=("G",), pip_mode="none", pip_min=1.0),
        "pips_min_zero": SearchCriteria(content_types=("card",),
            pips=("W",), pip_mode="all", pip_min=0.0),
        "pips_min_fraction": SearchCriteria(content_types=("card",),
            pips=("W", "U"), pip_mode="all", pip_min=1.5),
        "min1_no_pips": SearchCriteria(content_types=("card",), pip_min=1.0),
        "ui_defaults": SearchCriteria(
            content_types=("card",), pip_min=1.0, lang="en", paper_only=False,
            games=("paper", "arena", "mtgo")),
        "ui_defaults_plus_filters": SearchCriteria(
            content_types=("card",), pip_min=1.0, lang="en", paper_only=False,
            games=("paper", "arena", "mtgo"), card_types=("Creature",),
            colors=("G",), color_mode="within"),
        # Free-text name/rules search is now bitset-representable (scanned per
        # query), so every text draft must match count_search and the worker
        # exactly rather than falling back.
        "name_substring": SearchCriteria(content_types=("card",), name="bolt"),
        "names_exact": SearchCriteria(content_types=("card",),
            names=("Lightning Bolt", "Counterspell")),
        "rules_word_all": SearchCriteria(content_types=("card",), text=("draw",)),
        "rules_multiword_all": SearchCriteria(content_types=("card",),
            text=("draw a card",)),
        "rules_two_any": SearchCriteria(content_types=("card",),
            text=("draw", "destroy"), text_mode="any"),
        "rules_two_none": SearchCriteria(content_types=("card",),
            text=("token", "creature"), text_mode="none"),
        "rules_quoted_phrase": SearchCriteria(content_types=("card",),
            text=('"draw a card"',)),
        "text_plus_color_cmc": SearchCriteria(content_types=("card",),
            text=("creature",), colors=("G",), color_mode="any",
            cmc_min=2.0, cmc_max=5.0),
        "name_plus_format": SearchCriteria(content_types=("card",),
            name="a", fmt="modern"),
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

    # 3b. A property option's predicted count must equal what selecting it returns
    #     (SRCH-045).  Hybrid mana disagreed: the count treated a split/adventure
    #     cost's "//" as hybrid and missed a compleated {G/U/P}.  Checked on the
    #     bitset index AND the SQLite worker, against the real search count.
    parity_ok = True
    empty = SearchCriteria(content_types=("card",))
    index_counts = index.context_counts(empty, vocab)["mana_feature_counts"]
    worker_counts = worker(empty).mana_feature_counts
    for key in ("hybrid_mana", "phyrexian_mana", "has_x_cost"):
        selected = SearchCriteria(
            content_types=("card",), mana_features=(key,),
            mana_feature_mode="any")
        want = repo.count(selected, reader)
        for source, counts in (("index", index_counts), ("worker", worker_counts)):
            if counts.get(key, 0) != want:
                parity_ok = False
                print("    %s count for %s: %s != selecting returns %s"
                      % (source, key, counts.get(key), want))
    hybrid_returns = {
        card["id"] for card in db.search(
            content_types=["card"], mana_features=["hybrid_mana"],
            mana_feature_mode="any")}
    checks["mana-feature counts equal what selecting them returns"] = (
        parity_ok
        # {W/U} and the compleated {G/U/P} are hybrid; "//" costs are not.
        and hybrid_returns == {"hybrid", "compleated"})

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

    # 5. Only a mana-symbol minimum ABOVE ONE still declines (its hybrid
    #    counts-once rule is per-query) so the caller keeps the SQLite worker;
    #    free text, format, numeric ranges, and mana-symbol presence -- including
    #    the Minimum box's default of 1 -- are all represented.  The worker
    #    predicts colour counts with the threshold even when no colour is chosen
    #    yet, so a Minimum above one declines with or without a selection.
    checks["a pip Minimum above one falls back (None)"] = all(
        index.filter_bitset(c) is None and index.context_counts(c, vocab) is None
        for c in (
            SearchCriteria(content_types=("card",), pips=("G",), pip_min=2),
            SearchCriteria(content_types=("card",), pip_min=2),
            SearchCriteria(content_types=("card",), pips=("W", "U"), pip_min=3.5),
            SearchCriteria(content_types=("card",), pips=("G",),
                           pip_min=float("inf")),
            SearchCriteria(content_types=("card",), pips=("G",),
                           pip_min=float("-inf")),
            SearchCriteria(content_types=("card",), pip_min=float("nan")),
            # An infinite release year made the index raise OverflowError
            # (int(float(inf))); a NaN one it silently ignored.  The builder
            # rejects both, so the index must not answer for it.
            SearchCriteria(content_types=("card",), released_from=float("inf")),
            SearchCriteria(content_types=("card",), released_to=float("nan")),
            SearchCriteria(content_types=("card",), cmc_min=float("nan")),
            SearchCriteria(content_types=("card",), power_max=float("inf")),
            SearchCriteria(content_types=("card",), loyalty_min=float("-inf")),
            SearchCriteria(content_types=("card",), defense_max=float("nan"))))
    checks["the Minimum box's default of 1 is represented, so the UI never falls back"] = all(
        index.filter_bitset(drafts[name]) is not None
        and index.context_counts(drafts[name], vocab) is not None
        for name in (
            "pips_any_wu_min1", "pips_all_wu_min1", "pips_none_g_min1",
            "pips_min_zero", "pips_min_fraction", "min1_no_pips",
            "ui_defaults", "ui_defaults_plus_filters"))

    # A non-finite Minimum is refused where every other numeric filter is
    # (SRCH-015), never leaked as an OverflowError.  The shared reader and the
    # worker's predictor must not raise on it (they are also reached with no
    # colour chosen, where the builder never looks at the value), the builder
    # must say why, and the worker must report it as an ordinary error event and
    # stay alive for the next request.
    import time as _t
    non_finite = (float("inf"), float("-inf"), float("nan"), "abc", 1e400)
    checks["the Minimum reader and predictor never raise on non-finite input"] = (
        all(pip_minimum_threshold(v) == 1 for v in non_finite)
        and all(isinstance(_predict_pips([], ("G",), v), dict) for v in non_finite))
    try:
        repo.count(SearchCriteria(content_types=("card",), pips=("G",),
                                  pip_min=float("inf")), reader)
        builder_message = ""
    except ValueError as exc:
        builder_message = str(exc)
    checks["the builder refuses an infinite Minimum with a clear message"] = (
        "finite" in builder_message and "minimum" in builder_message)

    def _next_event(controller, timeout=10.0):
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            event = controller.poll_latest()
            if event is not None:
                return event
            _t.sleep(0.02)
        return None

    ctrl.request(SearchCriteria(
        content_types=("card",), pips=("G",), pip_min=float("inf")))
    bad_event = _next_event(ctrl)
    ctrl.request(SearchCriteria(content_types=("card",), card_types=("Creature",)))
    good_event = _next_event(ctrl)
    checks["the worker reports it as an error event and keeps serving"] = (
        bad_event is not None and bad_event.kind == "error"
        and "finite" in str(bad_event.payload)
        and good_event is not None and good_event.kind == "done")

    # The controller must actually take that path: an index that is built but
    # bypassed answers nothing.  The SQLite fallback is the only route that
    # calls repository.count, so a spy on it shows which path served the request.
    count_calls = []
    real_count = repo.count
    repo.count = lambda *a, **k: (count_calls.append(1), real_count(*a, **k))[1]
    try:
        ctrl._facet_index = index
        ctrl._facet_index_disabled = False
        ctrl._generation += 1
        ui_default_snapshot = ctrl._prepare(
            ctrl._generation, drafts["ui_defaults"], vocab)
        ctrl._generation += 1
        pip_two_snapshot = ctrl._prepare(
            ctrl._generation,
            SearchCriteria(content_types=("card",), pip_min=2), vocab)
    finally:
        repo.count = real_count
        ctrl._facet_index = None
        ctrl._facet_index_disabled = True
    checks["UI-default criteria are served by the index; a Minimum of 2 by SQLite"] = (
        ui_default_snapshot is not None and pip_two_snapshot is not None
        and len(count_calls) == 1)
    checks["text, format, numeric, and pip presence are represented (no fallback)"] = all(
        index.filter_bitset(c) is not None and index.context_counts(c, vocab) is not None
        for c in (
            SearchCriteria(content_types=("card",), text=("bear",)),
            SearchCriteria(content_types=("card",), name="bolt"),
            SearchCriteria(content_types=("card",), fmt="modern"),
            SearchCriteria(content_types=("card",), cmc_min=1.0),
            SearchCriteria(content_types=("card",), power_min=1.0, power_max=3.0),
            SearchCriteria(content_types=("card",), pips=("W", "U"), pip_mode="any")))

    # 5b. A database refresh that lands while the index is being built.  The
    #     refresh's reset saw no index (the build had not published yet) and its
    #     warm-up saw a build "in progress", so the finished build -- made from
    #     the OLD rows -- used to be published and kept until restart.
    class _RefreshLandsMidBuild:
        def __init__(self, real, controller):
            self._real = real
            self._controller = controller
            self.fired = False

        def open_reader(self):
            reader = self._real.open_reader()
            if not self.fired:
                self.fired = True
                self._controller.reset_facet_index()
            return reader

        def __getattr__(self, name):
            return getattr(self._real, name)

    racing = SearchContextController(repo)
    racing.repository = _RefreshLandsMidBuild(repo, racing)
    first = racing._ensure_facet_index()
    discarded = first is None and racing._facet_index is None
    second = racing._ensure_facet_index()
    republished = second is not None and racing._facet_index is second
    racing.shutdown()
    checks["a build overtaken by a database refresh is discarded, then rebuilt"] = (
        discarded and republished)

    # 6. warm_facet_index builds the index off the request path (startup / post
    #    sync) so the first live pick is instant.
    import time as _time
    warm_ctrl = SearchContextController(repo)
    warm_ctrl.reset_facet_index()
    assert warm_ctrl._facet_index is None
    warm_ctrl.warm_facet_index()
    deadline = _time.time() + 10
    while warm_ctrl._facet_index is None and _time.time() < deadline:
        _time.sleep(0.02)
    checks["warm_facet_index builds the index off the request path"] = (
        warm_ctrl._facet_index is not None)
    warm_ctrl.shutdown()

    search_src = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    sync_src = (ROOT / "mtgdb/ui/database_sync.py").read_text(encoding="utf-8")
    checks["warm-up is wired after the first catalog load and after a database sync"] = (
        "controller.warm_facet_index()" in search_src
        and "search_context_controller.warm_facet_index()" in sync_src)

    ok = True
    for label, passed in checks.items():
        print("  [%s] %s" % ("PASS" if passed else "FAIL", label))
        ok &= bool(passed)
    print("\nFACET INDEX:", "ALL PASS" if ok else "FAILURES")
    ctrl.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
