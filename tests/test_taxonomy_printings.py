"""Regression coverage for catalog parsing, unusual cards, and exact printings.

Run anywhere (no GUI/network needed): python tests/test_taxonomy_printings.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtgdb.deck.model import Deck
from mtgdb.database.db import CardDB
from mtgdb.database.semantics import _BFM_COMPLETE_TYPE_LINE
from mtgdb.database.schema import RULES_SUPERTYPES_META_KEY



def base(card_id, name, type_line, collector, **extra):
    card = {
        "object": "card", "id": card_id, "name": name,
        "type_line": type_line, "cmc": 0, "colors": [],
        "color_identity": [], "rarity": "special", "set": "tst",
        "set_name": "Taxonomy Test", "set_type": "funny",
        "collector_number": collector, "lang": "en",
        "released_at": "2026-08-28", "games": ["paper"],
        "keywords": [], "legalities": {"vintage": "legal"},
    }
    card.update(extra)
    return card


def build():
    related = [
        {"object": "related_card", "id": "bfm-left",
         "component": "combo_piece", "name": "B.F.M. (Big Furry Monster)",
         "type_line": "Creature — The Biggest, Baddest, Nastiest,",
         "uri": "https://api.scryfall.com/cards/bfm-left",
         "set": "ugl", "collector_number": "28"},
        {"object": "related_card", "id": "bfm-right",
         "component": "combo_piece", "name": "B.F.M. (Big Furry Monster)",
         "type_line": "Scariest Creature You'll Ever See",
         "uri": "https://api.scryfall.com/cards/bfm-right",
         "set": "ugl", "collector_number": "29"},
    ]
    left = base(
        "bfm-left", "B.F.M. (Big Furry Monster)",
        "Creature — The Biggest, Baddest, Nastiest,", "28",
        set="ugl", set_name="Unglued", all_parts=related)
    right = base(
        "bfm-right", "B.F.M. (Big Furry Monster)",
        "Scariest Creature You'll Ever See", "29",
        set="ugl", set_name="Unglued", all_parts=related,
        mana_cost="{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}{B}",
        power="99", toughness="99")
    cards = [
        left, right,
        base("jace", "Two-Faced Jace",
             "Creature — Human Wizard // Planeswalker — Jace", "3"),
        base("plane", "Capenna Plane", "Plane — New Capenna", "4"),
        base("land", "Future Power Plant",
             "Land — Urza's Power-Plant", "5"),
        base("ability-percent", "Percent Mechanic", "Creature — Weird", "6",
             keywords=["100% Awesome"]),
        base("ability-near", "Near Percent Mechanic", "Creature — Weird", "7",
             keywords=["100X Awesome", "Family gathering"]),
        base("no-dash", "Blue Screen of Death",
             "Legendary instant Artifact Enchantment", "RZ06f",
             keywords=["Family Gathering"]),
        base("summon-goblin", "Old Goblin", "Summon Goblin", "8"),
        base("summon-dragon", "Old Dragon", "Summon Dragon", "9"),
        base("modern-goblin", "Modern Goblin", "Creature — Goblin Boss", "10"),
        base("pig-latin", "Pig Latin Card", "Eaturecray — Igpay", "11", set_type="funny"),
        base("boss-only", "Boss Only", "Boss", "12"),
        base("elite", "Elite Challenge Creature", "Elite Creature — Human", "13"),
        base("host", "Host Acorn Creature", "Host Creature — Weird", "14"),
        base("token", "Test Token", "Token Creature — Marker", "15", layout="token"),
        base("emblem", "Test Emblem", "Emblem", "16", layout="emblem"),
    ]
    db = CardDB(os.path.join(tempfile.mkdtemp(), "cards.db"))
    for name, values in {
        "card-types": ["Artifact", "Creature", "Enchantment", "Instant", "Land", "Plane", "Planeswalker", "Sorcery"],
        "creature-types": [
            "Human", "Time Lord", "Weird", "Wizard", "Goblin", "Dragon",
            "The-Biggest-Baddest-Nastiest-Scariest-Creature-You'll-Ever-See",
        ],
        "planeswalker-types": ["Jace"],
        "land-types": ["Power-Plant", "Urza's"],
        "artifact-types": [], "enchantment-types": [], "battle-types": [],
        "spell-types": [],
        "keyword-abilities": ["100% Awesome"],
        "keyword-actions": [], "ability-words": ["Family Gathering"],
    }.items():
        db.set_meta(f"catalog:{name}", json.dumps(values))
    db.set_meta(
        RULES_SUPERTYPES_META_KEY,
        json.dumps(["basic", "legendary", "ongoing", "snow", "world"]))
    db.set_meta(
        "catalog:supertypes",
        json.dumps(["Basic", "Legendary", "Elite", "Host", "Token"]))
    db.load_cards(cards)
    return db


def main():
    db = build()
    complete = _BFM_COMPLETE_TYPE_LINE
    bfm = db.search(name="B.F.M.", set_codes=["ugl"])
    subtype_map = dict(db.subtype_catalog())
    checks = {
        "both B.F.M. physical printings retained":
            {card["collector_number"] for card in bfm} == {"28", "29"},
        "B.F.M. type fragments reconstructed identically":
            len(bfm) == 2 and all(card["type_line"] == complete for card in bfm),
        "B.F.M. raw fragments preserved":
            {card["raw_type_line"] for card in bfm} == {
                "Creature — The Biggest, Baddest, Nastiest,",
                "Scariest Creature You'll Ever See"},
        "B.F.M. has one atomic acorn creature subtype":
            subtype_map.get(
                "The-Biggest-Baddest-Nastiest-Scariest-Creature-You'll-Ever-See"
            ) == "Creature",
        "B.F.M. bogus type words eliminated":
            not ({"Scariest", "You'll", "Ever", "See"} & set(db.card_types())),
        "both DFC face card types searchable":
            len(db.search(card_types=["Creature", "Planeswalker"],
                          card_type_mode="all")) == 1,
        "uncataloged plane subtype is not invented":
            "New Capenna" not in subtype_map
            and "New" not in subtype_map and "Capenna" not in subtype_map,
        "hyphenated land subtype stays atomic":
            subtype_map.get("Power-Plant") == "Land",
        "exact keyword JSON match treats percent literally":
            [card["id"] for card in db.search(keywords=["100% Awesome"])]
            == ["ability-percent"],
        "unusual dashless real type line remains searchable":
            len(db.search(card_types=["Instant", "Artifact", "Enchantment"],
                          card_type_mode="all")) == 1,
        "case-only taxonomy variants have one picker entry": (
            len([value for value in db.card_types()
                 if value.casefold() == "instant"]) == 1
            and len([value for value, _category in db.keyword_catalog()
                     if value.casefold() == "family gathering"]) == 1),
        "card-type picker excludes subtype novelty and emblem pollution": (
            not ({"Goblin", "Dragon", "Boss", "Elite", "Eaturecray", "Emblem"}
                 & set(db.card_types()))),
        "Supertypes require Wizards rules authority and local occurrence": (
            db.supertypes() == ["Legendary"]
            and not ({"Elite", "Host", "Token"} & set(db.supertypes()))),
        "legacy Summon lines normalize to Creature searches": (
            {card["id"] for card in db.search(card_types=["Creature"])}
            >= {"summon-goblin", "summon-dragon", "modern-goblin"}),
        "legacy Summon subtype values remain searchable as subtypes": (
            {card["id"] for card in db.search(subtypes=["Goblin"])}
            == {"summon-goblin", "modern-goblin"}
            and [card["id"] for card in db.search(subtypes=["Dragon"])]
            == ["summon-dragon"]),
        "trusted card types correspond to searchable local rows": all(
            db.search(card_types=[value], content_types=["card", "token", "emblem"])
            for value in db.card_types()),
        "trusted subtypes correspond to searchable local rows": all(
            db.search(subtypes=[value]) for value in db.subtypes()),
        "trusted mechanics correspond to searchable local rows": all(
            db.search(keywords=[value]) for value, _category in db.keyword_catalog()),
    }

    deck = Deck("B.F.M. Pair", "vintage")
    for card in bfm:
        deck.add(card)
    saved = deck.to_text()
    reopened, missing = Deck.from_text(saved, db)
    checks["deck text writes collector-qualified printings"] = (
        "[UGL:28]" in saved and "[UGL:29]" in saved)
    checks["B.F.M. pair survives save/reopen without collapsing"] = (
        not missing and
        {e["card"]["collector_number"] for e in reopened.entries()} == {"28", "29"})
    legacy, missing = Deck.from_text(
        "1 B.F.M. (Big Furry Monster) [UGL]\n", db)
    checks["legacy set-only deck tag remains compatible"] = (
        not missing and len(legacy.entries()) == 1)

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    db.close()
    print("\nTAXONOMY/PRINTING REGRESSION:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
