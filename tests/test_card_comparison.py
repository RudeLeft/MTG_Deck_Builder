"""Headless regressions for card metadata retention through the database.

The comparison window no longer derives attribute/normalization data, so this
guards the schema side: metadata a card carries survives a load/read round trip
and remains available to the comparison collection.
"""
import shutil
import atexit
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mtgdb.database.db as S
from mtgdb.comparison.models import (
    ComparisonCollection, MAX_COMPARISON_CARDS, MIN_COMPARISON_CARDS,
    comparison_json_list,
)


def main():
    workspace = tempfile.mkdtemp()
    # Registered at creation so the directory is removed on every exit path,
    # including an exception mid-test. Left unmanaged, each suite run
    # abandoned a database in the system temp folder, and the suite runs on
    # every local build, every CI build, and every source release.
    atexit.register(shutil.rmtree, workspace, ignore_errors=True)
    db = S.CardDB(os.path.join(workspace, "cards.db"))
    db.load_cards([{
        "id": "cmp1", "oracle_id": "oracle1", "name": "Comparison Test",
        "mana_cost": "{2}{W}", "cmc": 3,
        "type_line": "Legendary Creature — Time Lord",
        "oracle_text": "Flying", "colors": ["W"], "color_identity": ["W"],
        "power": "2", "toughness": "3", "defense": "4", "rarity": "rare",
        "set": "cmp", "set_name": "Compare Set", "set_type": "expansion",
        "collector_number": "42", "lang": "en", "released_at": "2026-08-28",
        "games": ["paper"], "frame": "2015", "border_color": "black",
        "finishes": ["nonfoil", "foil"], "artist": "Example Artist",
        "reserved": True, "full_art": True, "game_changer": True,
        "color_indicator": ["W"], "security_stamp": "oval",
        "produced_mana": ["W"], "keywords": ["Flying"],
        "legalities": {"commander": "legal", "modern": "not_legal"},
        "layout": "normal",
    }])
    card = db.get_card("cmp1")

    collection = ComparisonCollection()
    add_result = collection.add(card)

    checks = {
        "comparison limits are 2 through 7": (
            MIN_COMPARISON_CARDS == 2 and MAX_COMPARISON_CARDS == 7),
        "new defense metadata is retained": card.get("defense") == "4",
        "printing artist is retained": card.get("artist") == "Example Artist",
        "finishes are retained as JSON": (
            comparison_json_list(card.get("finishes")) == ["nonfoil", "foil"]),
        "reserved/full-art/game-changer flags retained": (
            card.get("reserved") == 1 and card.get("full_art") == 1
            and card.get("game_changer") == 1),
        "color indicator retained": card.get("color_indicator") == "W",
        "layout retained": card.get("layout") == "normal",
        "collection stores the exact card by printing id": (
            add_result.status == "added"
            and collection.cards()[0]["id"] == "cmp1"),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    db.close()
    print("\nCARD COMPARISON REGRESSION:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
