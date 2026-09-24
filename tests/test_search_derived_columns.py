"""M1: precomputed search-derived columns must equal the functions they replace.

The re-architecture moves per-row search classification out of SQL functions and
into stored, indexed columns populated at import. Each such column MUST stay
byte-identical to the expression it replaces, or the new query path (built on the
columns in a later milestone) would drift from today's semantics. This test
builds the differential corpus and asserts that equality directly in SQL.

M1a covers ``content_kind`` (the CARD_CONTENT_KIND scope used by every search).
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import search_diff_harness as H


def main():
    harness = H.Harness()
    reader = harness.reader
    try:
        total = reader.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        # Rows whose stored column disagrees with the function it replaces.
        # ``IS NOT`` compares NULL-safely, so a NULL column against a non-NULL
        # function value is counted as a mismatch too.
        mismatches = reader.execute(
            "SELECT COUNT(*) FROM cards "
            "WHERE content_kind IS NOT CARD_CONTENT_KIND(layout, type_line)"
        ).fetchone()[0]
        populated = reader.execute(
            "SELECT COUNT(*) FROM cards WHERE content_kind IS NOT NULL"
        ).fetchone()[0]
        distinct_kinds = {
            row[0] for row in reader.execute(
                "SELECT DISTINCT content_kind FROM cards")}
        index_present = bool(reader.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='index' AND name='idx_cards_content_kind'").fetchone())

        checks = {
            "corpus built with rows": total > 0,
            "content_kind is populated for every row": populated == total,
            "content_kind equals CARD_CONTENT_KIND for every row": mismatches == 0,
            "content_kind covers multiple scopes (card/token/emblem/art)": (
                {"card", "token", "emblem", "art"} <= distinct_kinds),
            "content_kind is indexed": index_present,
        }

        ok = True
        for label, passed in checks.items():
            print("  [%s] %s" % ("PASS" if passed else "FAIL", label))
            ok &= bool(passed)
        print("\nSEARCH DERIVED COLUMNS (M1):", "ALL PASS" if ok else "FAILURES")
        return 0 if ok else 1
    finally:
        harness.close()


if __name__ == "__main__":
    raise SystemExit(main())
