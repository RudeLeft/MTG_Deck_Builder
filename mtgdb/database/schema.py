"""SQLite schema, indexes, migration, and connection configuration."""

import logging
import os
import re
import sqlite3
import time


log = logging.getLogger("mtg")


_SCHEMA_VERSION = 18

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

# When the trusted catalogs (Scryfall's and the Wizards rules) were last
# attempted, whether or not every one arrived.  The due policy reads it so a
# source that keeps failing is retried on a schedule instead of on every launch.
CATALOGS_ATTEMPT_META_KEY = "catalogs:last_attempt_epoch"

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
    content_kind      TEXT NOT NULL, -- precomputed CARD_CONTENT_KIND -- NOT NULL so an unpopulated row fails loudly rather than vanishing from every search
    colors_mask       INTEGER NOT NULL DEFAULT 0, -- WUBRGC bitmask of colors
    identity_mask     INTEGER NOT NULL DEFAULT 0, -- WUBRGC bitmask of color_identity
    produced_mask     INTEGER NOT NULL DEFAULT 0, -- WUBRGC bitmask of produced_mana
    trait_flags       INTEGER NOT NULL DEFAULT 0, -- packed per-card boolean traits
    -- Coloured mana symbols in the cost, counted once per colour. Hybrid
    -- halves count for both of their colours, which is what devotion does
    -- and what "costs two green" is asked to mean.
    pips_w            INTEGER NOT NULL DEFAULT 0,
    pips_u            INTEGER NOT NULL DEFAULT 0,
    pips_b            INTEGER NOT NULL DEFAULT 0,
    pips_r            INTEGER NOT NULL DEFAULT 0,
    pips_g            INTEGER NOT NULL DEFAULT 0,
    pips_c            INTEGER NOT NULL DEFAULT 0,
    -- The other face of a two-faced card (transform, modal, flip).  Search
    -- matches a card when EITHER face fits (SRCH-052), while the row and every
    -- table column keep showing the front face.  back_mana_cost holds only the
    -- cost NOT already inside mana_cost: split and adventure costs arrive as one
    -- "A // B" string, so theirs stays empty.  The pips_* columns and trait_flags
    -- above are computed over both faces.
    back_mana_cost    TEXT NOT NULL DEFAULT '',
    back_power        TEXT,
    back_toughness    TEXT,
    back_loyalty      TEXT,
    back_defense      TEXT
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

