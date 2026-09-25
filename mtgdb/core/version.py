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
_VERSION_PART = re.compile(r"\d+")


def _version_key(text):
    """Order versions by their numeric dotted parts (so 1.10.0 > 1.9.0)."""
    return tuple(int(part) for part in _VERSION_PART.findall(str(text or "")))


def app_version():
    """Return the running application version, or ``0.0.0`` when unknown.

    A frozen build carries its version as a ``mtg_deck_builder-<v>.dist-info``
    folder inside ``_internal``. The in-app update copies the new build over
    the old one *additively* (it never purges, so a failed copy can never
    delete the working install), which leaves the previous version's folder
    beside the new one. ``importlib.metadata.version`` returns whichever it
    finds first -- alphabetically the *older* one -- so an upgraded build
    reported the version it had just replaced and re-offered the same update.
    The swap only ever installs newer, so the running build is always the
    highest version present: resolve all matching distributions and take it.
    """
    try:
        from importlib.metadata import distributions
        candidates = [
            str(dist.version) for dist in distributions(name=_DISTRIBUTION)
            if dist.version]
        if candidates:
            return max(candidates, key=_version_key)
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
