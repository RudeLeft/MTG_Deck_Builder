"""Differential-test harness for the Search/database re-architecture (Option C).

This is the safety net for the staged rework: it builds a small but semantically
rich card database (multiple printings per card, every filterable trait), a
deterministic battery of randomized ``SearchCriteria``, and a *reference* capture
of today's canonical semantics -- ``count_search`` for the match count, the
ordered ``search_projection`` ids for the result order, and the SQLite context
worker for the contextual counts.

Every milestone that adds a new query path (new SQL, new in-memory engine) runs
that new path through :func:`capture` over the same battery and compares it to
the reference with :func:`compare`, so a change is proven byte-identical to
today's behavior before any old code is retired. Nothing here depends on the
real 323 MB database; the differential is old-path-vs-new-path on this fixture.

Not a ``test_*`` module: it is imported by the milestone tests, which own the
assertions.
"""

from __future__ import annotations

import atexit
import os
import random
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


# The context snapshot fields compared field-by-field.
CONTEXT_FIELDS = (
    "result_count", "card_type_counts", "supertype_counts", "subtype_counts",
    "keyword_counts", "color_counts", "produces_counts", "layout_counts",
    "rarity_counts", "numeric_ranges", "numeric_applicability", "release_years",
    "release_year_counts", "trait_counts", "mana_feature_counts",
    "special_property_counts", "status_property_counts", "pip_counts",
    "content_counts", "game_counts", "set_type_counts", "set_counts",
    "format_counts", "english_count", "all_language_count",
)


def _card(cid, name, type_line, *, set_code, set_type, rarity, released, lang="en",
          oracle_text="", games=("paper",), legalities=None, **extra):
    """One Scryfall-shaped printing dict for the importer."""
    row = {
        "object": "card", "id": cid, "name": name, "type_line": type_line,
        "oracle_text": oracle_text, "lang": lang, "games": list(games),
        "set": set_code, "set_name": set_code.upper(), "set_type": set_type,
        "collector_number": cid.rsplit("-", 1)[-1], "rarity": rarity,
        "released_at": released,
        "legalities": legalities or {"modern": "legal", "commander": "legal"},
        "image_uris": {"png": "https://img/%s.png" % cid},
    }
    row.update(extra)
    return row


def _printings(base, name, type_line, prints, **shared):
    """Expand one card into several printings that differ only by printing data.

    ``prints`` is a list of (set_code, set_type, rarity, released, lang) tuples;
    the oracle-level fields in ``shared`` (colors, cmc, keywords, ...) are copied
    to every printing, which is exactly the printing/oracle split the search
    must treat consistently.
    """
    rows = []
    for index, (set_code, set_type, rarity, released, lang) in enumerate(prints):
        cid = "%s-%d" % (base, index)
        rows.append(_card(cid, name, type_line, set_code=set_code,
                          set_type=set_type, rarity=rarity, released=released,
                          lang=lang, **shared))
    return rows


