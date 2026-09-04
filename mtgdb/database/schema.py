"""SQLite schema, indexes, migration, and connection configuration."""

import re
import sqlite3



_SCHEMA_VERSION = 10

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
    layout            TEXT
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
]

PRIMARY_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA cache_size=-32768",
    "PRAGMA mmap_size=268435456",
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


def open_primary_connection(path, functions):
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
