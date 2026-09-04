"""
Synthetic "future Magic" regression test — proves the import→discovery→search
pipeline absorbs unknown Scryfall content without code changes or crashes.
Runs anywhere (no GUI needed):  python tests/test_future_magic.py
"""
import shutil
import atexit
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mtgdb.database.db as S
from mtgdb.database.schema import RULES_SUPERTYPES_META_KEY
from mtgdb.database.semantics import _card_content_classification, _card_content_kind
import json



def build():
    workspace = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)
    db = S.CardDB(os.path.join(workspace, "cards.db"))
    db.load_cards([
        {"object": "card", "id": "n1", "name": "Normal Bear",
         "type_line": "Creature — Bear", "cmc": 2.0, "colors": ["G"],
         "color_identity": ["G"], "rarity": "common", "set": "dmu",
         "set_name": "DMU", "set_type": "expansion", "collector_number": "1",
         "lang": "en", "released_at": "2022-09-09", "games": ["paper"],
         "keywords": ["Trample"], "legalities": {"modern": "legal"},
         "image_uris": {"png": "p"}, "oracle_text": "Trample"},
        {"object": "card", "id": "f1", "name": "Future Entity",
         "type_line": "Ultra Legendary Contraption — Time Lord Mecha-Pilot",
         "cmc": 7.0, "colors": ["U"], "color_identity": ["U"], "rarity": "mythic",
         "set": "fut", "set_name": "Futura", "set_type": "expansion",
         "collector_number": "100", "lang": "en", "released_at": "2027-01-01",
         "games": ["paper"], "keywords": ["Chronoshift", "Warp Speed"],
         "legalities": {"neoformat": "legal", "modern": "legal"},
         "image_uris": {"png": "p"}, "oracle_text": "Chronoshift 3.",
         "an_unknown_future_field": 99, "another": {"x": 1}},
        {"object": "card", "id": "f2", "name": "Zu\u0308ku\u0308nfti\u0308g \u03a9",
         "type_line": "Enchantment — Aura Saga", "cmc": 3.0, "colors": ["W"],
         "color_identity": ["W"], "rarity": "ultramythic", "set": "zzz",
         "set_name": "Zeta", "set_type": "holo_experience",
         "collector_number": "GR-777\u2605", "lang": "en",
         "released_at": "2028-05-05", "games": ["paper"], "keywords": [],
         "legalities": {"modern": "legal"}, "layout": "quadruple_faced",
         "image_uris": {"png": "p"}, "oracle_text": "Very long " + ("text " * 400)},
        {"object": "card", "id": "f3", "name": "Split Future // Back Future",
         "type_line": "Instant // Sorcery", "cmc": 4.0, "colors": ["R"],
         "color_identity": ["R"], "rarity": "rare", "set": "fut",
         "set_name": "Futura", "set_type": "expansion", "collector_number": "200",
         "lang": "en", "released_at": "2027-01-01", "games": ["paper"],
         "keywords": [], "legalities": {"modern": "legal"}, "layout": "split",
         "card_faces": [
             {"name": "Split Future", "mana_cost": "{2}{Q}{R}",
              "oracle_text": "Deal 3 damage.", "type_line": "Instant"},
             {"name": "Back Future", "mana_cost": "{R}",
              "oracle_text": "Return target.", "type_line": "Sorcery"}],
         "image_uris": {"png": "p"}},
        {"object": "card", "id": "f4", "name": "Sparse Card",
         "type_line": "Artifact", "set": "fut", "set_name": "Futura",
         "set_type": "expansion", "collector_number": "201", "lang": "en",
         "games": ["paper"],
         "oracle_text": "Create a 1/1 colorless Servo artifact creature token. Draw a card."},
        {"object": "card", "id": "f5", "name": "Phrase Type Future",
         "type_line": "Quantum Being", "cmc": 5.0, "colors": ["U"],
         "color_identity": ["U"], "rarity": "rare", "set": "fut",
         "set_name": "Futura", "set_type": "expansion", "collector_number": "202",
         "lang": "en", "released_at": "2029-01-01", "games": ["paper"],
         "keywords": [], "legalities": {"modern": "legal"}, "layout": "normal"},
        {"object": "card", "id": "f6", "name": "Phrase Supertype Future",
         "type_line": "Eternal Prime Creature — Bear", "cmc": 4.0, "colors": ["G"],
         "color_identity": ["G"], "rarity": "rare", "set": "fut",
         "set_name": "Futura", "set_type": "expansion", "collector_number": "203",
         "lang": "en", "released_at": "2029-01-01", "games": ["paper"],
         "keywords": [], "legalities": {"modern": "legal"}, "layout": "normal"},
        {"object": "card", "id": "f7", "name": "New Family Future",
         "type_line": "Chronicle — Era", "cmc": 3.0, "colors": [],
         "color_identity": [], "rarity": "rare", "set": "fut",
         "set_name": "Futura", "set_type": "future_product", "collector_number": "204",
         "lang": "en", "released_at": "2030-01-01", "games": ["paper"],
         "keywords": [], "legalities": {"futureconditional": "conditionally_legal"},
         "layout": "normal"},
    ])
    return db


