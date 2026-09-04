"""Readable, collision-safe filenames for cached MTG card images."""

import json
import os
import re
import threading

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_INDEX_NAME = ".cache_index.json"
_LOCK = threading.RLock()
_INDEX_CACHE = {}


def _clean(value, fallback="Card"):
    text = _INVALID.sub(" - ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).rstrip(" .")
    if not text:
        text = fallback
    if text.upper() in _RESERVED:
        text += "_"
    return text[:150].rstrip(" .") or fallback


def readable_stem(card):
    """Preferred cache stem: ``Card Name [SET]``."""
    name = _clean(card.get("name"), "Card")
    set_code = _clean((card.get("set_code") or "SET").upper(), "SET")
    return f"{name} [{set_code}]"


def _card_key(card):
    return str(card.get("id") or card.get("oracle_id") or card.get("name") or "card")


def _load_index(cache_dir):
    """Load a cache index once per directory for the lifetime of the process."""
    key = os.path.abspath(cache_dir)
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    path = os.path.join(key, _INDEX_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data = data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        data = {}
    _INDEX_CACHE[key] = data
    return data


def _save_index(cache_dir, data):
    path = os.path.join(cache_dir, _INDEX_NAME)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def cache_path(card, cache_dir, extension, legacy_path=None):
    """Return a human-readable cache path without mixing different printings.

    Normal files are named ``Card Name [SET].ext``. Only when more than one
    distinct Scryfall card object has the same name in the same set is a
    collector-number suffix added. Existing ID-only cache files are migrated
    rather than downloaded again.
    """
    os.makedirs(cache_dir, exist_ok=True)
    ext = extension if extension.startswith(".") else "." + extension
    stem = readable_stem(card)
    card_key = _card_key(card)

    with _LOCK:
        index = _load_index(cache_dir)

        def owner(path):
            return index.get(os.path.basename(path))

        def available(path):
            known_owner = owner(path)
            if known_owner == card_key:
                return True
            # An unindexed existing file has unknown identity. Never assume it
            # belongs to this printing: choose a more specific path instead.
            return known_owner is None and not os.path.exists(path)

        preferred = os.path.join(cache_dir, stem + ext)
        if available(preferred):
            chosen = preferred
        else:
            collector = _clean(card.get("collector_number"), "")
            suffix = f" - {collector}" if collector else ""
            qualified = os.path.join(cache_dir, stem + suffix + ext)
            if available(qualified):
                chosen = qualified
            else:
                # Last-resort collision guard for variants that share card
                # name, set, and collector number or whose index was lost.
                short_id = _clean(card_key, "card")[:8]
                chosen = os.path.join(cache_dir, f"{stem}{suffix} - {short_id}{ext}")

        basename = os.path.basename(chosen)
        if index.get(basename) != card_key:
            index[basename] = card_key
            _save_index(cache_dir, index)

        # Reuse and rename old ID-only files where possible.
        if legacy_path and os.path.exists(legacy_path) and not os.path.exists(chosen):
            try:
                os.replace(legacy_path, chosen)
            except OSError:
                pass

        return chosen
