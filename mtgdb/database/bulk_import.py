"""Streaming Scryfall parsing, row projection, and transactional bulk import."""

import gzip
import json
import re
import os

from mtgdb.database.schema import _INDEX_DEFINITIONS, open_writer_connection
from mtgdb.database.semantics import (
    _card_content_kind, _complete_type_line, _face0, _normalize_rules_text,
    _raw_type_line, color_mask, derive_trait_flags,
)


def _all_oracle_text(card):
    """Return normalized rules text covering the parent object and every face."""
    parts = []
    parent = card.get("oracle_text")
    if parent:
        parts.append(parent)
    for face in card.get("card_faces") or []:
        text = face.get("oracle_text")
        if text:
            parts.append(text)
    # Deduplicate exact repeated text while preserving face order.
    seen, unique = set(), []
    for part in parts:
        key = str(part)
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return _normalize_rules_text(" ".join(unique))

_MANA_SYMBOL = re.compile(r"\{([^}]+)\}")
PIP_COLORS = ("W", "U", "B", "R", "G", "C")


def _mana_pips(mana_cost):
    """Count coloured symbols in a mana cost, once per colour.

    A hybrid symbol counts for both of its colours, which is how devotion
    reads them and what someone asking for "two green" means: {G/W}{G/W}
    costs two green and two white. Phyrexian {G/P} is one green. Generic,
    variable and snow symbols contribute to no colour.
    """
    counts = dict.fromkeys(PIP_COLORS, 0)
    for symbol in _MANA_SYMBOL.findall(str(mana_cost or "")):
        for part in str(symbol).upper().split("/"):
            if part in counts:
                counts[part] += 1
    return counts


def _extract_row(card):
    """Turn a Scryfall card object into the tuple our schema expects."""
    face = _face0(card)
    img = card.get("image_uris") or face.get("image_uris") or {}

    colors = card.get("colors")
    if colors is None:
        colors = face.get("colors")
    colors = colors or []

    # SRCH-033: exact colour/produced-mana matching compares against the
    # stored string, so the member order must not depend on upstream.
    identity = sorted(card.get("color_identity") or [])

    artist_values = []
    for value in [card.get("artist")] + [
            face_obj.get("artist") for face_obj in (card.get("card_faces") or [])]:
        value = str(value or "").strip()
        if value and value not in artist_values:
            artist_values.append(value)
    artist = ", ".join(artist_values)

    indicator = card.get("color_indicator")
    if indicator is None:
        indicator = []
        for face_obj in (card.get("card_faces") or []):
            for value in (face_obj.get("color_indicator") or []):
                if value not in indicator:
                    indicator.append(value)
    indicator = indicator or face.get("color_indicator") or []

    def either(key):
        return card.get(key, face.get(key))

    mana_cost = card.get("mana_cost") or face.get("mana_cost") or ""
    # Both halves of a split card are one printing with one cost string, so
    # counting the whole string answers "costs two green" for either half.
    pips = _mana_pips(mana_cost)

    # Precompute the content scope so searches filter on a stored, indexed column
    # instead of the per-row CARD_CONTENT_KIND SQL function.  Computed from the
    # exact stored values (layout and the completed type line) so it stays
    # identical to the function it replaces.
    type_line = _complete_type_line(card)
    content_kind = _card_content_kind(card.get("layout"), type_line)

    # Precomputed colour bitmasks and packed trait flags, from the exact stored
    # string values so a query on them reproduces the LIKE/GLOB filter results.
    colors_mask = color_mask(",".join(colors))
    identity_mask = color_mask(",".join(identity))
    produced_mask = color_mask(",".join(sorted(card.get("produced_mana") or [])))
    trait_flags = derive_trait_flags(
        mana_cost, either("power"), either("toughness"),
        json.dumps(card.get("card_faces") or []), ",".join(indicator))

    return (
        card["id"],
        card.get("oracle_id"),
        card["name"],
        mana_cost,
        float(card.get("cmc") or 0),
        type_line,
        _raw_type_line(card),
        card.get("oracle_text") or face.get("oracle_text") or "",
        _all_oracle_text(card),
        ",".join(colors),
        ",".join(identity),
        either("power"),
        either("toughness"),
        either("loyalty"),
        either("defense"),
        card.get("rarity"),
        card.get("set"),
        card.get("set_name"),
        card.get("set_type"),
        card.get("collector_number"),
        card.get("lang"),
        card.get("released_at"),
        1 if "paper" in (card.get("games") or []) else 0,
        # Sorted for the same reason the colour columns are: exact
        # comparisons must not depend on upstream ordering.
        ",".join(sorted(card.get("games") or [])),
        1 if card.get("promo") else 0,
        json.dumps(card.get("promo_types") or []),
        json.dumps(card.get("frame_effects") or []),
        card.get("frame"),
        card.get("border_color"),
        json.dumps(card.get("finishes") or []),
        artist,
        1 if card.get("reserved") else 0,
        1 if card.get("full_art") else 0,
        1 if card.get("game_changer") else 0,
        ",".join(indicator),
        card.get("security_stamp"),
        0,  # universes_beyond is derived set-wide after the bulk load
        ",".join(sorted(card.get("produced_mana") or [])),
        img.get("small"),
        img.get("normal"),
        img.get("png"),
        img.get("art_crop"),
        json.dumps(card.get("legalities") or {}),
        json.dumps(card.get("keywords") or []),
        json.dumps(card.get("all_parts") or []),
        json.dumps(card.get("card_faces") or []),
        card.get("layout"),
        content_kind,
        colors_mask, identity_mask, produced_mask, trait_flags,
        pips["W"], pips["U"], pips["B"], pips["R"], pips["G"], pips["C"],
    )