def build_corpus():
    """Return the fixed battery corpus: many cards, several with reprints."""
    rows = []
    add = rows.extend

    add(_printings("bear", "Grizzly Bear", "Creature — Bear", [
        ("lea", "core", "common", "2015-01-01", "en"),
        ("m19", "core", "common", "2018-07-13", "en"),
        ("m19", "core", "common", "2018-07-13", "ja")],
        cmc=2.0, mana_cost="{1}{G}", colors=["G"], color_identity=["G"],
        power="2", toughness="1", keywords=["Trample"],
        produced_mana=[], oracle_text="Trample."))

    add(_printings("bolt", "Lightning Bolt", "Instant", [
        ("lea", "core", "common", "2015-01-01", "en"),
        ("m10", "core", "common", "2016-07-15", "en"),
        ("2xm", "masters", "uncommon", "2020-08-07", "en"),
        ("sld", "promo", "rare", "2021-03-01", "en")],
        cmc=1.0, mana_cost="{R}", colors=["R"], color_identity=["R"],
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        legalities={"modern": "legal", "legacy": "legal", "commander": "legal",
                    "standard": "not_legal"}))

    add(_printings("counter", "Counterspell", "Instant", [
        ("lea", "core", "common", "2015-01-01", "en"),
        ("mh2", "masters", "uncommon", "2021-06-18", "en")],
        cmc=2.0, mana_cost="{U}{U}", colors=["U"], color_identity=["U"],
        oracle_text="Counter target spell.",
        legalities={"legacy": "legal", "commander": "legal"}))

    add(_printings("elf", "Llanowar Elves", "Creature — Elf Druid", [
        ("lea", "core", "common", "2015-01-01", "en"),
        ("dom", "expansion", "common", "2018-04-27", "en"),
        ("m19", "core", "common", "2018-07-13", "de")],
        cmc=1.0, mana_cost="{G}", colors=["G"], color_identity=["G"],
        power="1", toughness="1", produced_mana=["G"],
        oracle_text="{T}: Add {G}."))

    add(_printings("wrath", "Wrath of God", "Sorcery", [
        ("lea", "core", "rare", "2015-01-01", "en"),
        ("dmr", "masters", "rare", "2022-01-28", "en")],
        cmc=4.0, mana_cost="{2}{W}{W}", colors=["W"], color_identity=["W"],
        oracle_text="Destroy all creatures. They can't be regenerated.",
        legalities={"modern": "legal", "commander": "legal", "pioneer": "banned"}))

    add(_printings("teferi", "Teferi, Time Raveler",
                   "Legendary Planeswalker — Teferi", [
        ("war", "expansion", "rare", "2019-05-03", "en"),
        ("war", "expansion", "mythic", "2019-05-03", "ja")],
        cmc=3.0, mana_cost="{1}{W}{U}", colors=["W", "U"],
        color_identity=["W", "U"], loyalty="4", game_changer=True,
        oracle_text="Each opponent can cast spells only any time they could "
                    "cast a sorcery. Draw a card.",
        legalities={"modern": "banned", "pioneer": "banned", "commander": "legal"}))

    add(_printings("saga", "Urza's Saga", "Enchantment Land — Urza's Saga", [
        ("mh2", "masters", "rare", "2021-06-18", "en")],
        cmc=0.0, colors=[], color_identity=[], produced_mana=["C"],
        oracle_text="(As this Saga enters and after your draw step, add a lore "
                    "counter.)"))

    add(_printings("hybrid", "Boros Charm", "Instant", [
        ("gtc", "expansion", "uncommon", "2015-02-01", "en"),
        ("2xm", "masters", "uncommon", "2020-08-07", "en")],
        cmc=2.0, mana_cost="{R/W}{R/W}", colors=["R", "W"],
        color_identity=["R", "W"], oracle_text="Choose one — deal 4 damage."))

    add(_printings("phyrexian", "Gut Shot", "Instant", [
        ("nph", "expansion", "common", "2015-05-01", "en")],
        cmc=1.0, mana_cost="{R/P}", colors=["R"], color_identity=["R"],
        oracle_text="Gut Shot deals 1 damage to any target."))

    # A "compleated" hybrid-Phyrexian symbol ({W/U/P}, from March of the
    # Machine) is both a genuine hybrid choice between two colours AND a
    # Phyrexian life-payment option in one symbol -- distinct from a plain
    # Phyrexian symbol like Gut Shot's {R/P} above, which pairs one colour
    # with life and is not itself a hybrid choice.
    add(_printings("compleated", "Tyvar, Jubilant Brawler",
                   "Legendary Planeswalker — Tyvar", [
        ("mom", "expansion", "mythic", "2023-04-21", "en")],
        cmc=3.0, mana_cost="{1}{G/U/P}{G/U/P}", colors=["G", "U"],
        color_identity=["G", "U"], loyalty="3",
        oracle_text="Compleated. Creatures you control get +1/+1."))

    add(_printings("xspell", "Fireball", "Sorcery", [
        ("lea", "core", "common", "2015-01-01", "en"),
        ("ema", "masters", "rare", "2016-06-10", "en")],
        cmc=1.0, mana_cost="{X}{R}", colors=["R"], color_identity=["R"],
        oracle_text="Fireball deals X damage divided among any number of targets."))

    add(_printings("hydra", "Hydra Star", "Creature — Hydra", [
        ("m20", "core", "rare", "2019-07-12", "en")],
        cmc=4.0, mana_cost="{2}{G}{G}", colors=["G"], color_identity=["G"],
        power="*", toughness="*",
        oracle_text="Hydra Star's power and toughness are each equal to the "
                    "number of +1/+1 counters on it."))

    add(_printings("topheavy", "Ogre Brute", "Creature — Ogre Warrior", [
        ("m15", "core", "common", "2015-07-18", "en")],
        cmc=4.0, mana_cost="{3}{R}", colors=["R"], color_identity=["R"],
        power="4", toughness="2", oracle_text=""))

    add(_printings("ub", "Optimus Prime", "Legendary Creature — Robot", [
        ("bot", "expansion", "mythic", "2023-06-30", "en")],
        cmc=5.0, mana_cost="{3}{R}{W}", colors=["R", "W"],
        color_identity=["R", "W"], power="4", toughness="8",
        keywords=["Flying"], promo_types=["universesbeyond"],
        oracle_text="Flying. More than meets the eye."))

    add(_printings("battle", "Invasion of Test", "Battle — Siege", [
        ("mom", "expansion", "rare", "2023-04-21", "en")],
        cmc=4.0, mana_cost="{2}{R}{R}", colors=["R"], color_identity=["R"],
        defense="4", oracle_text="When this Battle enters, it deals 4 damage."))

    add(_printings("indicator", "Devoid Horror", "Creature — Eldrazi Horror", [
        ("bfz", "expansion", "uncommon", "2015-10-02", "en")],
        cmc=3.0, mana_cost="{2}{C}", colors=[], color_identity=[],
        color_indicator=["C"], power="3", toughness="2",
        oracle_text="Devoid."))

    add(_printings("reserved", "Old Power", "Enchantment", [
        ("leg", "expansion", "rare", "2015-01-01", "en")],
        cmc=3.0, mana_cost="{1}{W}{W}", colors=["W"], color_identity=["W"],
        reserved=True, oracle_text="At the beginning of your upkeep, gain 1 life."))

    add(_printings("dfc", "Delver of Secrets", "Creature — Human Wizard", [
        ("isd", "expansion", "common", "2015-09-30", "en")],
        cmc=1.0, mana_cost="{U}", colors=["U"], color_identity=["U"],
        power="1", toughness="1", layout="transform",
        card_faces=[{"name": "Delver of Secrets", "type_line": "Creature — Human Wizard",
                     "oracle_text": "At the beginning of your upkeep, look at the top card."},
                    {"name": "Insectile Aberration", "type_line": "Creature — Human Insect",
                     "oracle_text": "Flying."}],
        oracle_text="At the beginning of your upkeep, look at the top card."))

    add(_printings("arena", "Digital Only", "Creature — Construct", [
        ("hbg", "expansion", "rare", "2022-06-10", "en")],
        cmc=3.0, mana_cost="{3}", colors=[], color_identity=[],
        power="3", toughness="3", games=["arena", "mtgo"],
        oracle_text="Perpetually gain +1/+1."))

    # Non-card object classes so content-scope filters have something to move.
    add(_printings("token", "Servo", "Token Artifact Creature — Servo", [
        ("kld", "expansion", "common", "2016-09-30", "en")],
        colors=[], color_identity=[], power="1", toughness="1", layout="token",
        oracle_text=""))
    add(_printings("emblem", "Teferi Emblem", "Emblem", [
        ("war", "expansion", "common", "2019-05-03", "en")],
        colors=[], color_identity=[], layout="emblem", oracle_text=""))
    add(_printings("art", "Bear Art Card", "Card", [
        ("mh1", "masters", "common", "2019-06-14", "en")],
        colors=[], color_identity=[], layout="art_series", oracle_text=""))

    return rows


