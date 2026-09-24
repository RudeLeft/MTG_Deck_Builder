"""SQLite schema, indexes, migration, and connection configuration."""

import logging
import os
import re
import sqlite3


log = logging.getLogger("mtg")


_SCHEMA_VERSION = 14

# Scryfall catalogs are the authoritative, forward-updatable vocabulary for
# Card Types, subtypes, and abilities. Official Supertype vocabulary comes
# separately from the current Wizards Comprehensive Rules and is cached in
# ``meta`` after strict parsing. A failed terminology request never prevents
# the much more important bulk-card refresh.
# Re-exported for compatibility; the declarative registry lives in authorities.py.

# Verified Wizards Comprehensive Rules taxonomy metadata. The values stored under
# this key are parsed from the official Rules-page TXT link; no Supertype names
# are defined in application source.
RULES_SUPERTYPES_META_KEY = "rules:supertypes"
RULES_SUPERTYPES_ERROR_META_KEY = "rules:supertypes_last_error"
RULES_SUPERTYPES_ATTEMPT_META_KEY = "rules:supertypes_last_attempt_epoch"

# Card Types use Scryfall's first-class catalog as their authority. Persist
# refresh diagnostics separately so an empty valid scope is distinguishable
# from an unavailable catalog.
# Which crossover-classification rule produced the stored universes_beyond
# flags. A rule change is repaired in place from data already present rather
# than by re-downloading the card snapshot.
UNIVERSES_BEYOND_META_KEY = "classification:universes_beyond_rule"