# Bumped whenever the classification below changes, so a database built by an
# older rule can be repaired in place instead of re-downloaded.
UNIVERSES_BEYOND_RULE = "2"

# Scryfall marks a crossover printing with the "universesbeyond" promo type.
# The triangle security stamp used to imply the same thing and no longer does:
# The Hobbit, Avatar, Marvel and Teenage Mutant Ninja Turtles carry the
# ordinary oval stamp or none at all, so a stamp-only rule left 4,289 cards
# unclassified -- visible in Search when excluding Universes Beyond, and
# decisive during import, where five of those sets are typed "expansion" and
# therefore compete with main Magic releases on release date.
#
# The marker is read set-wide for the same reason the stamp was: commons and
# uncommons in a crossover set frequently carry no marker of their own.
_UNIVERSES_BEYOND_SQL = """
UPDATE cards SET universes_beyond = 1
WHERE set_code IN (
    SELECT DISTINCT set_code FROM cards
    WHERE set_code IS NOT NULL
      AND (security_stamp = 'triangle'
           OR promo_types LIKE '%universesbeyond%')
)
"""


def classify_universes_beyond(cursor):
    """Mark every crossover set, returning how many rows changed."""
    cursor.execute("UPDATE cards SET universes_beyond = 0")
    cursor.execute(_UNIVERSES_BEYOND_SQL)
    return cursor.rowcount


_INSERT = """
INSERT OR REPLACE INTO cards (
    id, oracle_id, name, mana_cost, cmc, type_line, raw_type_line,
    oracle_text, oracle_text_search,
    colors, color_identity, power, toughness, loyalty, defense, rarity,
    set_code, set_name, set_type, collector_number, lang, released_at, paper, games,
    promo, promo_types, frame_effects, frame, border_color, finishes, artist,
    reserved, full_art, game_changer, color_indicator, security_stamp,
    universes_beyond, produced_mana,
    image_small, image_normal, image_png, image_art_crop, legalities, keywords,
    related_parts, card_faces, layout, content_kind,
    colors_mask, identity_mask, produced_mask, trait_flags,
    pips_w, pips_u, pips_b, pips_r, pips_g, pips_c
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
          ?,?,?,?,?,?,?,?,?,?,?)
"""


def _open_maybe_gzip(path):
    """Open a file as text, transparently handling gzip regardless of extension."""
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "rt", encoding="utf-8")


def iter_card_objects(path, progress_cb=None):
    """
    Yield card dicts without materializing the bulk JSON array.

    progress_cb(source_units, total_or_None) reports parsing progress. For normal
    JSON files source_units are bytes/approximately bytes and total is file size;
    gzip input remains supported but uses indeterminate parsing progress.
    """
    with open(path, "rb") as probe:
        compressed = probe.read(2) == b"\x1f\x8b"
    total_units = None if compressed else os.path.getsize(path)
    consumed_units = 0
    last_report = 0
    fh = _open_maybe_gzip(path)
    try:
        first = fh.read(1)
        while first and first.isspace():
            first = fh.read(1)
        fh.seek(0)

        if first != "[":
            for line_number, raw_line in enumerate(fh, 1):
                consumed_units += len(raw_line.encode("utf-8"))
                line = raw_line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Malformed JSONL record at line {line_number}: {exc.msg}"
                        ) from exc
                    if not isinstance(obj, dict):
                        raise ValueError(
                            f"JSONL record at line {line_number} is not a card object")
                    yield obj
                if progress_cb and consumed_units - last_report >= (1 << 20):
                    progress_cb(consumed_units, total_units)
                    last_report = consumed_units
            if progress_cb:
                progress_cb(consumed_units, total_units)
            return

        decoder = json.JSONDecoder()
        buffer = ""
        eof = False
        started = False
        # A decode that ran out of input MUST force another read even when the
        # buffer is already large. Reading only while the buffer is small
        # cannot finish an object bigger than the read size: the decode fails
        # for want of input, the recovery slice removes nothing because
        # nothing was consumed, and the loop spins forever without raising or
        # reporting progress. Scryfall's Treasure token is the realistic case
        # -- its all_parts array names every card that makes a Treasure and
        # grows with every set.
        need_more_data = False

        while True:
            if not eof and (need_more_data or len(buffer) < 65536):
                chunk = fh.read(65536)
                if chunk:
                    buffer += chunk
                    consumed_units += len(chunk.encode("utf-8"))
                    if (progress_cb and
                            consumed_units - last_report >= (1 << 20)):
                        progress_cb(consumed_units, total_units)
                        last_report = consumed_units
                else:
                    eof = True
                    if progress_cb:
                        progress_cb(consumed_units, total_units)

            need_more_data = False
            pos = 0
            length = len(buffer)

            if not started:
                while pos < length and buffer[pos].isspace():
                    pos += 1
                if pos >= length:
                    if eof:
                        return
                    buffer = ""
                    continue
                if buffer[pos] != "[":
                    raise ValueError("Expected a JSON array")
                pos += 1
                started = True

            while True:
                while pos < length and (buffer[pos].isspace() or buffer[pos] == ","):
                    pos += 1
                if pos < length and buffer[pos] == "]":
                    trailing = buffer[pos + 1:]
                    remainder = fh.read()
                    if remainder:
                        consumed_units += len(remainder.encode("utf-8"))
                        trailing += remainder
                    if progress_cb:
                        progress_cb(consumed_units, total_units)
                    if trailing.strip():
                        raise ValueError("Unexpected data after JSON array")
                    return
                if pos >= length:
                    buffer = ""
                    break
                try:
                    obj, end_pos = decoder.raw_decode(buffer, pos)
                except json.JSONDecodeError:
                    # Preserve the incomplete object and fetch another chunk.
                    buffer = buffer[pos:]
                    if eof:
                        raise
                    need_more_data = True
                    break
                yield obj
                pos = end_pos

                # Compact consumed data periodically so large arrays stay bounded.
                if pos > 65536:
                    buffer = buffer[pos:]
                    break
            if eof and not buffer.strip():
                raise ValueError("Unterminated JSON array")
    finally:
        fh.close()


