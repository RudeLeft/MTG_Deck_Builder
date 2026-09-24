"""Compare the running version against the latest published GitHub release.

Pure and dependency-injected: the HTTP fetch and current version are passed in,
so this module is Tk-free, imports nothing internal, and is easy to test. The UI
adapter wires it to ``core.net`` and ``core.version`` on a background thread; any
failure (offline, rate limit, malformed response) is treated as "no update" so
the check is silent and never blocks or disrupts startup.
"""

from __future__ import annotations

import re

# The application's own release feed and human-readable releases page. This is
# the app's identity, not user-facing vocabulary.
LATEST_RELEASE_URL = (
    "https://api.github.com/repos/RudeLeft/MTG_Deck_Builder/releases/latest")
RELEASES_PAGE_URL = (
    "https://github.com/RudeLeft/MTG_Deck_Builder/releases/latest")

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(text):
    """Return a ``(major, minor, patch)`` tuple from a version/tag string.

    Tolerates a leading ``v`` and missing minor/patch. Returns ``None`` when no
    number is present.
    """
    match = _VERSION_RE.search(str(text or ""))
    if not match:
        return None
    return tuple(int(part) if part else 0 for part in match.groups())


def is_newer(candidate, current):
    """True only when ``candidate`` is a strictly newer version than ``current``."""
    latest = parse_version(candidate)
    running = parse_version(current)
    if latest is None or running is None:
        return False
    return latest > running


def latest_release(fetch_json, url=LATEST_RELEASE_URL):
    """Return ``(tag, page_url)`` for the newest release, or ``(None, None)``.

    ``fetch_json`` is a callable like ``core.net.get_json``. Any error, or a
    draft/prerelease, yields ``(None, None)`` so a failed or irrelevant check is
    silent.
    """
    try:
        data = fetch_json(url)
    except Exception:
        return None, None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None, None
    tag = data.get("tag_name") or data.get("name")
    page = data.get("html_url") or RELEASES_PAGE_URL
    return (str(tag) if tag else None), str(page)