def _unknown_set_type_import_fallback():
    import_workspace = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, import_workspace, ignore_errors=True)
    db = S.CardDB(os.path.join(import_workspace, "import.db"))
    db.load_cards([
        {"object": "card", "id": "u1", "name": "Future Choice",
         "type_line": "Artifact", "set": "new", "set_name": "New Product",
         "set_type": "future_product", "collector_number": "1", "lang": "en",
         "released_at": "2029-01-01", "games": ["paper"], "promo": False},
        {"object": "card", "id": "u2", "name": "Future Choice",
         "type_line": "Artifact", "set": "prm", "set_name": "Promo",
         "set_type": "promo", "collector_number": "2", "lang": "en",
         "released_at": "2030-01-01", "games": ["paper"], "promo": True},
    ])
    resolved = db.get_by_name("Future Choice")
    db.close()
    return bool(resolved and resolved.get("set_type") == "future_product")


def _fingerprint_change_detection():
    fingerprint_workspace = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, fingerprint_workspace, ignore_errors=True)
    db = S.CardDB(os.path.join(fingerprint_workspace, "fingerprint.db"))
    db.load_cards([{
        "object": "card", "id": "b1", "name": "Baseline",
        "type_line": "Creature — Bear", "set": "base", "set_name": "Base",
        "set_type": "expansion", "collector_number": "1", "lang": "en",
        "games": ["paper"], "layout": "normal",
        "legalities": {"modern": "legal"}, "keywords": [],
    }])
    db.store_catalogs({
        "card-types": ["Creature"], "artifact-types": [], "battle-types": [],
        "creature-types": ["Bear"], "enchantment-types": [], "land-types": [],
        "planeswalker-types": [], "spell-types": [],
        "keyword-abilities": [], "keyword-actions": [], "ability-words": [],
    })
    db.set_meta(RULES_SUPERTYPES_META_KEY, json.dumps(["Legendary"]))
    first = db.refresh_compatibility_diagnostics()
    db.load_cards([{
        "object": "card", "id": "b2", "name": "Later",
        "type_line": "Creature — Bear", "set": "later", "set_name": "Later",
        "set_type": "future_box", "collector_number": "2", "lang": "en",
        "games": ["paper"], "layout": "future_layout",
        "legalities": {"futureformat": "legal"}, "keywords": [],
    }], replace=False)
    second = db.refresh_compatibility_diagnostics()
    visible = [card["id"] for card in db.search(content_types=["card"])]
    db.close()
    return (
        bool(first.get("fingerprint"))
        and second.get("previous_fingerprint") == first.get("fingerprint")
        and "future_layout" in second.get("new_values", {}).get("layouts", [])
        and "future_box" in second.get("new_values", {}).get("set_types", [])
        and "futureformat" in second.get("new_values", {}).get("format_keys", [])
        and "b2" in visible
    )