class ScryfallBulkImporter:
    """Build a complete replacement snapshot through an isolated writer."""

    def __init__(self, path):
        self.path = path

    def reclassify_universes_beyond(self):
        """Re-run only the crossover classification over stored rows.

        Every input this needs is already in the database, so a rule change
        does not justify making the user download the whole card snapshot
        again.
        """
        writer = open_writer_connection(self.path)
        try:
            cur = writer.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                changed = classify_universes_beyond(cur)
                writer.commit()
            except Exception:
                writer.rollback()
                raise
        finally:
            writer.close()
        return changed

    def load_cards(self, objects, progress_cb=None, replace=True,
                   maintenance_cb=None, minimum_count=1):
        """Load Scryfall cards through an isolated SQLite writer connection.

        The GUI's primary connection remains available for reads while the
        refresh transaction is built. WAL mode gives readers the old committed
        snapshot until this writer commits the complete replacement atomically.
        This avoids UI stalls caused by waiting on the CardDB Python lock.
        """
        writer = open_writer_connection(self.path)
        try:
            cur = writer.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                if maintenance_cb:
                    maintenance_cb("drop_indexes", 0, len(_INDEX_DEFINITIONS))
                for i, (name, _sql) in enumerate(_INDEX_DEFINITIONS, 1):
                    cur.execute(f"DROP INDEX IF EXISTS {name}")
                    if maintenance_cb:
                        maintenance_cb("drop_indexes", i, len(_INDEX_DEFINITIONS))

                if replace:
                    cur.execute("DELETE FROM cards")

                batch, processed = [], 0
                for record_number, card in enumerate(objects, 1):
                    if not isinstance(card, dict):
                        raise ValueError(
                            f"Bulk record {record_number} is not a card object")
                    if not card.get("id") or not card.get("name"):
                        raise ValueError(
                            f"Bulk record {record_number} is missing card id or name")
                    batch.append(_extract_row(card))
                    if len(batch) >= 500:
                        cur.executemany(_INSERT, batch)
                        processed += len(batch)
                        batch.clear()
                        if progress_cb:
                            progress_cb(processed)
                if batch:
                    cur.executemany(_INSERT, batch)
                    processed += len(batch)
                    if progress_cb:
                        progress_cb(processed)

                actual_count = cur.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                threshold = max(1, int(minimum_count or 1))
                if actual_count < threshold:
                    raise ValueError(
                        f"Downloaded bulk data produced only {actual_count:,} distinct "
                        f"cards (minimum {threshold:,}); the existing database was "
                        "left unchanged.")

                if maintenance_cb:
                    maintenance_cb("classify", 0, 1)
                classify_universes_beyond(cur)
                if maintenance_cb:
                    maintenance_cb("classify", 1, 1)

                total_indexes = len(_INDEX_DEFINITIONS)
                for i, (_name, sql) in enumerate(_INDEX_DEFINITIONS, 1):
                    cur.execute(sql)
                    if maintenance_cb:
                        maintenance_cb("indexes", i, total_indexes)

                if maintenance_cb:
                    maintenance_cb("commit", 0, 1)
                writer.commit()
                if maintenance_cb:
                    maintenance_cb("commit", 1, 1)
            except Exception:
                writer.rollback()
                raise
        finally:
            writer.close()

        if progress_cb:
            progress_cb(actual_count)
        return actual_count

