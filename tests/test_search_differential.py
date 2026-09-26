"""M0 correctness harness for the Search/database re-architecture (Option C).

Builds the shared differential fixture, runs a large deterministic battery of
randomized criteria, and proves the current in-memory fast path (the facet index)
is byte-identical to the canonical SQLite reference (count_search, ordered result
ids, and the SQLite context worker). Every later milestone imports this harness
and asserts its NEW path reproduces the same reference, so nothing old is retired
until the replacement is proven equal.

This M0 test asserts three invariants over the whole battery:
  1. count_search == number of ordered result ids (the count and the result set
     agree for every criteria).
  2. Where the fast path is representable, its count equals count_search.
  3. Where the fast path is representable, its contextual counts equal the
     SQLite worker field by field; where it is not (a mana-symbol Minimum above
     one), it declines rather than guessing.  The Minimum box's default of 1 is
     plain colour presence and MUST be represented: the real UI sends it on every
     request, and declining it silently routed every live request to SQLite.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import search_diff_harness as H
from mtgdb.database.semantics import pip_minimum_threshold


JACE = "Jace, Vryn's Prodigy // Jace, Telepath Unbound"
VALKI = "Valki, God of Lies // Tibalt, Cosmic Impostor"
DELVER = "Delver of Secrets // Insectile Aberration"
IRONSMITH = "Village Ironsmith // Ironfang"
HYBRID_BACK = "Hybrid Back Test // Reverse Side"
STAR_FLIP = "Star Flip // Star Back"
BONECRUSHER = "Bonecrusher Giant // Stomp"
FIRE_ICE = "Fire // Ice"


def _either_face_checks(harness):
    """SRCH-052: a two-faced card is found by EITHER face's cost and stats.

    The randomized battery proves the SQL builder, the bitset index and the
    SQLite worker agree with one another; it cannot show they agree on the RIGHT
    answer, since all three could consistently ignore the back face.  These name
    the cards that must be found -- and the ones that must not -- and pin that
    the row itself still describes the front face.
    """
    from mtgdb.search.models import SearchCriteria

    def names(**fields):
        crit = SearchCriteria.from_mapping({"content_types": ("card",), **fields})
        _cols, rows = harness.db.search_projection(
            connection=harness.reader, columns=("name",),
            **crit.query_arguments())
        return {row[0] for row in rows}

    def parity(**fields):
        """Fast path == SQL == worker for one criteria (None when it declines)."""
        crit = SearchCriteria.from_mapping({"content_types": ("card",), **fields})
        reference = H.reference_capture(harness, crit)
        fast = H.fast_capture(harness, crit)
        if fast is None:
            return None
        return (fast["count"] == reference["count"]
                and not H.compare_context(reference["context"], fast["context"]))

    loyalty = names(loyalty_min=4.0)
    power = names(power_min=3.0)
    red = names(pips=("R",), pip_mode="any")
    black_red = names(pips=("B", "R"), pip_mode="all")
    hybrid = names(mana_features=("hybrid_mana",), mana_feature_mode="any")
    top_heavy = names(special_properties=("top_heavy",),
                      special_property_mode="any")
    variable = names(special_properties=("variable_stats",),
                     special_property_mode="any")
    x_cost = names(mana_features=("has_x_cost",), mana_feature_mode="any")
    two_black = names(pips=("B",), pip_mode="all", pip_min=2)
    two_red = names(pips=("R",), pip_mode="all", pip_min=2)
    small_back = names(power_min=3.0, toughness_max=1.0)

    columns = ("name", "mana_cost", "power", "toughness", "loyalty",
               "back_mana_cost", "back_power", "back_loyalty", "pips_b",
               "pips_r")
    _c, all_rows = harness.db.search_projection(
        connection=harness.reader, columns=columns,
        **SearchCriteria.from_mapping(
            {"content_types": ("card",)}).query_arguments())
    by_name = {row[0]: dict(zip(columns, row)) for row in all_rows}

    empty = SearchCriteria.from_mapping({"content_types": ("card",)})
    fast_context = H.fast_capture(harness, empty)["context"]
    worker_context = H.reference_capture(harness, empty)["context"]
    ranges = fast_context["numeric_ranges"]

    # SRCH-050: the count on a property option must equal what selecting it
    # returns.  The index and the worker share one count rule, so they can agree
    # with each other while both ignoring a back face; only the real result count
    # exposes that.
    def selecting(group, key):
        # Counts are per printing (a card with two printings counts twice).
        crit = SearchCriteria.from_mapping({
            "content_types": ("card",), group: (key,),
            f"{group[:-1]}_mode": "any"})
        return harness.repo.count(crit, harness.reader)

    property_counts = (
        ("mana_features", "mana_feature_counts", "hybrid_mana"),
        ("mana_features", "mana_feature_counts", "phyrexian_mana"),
        ("mana_features", "mana_feature_counts", "has_x_cost"),
        ("special_properties", "special_property_counts", "top_heavy"),
        ("special_properties", "special_property_counts", "variable_stats"),
    )
    counts_match_results = all(
        context[field].get(key, 0) == selecting(group, key)
        for group, field, key in property_counts
        for context in (fast_context, worker_context))

    return {
        "Loyalty finds a planeswalker back face": (
            {JACE, VALKI, "Teferi, Time Raveler"} <= loyalty
            and "Tyvar, Jubilant Brawler" not in loyalty),
        "Power finds a back-face power, and not a front-only card": (
            {DELVER, IRONSMITH, HYBRID_BACK} <= power
            and "Grizzly Bear" not in power and JACE not in power),
        "a colour in the cost finds a back-face cost": (
            VALKI in red and JACE not in red and VALKI in black_red),
        "Hybrid mana finds a back-face-only hybrid but not a '//' cost": (
            {HYBRID_BACK, "Boros Charm", "Tyvar, Jubilant Brawler"} <= hybrid
            and not ({FIRE_ICE, BONECRUSHER} & hybrid)),
        "top-heavy is judged per face, never power of one against toughness of the other": (
            {IRONSMITH, DELVER} <= top_heavy and HYBRID_BACK not in top_heavy
            and JACE not in top_heavy),
        "variable stats and X cost are found on the back face": (
            STAR_FLIP in variable and DELVER not in variable
            and STAR_FLIP in x_cost and BONECRUSHER not in x_cost),
        "a symbol Minimum counts every face once, without double-counting '//' costs": (
            VALKI in two_black and VALKI not in two_red
            and BONECRUSHER in two_red
            and by_name[BONECRUSHER]["pips_r"] == 2
            and by_name[BONECRUSHER]["back_mana_cost"] == ""
            and by_name[FIRE_ICE]["back_mana_cost"] == ""),
        "combined stat bounds are each read on either face": (
            {DELVER, IRONSMITH} <= small_back
            and "Ogre Brute" not in small_back),
        "the row still describes the FRONT face": (
            by_name[JACE]["mana_cost"] == "{1}{U}"
            and by_name[JACE]["power"] == "0" and by_name[JACE]["loyalty"] is None
            and by_name[JACE]["back_loyalty"] == "5"
            and by_name[VALKI]["mana_cost"] == "{1}{B}"
            and by_name[VALKI]["power"] == "2"
            and by_name[VALKI]["back_mana_cost"] == "{5}{B}{R}"
            and by_name[VALKI]["pips_b"] == 2 and by_name[VALKI]["pips_r"] == 1
            and by_name[DELVER]["power"] == "1"
            and by_name[DELVER]["back_power"] == "3"),
        "single-faced cards carry no back-face values": (
            by_name["Grizzly Bear"]["back_power"] is None
            and by_name["Grizzly Bear"]["back_mana_cost"] == ""),
        "context ranges span both faces": (
            ranges["loyalty"] == (3.0, 5.0)),
        "a property option's count equals what selecting it returns, on both engines": (
            counts_match_results),
        "the index, SQL and worker agree on every back-face search": all(
            parity(**fields) in (True, None) for fields in (
                {"loyalty_min": 4.0}, {"power_min": 3.0},
                {"pips": ("R",), "pip_mode": "any"},
                {"pips": ("B", "R"), "pip_mode": "all"},
                {"mana_features": ("hybrid_mana",), "mana_feature_mode": "any"},
                {"special_properties": ("top_heavy",),
                 "special_property_mode": "any"},
                {"special_properties": ("variable_stats",),
                 "special_property_mode": "any"},
                {"power_min": 3.0, "toughness_max": 1.0})),
    }


EOWYN = "\u00c9owyn, Fearless Knight"
JOTUN = "J\u00f6tun Grunt"
AETHER = "\u00c6ther Vial"
LIMDUL = "Lim-D\u00fbl the Necromancer"
SCHOLAR = "Civilized Scholar // Homicidal Brute"
MDFC = "Spell Then Land // Test Land"
BURN_FREEZE = "Burn // Freeze"


def _names_colors_and_text_checks(harness):
    """Card Name folds accents, a two-faced card's colours span both faces, a
    quoted phrase cannot span the seam between two halves, and an "Any" mana-symbol
    count is measured alone.  Each is checked on the SQL path AND the in-memory
    index, and against named cards rather than only against each other."""
    from mtgdb.search.models import SearchCriteria

    def crit(**fields):
        return SearchCriteria.from_mapping({"content_types": ("card",), **fields})

    def sql_names(**fields):
        _c, rows = harness.db.search_projection(
            connection=harness.reader, columns=("name",),
            **crit(**fields).query_arguments())
        return {row[0] for row in rows}

    def index_names(**fields):
        ids = harness.facet_result_ids(crit(**fields))
        if ids is None:
            return None
        _c, rows = harness.db.search_projection(
            connection=harness.reader, columns=("id", "name"),
            **crit(**fields).query_arguments())
        return {name for row_id, name in rows if row_id in ids}

    def both(**fields):
        """The names found; SQL and the index must give the same answer."""
        found, indexed = sql_names(**fields), index_names(**fields)
        return found if indexed is None or indexed == found else None

    # ---- Card Name: case- AND accent-insensitive (both engines) ----------------
    lookups = (
        ("\u00e9owyn", EOWYN), ("eowyn", EOWYN), ("\u00c9OWYN", EOWYN),
        ("J\u00d6TUN", JOTUN), ("jotun", JOTUN), ("lim-dul", LIMDUL),
        ("D\u00dbL", LIMDUL), ("aether vial", AETHER), ("\u00e6ther", AETHER),
        ("AETHER", AETHER))
    name_ok = all(
        target in (both(name=query) or set()) for query, target in lookups)
    plain_ok = (
        "Grizzly Bear" in (both(name="bear") or set())
        and EOWYN not in (both(name="jotun") or {EOWYN})
        # LIKE metacharacters stay literal: neither is a wildcard.
        and both(name="%") == set() and both(name="_") == set())
    suggestions = harness.db.name_suggestions("eowyn")
    suggestions_ok = EOWYN in suggestions and JOTUN in harness.db.name_suggestions("j\u00f6t")

    # ---- Colours: a two-faced card's own colours are every face's, together ------
    def colors(*chosen, mode, scope="colors"):
        return both(colors=chosen, color_mode=mode, color_scope=scope)

    colors_ok = (
        # Front blue, back red: found as red, as blue, and exactly as both.
        SCHOLAR in (colors("R", mode="includes") or set())
        and SCHOLAR in (colors("U", mode="includes") or set())
        and SCHOLAR in (colors("U", "R", mode="exact") or set())
        and SCHOLAR not in (colors("U", mode="exact") or {SCHOLAR})
        and SCHOLAR not in (colors("R", mode="within") or {SCHOLAR})
        # A spell // land: the colourless land back adds no colour and takes none
        # away, so it is black -- not Colorless, and not "within" any other colour.
        and MDFC in (colors("B", mode="exact") or set())
        and MDFC not in (colors("C", mode="exact") or {MDFC})
        and MDFC not in (colors("R", mode="within") or {MDFC})
        and AETHER in (colors("C", mode="exact") or set())
        # Colour identity is unchanged, and already covered both faces.
        and SCHOLAR in (colors("R", mode="includes", scope="identity") or set()))

    # ---- Rules text: no phrase across the seam; word search is unchanged ------------
    across = both(text=('"one or two targets tap target permanent"',))
    inside_first = both(text=('"deals 2 damage to one or two targets"',))
    inside_second = both(text=('"tap target permanent"',))
    words = both(text=("targets", "tap"), text_mode="all")
    text_ok = (
        across is not None and BURN_FREEZE not in across
        and BURN_FREEZE in (inside_first or set())
        and BURN_FREEZE in (inside_second or set())
        # Separate words still AND across the whole card, and the whole-word
        # behaviour of Rules Text is deliberately left as it was.
        and BURN_FREEZE in (words or set())
        and "Untap Everything" in (both(text=("tap",)) or set()))

    # ---- an "Any" symbol count is measured alone (SRCH-045) -------------------------
    parity = True
    for chosen in (("W",), ("R",), ("U", "B")):
        context = H.reference_capture(harness, crit(
            pips=chosen, pip_mode="any", pip_min=2.0))["context"]["pip_counts"]
        for color in ("W", "U", "B", "R", "G"):
            alone = harness.repo.count(
                crit(pips=(color,), pip_mode="any", pip_min=2.0), harness.reader)
            if context.get(color) != alone:
                parity = False

    # ---- the live colour counts (index AND SQLite worker) see the back face too ----
    live_ok = True
    for fields in (
            {"colors": ("U",), "color_mode": "includes", "color_scope": "colors"},
            {"colors": ("B",), "color_mode": "within", "color_scope": "colors"},
            {"colors": ("R",), "color_mode": "exact", "color_scope": "colors"},
            {"colors": ("C",), "color_mode": "exact", "color_scope": "colors"}):
        criteria = crit(**fields)
        reference = H.reference_capture(harness, criteria)
        fast = H.fast_capture(harness, criteria)
        if fast is None or H.compare_context(reference["context"], fast["context"]):
            live_ok = False
    # With Blue included, Red is offered ONLY through Civilized Scholar's red back.
    blue_counts = H.reference_capture(harness, crit(
        colors=("U",), color_mode="includes",
        color_scope="colors"))["context"]["color_counts"]
    live_ok = live_ok and blue_counts.get("R", 0) >= 1

    # ---- a printed "+1" is in neither Search nor the table filter (both engines) ----
    from mtgdb.search.results import row_passes_filters
    plus_one = {"power": "+1", "toughness": "+1", "name": "Vanguard Plus"}
    plus_one_ok = (
        "Vanguard Plus" not in (both(power_min=0.0) or {"Vanguard Plus"})
        and "Vanguard Plus" not in (both(toughness_max=9.0) or {"Vanguard Plus"})
        and row_passes_filters(
            plus_one, {"power": {"kind": "numeric", "min": 0.0, "max": None}}) is False
        and row_passes_filters(
            plus_one, {"toughness": {"kind": "numeric", "min": None, "max": 9.0}}) is False)

    return {
        "a printed +1 is excluded by Search and by the table filter alike": plus_one_ok,
        "the live colour counts include a back face's colours, on both engines": live_ok,
        "Card Name ignores case AND accents on both engines": name_ok and plain_ok,
        "autocomplete agrees with the accent-insensitive Name search": suggestions_ok,
        "a two-faced card's colours are its faces' colours together": colors_ok,
        "a quoted phrase cannot match across the seam between two halves": text_ok,
        "an Any symbol count equals selecting that colour alone, at Minimum 2": parity,
    }


def main():
    harness = H.Harness()
    try:
        battery = H.generate_criteria(harness, seed=1234, n=240)

        count_matches_results = True
        fast_count_matches = True
        fast_ids_match = True
        fast_context_matches = True
        representable_declines = True
        representable_seen = 0
        fallback_seen = 0
        default_minimum_seen = 0

        for crit in battery:
            reference = H.reference_capture(harness, crit)
            if reference["count"] != len(reference["ids"]):
                count_matches_results = False
                print("    count/result mismatch: %d != %d :: %r"
                      % (reference["count"], len(reference["ids"]), crit))

            fast = H.fast_capture(harness, crit)
            if fast is None:
                fallback_seen += 1
                # Only a Minimum above one may decline; the default of 1 is
                # colour presence and must never fall back.
                if pip_minimum_threshold(crit.pip_min) == 1:
                    representable_declines = False
                    print("    unexpected fallback (Minimum is 1): %r" % (crit,))
                continue

            representable_seen += 1
            if crit.pip_min is not None:
                default_minimum_seen += 1
            if fast["count"] != reference["count"]:
                fast_count_matches = False
                print("    fast count mismatch: %d != %d :: %r"
                      % (fast["count"], reference["count"], crit))
            facet_ids = harness.facet_result_ids(crit)
            if facet_ids != set(reference["ids"]):
                fast_ids_match = False
                print("    result-set mismatch (SQL vs facet golden) :: %r" % (crit,))
            bad = H.compare_context(reference["context"], fast["context"])
            if bad:
                fast_context_matches = False
                print("    fast context mismatch %s :: %r" % (bad, crit))

        either_face = _either_face_checks(harness)
        either_face.update(_names_colors_and_text_checks(harness))

        checks = {
            "battery exercises both fast and fallback paths": (
                representable_seen >= 100 and fallback_seen >= 1),
            "count_search equals the ordered result count for every criteria":
                count_matches_results,
            "fast-path count equals count_search where representable":
                fast_count_matches,
            "SQL result set equals the facet golden id set where representable":
                fast_ids_match,
            "fast-path context equals the SQLite worker where representable":
                fast_context_matches,
            "only a pip Minimum above one declines the fast path":
                representable_declines,
            "the battery proves the Minimum default of 1 against SQLite": (
                default_minimum_seen >= 40),
            **either_face,
        }

        ok = True
        for label, passed in checks.items():
            print("  [%s] %s" % ("PASS" if passed else "FAIL", label))
            ok &= bool(passed)
        print("\nSEARCH DIFFERENTIAL (M0):", "ALL PASS" if ok else "FAILURES")
        return 0 if ok else 1
    finally:
        harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