def main():
    db = build()
    before_subtypes = set(db.subtypes())
    before_mechanics = {value for value, _category in db.keyword_catalog()}
    db.store_catalogs({
        "card-types": ["Artifact", "Creature", "Enchantment", "Instant", "Sorcery", "Quantum Being", "Chronicle"],
        "artifact-types": [], "battle-types": [],
        "creature-types": ["Bear", "Time Lord", "Mecha-Pilot"],
        "enchantment-types": ["Aura", "Saga"],
        "land-types": [], "planeswalker-types": [], "spell-types": [],
        "keyword-abilities": ["Trample", "Chronoshift", "Warp Speed"],
        "keyword-actions": [], "ability-words": [],
    })
    db.set_meta(
        RULES_SUPERTYPES_META_KEY,
        json.dumps(["ultra", "legendary", "eternal prime"]))
    trusted_subtypes = set(db.subtypes())
    trusted_mechanics = {value for value, _category in db.keyword_catalog()}
    compatibility = db.refresh_compatibility_diagnostics()
    checks = {
        "importer loaded unknown content without crashing": db.count() == 8,
        "unknown set_type discovered from observed Scryfall data":
            "holo_experience" in [t for t, _ in db.set_types()],
        "unknown format discovered from observed Scryfall data":
            "neoformat" in db.formats(),
        "unknown rarity discovered from observed Scryfall data":
            "ultramythic" in db.rarities(),
        "uncataloged type-line word is not card-type picker vocabulary":
            "Contraption" not in db.card_types(),
        "uncataloged subtype is not picker vocabulary":
            "Mecha-Pilot" not in before_subtypes and "Time Lord" not in before_subtypes,
        "uncataloged mechanic is not picker vocabulary":
            "Chronoshift" not in before_mechanics,
        "cataloged observed future subtypes become trusted vocabulary":
            {"Time Lord", "Mecha-Pilot"} <= trusted_subtypes,
        "cataloged observed future mechanics become trusted vocabulary":
            {"Chronoshift", "Warp Speed"} <= trusted_mechanics,
        "new official Supertype needs no application code change": (
            db.supertypes() == ["Ultra", "Legendary", "Eternal Prime"]),
        "multi-word future Card Type is discovered atomically": (
            "Quantum Being" in db.card_types()
            and len(db.search(card_types=["Quantum Being"])) == 1),
        "multi-word future Supertype is searchable atomically": (
            "Eternal Prime" in db.supertypes()
            and len(db.search(supertypes=["Eternal Prime"])) == 1),
        "new subtype-bearing Card Type remains searchable without guessed subtype": (
            "Chronicle" in db.card_types()
            and len(db.search(card_types=["Chronicle"])) == 1
            and "Era" not in trusted_subtypes),
        "unknown rarity searchable": len(db.search(rarities=["ultramythic"])) == 1,
        "unknown format searchable": len(db.search(fmt="neoformat")) == 1,
        "unknown set_type searchable": len(db.search(set_types=["holo_experience"])) == 1,
        "unknown deck-import set_type degrades to neutral fallback tier":
            _unknown_set_type_import_fallback(),
        "explicit unknown keyword remains searchable": len(db.search(keywords=["Chronoshift"])) == 1,
        "explicit future subtype remains searchable": len(db.search(subtypes=["Mecha-Pilot"])) == 1,
        "multi-word Time Lord subtype remains atomic": len(db.search(subtypes=["Time Lord"])) == 1,
        "explicit unknown card type remains searchable for non-UI callers":
            len(db.search(card_types=["Contraption"])) == 1,
        "unknown layout preserved & shown": (
            len(db.search(set_codes=["zzz"])) == 1
            and db.search(set_codes=["zzz"])[0]["layout"] == "quadruple_faced"
            and _card_content_classification("quadruple_faced", "Enchantment — Saga") == "unknown"
            and _card_content_kind("quadruple_faced", "Enchantment — Saga") == "card"),
        "unknown layout is diagnosed without hiding the card": (
            "quadruple_faced" in compatibility.get("unknown_layouts", [])),
        "unknown legality status is preserved but not treated as playable": (
            "conditionally_legal" in compatibility.get("unknown_legality_statuses", [])
            and "futureconditional" not in db.formats()),
        "new subtype authority family is diagnosed without guessing vocabulary": (
            "Chronicle" in compatibility.get("subtype_authority_gaps", [])),
        "compatibility fingerprint persists for later update comparison": (
            bool(compatibility.get("fingerprint"))
            and db.upstream_compatibility_report().get("fingerprint") == compatibility.get("fingerprint")),
        "later upstream values are diffed without hiding unknown-layout cards":
            _fingerprint_change_detection(),
        "DFC front-face import resolves": (db.get_by_name("Split Future") or {}).get(
            "name") == "Split Future // Back Future",
        "sparse (missing-field) card imports": db.get_by_name("Sparse Card") is not None,
        "rules text searched across faces": len(db.search(text="return target")) == 1,
        "unquoted Rules Text words may have text between them": (
            [card["id"] for card in db.search(text="create token")] == ["f4"]),
        "quoted Rules Text remains an exact normalized phrase": (
            [card["id"] for card in db.search(
                text='"create a 1/1 colorless servo"')] == ["f4"]
            and db.search(text='"create token"') == []),
        "Rules Text All combines chips while each chip remains a word search": (
            [card["id"] for card in db.search(
                text=["create token", "draw card"], text_mode="all")] == ["f4"]),
        "Rules Text Any combines independent chips": (
            {card["id"] for card in db.search(
                text=["create token", "return target"], text_mode="any")}
            == {"f3", "f4"}),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    db.close()
    print("\nFUTURE-MAGIC REGRESSION:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
