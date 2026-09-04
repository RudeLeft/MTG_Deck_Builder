"""Shared parsing for Scryfall JSON columns that are stored as text.

Bulk import writes list-valued Scryfall fields (``card_faces``, ``keywords``,
``finishes``, ...) with ``json.dumps``, so a hydrated database row carries a
JSON *string* where the upstream API object carried a list. Consumers must
therefore parse before iterating: walking the raw column yields characters, not
face mappings, which fails silently instead of raising.
"""

from __future__ import annotations

import json


def json_list(value):
    """Return a list from a Scryfall JSON list column stored as text or list.

    Malformed or non-list payloads fail closed to an empty list so callers can
    iterate unconditionally.
    """
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def card_faces(card):
    """Return the well-formed face mappings for one card row or API object."""
    if not isinstance(card, dict):
        return []
    return [face for face in json_list(card.get("card_faces"))
            if isinstance(face, dict)]
