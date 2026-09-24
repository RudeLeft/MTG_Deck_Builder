"""Single runtime source for the application version.

The version lives in ``pyproject.toml`` (BLD-009). This resolves it at runtime
for logging, support, and the update check, working both from an installed or
frozen build (distribution metadata) and from an uninstalled source checkout
(``pyproject.toml``).
"""

from __future__ import annotations

from pathlib import Path
import re

_DISTRIBUTION = "mtg-deck-builder"
_FALLBACK = "0.0.0"


def app_version():
    """Return the running application version, or ``0.0.0`` when unknown."""
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version(_DISTRIBUTION)
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    try:
        root = Path(__file__).resolve().parents[2]
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return _FALLBACK
