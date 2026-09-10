"""Database-facing access used by the interactive card-search feature."""

from __future__ import annotations

from mtgdb.search.models import SearchCriteria
from mtgdb.search.results import SEARCH_RESULT_FIELDS


# Initial result rows contain everything required by configurable table columns.
# Full 46-column card records are fetched by primary key only when selected or
# acted upon, avoiding large unused JSON/image fields in broad search payloads.
SEARCH_RESULT_COLUMNS = SEARCH_RESULT_FIELDS


class SearchRepository:
    """Narrow search boundary around the portable local card database.

    ``CardDB`` remains the stable façade over the separated database internals.
    This repository is the only interface the search controller uses.
    """

    def __init__(self, card_db):
        self._db = card_db

    def has_cards(self):
        return self._db.has_cards()

    def open_reader(self):
        return self._db.open_reader()

    def search(self, criteria: SearchCriteria, connection):
        return self._db.search(
            connection=connection, columns=SEARCH_RESULT_COLUMNS,
            **criteria.query_arguments())

    def context_rows(self, criteria: SearchCriteria, connection, columns=None):
        """Unordered narrow projection for Tk-free contextual analysis."""
        if columns is None:
            from mtgdb.search.context import CONTEXT_COLUMNS
            columns = CONTEXT_COLUMNS
        return self._db.search_unordered(
            connection=connection, columns=tuple(columns),
            **criteria.query_arguments())

    def count(self, criteria: SearchCriteria, connection):
        """Count one Search criteria with canonical SQL semantics."""
        return self._db.count_search(
            connection=connection, **criteria.query_arguments())

    def card_by_id(self, card_id):
        return self._db.get_card(card_id)

    def name_suggestions(self, query, limit=20):
        return self._db.name_suggestions(query, limit=limit)

    def set_types(self, content_types=None, paper_only=False, games=None):
        return self._db.set_types(content_types, paper_only, games=games)

    def sets(self, allowed_types=None, content_types=None, paper_only=False,
             games=None):
        return self._db.sets(allowed_types, content_types, paper_only, games=games)

    def card_types(self, content_types=None, paper_only=False):
        return self._db.card_types(content_types, paper_only)

    def card_type_taxonomy_status(self):
        return self._db.card_type_taxonomy_status()

    def supertypes(self, content_types=None, paper_only=False):
        return self._db.supertypes(content_types, paper_only)

    def supertype_taxonomy_status(self):
        return self._db.supertype_taxonomy_status()

    def formats(self, content_types=None, paper_only=False):
        return self._db.formats(content_types, paper_only)

    def formats_by_status(self, content_types=None, paper_only=False,
                          games=None):
        return self._db.formats_by_status(content_types, paper_only, games=games)

    def layouts(self, content_types=None, paper_only=False, games=None):
        return self._db.layouts(content_types, paper_only, games=games)

    def rarities(self, content_types=None, paper_only=False):
        return self._db.rarities(content_types, paper_only)

    def release_years(self, content_types=None, paper_only=False, games=None):
        return self._db.release_years(content_types, paper_only, games=games)

    def equivalent_layouts(self, content_types=None, paper_only=False, games=None,
                           **catalogs):
        return self._db.equivalent_layouts(
            content_types, paper_only, games=games, **catalogs)

    def keyword_catalog(self, content_types=None, paper_only=False):
        return self._db.keyword_catalog(content_types, paper_only)

    def subtype_catalog(self, content_types=None, paper_only=False):
        return self._db.subtype_catalog(content_types, paper_only)
