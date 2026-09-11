"""Atomic persistence boundary for application UI preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from mtgdb.core.atomic_files import (
    TEMP_SUFFIX, sweep_abandoned_writes, temp_prefix,
)


TABLE_COLUMNS_VERSION = 3
SEARCH_TYPE_LINE_CACHE_VERSION = 1



class UIPreferencesRepository:
    """Read and update UI-only preferences without any Tk dependency."""

    def __init__(self, path):
        self.path = Path(path)
        # Same reasoning as the workspace writer: a killed process leaves its
        # temporary file in the portable folder with nothing to remove it.
        sweep_abandoned_writes(self.path.parent, self.path.name)

    def load(self):
        """Return a preference mapping, tolerating absent or invalid files."""
        try:
            with self.path.open("r", encoding="utf-8") as source:
                value = json.load(source)
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, data):
        """Atomically replace the complete preference mapping."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Same durability contract as the workspace and deck-TXT writers: a
        # unique temp name so two writers can never interleave into one path,
        # and fsync before replace so the rename cannot become visible ahead of
        # the bytes it points at.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=temp_prefix(self.path.name), suffix=TEMP_SUFFIX,
            dir=self.path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                json.dump(data, target, indent=2)
                target.flush()
                try:
                    os.fsync(target.fileno())
                except OSError:
                    pass
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def save_table_columns(self, visible_columns):
        """Preserve unrelated preferences and atomically save table layouts."""
        data = self.load()
        data["table_columns"] = {
            view: list(columns) for view, columns in visible_columns.items()
        }
        data["table_columns_version"] = TABLE_COLUMNS_VERSION
        self._save(data)

    def load_search_type_line_catalogs(self):
        """Return last successful Type Line chip labels for disabled warm-start UI."""
        data = self.load().get("search_type_line_catalogs", {})
        if not isinstance(data, dict):
            return {"card_types": (), "supertypes": ()}

        def clean(values):
            if not isinstance(values, list):
                return ()
            seen, result = set(), []
            for value in values:
                label = " ".join(str(value or "").split())
                key = label.casefold()
                if label and key not in seen:
                    seen.add(key)
                    result.append(label)
            return tuple(result)

        if data.get("version") != SEARCH_TYPE_LINE_CACHE_VERSION:
            return {"card_types": (), "supertypes": ()}
        return {
            "card_types": clean(data.get("card_types")),
            "supertypes": clean(data.get("supertypes")),
        }

    def save_search_type_line_catalogs(self, card_types, supertypes):
        """Persist trusted labels for presentation-only disabled startup chips."""
        data = self.load()
        data["search_type_line_catalogs"] = {
            "version": SEARCH_TYPE_LINE_CACHE_VERSION,
            "card_types": [str(value) for value in card_types or ()],
            "supertypes": [str(value) for value in supertypes or ()],
        }
        self._save(data)
