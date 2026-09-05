"""Separated SQLite schema, import, query, taxonomy, and façade contracts."""

import ast
import atexit
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.database.schema import _CARD_COLUMN_NAMES, _SCHEMA_VERSION


def _class(source, name):
    tree = ast.parse(source)
    return next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name)


def _methods(cls):
    return {node.name for node in cls.body if isinstance(node, ast.FunctionDef)}


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

    checks = {
        "every connection sets an explicit busy timeout": (
            all(value >= 30000 for value in busy_timeouts.values())
            # 5000 is sqlite3's default: proof none of them merely inherited it.
            and 5000 not in set(busy_timeouts.values())),
        "the primary connection is not the first to give up": (
            busy_timeouts["primary"] >= busy_timeouts["reader"]),
        "search projection rejects columns outside the allow-list": (
            projection_guarded),
        "schema version and columns survive extraction": (
            schema_version == str(_SCHEMA_VERSION) == "11"
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
            "from mtgdb.database.bulk_import import iter_card_objects"
            in sources["mtgdb/database/sync.py"]
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
