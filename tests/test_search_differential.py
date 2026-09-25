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
