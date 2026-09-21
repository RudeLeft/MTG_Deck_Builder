"""Tk-free workspace schema, atomic storage, and crash-recovery snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

from mtgdb.core.atomic_files import (
    TEMP_SUFFIX, sweep_abandoned_writes, temp_prefix,
)
from mtgdb.core.background_jobs import spawn_daemon

from mtgdb.deck.model import Deck
from mtgdb.deck.sessions import DeckSession


log = logging.getLogger("mtg")

WORKSPACE_VERSION = 1
DEFAULT_RECOVERY_INTERVAL_SECONDS = 30
DEFAULT_RECOVERY_KEEP = 8


@dataclass(frozen=True)
class WorkspaceSaveResult:
    wrote_session: bool
    wrote_recovery: bool
    signature: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Cheap detached UI snapshot; expensive JSON shaping stays off Tk."""

    sessions: tuple
    active_index: int
    search: object
    geometry: object


@dataclass(frozen=True)
class WorkspaceLoadResult:
    payload: object
    sessions: tuple


class WorkspaceRepository:
    """Persist one versioned workspace and a bounded recovery history."""

    def __init__(
            self, data_dir, recovery_interval=DEFAULT_RECOVERY_INTERVAL_SECONDS,
            recovery_keep=DEFAULT_RECOVERY_KEEP, clock=time.time):
        root = Path(data_dir)
        self.session_path = root / "session.json"
        self.recovery_dir = root / "workspace_recovery"
        self.recovery_interval = max(0, int(recovery_interval))
        self.recovery_keep = max(1, int(recovery_keep))
        self._clock = clock
        self._last_signature = None
        self._last_recovery = 0.0
        # Autosave runs constantly, so the window between writing a temporary
        # file and replacing the target is entered often. A process killed
        # inside it leaves the temporary behind for good; sweep those now.
        sweep_abandoned_writes(root, self.session_path.name)
        sweep_abandoned_writes(self.recovery_dir, "session_")

    @staticmethod
    def json_safe(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): WorkspaceRepository.json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [WorkspaceRepository.json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def payload_signature(payload):
        stable = dict(payload)
        stable.pop("saved_at", None)
        return json.dumps(stable, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def deck_to_data(deck):
        return {
            "name": deck.name,
            "format": deck.fmt,
            "entries": [
                {
                    "card": WorkspaceRepository.json_safe(entry["card"]),
                    "qty": int(entry.get("qty", 0)),
                    "board": entry.get("board", "main"),
                }
                for entry in deck.entries()
                if int(entry.get("qty", 0)) > 0
            ],
        }

    @staticmethod
    def deck_from_data(data, card_lookup=None):
        if not isinstance(data, dict):
            return Deck()
        deck = Deck(
            name=str(data.get("name") or "Untitled Deck"),
            fmt=str(data.get("format") or "commander"),
        )
        for item in data.get("entries", []):
            if not isinstance(item, dict):
                continue
            card = item.get("card")
            board = item.get("board", "main")
            try:
                quantity = int(item.get("qty", 0))
            except (TypeError, ValueError):
                quantity = 0
            if not isinstance(card, dict) or not card.get("id") or quantity <= 0:
                continue
            fresh = None
            if card_lookup is not None:
                try:
                    fresh = card_lookup(card.get("id"))
                except Exception:
                    fresh = None
            deck.add(
                fresh or card,
                board if board in ("main", "side") else "main",
                quantity,
            )
        return deck

    @classmethod
    def session_to_data(cls, session):
        return {
            "deck": cls.deck_to_data(session.deck),
            "path": session.path,
            "dirty": bool(session.dirty),
            "selected": cls.json_safe(session.selected),
            "filters": cls.json_safe(session.filters),
            "sorts": cls.json_safe(session.sorts),
        }

    @classmethod
    def session_from_data(cls, data, card_lookup=None):
        if not isinstance(data, dict):
            return None
        return DeckSession(
            deck=cls.deck_from_data(data.get("deck", {}), card_lookup),
            path=data.get("path"),
            dirty=bool(data.get("dirty", False)),
            selected=data.get("selected"),
            filters=data.get("filters", {}),
            sorts=data.get("sorts", {}),
        )

    @staticmethod
    def capture_snapshot(sessions, active_index, search, geometry):
        """Capture only detached structural state while Tk is authoritative.

        Card dictionaries originate from immutable database records and are held
        by reference here; the background writer performs JSON-safe deep shaping.
        """
        session_rows = []
        for session in sessions:
            entries = tuple(
                (entry["card"], int(entry.get("qty", 0)), entry.get("board", "main"))
                for entry in session.deck.iter_entries()
                if int(entry.get("qty", 0)) > 0
            )
            session_rows.append((
                str(session.deck.name), str(session.deck.fmt), entries,
                session.path, bool(session.dirty), session.selected,
                dict(session.filters), dict(session.sorts),
            ))
        return WorkspaceSnapshot(
            sessions=tuple(session_rows),
            active_index=int(active_index),
            search=search,
            geometry=geometry,
        )

    def payload_from_snapshot(self, snapshot):
        decks = []
        for (name, fmt, entries, path, dirty, selected, filters, sorts) in snapshot.sessions:
            decks.append({
                "deck": {
                    "name": name,
                    "format": fmt,
                    "entries": [
                        {
                            "card": self.json_safe(card),
                            "qty": int(qty),
                            "board": board,
                        }
                        for card, qty, board in entries
                    ],
                },
                "path": path,
                "dirty": bool(dirty),
                "selected": self.json_safe(selected),
                "filters": self.json_safe(filters),
                "sorts": self.json_safe(sorts),
            })
        return {
            "version": WORKSPACE_VERSION,
            "saved_at": self._clock(),
            "active_deck": snapshot.active_index,
            "decks": decks,
            "search": self.json_safe(snapshot.search),
            "geometry": self.json_safe(snapshot.geometry),
        }

    def build_payload(self, sessions, active_index, search, geometry):
        return self.payload_from_snapshot(self.capture_snapshot(
            sessions, active_index, search, geometry))

    @staticmethod
    def _valid_payload(data):
        try:
            version = int(data.get("version", 0))
        except (AttributeError, TypeError, ValueError):
            return False
        return (
            isinstance(data, dict)
            and version == WORKSPACE_VERSION
            and isinstance(data.get("decks"), list)
        )

    @classmethod
    def read_candidate(cls, path):
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError, TypeError):
            return None
        return data if cls._valid_payload(data) else None

    @staticmethod
    def _write_json_atomic(path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=temp_prefix(path.name), suffix=TEMP_SUFFIX, dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _recovery_paths(self):
        try:
            return sorted(
                (
                    path for path in self.recovery_dir.iterdir()
                    if path.name.startswith("session_")
                    and path.suffix.casefold() == ".json"
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []

    def _write_recovery_snapshot(self, payload, force=False):
        now = self._clock()
        if (
                not force
                and now - self._last_recovery < self.recovery_interval):
            return False
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        milliseconds = int((now - int(now)) * 1000)
        base = self.recovery_dir / f"session_{stamp}_{milliseconds:03d}.json"
        path = base
        collision = 2
        while path.exists():
            path = base.with_name(f"{base.stem}_{collision}{base.suffix}")
            collision += 1
        self._write_json_atomic(path, payload)
        self._last_recovery = now
        for old in self._recovery_paths()[self.recovery_keep:]:
            try:
                old.unlink()
            except OSError:
                pass
        return True

    def _try_recovery_snapshot(self, payload, force=False):
        """Write supplemental recovery history without invalidating a primary save."""
        try:
            return self._write_recovery_snapshot(payload, force=force)
        except OSError as exc:
            log.warning("Could not write workspace recovery snapshot: %s", exc)
            return False

    def save(self, payload, force=False, recovery=False):
        signature = self.payload_signature(payload)
        changed = signature != self._last_signature
        wrote_session = False
        wrote_recovery = False
        if force or changed:
            self._write_json_atomic(self.session_path, payload)
            self._last_signature = signature
            wrote_session = True
            wrote_recovery = self._try_recovery_snapshot(
                payload, force=recovery)
        elif recovery:
            wrote_recovery = self._try_recovery_snapshot(payload, force=True)
        return WorkspaceSaveResult(
            wrote_session=wrote_session,
            wrote_recovery=wrote_recovery,
            signature=signature,
        )

    def load(self):
        data = self.read_candidate(self.session_path)
        if data is not None:
            return data
        for path in self._recovery_paths():
            data = self.read_candidate(path)
            if data is not None:
                log.warning("Recovered workspace from %s", path)
                return data
        return None

    def remember(self, payload):
        self._last_signature = self.payload_signature(payload)


class WorkspaceSaveWorker:
    """Latest-wins background durability worker for workspace snapshots.

    Tk captures a detached workspace payload and submits it here. JSON
    serialization, temporary-file I/O, fsync, recovery snapshots, and atomic
    replacement all remain on this worker thread. A write already in flight is
    allowed to finish; repeated autosaves collapse to the newest pending
    snapshot.
    """

    def __init__(self, repository):
        self.repository = repository
        self._condition = threading.Condition()
        self._pending = None
        self._running = False
        self._stopping = False
        self._generation = 0
        self._completed_generation = 0
        self._submitted = 0
        self._completed = 0
        self._superseded = 0
        self._failures = 0
        self._last_duration_ms = 0.0
        self._max_duration_ms = 0.0
        self._last_error = None
        self._thread = spawn_daemon(self._run, "mtg-workspace-writer")

    def submit(self, payload, *, force=False, recovery=False):
        """Queue one detached payload; replace an older pending autosave."""
        with self._condition:
            if self._stopping:
                return None
            self._generation += 1
            generation = self._generation
            self._submitted += 1
            if self._pending is not None:
                self._superseded += 1
            self._pending = (generation, payload, bool(force), bool(recovery))
            self._condition.notify_all()
            return generation

    def _run(self):
        while True:
            with self._condition:
                while self._pending is None and not self._stopping:
                    self._condition.wait()
                if self._pending is None and self._stopping:
                    return
                generation, payload, force, recovery = self._pending
                self._pending = None
                self._running = True

            started = time.perf_counter()
            error = None
            try:
                if isinstance(payload, WorkspaceSnapshot):
                    payload = self.repository.payload_from_snapshot(payload)
                self.repository.save(
                    payload, force=force, recovery=recovery)
            except Exception as exc:  # background boundary: retain diagnostics
                error = exc
                log.exception("Could not save workspace session")
            duration_ms = (time.perf_counter() - started) * 1000.0

            with self._condition:
                self._running = False
                self._completed_generation = max(
                    self._completed_generation, generation)
                self._completed += 1
                self._last_duration_ms = duration_ms
                self._max_duration_ms = max(self._max_duration_ms, duration_ms)
                if error is not None:
                    self._failures += 1
                    self._last_error = str(error)
                else:
                    self._last_error = None
                self._condition.notify_all()

    def flush(self, timeout=None):
        """Wait until both the in-flight and latest pending snapshot are done."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while self._running or self._pending is not None:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def shutdown(self, *, flush=True, timeout=None):
        """Stop the writer after optionally making the newest snapshot durable."""
        if flush and not self.flush(timeout=timeout):
            return False
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread.is_alive():
            if timeout is None:
                self._thread.join()
            else:
                self._thread.join(max(0.0, float(timeout)))
        return not self._thread.is_alive()

    def diagnostics(self):
        with self._condition:
            return {
                "submitted": self._submitted,
                "completed": self._completed,
                "superseded": self._superseded,
                "failures": self._failures,
                "pending": int(self._pending is not None),
                "running": int(self._running),
                "last_duration_ms": self._last_duration_ms,
                "max_duration_ms": self._max_duration_ms,
                "last_error": self._last_error,
            }


class WorkspaceLoadWorker:
    """One-shot Tk-free loader/parser/exact-printing hydrator."""

    def __init__(self, repository, card_lookup, bulk_lookup=None):
        self.repository = repository
        self.card_lookup = card_lookup
        # Optional {id: card} batch hydrator. It reads on an independent
        # connection, so restore does not queue behind the primary lock while
        # startup catalog scans hold it.
        self.bulk_lookup = bulk_lookup
        self._lock = threading.Lock()
        self._generation = 0
        self._result = None
        self._error = None
        self._running = False
        self._duration_ms = 0.0
        self._thread = None

    def start(self):
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._result = None
            self._error = None
            self._running = True
        thread = spawn_daemon(
            lambda: self._run(generation), "mtg-workspace-loader")
        with self._lock:
            if generation == self._generation:
                self._thread = thread
        return generation

    def _run(self, generation):
        started = time.perf_counter()
        result = None
        error = None
        try:
            payload = self.repository.load()
            if payload is not None:
                lookup = self.card_lookup
                if self.bulk_lookup is not None:
                    # Hydrate every deck's cards in one concurrent reader pass
                    # rather than a per-card primary-lock lookup that the startup
                    # catalog scans would starve.
                    card_ids = [
                        entry["card"].get("id")
                        for item in payload.get("decks", [])
                        if isinstance(item, dict)
                        for entry in (item.get("deck") or {}).get("entries", [])
                        if isinstance(entry, dict)
                        and isinstance(entry.get("card"), dict)
                    ]
                    try:
                        hydrated = self.bulk_lookup(card_ids)
                        lookup = hydrated.get
                    except Exception:
                        log.exception(
                            "Batch deck hydration failed; using per-card lookup")
                sessions = tuple(
                    session
                    for item in payload.get("decks", [])
                    for session in [self.repository.session_from_data(
                        item, lookup)]
                    if session is not None
                )
                result = WorkspaceLoadResult(payload=payload, sessions=sessions)
        except Exception as exc:  # background boundary
            error = exc
            log.exception("Could not restore workspace session")
        duration = (time.perf_counter() - started) * 1000.0
        with self._lock:
            if generation != self._generation:
                return
            self._result = result
            self._error = error
            self._running = False
            self._duration_ms = duration

    def poll(self, generation):
        with self._lock:
            if generation != self._generation:
                return True, None, None
            if self._running:
                return False, None, None
            return True, self._result, self._error

    def shutdown(self, timeout=None):
        """Invalidate pending delivery and join the loader before DB teardown."""
        with self._lock:
            self._generation += 1
            self._result = None
            self._error = None
            self._running = False
            thread = self._thread
        if thread is not None and thread.is_alive():
            if timeout is None:
                thread.join()
            else:
                thread.join(max(0.0, float(timeout)))
        alive = bool(thread is not None and thread.is_alive())
        with self._lock:
            if not alive and self._thread is thread:
                self._thread = None
        return not alive

    def diagnostics(self):
        with self._lock:
            return {
                "running": int(self._running),
                "generation": self._generation,
                "thread_alive": int(
                    self._thread is not None and self._thread.is_alive()),
                "last_duration_ms": self._duration_ms,
                "last_error": str(self._error) if self._error else None,
            }