CARD_TYPES_ERROR_META_KEY = "catalog:card-types_last_error"
CARD_TYPES_ATTEMPT_META_KEY = "catalog:card-types_last_attempt_epoch"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id                TEXT PRIMARY KEY,
    oracle_id         TEXT,
    name              TEXT NOT NULL,
    mana_cost         TEXT,
    cmc               REAL,
    type_line         TEXT,
    raw_type_line     TEXT,   -- exact Scryfall printing fragment (B.F.M. needs both)
    oracle_text       TEXT,
    oracle_text_search TEXT,   -- normalized searchable text from every card face
    colors            TEXT,   -- comma-joined, e.g. "W,U"
    color_identity    TEXT,   -- comma-joined
    power             TEXT,
    toughness         TEXT,
    loyalty           TEXT,
    defense           TEXT,
    rarity            TEXT,
    set_code          TEXT,
    set_name          TEXT,
    set_type          TEXT,   -- core, expansion, promo, token, ...
    collector_number  TEXT,
    lang              TEXT,   -- language code, e.g. "en"
    released_at       TEXT,   -- printing release date (YYYY-MM-DD)
    paper             INTEGER NOT NULL DEFAULT 0, -- 1 when Scryfall games includes "paper"
    games             TEXT,   -- comma-joined Scryfall games: paper, mtgo, arena
    promo             INTEGER NOT NULL DEFAULT 0, -- Scryfall promo flag
    promo_types       TEXT,   -- json array
    frame_effects     TEXT,   -- json array (showcase, extendedart, etc.)
    frame             TEXT,   -- 1993, 1997, 2003, 2015, future, ...
    border_color      TEXT,   -- black, white, borderless, silver, gold
    finishes          TEXT,   -- json array (nonfoil, foil, etched, ...)
    artist            TEXT,
    reserved          INTEGER NOT NULL DEFAULT 0,
    full_art          INTEGER NOT NULL DEFAULT 0,
    game_changer      INTEGER NOT NULL DEFAULT 0,
    color_indicator   TEXT,   -- comma-joined parent/front-face color indicator
    security_stamp    TEXT,   -- oval, triangle, acorn, arena, ...
    universes_beyond  INTEGER NOT NULL DEFAULT 0, -- set-level UB classification
    produced_mana     TEXT,   -- comma-joined colors this card can produce
    image_small       TEXT,
    image_normal      TEXT,
    image_png         TEXT,
    image_art_crop    TEXT,
    legalities        TEXT,   -- json object
    keywords          TEXT,   -- json array
    related_parts     TEXT,   -- json array copied from Scryfall all_parts
    card_faces        TEXT,   -- json array; preserves future multi-face structure
    layout            TEXT,
    content_kind      TEXT,   -- precomputed CARD_CONTENT_KIND(layout, type_line)
    -- Coloured mana symbols in the cost, counted once per colour. Hybrid
    -- halves count for both of their colours, which is what devotion does
    -- and what "costs two green" is asked to mean.
    pips_w            INTEGER NOT NULL DEFAULT 0,
    pips_u            INTEGER NOT NULL DEFAULT 0,
    pips_b            INTEGER NOT NULL DEFAULT 0,
    pips_r            INTEGER NOT NULL DEFAULT 0,
    pips_g            INTEGER NOT NULL DEFAULT 0,
    pips_c            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_name_nocase ON cards(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_cards_import_latest ON cards(name COLLATE NOCASE, paper, universes_beyond, set_type, released_at DESC);
CREATE INDEX IF NOT EXISTS idx_cards_cmc  ON cards(cmc);
CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(type_line);
CREATE INDEX IF NOT EXISTS idx_cards_set  ON cards(set_code);
CREATE INDEX IF NOT EXISTS idx_cards_lang ON cards(lang);
CREATE INDEX IF NOT EXISTS idx_cards_set_lang ON cards(set_code, lang);
CREATE INDEX IF NOT EXISTS idx_cards_settype_lang ON cards(set_type, lang);
CREATE INDEX IF NOT EXISTS idx_cards_rarity ON cards(rarity);
CREATE INDEX IF NOT EXISTS idx_cards_color_identity ON cards(color_identity);
CREATE INDEX IF NOT EXISTS idx_cards_content_kind ON cards(content_kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_CARD_COLUMN_NAMES = frozenset(
    re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s+", _SCHEMA, flags=re.MULTILINE))


_INDEX_DEFINITIONS = [
    ("idx_cards_name", "CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name)"),
    ("idx_cards_name_nocase",
     "CREATE INDEX IF NOT EXISTS idx_cards_name_nocase ON cards(name COLLATE NOCASE)"),
    ("idx_cards_import_latest",
     "CREATE INDEX IF NOT EXISTS idx_cards_import_latest "
     "ON cards(name COLLATE NOCASE, paper, universes_beyond, set_type, released_at DESC)"),
    ("idx_cards_cmc", "CREATE INDEX IF NOT EXISTS idx_cards_cmc ON cards(cmc)"),
    ("idx_cards_type", "CREATE INDEX IF NOT EXISTS idx_cards_type ON cards(type_line)"),
    ("idx_cards_set", "CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_code)"),
    ("idx_cards_lang", "CREATE INDEX IF NOT EXISTS idx_cards_lang ON cards(lang)"),
    ("idx_cards_set_lang",
     "CREATE INDEX IF NOT EXISTS idx_cards_set_lang ON cards(set_code, lang)"),
    ("idx_cards_settype_lang",
     "CREATE INDEX IF NOT EXISTS idx_cards_settype_lang ON cards(set_type, lang)"),
    ("idx_cards_rarity", "CREATE INDEX IF NOT EXISTS idx_cards_rarity ON cards(rarity)"),
    ("idx_cards_color_identity",
     "CREATE INDEX IF NOT EXISTS idx_cards_color_identity ON cards(color_identity)"),
    ("idx_cards_content_kind",
     "CREATE INDEX IF NOT EXISTS idx_cards_content_kind ON cards(content_kind)"),
]

PRIMARY_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32768",
    "PRAGMA mmap_size=268435456",
    # The UI uses this connection from background threads as well as the Tk
    # thread, so it contends with a bulk rebuild exactly like the reader does.
    # Without this it inherits sqlite3's 5s default -- the shortest tolerance
    # of the three connections, on the one most visible to the user.
    "PRAGMA busy_timeout=30000",
)

READER_PRAGMAS = (
    "PRAGMA query_only=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32768",
    "PRAGMA mmap_size=268435456",
    "PRAGMA busy_timeout=30000",
)

WRITER_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32768",
    "PRAGMA mmap_size=268435456",
    "PRAGMA busy_timeout=90000",
)


def _apply_pragmas(connection, pragmas):
    for pragma in pragmas:
        try:
            connection.execute(pragma)
        except sqlite3.DatabaseError:
            pass


def register_functions(connection, functions):
    for name, arguments, callback in functions:
        try:
            connection.create_function(
                name, arguments, callback, deterministic=True)
        except TypeError:
            connection.create_function(name, arguments, callback)


def _database_is_corrupt(path):
    """True only when an existing file will not open as a SQLite database.

    A throwaway connection probes ``sqlite_master`` and is always closed before
    returning, so no handle lingers to block quarantining the file on Windows.
    A missing file, or an empty/valid database whose ``cards`` table is simply
    absent, is not corruption -- schema init recreates the table. Only a
    malformed file (interrupted write, disk fault) raises ``DatabaseError`` here.
    """
    if not os.path.exists(path):
        return False
    connection = sqlite3.connect(path)
    try:
        connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return False
    except sqlite3.DatabaseError:
        return True
    finally:
        connection.close()


def _quarantine_database(path):
    """Move a corrupt database and its WAL sidecars aside as ``*.corrupt``.

    The bad file is kept for inspection; only the most recent quarantine is
    retained. A rename that fails (locked, permissions) falls back to deletion
    so a fresh rebuild can always proceed rather than stalling on the bad file.
    """
    for suffix in ("", "-wal", "-shm"):
        candidate = path + suffix
        if not os.path.exists(candidate):
            continue
        try:
            os.replace(candidate, candidate + ".corrupt")
        except OSError:
            try:
                os.remove(candidate)
            except OSError:
                pass


def open_primary_connection(path, functions):
    # A truncated or malformed cards.db (interrupted write, disk fault) would
    # otherwise crash startup on the first query with no recourse. Quarantine it
    # and let a fresh empty database be created here; the launch sync then
    # repopulates it from Scryfall exactly as it does on a first launch. Subtle
    # page corruption not caught by this cheap probe still surfaces as an
    # ordinary query error later -- a full integrity scan every launch is not
    # worth its cost. This is the single choke point through which the primary
    # connection is opened, so every CardDB gets the same recovery.
    if _database_is_corrupt(path):
        log.warning("cards.db failed its integrity probe; rebuilding it from scratch")
        _quarantine_database(path)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    _apply_pragmas(connection, PRIMARY_PRAGMAS)
    register_functions(connection, functions)
    return connection


def open_reader_connection(path, functions):
    connection = sqlite3.connect(
        path, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    _apply_pragmas(connection, READER_PRAGMAS)
    register_functions(connection, functions)
    return connection


def open_writer_connection(path):
    connection = sqlite3.connect(path, timeout=90)
    _apply_pragmas(connection, WRITER_PRAGMAS)
    return connection


def _existing_schema_version(connection):
    """Return the committed schema version without modifying the database."""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not row:
        return 0
    row = connection.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def initialize_schema(connection):
    """Migrate and initialize the complete SQLite contract atomically."""
    version = _existing_schema_version(connection)
    statements = ["BEGIN IMMEDIATE;"]
    if version != _SCHEMA_VERSION:
        statements.append("DROP TABLE IF EXISTS cards;")
    statements.append(_SCHEMA)
    statements.append(
        "INSERT OR REPLACE INTO meta (key, value) VALUES "
        f"('schema_version', '{_SCHEMA_VERSION}');"
    )
    statements.append("COMMIT;")
    try:
        connection.executescript("\n".join(statements))
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
