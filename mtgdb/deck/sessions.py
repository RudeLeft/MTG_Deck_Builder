"""Typed, Tk-free state and lifecycle management for open deck sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Optional

from mtgdb.deck.model import Deck


def _default_filters():
    return {"main": {}, "side": {}}


def _default_sorts():
    return {"main": [None, False], "side": [None, False]}


def _normalized_sort(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return [None, False]
    return [value[0], bool(value[1])]


@dataclass
class DeckSession:
    """One open deck plus its independent editor view state."""

    deck: Deck
    path: Optional[str] = None
    dirty: bool = False
    selected: object = None
    filters: dict = field(default_factory=_default_filters)
    sorts: dict = field(default_factory=_default_sorts)

    def __post_init__(self):
        filters = self.filters if isinstance(self.filters, dict) else {}
        sorts = self.sorts if isinstance(self.sorts, dict) else {}
        self.filters = {
            "main": dict(filters.get("main", {}))
            if isinstance(filters.get("main", {}), dict) else {},
            "side": dict(filters.get("side", {}))
            if isinstance(filters.get("side", {}), dict) else {},
        }
        self.sorts = {
            "main": _normalized_sort(sorts.get("main")),
            "side": _normalized_sort(sorts.get("side")),
        }
        if not (
                isinstance(self.selected, (list, tuple))
                and len(self.selected) == 2
                and self.selected[1] in ("main", "side")):
            self.selected = None
        elif isinstance(self.selected, list):
            self.selected = tuple(self.selected)
        if self.path is not None and not isinstance(self.path, str):
            self.path = None

    @property
    def title(self):
        name = (self.deck.name or "Untitled Deck").strip() or "Untitled Deck"
        return name + ("*" if self.dirty else "")


class DeckSessionManager:
    """Maintain one valid active index and at least one open deck session."""

    def __init__(
            self, sessions: Optional[Iterable[DeckSession]] = None,
            active_index: int = 0,
            deck_factory: Callable[[], Deck] = Deck):
        self._deck_factory = deck_factory
        self._sessions = list(sessions or ())
        if not self._sessions:
            self._sessions.append(DeckSession(self._deck_factory()))
        self._active_index = self._clamp(active_index)

    def _clamp(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        return max(0, min(index, len(self._sessions) - 1))

    def __len__(self):
        return len(self._sessions)

    def __iter__(self) -> Iterator[DeckSession]:
        return iter(self._sessions)

    def __getitem__(self, index):
        return self._sessions[index]

    @property
    def active_index(self):
        return self._active_index

    @property
    def active(self):
        return self._sessions[self._active_index]

    def is_valid_index(self, index):
        return isinstance(index, int) and 0 <= index < len(self._sessions)

    def activate(self, index):
        if not self.is_valid_index(index):
            return False
        changed = index != self._active_index
        self._active_index = index
        return changed

    def append(self, session):
        if not isinstance(session, DeckSession):
            raise TypeError("session must be a DeckSession")
        self._sessions.append(session)
        self._active_index = len(self._sessions) - 1
        return self._active_index

    def remove(self, index):
        if not self.is_valid_index(index):
            return None
        removed = self._sessions.pop(index)
        if not self._sessions:
            self._sessions.append(DeckSession(self._deck_factory()))
            self._active_index = 0
        elif index < self._active_index:
            self._active_index -= 1
        elif index == self._active_index:
            self._active_index = min(index, len(self._sessions) - 1)
        return removed

    def replace(self, sessions, active_index=0):
        replacement = list(sessions or ())
        if not all(isinstance(session, DeckSession) for session in replacement):
            raise TypeError("all sessions must be DeckSession instances")
        self._sessions = replacement or [DeckSession(self._deck_factory())]
        self._active_index = self._clamp(active_index)

    def mark_active_dirty(self):
        changed = not self.active.dirty
        self.active.dirty = True
        return changed