# -- fixture construction -------------------------------------------------

class Harness:
    """Holds the built DB, repository, reader, facet index, worker, and vocab."""

    def __init__(self):
        self.workspace = tempfile.mkdtemp(prefix="mtg-searchdiff-")
        atexit.register(shutil.rmtree, self.workspace, ignore_errors=True)
        self.db = CardDB(os.path.join(self.workspace, "cards.db"))
        self.db.load_cards(build_corpus())
        self.repo = SearchRepository(self.db)
        self.reader = self.db.open_reader()
        # Fetch id alongside the index columns in one scan so bit position i in a
        # facet bitset maps to id_order[i] in the exact same row order the index
        # was built from. FacetIndex ignores the extra id key.
        raw = [dict(r) for r in self.reader.execute(
            "SELECT id, " + ", ".join(FacetIndex.INDEX_COLUMNS) + " FROM cards")]
        self.id_order = [row["id"] for row in raw]
        self.index = FacetIndex(raw)
        self.ctrl = SearchContextController(self.repo)
        self.ctrl._facet_index_disabled = True   # force the SQLite reference path
        self.vocab = self.ctrl._vocabulary_payload(**self._raw_vocab())

    def _raw_vocab(self):
        repo = self.repo
        scope = ["card", "token", "emblem", "art"]
        return dict(
            card_types=list(repo.card_types(scope)),
            supertypes=list(repo.supertypes(scope)),
            subtypes=repo.subtype_catalog(scope),
            keywords=repo.keyword_catalog(scope),
            layouts=[v for v, _ in repo.layouts(scope)],
            rarities=list(repo.rarities(scope)),
            formats=list((repo.formats_by_status(scope) or {}).get("playable") or ()),
            set_types=[s[0] for s in repo.set_types(scope)],
            sets=repo.sets(content_types=scope))

    def facet_result_ids(self, crit):
        """The matching card-id set from the facet index, or None if it declines.

        Bit position i in the filter bitset maps to id_order[i], so this is the
        independent (bitset) golden result set the SQL query path is checked
        against.
        """
        bitset = self.index.filter_bitset(crit)
        if bitset is None:
            return None
        ids = set()
        while bitset:
            low = bitset & -bitset
            ids.add(self.id_order[low.bit_length() - 1])
            bitset ^= low
        return ids

    def close(self):
        try:
            self.ctrl.shutdown()
        except Exception:
            pass
        try:
            self.reader.close()
        except Exception:
            pass


