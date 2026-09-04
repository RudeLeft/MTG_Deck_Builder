"""Atomic persistence boundary for application UI preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


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
        # Same durability contract as the workspace and deck-TXT writers: a
        # unique temp name so two writers can never interleave into one path,
        # and fsync before replace so the rename cannot become visible ahead of
        # the bytes it points at. Without the fsync a power loss can publish an
        # empty preferences file, which load() silently reads back as {} --
        # resetting every saved table layout.
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
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
