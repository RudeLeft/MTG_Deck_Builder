"""Informational search/discovery benchmark on a synthetic database.
Not a pass/fail test. Run: python tests/db_benchmark.py [n_rows]"""
import os, random, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mtgdb.database.db as S


def main(n=60000):
    db = S.CardDB(os.path.join(tempfile.mkdtemp(), "cards.db"))
    TYPES = ["Creature", "Instant", "Sorcery", "Land", "Artifact", "Enchantment"]
    SUBS = ["Goblin", "Elf", "Dragon", "Human Warrior", "Zombie", "Angel"]
    KWS = [["Flying"], ["Trample"], ["Haste"], [], ["Ward"], ["Menace"]]
    SETS = [("dmu", "expansion"), ("fdn", "core"), ("cmm", "masters"),
            ("spm", "expansion"), ("sld", "box")]
    def mk(i):
        setc, st = random.choice(SETS); t = random.choice(TYPES)
        tl = f"{t} — {random.choice(SUBS)}" if t in ("Creature", "Land") else t
        return {"object": "card", "id": f"{i:08x}", "name": f"Card {i%(n//2)}",
                "type_line": tl, "cmc": float(i % 8), "colors": ["G"],
                "color_identity": ["G"], "rarity": random.choice(
                    ["common", "uncommon", "rare", "mythic"]), "set": setc,
                "set_name": setc.upper(), "set_type": st,
                "collector_number": str(i % 400), "lang": "en",
                "released_at": f"20{10+(i%15):02d}-01-01", "games": ["paper"],
                "security_stamp": "triangle" if setc == "spm" else "oval",
                "keywords": random.choice(KWS), "oracle_text": "draw a card",
                "legalities": {"modern": "legal" if i % 2 else "n"},
                "image_uris": {"png": "p"}}
    t = time.time(); db.load_cards((mk(i) for i in range(n)))
    print(f"loaded {db.count():,} rows in {time.time()-t:.1f}s")
    rdr = db.open_reader()
    def bench(label, **kw):
        best = 9e9; rows = []
        for _ in range(3):
            s = time.perf_counter(); rows = db.search(connection=rdr, **kw)
            best = min(best, time.perf_counter() - s)
        print(f"  {label:42s} {best*1000:7.1f} ms  ({len(rows):>7,} rows)")
    for label, kw in [("type=Creature", dict(card_types=["Creature"])),
                      ("subtype=Goblin", dict(subtypes=["Goblin"])),
                      ("keyword=Flying", dict(keywords=["Flying"])),
                      ("format=modern", dict(fmt="modern")),
                      ("colors within G", dict(colors=["G"], color_mode="within")),
                      ("name contains 'Card 1'", dict(name="Card 1"))]:
        bench(label, **kw)
    for label, fn in [("set_types", db.set_types), ("keywords", db.keywords),
                      ("subtypes", db.subtypes), ("creature_types", db.creature_types)]:
        s = time.perf_counter(); r = fn()
        print(f"  discovery {label:20s} {(time.perf_counter()-s)*1000:6.1f} ms ({len(r)})")
    rdr.close(); db.close()

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 60000)
