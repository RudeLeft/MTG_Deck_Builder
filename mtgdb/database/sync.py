"""Tk-free Scryfall card sync plus trusted Wizards taxonomy retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse

from mtgdb.core.background_jobs import (
    GenerationalWorker, JobCancelled, check_cancel,
)

import mtgdb.core.net as net

from mtgdb.database.authorities import SCRYFALL_CATALOGS
from mtgdb.database.bulk_import import iter_card_objects
from mtgdb.database.schema import (
    CARD_TYPES_ATTEMPT_META_KEY, CARD_TYPES_ERROR_META_KEY,
    RULES_SUPERTYPES_ATTEMPT_META_KEY, RULES_SUPERTYPES_ERROR_META_KEY,
    RULES_SUPERTYPES_META_KEY,
)


log = logging.getLogger("mtg")

API_BASE = "https://api.scryfall.com"
AUTO_SYNC_SECONDS = 48 * 60 * 60
BULK_KIND = "default_cards"
WIZARDS_RULES_PAGE_URL = "https://magic.wizards.com/en/rules"


class RulesTaxonomyError(ValueError):
    """Raised when an official rules document cannot be parsed unambiguously."""


class _RulesPageLinkParser(HTMLParser):
    """Collect anchor destinations/text from the official Wizards Rules page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if str(tag).casefold() != "a":
            return
        href = next((value for name, value in attrs
                     if str(name).casefold() == "href"), None)
        self._href = str(href or "").strip() or None
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(str(data or ""))

    def handle_endtag(self, tag):
        if str(tag).casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None
            self._text = []


def _official_rules_txt_url(value, page_url=WIZARDS_RULES_PAGE_URL):
    """Canonicalize one explicit official Wizards Comprehensive Rules TXT URL.

    Wizards page payloads may expose a human-readable asset path containing
    ordinary spaces (for example ``MagicCompRules 20260819.txt``). Browsers
    percent-encode those spaces before transport, while ``urllib`` correctly
    refuses to construct a request from the raw form. Canonicalize legal path
    characters here, at the discovery/authority boundary, and reject actual
    C0/DEL controls instead of allowing transport code to reinterpret them.
    """
    raw = str(value or "").strip()
    if not raw or any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return None
    try:
        absolute = urllib.parse.urljoin(page_url, raw)
        parsed = urllib.parse.urlsplit(absolute)
    except (TypeError, ValueError):
        return None
    if parsed.scheme.casefold() != "https":
        return None
    if (parsed.hostname or "").casefold() != "media.wizards.com":
        return None

    # Preserve existing percent escapes while encoding raw spaces/non-ASCII
    # characters into an RFC-safe request target. Query separators remain
    # structural; fragments are deliberately discarded below.
    path = urllib.parse.quote(
        parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(
        parsed.query, safe="%=&?/:;+,@!$'()*-._~")
    if not path.casefold().endswith(".txt") or "magiccomprules" not in path.casefold():
        return None
    canonical = urllib.parse.urlunsplit((
        "https", parsed.netloc, path, query, ""))
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in canonical):
        return None
    return canonical


def _embedded_rules_txt_candidates(html, page_url=WIZARDS_RULES_PAGE_URL):
    """Find official TXT assets embedded in SSR/JSON page source.

    Wizards' site can expose document links either as ordinary anchors or as
    serialized page data consumed by the frontend. This fallback does not guess
    a filename; it only recognizes explicit official media.wizards.com TXT URLs
    already present in the fetched Rules-page source.
    """
    normalized = unescape(str(html or ""))
    normalized = (normalized.replace(r"\/", "/")
                  .replace(r"\u002F", "/").replace(r"\u002f", "/")
                  .replace(r"\u003A", ":").replace(r"\u003a", ":"))
    # Serialized page data may carry the same human-readable path with raw
    # spaces. Stop only at quoting/markup/control boundaries, then let the
    # authority validator above canonicalize a safe request URL.
    pattern = re.compile(
        r"https://media\.wizards\.com/[^\r\n\t\"'<>]+?\.txt(?:\?[^\r\n\t\"'<>]*)?",
        flags=re.IGNORECASE)
    found = []
    for match in pattern.findall(normalized):
        candidate = _official_rules_txt_url(match, page_url)
        if candidate:
            found.append(candidate)
    return list(dict.fromkeys(found))


