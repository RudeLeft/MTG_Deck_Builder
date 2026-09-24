"""M1: precomputed search-derived columns must equal the functions they replace.

The re-architecture moves per-row search classification out of SQL functions and
into stored, indexed columns populated at import. Each such column MUST stay
byte-identical to the expression it replaces, or the new query path (built on the
columns in a later milestone) would drift from today's semantics. This test
builds the differential corpus and asserts that equality directly in SQL.

M1a covers ``content_kind`` (the CARD_CONTENT_KIND scope used by every search).
M1b covers the colour bitmasks and the packed ``trait_flags``.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import search_diff_harness as H
from mtgdb.database.semantics import (
    COLOR_BITS, TRAIT_COLOR_INDICATOR, TRAIT_HAS_X_COST, TRAIT_HYBRID_MANA,
    TRAIT_MULTI_FACED, TRAIT_PHYREXIAN_MANA, TRAIT_TOP_HEAVY,
    TRAIT_VARIABLE_STATS)
from mtgdb.search.facet_index import _trait_filter_keys


# The trait-flag bit for each key name the independent filter classifier emits.
_TRAIT_BITS = {
    "multi_faced": TRAIT_MULTI_FACED,
    "hybrid_mana": TRAIT_HYBRID_MANA,
    "phyrexian_mana": TRAIT_PHYREXIAN_MANA,
    "has_x_cost": TRAIT_HAS_X_COST,
    "variable_stats": TRAIT_VARIABLE_STATS,
    "top_heavy": TRAIT_TOP_HEAVY,
    "color_indicator": TRAIT_COLOR_INDICATOR,
}


def _members(comma_text):
    return {m.strip().upper() for m in str(comma_text or "").split(",") if m.strip()}


def _expected_flags(row):
    """Rebuild trait_flags from facet_index._trait_filter_keys (independent path)."""
    keys = _trait_filter_keys(row)
    flags = 0
    for name, bit in _TRAIT_BITS.items():
        if name in keys:
            flags |= bit
    return flags


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

        # M1b: colour bitmasks and packed trait flags, checked per row against
        # independent semantics (comma membership; the facet filter classifier).
        mask_cols = ("colors", "colors_mask", "color_identity", "identity_mask",
                     "produced_mana", "produced_mask")
        mask_ok = True
        colors_seen = identity_seen = produced_seen = 0
        for colors, cmask, identity, imask, produced, pmask in reader.execute(
                "SELECT %s FROM cards" % ", ".join(mask_cols)):
            for text, mask in ((colors, cmask), (identity, imask),
                               (produced, pmask)):
                members = _members(text)
                for color, bit in COLOR_BITS.items():
                    if bool(mask & bit) != (color in members):
                        mask_ok = False
            colors_seen += 1 if cmask else 0
            identity_seen += 1 if imask else 0
            produced_seen += 1 if pmask else 0

        trait_cols = ("mana_cost", "power", "toughness", "card_faces",
                      "color_indicator", "universes_beyond", "reserved",
                      "game_changer", "trait_flags")
        flags_ok = True
        flags_seen = 0
        for row in reader.execute(
                "SELECT %s FROM cards" % ", ".join(trait_cols)):
            record = dict(zip(trait_cols, row))
            stored = record["trait_flags"] or 0
            if _expected_flags(record) != stored:
                flags_ok = False
            flags_seen += 1 if stored else 0

        checks = {
            "corpus built with rows": total > 0,
            "content_kind is populated for every row": populated == total,
            "content_kind equals CARD_CONTENT_KIND for every row": mismatches == 0,
            "content_kind covers multiple scopes (card/token/emblem/art)": (
                {"card", "token", "emblem", "art"} <= distinct_kinds),
            "content_kind is indexed": index_present,
            "colour bitmasks match comma membership for every row": mask_ok,
            "bitmasks are actually populated (not all zero)": (
                colors_seen > 0 and identity_seen > 0 and produced_seen > 0),
            "trait_flags match the filter classifier for every row": flags_ok,
            "trait_flags are actually populated (not all zero)": flags_seen > 0,
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
