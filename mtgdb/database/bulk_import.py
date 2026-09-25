"""Streaming Scryfall parsing, row projection, and transactional bulk import."""

import gzip
import json
import re
import os

from mtgdb.database.schema import (
    _INDEX_DEFINITIONS, MEMBERSHIP_TABLES, open_writer_connection)
from mtgdb.database.semantics import (
    _card_content_kind, _complete_type_line, _face0, _normalize_rules_text,
    _raw_type_line, _type_line_search_parts, color_mask, combined_mana_cost,
    derive_trait_flags,
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


def _back_face(card):
    """The second face of a two-faced card, or ``{}``.

    Search matches a card when either face fits (SRCH-052), so the back face's
    cost and stats are stored beside the front's.  Three or more faces are rare
    and reach only the second.
    """
    faces = card.get("card_faces") or []
    if len(faces) > 1 and isinstance(faces[1], dict):
        return faces[1]
    return {}


def _extract_row(card, type_line=None):
    """Turn a Scryfall card object into the tuple our schema expects.

    ``type_line`` is the completed type line when the caller already computed
    it (the import loop shares one computation with the membership rows).
    """
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
    # A transform or modal card's back-face cost is stored separately (its front
    # cost stays what the tables draw) and counts here too, so the colour
    # columns describe every face.  When the top-level cost already holds both
    # halves ("A // B") there is nothing more to add.
    back = _back_face(card)
    back_mana_cost = "" if "//" in mana_cost else str(back.get("mana_cost") or "")
    pips = _mana_pips(combined_mana_cost(mana_cost, back_mana_cost))

    # Precompute the content scope so searches filter on a stored, indexed column
    # instead of the per-row CARD_CONTENT_KIND SQL function.  Computed from the
    # exact stored values (layout and the completed type line) so it stays
    # identical to the function it replaces.
    if type_line is None:
        type_line = _complete_type_line(card)
    content_kind = _card_content_kind(card.get("layout"), type_line)

    # Precomputed colour bitmasks and packed trait flags, from the exact stored
    # string values so a query on them reproduces the LIKE/GLOB filter results.
    colors_mask = color_mask(",".join(colors))
    identity_mask = color_mask(",".join(identity))
    produced_mask = color_mask(",".join(sorted(card.get("produced_mana") or [])))
    trait_flags = derive_trait_flags(
        mana_cost, either("power"), either("toughness"),
        json.dumps(card.get("card_faces") or []), ",".join(indicator),
        back_mana_cost, back.get("power"), back.get("toughness"))

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
        back_mana_cost, back.get("power"), back.get("toughness"),
        back.get("loyalty"), back.get("defense"),
    )


def _contiguous_ngrams(tokens):
    """Every contiguous run of ``tokens`` joined by a space (widths 1..len)."""
    tokens = tuple(tokens)
    total = len(tokens)
    grams = set()
    for width in range(1, total + 1):
        for start in range(0, total - width + 1):
            grams.add(" ".join(tokens[start:start + width]))
    return grams


def _membership_terms(card, type_line=None):
    """Return (type, subtype, keyword) membership rows for one card.

    Type/subtype rows are every contiguous n-gram of the card's normalized left
    type-line words (types and supertypes) and subtype words, so an equality
    lookup reproduces the contiguous-run semantics of ``CARD_HAS_TYPE`` and
    ``CARD_HAS_SUBTYPE``.  Keyword rows are the casefolded keyword values, which
    the current filter matches with ``COLLATE NOCASE`` equality.
    """
    card_id = card["id"]
    if type_line is None:
        type_line = _complete_type_line(card)
    left_sequences, subtype_texts = _type_line_search_parts(type_line)
    type_terms = set()
    for sequence in left_sequences:
        type_terms |= _contiguous_ngrams(sequence)
    subtype_terms = set()
    for text in subtype_texts:
        subtype_terms |= _contiguous_ngrams(text.split())
    keyword_terms = {
        str(value).strip().casefold()
        for value in (card.get("keywords") or []) if str(value).strip()}
    return (
        [(card_id, term) for term in type_terms],
        [(card_id, term) for term in subtype_terms],
        [(card_id, term) for term in keyword_terms],
    )


_INSERT_TYPE = "INSERT OR IGNORE INTO card_types (card_id, term) VALUES (?, ?)"
_INSERT_SUBTYPE = "INSERT OR IGNORE INTO card_subtypes (card_id, term) VALUES (?, ?)"
_INSERT_KEYWORD = "INSERT OR IGNORE INTO card_keywords (card_id, term) VALUES (?, ?)"


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
    pips_w, pips_u, pips_b, pips_r, pips_g, pips_c,
    back_mana_cost, back_power, back_toughness, back_loyalty, back_defense
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                   maintenance_cb=None, minimum_count=1, meta=None):
        """Load Scryfall cards through an isolated SQLite writer connection.

        The GUI's primary connection remains available for reads while the
        refresh transaction is built. WAL mode gives readers the old committed
        snapshot until this writer commits the complete replacement atomically.
        This avoids UI stalls caused by waiting on the CardDB Python lock.

        ``meta`` (a dict, or a callable returning one) is written in the SAME
        transaction as the cards.  Recording what was downloaded in a separate
        step afterwards left a window in which the cards were replaced but the
        record was not -- a shutdown or error there made the next launch
        download everything again.
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
                    cur.execute("DELETE FROM card_types")
                    cur.execute("DELETE FROM card_subtypes")
                    cur.execute("DELETE FROM card_keywords")

                batch, processed = [], 0
                type_rows, subtype_rows, keyword_rows = [], [], []

                def flush():
                    nonlocal processed
                    if batch:
                        cur.executemany(_INSERT, batch)
                        processed += len(batch)
                        batch.clear()
                    if type_rows:
                        cur.executemany(_INSERT_TYPE, type_rows)
                        type_rows.clear()
                    if subtype_rows:
                        cur.executemany(_INSERT_SUBTYPE, subtype_rows)
                        subtype_rows.clear()
                    if keyword_rows:
                        cur.executemany(_INSERT_KEYWORD, keyword_rows)
                        keyword_rows.clear()
                    if progress_cb:
                        progress_cb(processed)

                for record_number, card in enumerate(objects, 1):
                    if not isinstance(card, dict):
                        raise ValueError(
                            f"Bulk record {record_number} is not a card object")
                    if not card.get("id") or not card.get("name"):
                        raise ValueError(
                            f"Bulk record {record_number} is missing card id or name")
                    # Complete the type line once and share it between the
                    # card row and its membership rows.
                    type_line = _complete_type_line(card)
                    batch.append(_extract_row(card, type_line))
                    if not replace:
                        # An in-place load replaces the card row (INSERT OR
                        # REPLACE), so drop the card's old membership too or a
                        # changed type/subtype/keyword would keep matching its
                        # stale terms alongside the new ones.
                        for table in MEMBERSHIP_TABLES:
                            cur.execute(
                                f"DELETE FROM {table} WHERE card_id = ?",
                                (card["id"],))
                    types, subtypes, keywords = _membership_terms(card, type_line)
                    type_rows.extend(types)
                    subtype_rows.extend(subtypes)
                    keyword_rows.extend(keywords)
                    if len(batch) >= 500:
                        flush()
                flush()

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
                if meta is not None:
                    values = meta() if callable(meta) else meta
                    cur.executemany(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        [(str(key), str(value)) for key, value in dict(values).items()])
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