def _discover_comprehensive_rules_txt(page_bytes, page_url=WIZARDS_RULES_PAGE_URL):
    """Return the unique current Comprehensive Rules TXT link from Wizards."""
    try:
        html = bytes(page_bytes).decode("utf-8-sig")
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise RulesTaxonomyError("Wizards Rules page is not valid UTF-8 HTML") from exc
    parser = _RulesPageLinkParser()
    parser.feed(html)
    exact = []
    fallback = []
    for href, text in parser.links:
        candidate = _official_rules_txt_url(href, page_url)
        if not candidate:
            continue
        if text.casefold() == "txt":
            exact.append(candidate)
        else:
            fallback.append(candidate)

    exact = list(dict.fromkeys(exact))
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise RulesTaxonomyError(
            "Wizards Rules page exposed multiple TXT-labeled Comprehensive Rules links")

    candidates = list(dict.fromkeys(
        fallback + _embedded_rules_txt_candidates(html, page_url)))
    if len(candidates) != 1:
        raise RulesTaxonomyError(
            "Wizards Rules page did not expose one unambiguous Comprehensive Rules TXT link")
    return candidates[0]


def _normalize_rules_document(text):
    return (str(text or "").replace("\ufeff", "")
            .replace("\r\n", "\n").replace("\r", "\n")
            .replace("\u00a0", " "))


def _rule_block(text, heading="Supertypes"):
    """Return one numbered section's first lettered subrule by exact heading."""
    normalized = _normalize_rules_document(text)
    heading_re = re.compile(
        rf"(?mi)^(\d+\.\d+)\.\s+{re.escape(heading)}\s*$")
    matches = list(heading_re.finditer(normalized))
    if len(matches) != 1:
        raise RulesTaxonomyError(
            f"Comprehensive Rules must contain one numbered {heading} section")
    section = matches[0].group(1)
    lines = normalized[matches[0].end():].splitlines()
    prefix = f"{section}a"
    collected = []
    started = False
    numbered = re.compile(r"^\d+(?:\.\d+)+(?:[a-z])?\b", re.IGNORECASE)
    for line in lines:
        stripped = line.strip()
        if not started:
            if stripped.casefold().startswith(prefix.casefold() + " "):
                collected.append(stripped[len(prefix):].strip())
                started = True
            continue
        if numbered.match(stripped):
            break
        if stripped:
            collected.append(stripped)
    block = " ".join(collected).strip()
    if not started or not block:
        raise RulesTaxonomyError(
            f"Comprehensive Rules section {section} has no readable {prefix} subrule")
    return section, block


