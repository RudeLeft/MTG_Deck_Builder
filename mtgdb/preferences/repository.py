"""Atomic persistence boundary for application UI preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path


TABLE_COLUMNS_VERSION = 3


class UIPreferencesRepository:
    """Read and update UI-only preferences without any Tk dependency."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        """Return a preference mapping, tolerating absent or invalid files."""
        try:
            with self.path.open("r", encoding="utf-8") as source:
                value = json.load(source)
        except (OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def save_table_columns(self, visible_columns):
        """Preserve unrelated preferences and atomically save table layouts."""
        data = self.load()
        data["table_columns"] = {
            view: list(columns) for view, columns in visible_columns.items()
        }
        data["table_columns_version"] = TABLE_COLUMNS_VERSION

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as target:
                json.dump(data, target, indent=2)
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
