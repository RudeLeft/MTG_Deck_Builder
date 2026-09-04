"""Deck model, TXT I/O, analysis, legality, and facade contracts."""

import ast
import json
import math
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.analysis import (
    average_mana_value, card_draw_odds, classify_type, color_pips,
    color_sources, curve_breakdown, deck_stats, hyper_at_least, hyper_between,
    opening_land_stats, sample_hand,
)
from mtgdb.deck.io import deck_from_text, deck_to_text
from mtgdb.deck.legality import (
    _card_copy_limit,
    _oracle_text as _legality_oracle_text,
)
from mtgdb.deck.legality import legality_problems
from mtgdb.deck.model import BOARDS, Deck


def _card(card_id, name, *, oracle_id=None, type_line="Creature",
          cmc=2, mana_cost="{1}{G}", identity="G", produced="",
          set_code="tst", collector="1", legal="legal"):
    return {
        "id": card_id,
        "oracle_id": oracle_id or f"oracle-{card_id}",
        "name": name,
        "type_line": type_line,
        "cmc": cmc,
        "mana_cost": mana_cost,
        "color_identity": identity,
        "produced_mana": produced,
        "set_code": set_code,
        "set_name": set_code.upper(),
        "collector_number": collector,
        "legalities": {"modern": legal, "commander": legal, "vintage": legal},
    }


class Resolver:
    def __init__(self, cards):
        self.cards = list(cards)
        self.calls = []

    def get_by_name(self, name, **options):
        self.calls.append((name, options))
        candidates = [card for card in self.cards if card["name"] == name]
        set_codes = options.get("allowed_set_codes")
        if set_codes is not None:
            candidates = [
                card for card in candidates
                if card.get("set_code") in set_codes]
        collectors = options.get("allowed_collector_numbers")
        if collectors is not None:
            candidates = [
                card for card in candidates
                if card.get("collector_number") in collectors]
        return candidates[0] if candidates else None