-- Normalized type/subtype/keyword membership, one row per (card, term).  The
-- term is every contiguous n-gram of a card's left type-line words (types and
-- supertypes) or subtype words, and each casefolded keyword, so a query matches
-- the exact CARD_HAS_TYPE / CARD_HAS_SUBTYPE / keyword semantics with an indexed
-- equality lookup instead of a per-row SQL function.
CREATE TABLE IF NOT EXISTS card_types (
    card_id TEXT NOT NULL,
    term    TEXT NOT NULL,
    PRIMARY KEY (card_id, term)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS card_subtypes (
    card_id TEXT NOT NULL,
    term    TEXT NOT NULL,
    PRIMARY KEY (card_id, term)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS card_keywords (
    card_id TEXT NOT NULL,
    term    TEXT NOT NULL,
    PRIMARY KEY (card_id, term)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_card_types_term ON card_types(term);
CREATE INDEX IF NOT EXISTS idx_card_subtypes_term ON card_subtypes(term);
CREATE INDEX IF NOT EXISTS idx_card_keywords_term ON card_keywords(term);
"""

# Membership tables cleared and repopulated with the cards table on every import.
MEMBERSHIP_TABLES = ("card_types", "card_subtypes", "card_keywords")

# Only the ``cards`` table's own columns: the schema now defines other tables
# (meta, the membership tables) whose columns must not leak into the card
# projection contract, so scan just the cards CREATE TABLE block. The block
# ends at the closing paren on its own line ("\n);"), never at a ");" that
# happens to appear inside a column comment -- matching a bare ");" once
# truncated the contract at a comment and silently dropped every later column.
_CARDS_TABLE_SQL = _SCHEMA.split(
    "CREATE TABLE IF NOT EXISTS cards (", 1)[1].split("\n);", 1)[0]
_CARD_COLUMN_NAMES = frozenset(
    re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s+", _CARDS_TABLE_SQL, flags=re.MULTILINE))


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
    ("idx_card_types_term",
     "CREATE INDEX IF NOT EXISTS idx_card_types_term ON card_types(term)"),
    ("idx_card_subtypes_term",
     "CREATE INDEX IF NOT EXISTS idx_card_subtypes_term ON card_subtypes(term)"),
    ("idx_card_keywords_term",
     "CREATE INDEX IF NOT EXISTS idx_card_keywords_term ON card_keywords(term)"),
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


# SQLite result codes that mean the FILE is damaged.  Everything else a probe can
# raise (busy, locked, I/O error, cannot open, read-only) says something about the
# moment, not the file: antivirus, a backup tool or OneDrive holding cards.db
# produces exactly those, and a healthy database must never be discarded for it.
_SQLITE_CORRUPT = 11
_SQLITE_NOTADB = 26

# Waits between probe attempts while the file is unavailable (seconds).
PROBE_RETRY_DELAYS = (0.2, 0.4, 0.8)

# Written beside the database to ask the NEXT launch to quarantine and rebuild
# it.  A damaged page deep inside the file passes the startup probe and fails
# only when something reads it; the connections open then keep the file locked
# on Windows, so the rebuild is deferred to a launch with nothing open.
REBUILD_SENTINEL_SUFFIX = ".rebuild"


def is_corruption_error(exc):
    """True only for an error that means the database FILE is damaged."""
    if not isinstance(exc, sqlite3.DatabaseError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (int(code) & 0xFF) in (_SQLITE_CORRUPT, _SQLITE_NOTADB)
    text = str(exc).casefold()
    return "malformed" in text or "not a database" in text


def request_database_rebuild(path):
    """Ask the next launch to set ``path`` aside and start a fresh database."""
    try:
        with open(path + REBUILD_SENTINEL_SUFFIX, "w", encoding="utf-8") as sentinel:
            sentinel.write("damaged; rebuild on next launch\n")
    except OSError:
        log.exception("Could not write the database rebuild request")
        return False
    return True


def _database_is_corrupt(path):
    """True only when an existing file is genuinely damaged.

    A throwaway connection probes ``sqlite_master`` and is always closed before
    returning, so no handle lingers to block quarantining the file on Windows.
    A missing file, or an empty/valid database whose ``cards`` table is simply
    absent, is not corruption -- schema init recreates the table.  Only an
    error that says the file is damaged (see ``is_corruption_error``) counts.
    A transient condition -- locked, disk I/O error, cannot open -- is retried
    briefly and then treated as "unknown", never as corruption: quarantining a
    healthy database throws away a 500 MB download.
    """
    if not os.path.exists(path):
        return False
    delays = iter(PROBE_RETRY_DELAYS)
    while True:
        connection = None
        try:
            connection = sqlite3.connect(path)
            connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return False
        except sqlite3.DatabaseError as exc:
            if is_corruption_error(exc):
                return True
            failure = exc
        finally:
            if connection is not None:
                connection.close()
        delay = next(delays, None)
        if delay is None:
            log.warning(
                "cards.db is unavailable (%s); leaving it in place instead of "
                "treating it as corrupt", failure)
            return False
        time.sleep(delay)


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
    sentinel = path + REBUILD_SENTINEL_SUFFIX
    if os.path.exists(sentinel) or _database_is_corrupt(path):
        log.warning("cards.db is damaged; rebuilding it from scratch")
        _quarantine_database(path)
        if not os.path.exists(path):
            # Gone (or replaced by a fresh file below): the request is met.  A
            # file that could not be moved keeps the request for the next launch.
            try:
                os.remove(sentinel)
            except OSError:
                pass
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
        # Membership tables are derived from cards; drop them on any version
        # change so a rebuilt snapshot never inherits stale (card, term) rows.
        for table in MEMBERSHIP_TABLES:
            statements.append(f"DROP TABLE IF EXISTS {table};")
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