def _parse_english_term_list(value):
    """Parse a strict comma/final-and enumeration without knowing its values."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise RulesTaxonomyError("Supertype declaration is empty")
    parts = [part.strip() for part in text.split(",")]
    if len(parts) == 1:
        pair = re.split(r"\s+and\s+", parts[0], maxsplit=1, flags=re.IGNORECASE)
        parts = [part.strip() for part in pair]
    else:
        leading_and = re.match(r"(?i)^and\s+(.+)$", parts[-1])
        if leading_and:
            parts[-1] = leading_and.group(1).strip()
        else:
            tail = re.split(
                r"\s+and\s+", parts[-1], maxsplit=1, flags=re.IGNORECASE)
            if len(tail) == 2:
                parts[-1:] = [part.strip() for part in tail]
    clean = []
    seen = set()
    for part in parts:
        part = part.strip().strip('"\'“”')
        if not part or len(part) > 80:
            raise RulesTaxonomyError("Supertype declaration contains an invalid term")
        if not any(char.isalpha() for char in part):
            raise RulesTaxonomyError("Supertype term contains no letters")
        if any(not (char.isalpha() or char in " -'’") for char in part):
            raise RulesTaxonomyError("Supertype term contains unsupported punctuation")
        key = part.casefold()
        if key in seen:
            raise RulesTaxonomyError("Supertype declaration contains a duplicate term")
        seen.add(key)
        clean.append(part)
    if not (1 <= len(clean) <= 25):
        raise RulesTaxonomyError("Supertype declaration has an implausible term count")
    return clean


def _extract_comprehensive_rules_supertypes(text):
    """Strictly extract official Supertype values from the Supertypes subrule."""
    section, block = _rule_block(text, "Supertypes")
    patterns = (
        r"\bThe\s+supertypes\s+are\s*:?[ \t]*(.+?)\.",
        r"\bThe\s+following\s+are\s+supertypes\s*:?[ \t]*(.+?)\.",
        r"\bThere\s+are\s+(?:\d+|[A-Za-z-]+)\s+supertypes\s*:[ \t]*(.+?)\.",
    )
    matches = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, block, flags=re.IGNORECASE))
    matches = list(dict.fromkeys(" ".join(value.split()) for value in matches))
    if len(matches) != 1:
        raise RulesTaxonomyError(
            f"Comprehensive Rules {section}a has no unambiguous complete Supertype enumeration")
    return _parse_english_term_list(matches[0])


def _rules_effective_date(text):
    match = re.search(
        r"(?mi)^These rules are effective as of ([^.]+)\.\s*$",
        _normalize_rules_document(text))
    return " ".join(match.group(1).split()) if match else ""


class DatabaseSyncCancelled(JobCancelled):
    """Raised when shutdown cooperatively stops an incomplete refresh."""


@dataclass(frozen=True)
class DatabaseSyncEvent:
    kind: str
    generation: int
    stage: str = ""
    payload: object = None


@dataclass(frozen=True)
class DatabaseSyncStart:
    status: str
    generation: int
    reason: str


@dataclass(frozen=True)
class DatabaseSyncPoll:
    progress: DatabaseSyncEvent | None
    terminal: DatabaseSyncEvent | None
    timings: tuple[DatabaseSyncEvent, ...] = ()


class DatabaseSyncService:
    """Synchronize one local card database without importing or calling Tk."""

    def __init__(self, card_db, http=net, clock=time.time,
                 monotonic=time.monotonic):
        self.db = card_db
        self.http = http
        self.clock = clock
        self.monotonic = monotonic
        self._remove_stale_bulk_downloads()

    def _remove_stale_bulk_downloads(self):
        """Remove bulk temp files abandoned by a killed or crashed process.

        The active download is cleaned by a ``finally`` block, but that cannot
        run when the process is terminated mid-sync. Without this startup sweep
        a partial bulk export -- tens of megabytes -- stays in the portable data
        folder until another full sync happens to overwrite the same name.
        """
        try:
            directory = os.path.dirname(os.path.abspath(self.db.path))
            names = os.listdir(directory)
        except (AttributeError, OSError, TypeError):
            return
        for name in names:
            if not (name.startswith("scryfall_") and name.endswith(".download")):
                continue
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass

    def database_age_seconds(self):
        """Return the age of the last successful refresh, or None when empty."""
        if not self.db.has_cards():
            return None
        raw = self.db.get_meta("last_successful_sync_epoch", "")
        try:
            if raw:
                return max(0.0, self.clock() - float(raw))
        except (TypeError, ValueError):
            pass
        try:
            return max(0.0, self.clock() - os.path.getmtime(self.db.path))
        except OSError:
            return AUTO_SYNC_SECONDS

    def due_reason(self):
        age = self.database_age_seconds()
        if age is None:
            return "first_launch"
        if any(not self.db.catalog(name) for name in SCRYFALL_CATALOGS):
            return "catalog_refresh"
        try:
            rules_values = json.loads(
                self.db.get_meta(RULES_SUPERTYPES_META_KEY, "[]"))
        except (TypeError, ValueError):
            rules_values = []
        if not isinstance(rules_values, list) or not rules_values:
            return "catalog_refresh"
        if age >= AUTO_SYNC_SECONDS:
            return "scheduled"
        return None

    @staticmethod
    def _check_cancel(cancel_event):
        check_cancel(
            cancel_event, "Database synchronization was cancelled.",
            DatabaseSyncCancelled)

    def _fetch_bulk_metadata(self, kind, cancel_event):
        self._check_cancel(cancel_event)
        try:
            obj = self.http.get_json(f"{API_BASE}/bulk-data/{kind}")
        except Exception:
            obj = None
        self._check_cancel(cancel_event)
        if isinstance(obj, dict) and obj.get("object") == "list":
            items = obj.get("data", [])
            obj = next((item for item in items if item.get("type") == kind), obj)
        return obj

    def _refresh_rules_supertypes(self, cancel_event):
        """Refresh official Supertype vocabulary, preserving last verified data."""
        self._check_cancel(cancel_event)
        page = self.http.fetch_bytes(WIZARDS_RULES_PAGE_URL)
        self._check_cancel(cancel_event)
        txt_url = _discover_comprehensive_rules_txt(page)
        raw = self.http.fetch_bytes(txt_url)
        self._check_cancel(cancel_event)
        if not (10_000 <= len(raw) <= 10_000_000):
            raise RulesTaxonomyError("Comprehensive Rules TXT size is outside safe bounds")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RulesTaxonomyError("Comprehensive Rules TXT is not valid UTF-8") from exc
        values = _extract_comprehensive_rules_supertypes(text)
        now = f"{self.clock():.6f}"
        metadata = {
            RULES_SUPERTYPES_META_KEY: json.dumps(values, ensure_ascii=False),
            "rules:supertypes_source_url": txt_url,
            "rules:supertypes_document_sha256": hashlib.sha256(raw).hexdigest(),
            "rules:supertypes_effective_date": _rules_effective_date(text),
            "rules:supertypes_verified_epoch": now,
            RULES_SUPERTYPES_ATTEMPT_META_KEY: now,
            RULES_SUPERTYPES_ERROR_META_KEY: "",
        }
        self.db.set_meta_many(metadata)
        return values

    def _refresh_catalogs(self, stage, cancel_event):
        refreshed = {}
        catalog_meta = {}
        total = len(SCRYFALL_CATALOGS) + 1
        for index, name in enumerate(SCRYFALL_CATALOGS, 1):
            self._check_cancel(cancel_event)
            stage("catalogs", (name, index - 1, total))
            attempted = f"{self.clock():.6f}"
            try:
                obj = self.http.get_json(f"{API_BASE}/catalog/{name}")
                data = obj.get("data") if isinstance(obj, dict) else None
                if not isinstance(data, list):
                    raise ValueError(f"Scryfall {name} catalog response has no data list")
                values = [str(value) for value in data if str(value).strip()]
                if name == "card-types" and not values:
                    raise ValueError("Scryfall card-types catalog is empty")
                refreshed[name] = values
                if name == "card-types":
                    catalog_meta[CARD_TYPES_ATTEMPT_META_KEY] = attempted
                    catalog_meta[CARD_TYPES_ERROR_META_KEY] = ""
            except Exception as exc:
                # Catalog endpoints evolve independently. Preserve their old
                # cached values and continue the authoritative card refresh.
                # Card Types are user-visible trusted vocabulary, so retain the
                # exact last failure rather than making missing authority look
                # like a legitimate empty Content/Paper intersection.
                if name == "card-types":
                    catalog_meta[CARD_TYPES_ATTEMPT_META_KEY] = attempted
                    catalog_meta[CARD_TYPES_ERROR_META_KEY] = (
                        f"{type(exc).__name__}: {exc}"[:1000])
        self._check_cancel(cancel_event)
        if refreshed:
            self.db.store_catalogs(refreshed)
        if catalog_meta:
            self.db.set_meta_many(catalog_meta)

        stage("catalogs", ("Comprehensive Rules supertypes", total - 1, total))
        rules_refreshed = False
        try:
            self._refresh_rules_supertypes(cancel_event)
            rules_refreshed = True
        except DatabaseSyncCancelled:
            raise
        except Exception as exc:
            # Fail closed: never invent or fall back to Scryfall's broader
            # supertype catalog. A previous verified rules list remains intact.
            error = f"{type(exc).__name__}: {exc}"[:1000]
            self.db.set_meta_many({
                RULES_SUPERTYPES_ATTEMPT_META_KEY: f"{self.clock():.6f}",
                RULES_SUPERTYPES_ERROR_META_KEY: error,
            })
            log.warning("Comprehensive Rules supertype refresh failed: %s", exc)
        self._check_cancel(cancel_event)
        stage("catalogs", ("", total, total))
        return len(refreshed) + int(rules_refreshed)

    def _record_compatibility_diagnostics(self):
        """Refresh internal upstream-change diagnostics without risking sync."""
        if not self.db.has_cards():
            return {}
        try:
            report = self.db.refresh_compatibility_diagnostics()
        except Exception:
            log.exception("Upstream compatibility diagnostics failed")
            return {}
        if report.get("needs_attention"):
            log.warning("Upstream compatibility change detected: %s", report)
        return report

    def refresh_catalogs(self, progress_cb=None, cancel_event=None):
        """Refresh trusted Scryfall/Wizards taxonomy sources independently."""
        def stage(name, payload):
            if (progress_cb and name == "catalogs"
                    and isinstance(payload, (tuple, list))
                    and len(payload) >= 3):
                progress_cb(*payload[:3])

        result = self._refresh_catalogs(stage, cancel_event)
        self._record_compatibility_diagnostics()
        return result

    def sync(self, progress_cb=None, cancel_event=None):
        """Refresh Default Cards with measurable, cancellable phases."""
        kind = BULK_KIND
        phase_clock = {"name": None, "started": self.monotonic()}

        def phase_group(name, info):
            if name in ("load_start", "load_source", "load"):
                return "database_build"
            if name == "maintenance" and isinstance(info, (tuple, list)) and info:
                return f"maintenance:{info[0]}"
            if name in (
                    "meta", "catalogs", "download", "current", "cleanup", "done"):
                return name
            return str(name)

        def stage(name, info=None):
            group = phase_group(name, info)
            previous = phase_clock["name"]
            now = self.monotonic()
            if previous is not None and group != previous:
                elapsed = now - phase_clock["started"]
                if progress_cb:
                    progress_cb("timing", (previous, elapsed))
                phase_clock["started"] = now
            phase_clock["name"] = group
            if progress_cb:
                progress_cb(name, info)

        stage("meta", "Checking Scryfall bulk-data metadata...")
        obj = self._fetch_bulk_metadata(kind, cancel_event)
        download_url = None
        updated_at = ""
        pretty = kind
        if isinstance(obj, dict):
            download_url = obj.get("jsonl_download_uri") or obj.get("download_uri")
            updated_at = obj.get("updated_at") or ""
            pretty = obj.get("name") or kind
        if not download_url:
            download_url = f"{API_BASE}/bulk-data/{kind}?format=file"

        stage("catalogs", "Refreshing trusted Magic terminology...")
        self._refresh_catalogs(stage, cancel_event)

        self._check_cancel(cancel_event)
        previous_updated = self.db.get_meta("last_sync_updated_at", "")
        if self.db.has_cards() and updated_at and updated_at == previous_updated:
            stage("current", "Local card data already matches Scryfall's latest bulk revision.")
            self.db.set_meta_many({
                "last_successful_sync_epoch": f"{self.clock():.6f}",
            })
            self._record_compatibility_diagnostics()
            total = self.db.count()
            stage("done", total)
            return total

        stage("meta", f"Downloading {pretty}...")
        db_dir = os.path.dirname(os.path.abspath(self.db.path))
        os.makedirs(db_dir, exist_ok=True)
        temporary_path = os.path.join(db_dir, f"scryfall_{kind}.download")

        try:
            def downloaded(read, total):
                self._check_cancel(cancel_event)
                stage("download", (read, total))

            self.http.download(download_url, temporary_path, progress_cb=downloaded)
            self._check_cancel(cancel_event)
            stage("load_start", os.path.getsize(temporary_path))

            parse_state = {
                "read": 0,
                "total": os.path.getsize(temporary_path),
                "loaded": 0,
                "estimated_cards": None,
            }

            def parsed(read, total):
                self._check_cancel(cancel_event)
                parse_state["read"] = read
                parse_state["total"] = total
                stage("load_source", (
                    read, total, parse_state["loaded"],
                    parse_state["estimated_cards"]))

            def loaded_cards(count):
                self._check_cancel(cancel_event)
                parse_state["loaded"] = int(count or 0)
                read = float(parse_state["read"] or 0)
                total_bytes = float(parse_state["total"] or 0)
                if read > 0 and total_bytes > 0:
                    fraction = min(1.0, read / total_bytes)
                    if fraction >= 0.02:
                        estimate = max(
                            parse_state["loaded"],
                            int(parse_state["loaded"] / fraction))
                        previous = parse_state["estimated_cards"]
                        if previous is None:
                            parse_state["estimated_cards"] = estimate
                        else:
                            parse_state["estimated_cards"] = int(
                                previous * 0.75 + estimate * 0.25)
                stage("load", (
                    parse_state["loaded"], parse_state["read"],
                    parse_state["total"], parse_state["estimated_cards"]))

            def maintenance(phase, current, total):
                self._check_cancel(cancel_event)
                stage("maintenance", (phase, current, total))

            def card_objects():
                for card in iter_card_objects(temporary_path, progress_cb=parsed):
                    self._check_cancel(cancel_event)
                    yield card

            total = self.db.load_cards(
                card_objects(), progress_cb=loaded_cards,
                maintenance_cb=maintenance, minimum_count=1000)

            # load_cards commits atomically. Once it returns, finish the small
            # metadata write even if shutdown is requested at the same instant.
            metadata = {
                "last_sync_kind": kind,
                "last_successful_sync_epoch": f"{self.clock():.6f}",
            }
            if updated_at:
                metadata["last_sync_updated_at"] = updated_at
            self.db.set_meta_many(metadata)
            self._record_compatibility_diagnostics()
            stage("cleanup", "Removing the temporary bulk-data file.")
        finally:
            try:
                os.remove(temporary_path)
            except OSError:
                pass

        stage("done", total)
        return total


class DatabaseSyncController(GenerationalWorker):
    """Own one synchronization worker and its generation-tagged event queue."""

    def __init__(self, service):
        super().__init__()
        self.service = service

    def due_reason(self):
        return self.service.due_reason()

    def start(self, reason="manual"):
        generation = self._begin()
        if generation is None:
            return DatabaseSyncStart("busy", self.generation, reason)

        def progress(stage, payload):
            if stage == "done":
                return
            self.events.put(DatabaseSyncEvent(
                "progress", generation, stage, payload))

        def worker():
            try:
                log.info("Card database update starting (Scryfall default_cards)")
                total = self.service.sync(
                    progress_cb=progress, cancel_event=self._cancel_event)
                log.info("Card database update finished: %s cards", total)
                event = DatabaseSyncEvent("done", generation, payload=total)
            except JobCancelled:
                log.info("Card database update cancelled during shutdown")
                event = DatabaseSyncEvent("cancelled", generation)
            except Exception as exc:
                log.exception("Card database update failed")
                event = DatabaseSyncEvent("error", generation, payload=str(exc))
            self.events.put(event)

        self._spawn(worker, "database-sync")
        return DatabaseSyncStart("started", generation, reason)

    def poll(self):
        latest_progress = None
        terminal = None
        timings = []
        for event in self._drain():
            if event.kind == "progress":
                if event.stage == "timing":
                    timings.append(event)
                else:
                    latest_progress = event
            else:
                terminal = event
        if terminal is not None:
            self.running = False
        return DatabaseSyncPoll(latest_progress, terminal, tuple(timings))

    def shutdown(self, timeout=3.0):
        return super().shutdown(timeout)