def _import_roots(source):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def main():
    first = _card(
        "spell-a", "Hybrid Spell", oracle_id="oracle-spell",
        mana_cost="{W/U}{2/G}", identity=["W", "U"], collector="10")
    second = _card(
        "spell-b", "Hybrid Spell", oracle_id="oracle-spell",
        mana_cost="{W/U}{2/G}", identity="W,U", set_code="alt",
        collector="44")
    land = _card(
        "land", "Test Forest", type_line="Basic Land — Forest", cmc=0,
        mana_cost="", identity="G", produced=["G"], collector="2")
    artifact = _card(
        "rock", "Mana Rock", type_line="Artifact", cmc=3,
        mana_cost="{3}", identity="", produced="W,U", collector="3")
    banned = _card(
        "banned", "Banned Card", legal="banned", collector="4")

    model = Deck("Domain Test", "modern")
    model.add(first, "main", 2)
    model.add(second, "main", 3)
    model.add(land, "main", 10)
    model.add(artifact, "side", 2)
    model.change_qty("spell-a", "main", 1)
    model.move("rock", "side", "main")
    model.remove("missing", "main")

    stats = deck_stats(model)
    method_stats = model.stats()
    pips = color_pips(model)
    sources, source_total = color_sources(model)
    type_labels, type_curve = curve_breakdown(model, "type")
    color_labels, color_curve = curve_breakdown(model, "color")
    lands = opening_land_stats(model)
    average_mv = average_mana_value(model)
    draw_odds = card_draw_odds(model, "Hybrid Spell")
    hand = sample_hand(model, 7)

    serialized = deck_to_text(model)
    method_serialized = model.to_text()
    resolver = Resolver([first, second, land, artifact, banned])
    imported, missing = deck_from_text(serialized, resolver)
    method_imported, method_missing = Deck.from_text(serialized, resolver)
    sectioned, section_missing = deck_from_text(
        "// Sections (modern)\n"
        "Mainboard:\n1 Hybrid Spell [TST:10]\n"
        "Sideboard:\n2 Mana Rock [TST:3]\n"
        "Maybeboard:\n4 Banned Card [TST:4]\n"
        "Main:\n1 Missing Card [TST:999]\n",
        resolver, allowed_set_types={"expansion"},
        allowed_set_codes={"alt"})

    # Importer robustness: a "0 Cardname" line must not abort the whole file,
    # and a bare unquantified line must import as one copy instead of vanishing.
    tolerant, tolerant_missing = deck_from_text(
        "// Tolerant (modern)\n"
        "4 Hybrid Spell [TST:10]\n"
        "0 Mana Rock [TST:3]\n"
        "2 Mana Rock [TST:3]\n",
        resolver)
    bare, bare_missing = deck_from_text(
        "// Bare (modern)\n"
        "Creatures (2)\n"
        "Hybrid Spell\n"
        "Mana Rock\n"
        "3 Banned Card\n"
        "2 Totally Absent Card\n",
        resolver)

    # card_faces arrives as JSON text from the database; iterating the raw
    # column walks characters and silently drops every face's rules text.
    faced_card = _card(
        "faced-1", "Faced Card // Faced Back", type_line="Creature // Creature")
    faced_card["oracle_text"] = ""
    faced_card["card_faces"] = json.dumps([
        {"name": "Faced Card",
         "oracle_text":
             "A deck can have any number of cards named "
             "Faced Card // Faced Back."},
        {"name": "Faced Back", "oracle_text": "Trample."},
    ])
    faced_limit = _card_copy_limit(faced_card, 4)
    faced_text = _legality_oracle_text(faced_card)

    # _FORMAT_RULES copy limits drive user-facing legality verdicts, so pin the
    # boundary itself: four copies are legal in Constructed and five are not.
    standard_legal = Deck("Standard Legal", "standard")
    standard_legal.add(first, "main", 4)
    standard_four_problems = [
        problem for problem in legality_problems(standard_legal)
        if "copies" in problem]
    standard_over = Deck("Standard Over", "standard")
    standard_over.add(first, "main", 5)
    standard_five_problems = [
        problem for problem in legality_problems(standard_over)
        if "copies" in problem]
    # Singleton formats must reject a second copy through the same table.
    singleton = Deck("Singleton", "commander")
    singleton.add(first, "main", 2)
    singleton_problems = [
        problem for problem in legality_problems(singleton)
        if "copies" in problem]

    # Deck.add must refuse non-positive quantities: a zero-quantity entry would
    # count toward unique() but not total() and serialize as "0 Cardname".
    def _rejects_quantity(value):
        try:
            Deck().add(first, "main", value)
        except ValueError:
            return True
        return False

    quantity_guard = (
        _rejects_quantity(0)
        and _rejects_quantity(-1)
        and not _rejects_quantity(1))

    illegal = Deck("Illegal", "modern")
    illegal.add(first, "main", 3)
    illegal.add(second, "side", 2)
    illegal.add(banned, "side", 1)
    illegal_problems = legality_problems(illegal)
    commander = Deck("Commander", "commander")
    commander.add(first, "main", 2)
    commander.add(artifact, "side", 1)
    commander_problems = legality_problems(commander)

    brawl_basic = _card(
        "brawl-land", "Brawl Plains", type_line="Basic Land — Plains",
        mana_cost="", identity="W")
    brawl_basic["legalities"].update({
        "brawl": "legal", "competitivebrawl": "legal"})
    brawl_spell = _card("brawl-spell", "Brawl Spell")
    brawl_spell["legalities"].update({
        "brawl": "legal", "competitivebrawl": "legal"})
    brawl = Deck("Brawl", "brawl")
    brawl.add(brawl_basic, "main", 99)
    brawl.add(brawl_spell, "main", 1)
    brawl_problems = legality_problems(brawl)
    short_brawl = Deck("Short Brawl", "brawl")
    short_brawl.add(brawl_basic, "main", 59)
    short_brawl.add(brawl_spell, "main", 1)
    short_brawl_problems = legality_problems(short_brawl)
    duplicate_brawl = Deck("Duplicate Brawl", "brawl")
    duplicate_brawl.add(brawl_basic, "main", 98)
    duplicate_brawl.add(brawl_spell, "main", 2)
    duplicate_brawl_problems = legality_problems(duplicate_brawl)

    competitive_brawl = Deck("Competitive Brawl", "competitivebrawl")
    competitive_brawl.add(brawl_basic, "main", 99)
    competitive_brawl.add(brawl_spell, "main", 1)
    competitive_brawl_problems = legality_problems(competitive_brawl)

    unknown_format_card = _card("future-format", "Future Format Card")
    unknown_format_card["legalities"] = {"neoformat": "legal"}
    unknown_format = Deck("Future Format", "neoformat")
    unknown_format.add(unknown_format_card, "main", 4)
    unknown_format_problems = legality_problems(unknown_format)

    missing_status_land = _card(
        "missing-land", "Missing Plains", type_line="Basic Land — Plains",
        mana_cost="", identity="W")
    missing_status_land["legalities"] = {}
    missing_status = Deck("Missing Status", "modern")
    missing_status.add(missing_status_land, "main", 60)
    missing_status_problems = legality_problems(missing_status)

    any_number = _card("rats", "Test Rats")
    any_number["oracle_text"] = (
        "A deck can have any number of cards named Test Rats.")
    any_number_deck = Deck("Any Number", "modern")
    any_number_deck.add(any_number, "main", 60)
    any_number_problems = legality_problems(any_number_deck)

    capped = _card("seven", "Test Seven")
    capped["oracle_text"] = (
        "A deck can have up to seven cards named Test Seven.")
    capped_land = _card(
        "cap-land", "Cap Plains", type_line="Basic Land — Plains",
        mana_cost="", identity="W")
    capped_ok = Deck("Capped OK", "modern")
    capped_ok.add(capped, "main", 7)
    capped_ok.add(capped_land, "main", 53)
    capped_ok_problems = legality_problems(capped_ok)
    capped_bad = Deck("Capped Bad", "modern")
    capped_bad.add(capped, "main", 8)
    capped_bad.add(capped_land, "main", 52)
    capped_bad_problems = legality_problems(capped_bad)

    sources_by_name = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/deck/model.py", "mtgdb/deck/io.py", "mtgdb/deck/analysis.py",
            "mtgdb/deck/legality.py", "mtgdb/ui/app.py", "mtgdb/ui/deck.py",
            "mtgdb/ui/deck_files.py", "mtgdb/ui/deck_stats.py")
    }
    imports = {
        name: _import_roots(source) for name, source in sources_by_name.items()
    }

    checks = {
        "face rules text reaches the copy-limit engine through JSON columns": (
            faced_limit is None and "Trample." in faced_text),
        "zero-quantity line skips one entry without aborting the import": (
            tolerant.total("main") == 6 and not tolerant_missing),
        "bare unquantified lines import as one copy": (
            bare.total("main") == 5),
        "unresolved bare lines stay quiet while quantified typos report": (
            bare_missing == ["Totally Absent Card"]),
        "Deck owns exact-printing state and board mutations": (
            BOARDS == ("main", "side")
            and model.total("main") == 18 and model.total("side") == 0
            and model.unique("main") == 4
            and next(
                entry for entry in model.entries()
                if entry["card"]["id"] == "spell-a")["qty"] == 3),
        "Deck entries retain deterministic name order": (
            [entry["card"]["name"] for entry in model.entries()]
            == sorted(
                [entry["card"]["name"] for entry in model.entries()])),
        "Deck.stats delegates without changing results": (
            method_stats == stats
            and stats["main_total"] == 18
            and stats["side_total"] == 0
            and stats["curve"][2] == 6
            and stats["curve"][3] == 2),
        "mana pips and sources retain quantity semantics": (
            pips == {"W": 6, "U": 6, "B": 0, "R": 0, "G": 6}
            and sources == {"W": 2, "U": 2, "B": 0, "R": 0, "G": 10}
            and source_total == 12),
        "format copy limits come from the verified rule table": (
            standard_four_problems == []
            and len(standard_five_problems) == 1
            and "limit 4" in standard_five_problems[0]
            and len(singleton_problems) == 1
            and "limit 1" in singleton_problems[0]),
        "Deck.add refuses non-positive quantities": quantity_guard,
        "probability calculations retain exact formulas": (
            math.isclose(
                hyper_at_least(60, 4, 7),
                1 - math.comb(56, 7) / math.comb(60, 7))
            # Independent closed form, NOT a second call to the function
            # under test: comparing hyper_between against itself is a tautology
            # that cannot fail, which previously let an off-by-one in its
            # summation bounds through undetected.
            and math.isclose(
                hyper_between(18, 10, 7, 2, 4),
                sum(math.comb(10, k) * math.comb(8, 7 - k)
                    for k in range(2, 5)) / math.comb(18, 7))
            # The deck-level helper must still route through that formula.
            and math.isclose(lands[3], hyper_between(18, 10, 7, 2, 4))
            # Inclusive bounds: a single-value window is one exact term.
            and math.isclose(
                hyper_between(18, 10, 7, 3, 3),
                math.comb(10, 3) * math.comb(8, 4) / math.comb(18, 7))
            and hyper_at_least(0, 4, 7) == 0.0),
        "curve and opening-hand calculations remain complete": (
            type_labels[0] == "Creatures"
            and type_curve[2]["Creatures"] == 6
            and type_curve[3]["Artifacts"] == 2
            and color_labels[-1] == "Colorless"
            and color_curve[3]["Colorless"] == 2
            and lands[:3] == (18, 10, 70 / 18)
            and len(hand) == 7),
        "UI summary calculations remain in the analysis domain": (
            average_mv == 18 / 8
            and draw_odds["deck_size"] == 18
            and draw_odds["copies"] == 6
            and math.isclose(
                draw_odds["opening"], hyper_at_least(18, 6, 7))
            and math.isclose(
                draw_odds["turn_3"], hyper_at_least(18, 6, 9))
            and math.isclose(
                draw_odds["turn_6"], hyper_at_least(18, 6, 12))),
        "TXT serialization preserves exact printings and metadata": (
            method_serialized == serialized
            and serialized.startswith("// Domain Test (modern)\n")
            and "[TST:10]" in serialized and "[ALT:44]" in serialized),
        "TXT function and compatibility method round-trip identically": (
            not missing and not method_missing
            and imported.name == method_imported.name == "Domain Test"
            and imported.fmt == method_imported.fmt == "modern"
            and [(entry["card"]["id"], entry["qty"], entry["board"])
                 for entry in imported.entries()]
            == [(entry["card"]["id"], entry["qty"], entry["board"])
                for entry in method_imported.entries()]),
        "section and authoritative set-tag behavior is preserved": (
            sectioned.total("main") == 1 and sectioned.total("side") == 2
            and section_missing == ["Missing Card [TST:999]"]
            and all(
                call[1].get("allowed_set_types") is None
                for call in resolver.calls if call[0] in {
                    "Hybrid Spell", "Mana Rock", "Missing Card"}
                and call[1].get("allowed_set_codes") == {"tst"})),
        "legality aggregates printings and checks sideboard-only cards": (
            any("5 copies" in problem for problem in illegal_problems)
            and any("BANNED" in problem for problem in illegal_problems)
            and any("minimum is 60" in problem for problem in illegal_problems)),
        "singleton size, copies, and sideboard rules are preserved": (
            any("exactly 100" in problem for problem in commander_problems)
            and any("limit 1" in problem for problem in commander_problems)
            and any("no sideboard" in problem for problem in commander_problems)),
        "current Brawl is exactly 100 cards and singleton": (
            not brawl_problems
            and any("exactly 100" in problem for problem in short_brawl_problems)
            and any("limit 1" in problem for problem in duplicate_brawl_problems)),
        "Competitive Brawl uses the verified 100-card singleton profile": (
            not competitive_brawl_problems),
        "unknown formats fail closed instead of guessing Constructed rules": (
            len(unknown_format_problems) == 1
            and "construction rules are not verified" in unknown_format_problems[0]
            and not any("minimum is 60" in problem
                        for problem in unknown_format_problems)
            and not any("copies" in problem for problem in unknown_format_problems)),
        "missing Scryfall statuses are reported as unverified": (
            any("Scryfall legality status is unavailable" in problem
                for problem in missing_status_problems)),
        "Oracle copy-limit text overrides generic and singleton defaults": (
            not any("copies" in problem for problem in any_number_problems)
            and not any("copies" in problem for problem in capped_ok_problems)
            and any("limit 7" in problem for problem in capped_bad_problems)),
        "type classification priority is preserved": (
            classify_type("Artifact Creature — Golem") == "Creatures"
            and classify_type("Land Creature — Forest Dryad") == "Lands"),
        "all deck-domain modules are Tk and SQLite free": all(
            "tkinter" not in imports[name] and "sqlite3" not in imports[name]
            for name in (
                "mtgdb/deck/model.py", "mtgdb/deck/io.py", "mtgdb/deck/analysis.py",
                "mtgdb/deck/legality.py")),
        "model owns no parser, probability, randomness, or legality imports": (
            not ({"re", "json", "math", "random"} & imports["mtgdb/deck/model.py"])),
        "production UI imports each deck responsibility from its owner": (
            "from mtgdb.deck.model import Deck" in sources_by_name["mtgdb/ui/app.py"]
            and "from mtgdb.deck.io import deck_from_text, save_deck_text"
            in sources_by_name["mtgdb/ui/deck_files.py"]
            and "from mtgdb.deck.model import Deck" in sources_by_name["mtgdb/ui/deck.py"]
            and "from mtgdb.deck.analysis import" in sources_by_name["mtgdb/ui/deck_stats.py"]
            and "from mtgdb.deck.legality import legality_problems"
            in sources_by_name["mtgdb/ui/deck_stats.py"]
            and "from mtgdb.deck.io import" not in sources_by_name["mtgdb/ui/app.py"]
            and "from mtgdb.deck.analysis import" not in sources_by_name["mtgdb/ui/app.py"]
            and "from mtgdb.deck.legality import" not in sources_by_name["mtgdb/ui/app.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nDECK ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
