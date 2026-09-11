"""Stable CardDB façade over separated Scryfall database internals."""

from __future__ import annotations

import json
import threading

from mtgdb.database.bulk_import import ScryfallBulkImporter
from mtgdb.database.queries import CardQueryMixin
from mtgdb.database.search_queries import CardSearchQueryMixin
from mtgdb.database.schema import (
    initialize_schema, open_primary_connection, open_reader_connection,
)
from mtgdb.database.taxonomy import CardTaxonomyMixin
from mtgdb.database.semantics import (
    _card_content_kind, _card_has_subtype, _card_has_type,
    _mana_cost_symbol_match, _type_key,
)


_SQL_FUNCTIONS = (
    ("CARD_CONTENT_KIND", 2, _card_content_kind),
    ("CARD_HAS_TYPE", 2, _card_has_type),
    ("CARD_HAS_SUBTYPE", 2, _card_has_subtype),
    ("MANA_COST_SYMBOL_MATCH", 4, _mana_cost_symbol_match),
)


class CardDB(CardQueryMixin, CardSearchQueryMixin, CardTaxonomyMixin):
    """Compose schema, import, metadata, query, and taxonomy boundaries."""

    def __init__(self, path):
        self.path = path
        self.conn = open_primary_connection(path, _SQL_FUNCTIONS)
        self._lock = threading.RLock()
        self._bulk_importer = ScryfallBulkImporter(path)
        with self._lock:
            initialize_schema(self.conn)

    def close(self):
        with self._lock:
            self.conn.close()

    def open_reader(self):
        """Return an independent connection for background read/search work."""
        return open_reader_connection(self.path, _SQL_FUNCTIONS)

    def count(self):
        with self._lock:
            return self.conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    def has_cards(self):
        """Cheap existence check for callers that do not need an exact count."""
        with self._lock:
            return self.conn.execute(
                "SELECT 1 FROM cards LIMIT 1").fetchone() is not None

    def get_meta(self, key, default=None):
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        self.set_meta_many({key: value})

    def set_meta_many(self, values):
        """Commit related metadata keys together on the primary connection."""
        rows = [(str(key), str(value)) for key, value in dict(values).items()]
        if not rows:
            return
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", rows)
            self.conn.commit()

    def store_catalogs(self, catalogs):
        """Persist successfully fetched Scryfall catalogs without network work."""
        self.set_meta_many({
            f"catalog:{name}": json.dumps(values, ensure_ascii=False)
            for name, values in dict(catalogs).items()
        })

    def catalog(self, name):
        """Return one cached Scryfall catalog as a clean, ordered list."""
        raw = self.get_meta(f"catalog:{name}", "[]")
        try:
            values = json.loads(raw)
        except (TypeError, ValueError):
            values = []
        if not isinstance(values, list):
            return []
        seen, clean = set(), []
        for value in values:
            value = str(value or "").strip()
            key = _type_key(value)
            if value and key not in seen:
                seen.add(key)
                clean.append(value)
        return clean

    def load_cards(self, objects, progress_cb=None, replace=True,
                   maintenance_cb=None, minimum_count=1):
        """Delegate one transactional bulk import to its exclusive owner."""
        return self._bulk_importer.load_cards(
            objects, progress_cb=progress_cb, replace=replace,
            maintenance_cb=maintenance_cb, minimum_count=minimum_count)
