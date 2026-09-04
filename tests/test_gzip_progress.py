"""Regression: gzip bulk files parse with indeterminate progress (not a bogus
determinate total). Guards the gzip magic-byte fix. Run: python tests/test_gzip_progress.py"""
import shutil
import atexit
import gzip, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mtgdb.database.bulk_import import iter_card_objects


def main():
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    cards = [{"object": "card", "id": str(i), "name": f"Card {i}", "set": "x",
              "set_type": "core", "lang": "en", "collector_number": str(i),
              "games": ["paper"], "type_line": "Instant",
              "oracle_text": "padding " * 40} for i in range(6000)]
    gz = os.path.join(d, "bulk.json.gz")
    with gzip.open(gz, "wt", encoding="utf-8") as f:
        f.write(json.dumps(cards))
    pj = os.path.join(d, "bulk.json")
    with open(pj, "w", encoding="utf-8") as f:
        f.write(json.dumps(cards))

    def totals(path):
        seen = []
        n = len(list(iter_card_objects(path, progress_cb=lambda r, t: seen.append(t))))
        return n, set(seen)

    n_gz, t_gz = totals(gz)
    n_pj, t_pj = totals(pj)
    ok = (n_gz == 6000 and t_gz == {None}          # gzip -> indeterminate
          and n_pj == 6000 and t_pj == {os.path.getsize(pj)})  # plain -> file size
    print(f"  gzip: {n_gz} cards, totals={t_gz}")
    print(f"  plain: {n_pj} cards, totals={t_pj}")
    print("GZIP PROGRESS:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
