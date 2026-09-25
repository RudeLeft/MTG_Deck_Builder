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
    TRAIT_VARIABLE_STATS, _type_key)
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

        # A "compleated" hybrid-Phyrexian symbol ({G/U/P}) is both a genuine
        # hybrid colour choice AND a Phyrexian life-payment option in one
        # symbol. A prior substring check ("/P" present => not hybrid) wrongly
        # excluded it from hybrid_mana; this asserts the fix directly against
        # the live SQL query and the stored trait_flags, not just that the two
        # independent implementations agree with each other (which they could
        # do on the same wrong answer).
        from mtgdb.database.semantics import TRAIT_HYBRID_MANA, TRAIT_PHYREXIAN_MANA
        from mtgdb.search.models import SearchCriteria
        compleated_flags = reader.execute(
            "SELECT trait_flags FROM cards WHERE id = 'compleated-0'"
        ).fetchone()[0]
        compleated_flags_correct = (
            bool(compleated_flags & TRAIT_HYBRID_MANA)
            and bool(compleated_flags & TRAIT_PHYREXIAN_MANA))
        hybrid_ids = {
            card_id for (card_id,) in reader.execute(
                "SELECT id FROM cards WHERE (trait_flags & ?) != 0",
                (TRAIT_HYBRID_MANA,))}
        compleated_in_hybrid_column = "compleated-0" in hybrid_ids
        hybrid_search_ids = {
            row[0] for row in harness.db.search_projection(
                connection=reader, columns=("id",),
                **SearchCriteria(
                    content_types=("card",), mana_features=("hybrid_mana",),
                ).query_arguments())[1]}
        compleated_in_hybrid_search = "compleated-0" in hybrid_search_ids

        # M1c: type/subtype/keyword membership tables reproduce the per-row
        # CARD_HAS_TYPE / CARD_HAS_SUBTYPE functions and the keyword filter,
        # checked as a full cross-product of every card against every vocab term.
        def in_table(table, card_id, term):
            return reader.execute(
                "SELECT 1 FROM %s WHERE card_id=? AND term=?" % table,
                (card_id, term)).fetchone() is not None

        card_rows = reader.execute(
            "SELECT id, type_line, keywords FROM cards").fetchall()
        type_vocab = list(harness.vocab["card_types"]) + list(
            harness.vocab["supertypes"])
        subtype_vocab = list(harness.vocab["subtypes"])
        keyword_vocab = list(harness.vocab["keywords"])
        types_ok = subtypes_ok = keywords_ok = True
        type_rows = reader.execute("SELECT COUNT(*) FROM card_types").fetchone()[0]
        subtype_rows = reader.execute(
            "SELECT COUNT(*) FROM card_subtypes").fetchone()[0]
        keyword_rows = reader.execute(
            "SELECT COUNT(*) FROM card_keywords").fetchone()[0]
        for card_id, type_line, keywords_json in card_rows:
            for value in type_vocab:
                term = " ".join(_type_key(value).split())
                want = reader.execute(
                    "SELECT CARD_HAS_TYPE(?, ?)", (type_line, value)
                ).fetchone()[0] == 1
                if in_table("card_types", card_id, term) != want:
                    types_ok = False
            for value in subtype_vocab:
                term = " ".join(_type_key(value).split())
                want = reader.execute(
                    "SELECT CARD_HAS_SUBTYPE(?, ?)", (type_line, value)
                ).fetchone()[0] == 1
                if in_table("card_subtypes", card_id, term) != want:
                    subtypes_ok = False
            for value in keyword_vocab:
                term = str(value).strip().casefold()
                want = reader.execute(
                    "SELECT EXISTS(SELECT 1 FROM json_each(COALESCE(?, '[]')) v "
                    "WHERE v.value = ? COLLATE NOCASE)", (keywords_json, value)
                ).fetchone()[0] == 1
                if in_table("card_keywords", card_id, term) != want:
                    keywords_ok = False

        membership_indexes = {
            row[0] for row in reader.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
                "('idx_card_types_term','idx_card_subtypes_term',"
                "'idx_card_keywords_term')")}

        # An in-place (replace=False) load INSERT-OR-REPLACEs the card row, so
        # it must also drop the card's old membership or a changed type would
        # keep matching its stale terms alongside the new ones.
        from mtgdb.database.db import CardDB
        incremental = CardDB(str(Path(harness.workspace) / "incremental.db"))
        try:
            printing = dict(set_code="tst", set_type="core", rarity="common",
                            released="2020-01-01")
            incremental.load_cards([
                H._card("inc-1", "Shape Shifter", "Creature — Bear", **printing)])
            incremental.load_cards([
                H._card("inc-1", "Shape Shifter", "Artifact", **printing)],
                replace=False)
            inc_reader = incremental.open_reader()
            try:
                inc_terms = {row[0] for row in inc_reader.execute(
                    "SELECT term FROM card_types WHERE card_id = 'inc-1'")}
            finally:
                inc_reader.close()
        finally:
            incremental.close()

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
            "a compleated hybrid-Phyrexian symbol sets both trait_flags bits":
                compleated_flags_correct,
            "the hybrid_mana trait_flags bit includes a compleated symbol":
                compleated_in_hybrid_column,
            "a live Mana Features=Hybrid search includes a compleated symbol":
                compleated_in_hybrid_search,
            "card_types membership matches CARD_HAS_TYPE for every card/term":
                types_ok,
            "card_subtypes membership matches CARD_HAS_SUBTYPE for every card/term":
                subtypes_ok,
            "card_keywords membership matches the keyword filter for every card/term":
                keywords_ok,
            "membership tables are populated": (
                type_rows > 0 and subtype_rows > 0 and keyword_rows > 0),
            "membership term indexes exist": len(membership_indexes) == 3,
            "an in-place load drops a card's stale membership terms": (
                "artifact" in inc_terms and "creature" not in inc_terms),
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