# -- criteria battery -----------------------------------------------------

def _vocab_lists(harness):
    v = harness.vocab
    return {
        "card_types": [str(x) for x in v["card_types"]],
        "supertypes": [str(x) for x in v["supertypes"]],
        "subtypes": [str(x) for x in v["subtypes"]],
        "keywords": [str(x) for x in v["keywords"]],
        "layouts": [str(x) for x in v["layouts"]],
        "rarities": [str(x) for x in v["rarities"]],
        "formats": [str(x) for x in v["formats"]],
        "set_types": [str(x) for x in v["set_types"]],
        "sets": [str(s[0]) for s in v["sets"]],
    }


_COLORS = ("W", "U", "B", "R", "G")
_TEXT_WORDS = ("draw", "damage", "creature", "token", "flying", "destroy",
               "counter", "target", "add", "life", '"deals 3 damage"',
               "draw a card")


def generate_criteria(harness, seed=1234, n=240):
    """Return a deterministic, diverse battery of ``SearchCriteria``.

    Each criteria activates 1-4 random facets drawn from the corpus's own
    vocabulary so counts genuinely move, and the battery deliberately includes
    the representable filters (types, colors, text, ranges, format, pip presence,
    printing filters) plus a handful of pip-minimum filters that must still fall
    back, so both paths are exercised.
    """
    rng = random.Random(seed)
    vocab = _vocab_lists(harness)
    battery = []

    def pick(items, lo=1, hi=2):
        if not items:
            return ()
        k = rng.randint(lo, min(hi, len(items)))
        return tuple(rng.sample(items, k))

    dimensions = [
        lambda c: c.update(card_types=pick(vocab["card_types"]),
                           card_type_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(supertypes=pick(vocab["supertypes"]),
                           supertype_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(subtypes=pick(vocab["subtypes"]),
                           subtype_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(keywords=pick(vocab["keywords"]),
                           keyword_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(colors=pick(list(_COLORS), 1, 3),
                           color_mode=rng.choice(("within", "exact", "includes")),
                           color_scope=rng.choice(("identity", "colors"))),
        lambda c: c.update(produces=pick(list(_COLORS) + ["C"], 1, 2),
                           produces_mode=rng.choice(("includes", "exact", "within"))),
        lambda c: c.update(mana_features=pick(
            ["hybrid_mana", "phyrexian_mana", "has_x_cost"], 1, 2),
            mana_feature_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(special_properties=pick(
            ["multi_faced", "single_faced", "variable_stats", "top_heavy",
             "color_indicator"], 1, 2),
            special_property_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(status_properties=pick(
            ["universes_beyond", "not_universes_beyond", "reserved",
             "game_changer"], 1, 2),
            status_property_mode=rng.choice(("any", "all", "none"))),
        lambda c: c.update(layouts=pick(vocab["layouts"]),
                           layout_mode=rng.choice(("any", "none"))),
        lambda c: c.update(rarities=pick(vocab["rarities"], 1, 2)),
        lambda c: c.update(fmt=rng.choice(vocab["formats"] or [""]),
                           fmt_status=rng.choice(("playable", "banned", "restricted"))),
        lambda c: c.update(cmc_min=float(rng.randint(0, 3)),
                           cmc_max=float(rng.randint(3, 8))),
        lambda c: c.update(power_min=float(rng.randint(0, 2)),
                           power_max=float(rng.randint(2, 6))),
        lambda c: c.update(toughness_max=float(rng.randint(1, 6))),
        lambda c: c.update(text=pick(list(_TEXT_WORDS), 1, 2),
                           text_mode=rng.choice(("all", "any", "none"))),
        lambda c: c.update(name=rng.choice(("bear", "bolt", "a", "e", "of"))),
        lambda c: c.update(pips=pick(list(_COLORS), 1, 2),
                           pip_mode=rng.choice(("any", "all", "none"))),
        # The Minimum box's own default (1) and the values the SQL clamps to it.
        lambda c: c.update(pips=pick(list(_COLORS), 1, 2),
                           pip_mode=rng.choice(("any", "all", "none")),
                           pip_min=rng.choice((1.0, 0.0, 1.5))),
        lambda c: c.update(pips=pick(list(_COLORS), 1, 2), pip_min=2),  # fallback
        lambda c: c.update(set_types=pick(vocab["set_types"], 1, 2)),
        lambda c: c.update(set_codes=pick(vocab["sets"], 1, 2)),
        lambda c: c.update(rarities=pick(vocab["rarities"], 1, 1),
                           lang=rng.choice(("en", "ja", "de"))),
        lambda c: c.update(games=pick(["paper", "mtgo", "arena"], 1, 2)),
        lambda c: c.update(released_from=float(rng.randint(2015, 2019)),
                           released_to=float(rng.randint(2019, 2023))),
    ]

    contents = [("card",), ("card", "token"), ("card", "token", "emblem", "art"),
                ("token",), ("card",), ("card",)]

    # The real UI carries pip_min=1.0 on every request (the Minimum box's
    # default), so a share of the battery starts from it -- drawn from its own
    # stream so the rest of the battery is unchanged.  Dimensions applied later
    # may still override it (the pip_min=2 fallback case does).
    ui_default_rng = random.Random(seed + 1)

    for _ in range(n):
        data = {"content_types": rng.choice(contents)}
        if ui_default_rng.random() < 0.5:
            data["pip_min"] = 1.0
        for mutate in rng.sample(dimensions, rng.randint(1, 4)):
            mutate(data)
        battery.append(SearchCriteria.from_mapping(data))
    return battery


# -- captures + comparison ------------------------------------------------

def reference_capture(harness, crit):
    """Today's canonical semantics: count_search, ordered ids, SQLite context."""
    count = harness.repo.count(crit, harness.reader)
    _cols, rows = harness.db.search_projection(
        connection=harness.reader, columns=("id",), **crit.query_arguments())
    ids = [row[0] for row in rows]
    harness.ctrl._generation += 1
    snap = harness.ctrl._prepare(harness.ctrl._generation, crit, harness.vocab)
    context = {field: _norm(getattr(snap, field)) for field in CONTEXT_FIELDS}
    return {"count": count, "ids": ids, "context": context}


def fast_capture(harness, crit):
    """Current in-memory fast path (facet index): count + context, or None."""
    bitset = harness.index.filter_bitset(crit)
    context = harness.index.context_counts(crit, harness.vocab)
    if bitset is None or context is None:
        return None
    return {
        "count": harness.index.popcount(bitset),
        "context": {field: _norm(context[field]) for field in CONTEXT_FIELDS},
    }


def _norm(value):
    """Normalize a context field for comparison (dicts/tuples compare by value)."""
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return value


def compare_context(reference, candidate):
    """Return a list of mismatching field names between two context dicts."""
    bad = []
    for field in CONTEXT_FIELDS:
        if reference.get(field) != candidate.get(field):
            bad.append(field)
    return bad
