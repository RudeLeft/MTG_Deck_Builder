"""Separated SQLite schema, import, query, taxonomy, and façade contracts."""

import ast
import atexit
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.database.schema import (
    _CARD_COLUMN_NAMES, _SCHEMA_VERSION, initialize_schema)


def _class(source, name):
    tree = ast.parse(source)
    return next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name)


def _methods(cls):
    return {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


def _booster_insert_check():
    """A booster insert must not outrank the set it was inserted into.

    A collector number carrying another set's prefix ("CLB-187") marks a card
    reprinted into a different product. The List is all of this in practice:
    Scryfall types it "masters", which the resolver reads as a main Magic
    release, and it is continuously updated, so it won import for anything it
    had ever carried -- handing the user "plst #OTC-280" for Command Tower.

    The rule only ever demotes a printing that would otherwise rank as a main
    release, so Secret Lair and promo printings, which carry prefixed numbers
    of their own, keep the tier they already had.
    """
    from mtgdb.database.bulk_import import ScryfallBulkImporter
    from mtgdb.database.queries import _BOOSTER_INSERT_COLLECTOR

    tmp = tempfile.mkdtemp(prefix="mtgdb-inserts-")
    atexit.register(shutil.rmtree, tmp, True)
    path = Path(tmp) / "cards.db"
    connection = sqlite3.connect(path)
    initialize_schema(connection)
    connection.commit()
    connection.close()

    def card(card_id, set_code, number, released, set_type="expansion", **extra):
        row = {
            "id": card_id, "name": "Shared Card", "set": set_code,
            "set_name": set_code.upper(), "set_type": set_type,
            "collector_number": number, "lang": "en", "games": ["paper"],
            "released_at": released, "type_line": "Instant",
            "cmc": 1, "mana_cost": "{R}",
        }
        row.update(extra)
        return row

    ScryfallBulkImporter(str(path)).load_cards([
        # Newest by date, but an insert carrying another set's number.
        card("insert", "plst", "CLB-187", "2026-11-09", set_type="masters"),
        # An older ordinary printing, which should still win.
        card("ordinary", "2x2", "117", "2022-07-08", set_type="masters"),
        # A promo with a prefixed number keeps its own lower tier either way.
        card("promo", "plg24", "2J-b", "2026-01-01", set_type="promo", promo=True),
    ])

    from mtgdb.database.db import CardDB
    db = CardDB(str(path))
    try:
        chosen = db.get_by_name("Shared Card")
    finally:
        db.close()

    # Which tier it lands in matters, not merely that it was demoted. An
    # insert is an ordinary-frame reprint, so it belongs one step down with
    # the supplemental products -- still ahead of promos and special products,
    # which a player would want even less.
    second = Path(tmp) / "against-promo.db"
    connection = sqlite3.connect(second)
    initialize_schema(connection)
    connection.commit()
    connection.close()
    ScryfallBulkImporter(str(second)).load_cards([
        card("insert", "plst", "CLB-187", "2020-01-01", set_type="masters"),
        card("promo", "pxyz", "5p", "2026-01-01", set_type="promo", promo=True),
    ])
    db = CardDB(str(second))
    try:
        over_promo = db.get_by_name("Shared Card")
    finally:
        db.close()

    return (chosen is not None
            and chosen["id"] == "ordinary"
            and over_promo is not None
            and over_promo["id"] == "insert"
            # The marker is expressed once, as data, not spelled out inline.
            and "GLOB" in _BOOSTER_INSERT_COLLECTOR)


def _universes_beyond_check():
    """Crossover sets are found by Scryfall's marker, not by a stamp.

    The triangle security stamp used to imply Universes Beyond and no longer
    does: The Hobbit, Avatar, Marvel and Teenage Mutant Ninja Turtles carry the
    ordinary oval stamp or none. A stamp-only rule left those sets unclassified,
    which both leaked them into Search when excluding Universes Beyond and let
    them win deck import, because several are typed "expansion" and so compete
    with main Magic releases on release date.

    The rule is deliberately set-wide: commons and uncommons in a crossover set
    frequently carry no marker of their own.
    """
    from mtgdb.database.bulk_import import (
        UNIVERSES_BEYOND_RULE, ScryfallBulkImporter)

    tmp = tempfile.mkdtemp(prefix="mtgdb-crossover-")
    atexit.register(shutil.rmtree, tmp, True)
    path = Path(tmp) / "cards.db"
    connection = sqlite3.connect(path)
    initialize_schema(connection)
    connection.commit()
    connection.close()

    def card(card_id, name, set_code, **extra):
        row = {
            "id": card_id, "name": name, "set": set_code,
            "set_name": set_code.upper(), "set_type": "expansion",
            "collector_number": card_id, "lang": "en", "games": ["paper"],
            "type_line": "Creature", "cmc": 1, "mana_cost": "{G}",
        }
        row.update(extra)
        return row

    ScryfallBulkImporter(str(path)).load_cards([
        # Marked itself.
        card("1", "Marked Hero", "hob", promo_types=["universesbeyond"]),
        # Same set, no marker of its own: the set-wide rule must reach it.
        card("2", "Unmarked Common", "hob"),
        # The older stamp convention must keep working.
        card("3", "Stamped Card", "ltr", security_stamp="triangle"),
        card("4", "Stamped Set Sibling", "ltr"),
        # An oval stamp alone means nothing.
        card("5", "Ordinary Card", "fdn", security_stamp="oval"),
    ])

    check = sqlite3.connect(path)
    flags = dict(check.execute("select name, universes_beyond from cards"))
    check.close()

    classified = flags == {
        "Marked Hero": 1, "Unmarked Common": 1,
        "Stamped Card": 1, "Stamped Set Sibling": 1,
        "Ordinary Card": 0,
    }

    # A database built by an older rule is repaired from stored data alone.
    stale = sqlite3.connect(path)
    stale.execute("update cards set universes_beyond = 0")
    stale.commit()
    stale.close()
    changed = ScryfallBulkImporter(str(path)).reclassify_universes_beyond()
    repaired = sqlite3.connect(path)
    after = dict(repaired.execute("select name, universes_beyond from cards"))
    repaired.close()

    return (classified and after == flags and changed > 0
            and str(UNIVERSES_BEYOND_RULE).strip() != "")


def _oversized_object_check():
    """One card object bigger than the read size must parse, not hang.

    The array parser used to refill its buffer only while that buffer was
    small. An object larger than the read size can never complete under that
    rule: the decode starves, the recovery slice removes nothing because
    nothing was consumed, and the loop spins forever -- no exception, no
    progress, a frozen sync with nothing to explain it. Scryfall's Treasure
    token is already over 95,000 characters because its all_parts array names
    every card that makes a Treasure, and it grows with every set.

    Run on a worker with a deadline: a regression here is a hang, and a gate
    that hangs never reports anything.
    """
    from mtgdb.database.bulk_import import iter_card_objects

    tmp = tempfile.mkdtemp(prefix="mtgdb-oversized-")
    atexit.register(shutil.rmtree, tmp, True)
    path = Path(tmp) / "oversized.json"
    # Comfortably past two read chunks, and past any single real card.
    cards = [
        {"id": "small", "name": "Small"},
        {"id": "huge", "name": "Huge", "oracle_text": "x" * 400000},
        {"id": "after", "name": "After"},
    ]
    path.write_text(json.dumps(cards), encoding="utf-8")

    truncated = Path(tmp) / "truncated.json"
    truncated.write_text('[{"id":"x","t":"' + "q" * 300000, encoding="utf-8")

    outcome = {}

    def parse():
        try:
            outcome["ids"] = [card.get("id")
                              for card in iter_card_objects(path)]
        except Exception as exc:                            # noqa: BLE001
            outcome["error"] = type(exc).__name__
        try:
            list(iter_card_objects(truncated))
            outcome["truncated"] = "accepted"
        except (ValueError, json.JSONDecodeError):
            outcome["truncated"] = "rejected"
        except Exception as exc:                            # noqa: BLE001
            outcome["truncated"] = type(exc).__name__

    worker = threading.Thread(target=parse, daemon=True)
    worker.start()
    worker.join(60)
    if worker.is_alive():
        return False, False
    parsed = outcome.get("ids") == ["small", "huge", "after"]
    # A corrupt oversized file must still raise rather than read forever.
    rejected = outcome.get("truncated") == "rejected"
    return parsed, rejected


def _content_scope_sql_parity_check():
    """The pure-SQL content-scope predicate must match CARD_CONTENT_KIND exactly.

    Catalog scope filtering was switched from the per-row CARD_CONTENT_KIND
    Python function to a constants-derived SQL layout predicate for speed. Any
    drift would silently change which cards each Search content scope includes,
    so prove they select identical rows for every layout and content subset.
    """
    import itertools
    from mtgdb.database.constants import CONTENT_TYPES, KNOWN_SCRYFALL_LAYOUTS
    from mtgdb.database.semantics import (
        _card_content_kind, content_scope_layout_sql)

    layouts = sorted(KNOWN_SCRYFALL_LAYOUTS) + [
        "some_future_layout", "prepare_next", "", None]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cards (n INTEGER, layout TEXT)")
    conn.executemany(
        "INSERT INTO cards (n, layout) VALUES (?, ?)", list(enumerate(layouts)))
    conn.commit()

    ok = True
    for size in range(1, len(CONTENT_TYPES) + 1):
        for subset in itertools.combinations(CONTENT_TYPES, size):
            sql, params = content_scope_layout_sql(subset, "layout")
            selected = {row[0] for row in conn.execute(
                f"SELECT n FROM cards WHERE {sql}", params)}
            expected = {n for n, lay in enumerate(layouts)
                        if _card_content_kind(lay, "") in subset}
            ok = ok and selected == expected
    conn.close()

    rejected = False
    try:
        content_scope_layout_sql(("not_a_content_type",), "layout")
    except ValueError:
        rejected = True
    return ok and rejected


def main():
    sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/database/db.py", "mtgdb/database/schema.py", "mtgdb/database/bulk_import.py",
            "mtgdb/database/semantics.py", "mtgdb/database/queries.py",
            "mtgdb/database/search_queries.py", "mtgdb/database/taxonomy.py",
            "mtgdb/database/sync.py",
        )
    }
    facade_class = _class(sources["mtgdb/database/db.py"], "CardDB")
    facade_methods = _methods(facade_class)
    facade_bases = {
        base.id for base in facade_class.bases if isinstance(base, ast.Name)}
    query_methods = _methods(
        _class(sources["mtgdb/database/queries.py"], "CardQueryMixin"))
    search_query_methods = _methods(
        _class(sources["mtgdb/database/search_queries.py"], "CardSearchQueryMixin"))
    search_builder_methods = _methods(
        _class(sources["mtgdb/database/search_queries.py"], "SearchQueryBuilder"))
    taxonomy_methods = _methods(
        _class(sources["mtgdb/database/taxonomy.py"], "CardTaxonomyMixin"))
    importer_methods = _methods(
        _class(sources["mtgdb/database/bulk_import.py"], "ScryfallBulkImporter"))

    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "cards.db"
        legacy = sqlite3.connect(path)
        legacy.execute(
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        legacy.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', '9')")
        legacy.execute("CREATE TABLE cards (obsolete TEXT)")
        legacy.commit()
        legacy.close()

        db = CardDB(str(path))
        schema_version = db.get_meta("schema_version")
        table_columns = {
            row[1] for row in db.conn.execute("PRAGMA table_info(cards)")}
        journal_mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
        reader = db.open_reader()
        reader_query_only = reader.execute("PRAGMA query_only").fetchone()[0]
        reader_udf = reader.execute(
            "SELECT CARD_HAS_TYPE(?, ?), CARD_HAS_SUBTYPE(?, ?)",
            ("Creature — Time Lord", "Creature",
             "Creature — Time Lord", "Time Lord"),
        ).fetchone()
        reader.close()

        db.load_cards([{
            "id": "printing-a",
            "oracle_id": "oracle-a",
            "name": "Architecture Card",
            "type_line": "Creature — Time Lord",
            "oracle_text": "Flying",
            "keywords": ["Flying"],
            "games": ["paper"],
            "set": "tst",
            "set_name": "Test Set",
            "set_type": "expansion",
            "collector_number": "1",
            "rarity": "rare",
            "legalities": {"modern": "legal"},
        }])
        db.store_catalogs({"creature-types": ["Time Lord"], "keyword-abilities": ["Flying"]})
        behavior_preserved = (
            db.count() == 1
            and db.get_card("printing-a")["name"] == "Architecture Card"
            and len(db.search(subtypes=["Time Lord"], fmt="modern")) == 1
            and "Time Lord" in db.subtypes()
            and ("Flying", "Keyword ability") in db.keyword_catalog())

        # The projection allow-list is the only thing between caller-supplied
        # column names and the interpolated SELECT list, so exercise it with
        # adversarial input rather than trusting it exists.
        def _projection_rejected(columns):
            try:
                db.search(columns=columns)
            except ValueError:
                return True
            return False

        valid_projection = [
            row["name"] for row in db.search(columns=("id", "name"))]
        projection_guarded = (
            valid_projection == ["Architecture Card"]
            # Unknown column.
            and _projection_rejected(("id", "not_a_column"))
            # SQL smuggled through the projection.
            and _projection_rejected(("id", "name FROM cards; DROP TABLE cards --"))
            and _projection_rejected(("*",))
            and _projection_rejected(("(SELECT 1)",))
            # An empty projection must not silently become SELECT *.
            and _projection_rejected(())
            # The guard must not have destroyed the table it protects.
            and db.count() == 1)
        db.close()

    # DBI-009 busy timeouts. Ask each real connection what it will actually do
    # rather than matching pragma text: a source-literal check passes while the
    # connection silently keeps sqlite3's 5s default.
    from mtgdb.database.schema import (
        open_primary_connection, open_reader_connection, open_writer_connection)

    timeout_dir = tempfile.mkdtemp(prefix="mtgdb-busy-")
    atexit.register(shutil.rmtree, timeout_dir, True)
    timeout_db = str(Path(timeout_dir) / "cards.db")
    busy_timeouts = {}
    for label, opener in (
            ("primary", lambda: open_primary_connection(timeout_db, ())),
            ("reader", lambda: open_reader_connection(timeout_db, ())),
            ("writer", lambda: open_writer_connection(timeout_db))):
        connection = opener()
        try:
            busy_timeouts[label] = connection.execute(
                "PRAGMA busy_timeout").fetchone()[0]
        finally:
            connection.close()

    oversized_parsed, oversized_rejected = _oversized_object_check()
    crossover_classified = _universes_beyond_check()
    inserts_demoted = _booster_insert_check()

    checks = {
        "pure-SQL content scope matches CARD_CONTENT_KIND for every layout":
            _content_scope_sql_parity_check(),
        "a booster insert never outranks an ordinary printing":
            inserts_demoted,
        "crossover sets are classified by marker, stamp, and set":
            crossover_classified,
        "a card object larger than the read size parses instead of hanging":
            oversized_parsed,
        "an oversized corrupt file raises instead of reading forever":
            oversized_rejected,
        "every connection sets an explicit busy timeout": (
            all(value >= 30000 for value in busy_timeouts.values())
            # 5000 is sqlite3's default: proof none of them merely inherited it.
            and 5000 not in set(busy_timeouts.values())),
        "the primary connection is not the first to give up": (
            busy_timeouts["primary"] >= busy_timeouts["reader"]),
        "search projection rejects columns outside the allow-list": (
            projection_guarded),
        "schema version and columns survive extraction": (
            schema_version == str(_SCHEMA_VERSION) == "13"
            and table_columns == _CARD_COLUMN_NAMES),
        "connection policies and registered type functions survive": (
            str(journal_mode).casefold() == "wal"
            and reader_query_only == 1 and tuple(reader_udf) == (1, 1)),
        "CardDB public query and taxonomy behavior is preserved": (
            behavior_preserved),
        "schema module exclusively owns schema and connections": (
            "CREATE TABLE IF NOT EXISTS cards" in sources["mtgdb/database/schema.py"]
            and "CREATE INDEX IF NOT EXISTS" in sources["mtgdb/database/schema.py"]
            and "sqlite3.connect" in sources["mtgdb/database/schema.py"]
            and all(
                "sqlite3.connect" not in sources[name]
                for name in (
                    "mtgdb/database/db.py", "mtgdb/database/bulk_import.py",
                    "mtgdb/database/queries.py", "mtgdb/database/search_queries.py",
                    "mtgdb/database/taxonomy.py"))),
        "import module exclusively owns projection parsing and transactions": (
            {"__init__", "load_cards"} <= importer_methods
            and "def iter_card_objects(" in sources["mtgdb/database/bulk_import.py"]
            and "def _extract_row(" in sources["mtgdb/database/bulk_import.py"]
            and "BEGIN IMMEDIATE" in sources["mtgdb/database/bulk_import.py"]
            and "minimum_count" in sources["mtgdb/database/bulk_import.py"]),
        "semantic module owns shared type interpretation": (
            "def _complete_type_line(" in sources["mtgdb/database/semantics.py"]
            and "def _card_has_type(" in sources["mtgdb/database/semantics.py"]
            and "def _card_has_subtype(" in sources["mtgdb/database/semantics.py"]
            and "sqlite3" not in sources["mtgdb/database/semantics.py"]),
        "query modules separate lookup from canonical search SQL": (
            {"get_card", "get_by_name", "name_suggestions"} <= query_methods
            and "search" not in query_methods
            and {"search"} <= search_query_methods
            and {"add_content_filter", "add_type_filters", "add_color_filter",
                 "add_numeric_filters", "add_printing_filters", "build"}
                <= search_builder_methods
            and "CREATE TABLE" not in sources["mtgdb/database/queries.py"]
            and "CREATE TABLE" not in sources["mtgdb/database/search_queries.py"]),
        "taxonomy module owns observed catalog discovery": (
            {
                "sets", "set_types", "rarities", "formats", "card_types", "supertypes",
                "subtype_catalog", "keywords", "keyword_catalog",
            } <= taxonomy_methods
            and "def search(" not in sources["mtgdb/database/taxonomy.py"]),
        "CardDB remains a small composition and metadata facade": (
            {"CardQueryMixin", "CardSearchQueryMixin", "CardTaxonomyMixin"} <= facade_bases
            and {
                "__init__", "close", "open_reader", "count", "has_cards",
                "get_meta", "set_meta", "set_meta_many", "store_catalogs",
                "catalog", "load_cards",
            } <= facade_methods
            and not ({"search", "get_card", "subtype_catalog"} & facade_methods)
            and len(sources["mtgdb/database/db.py"].splitlines()) < 150),
        "database internals have no circular facade imports": all(
            "import mtgdb.database.db" not in sources[name]
            and "from mtgdb.database.db" not in sources[name]
            for name in (
                "mtgdb/database/schema.py", "mtgdb/database/bulk_import.py",
                "mtgdb/database/semantics.py", "mtgdb/database/queries.py",
                "mtgdb/database/search_queries.py", "mtgdb/database/taxonomy.py")),
        "synchronization imports the responsible internal owners": (
            "from mtgdb.database.bulk_import import ("
            in sources["mtgdb/database/sync.py"]
            and "iter_card_objects" in sources["mtgdb/database/sync.py"]
            # The crossover repair reuses the importer that owns the rule
            # rather than restating the classification here.
            and "ScryfallBulkImporter" in sources["mtgdb/database/sync.py"]
            and "UNIVERSES_BEYOND_META_KEY" in sources["mtgdb/database/sync.py"]
            and "universesbeyond" not in sources["mtgdb/database/sync.py"]
            and "from mtgdb.database.schema import ("
            in sources["mtgdb/database/sync.py"]
            and "SCRYFALL_CATALOGS" in sources["mtgdb/database/sync.py"]
            and "RULES_SUPERTYPES_META_KEY" in sources["mtgdb/database/sync.py"]
            and "from mtgdb.database.db import" not in sources["mtgdb/database/sync.py"]),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nDATABASE INTERNALS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
