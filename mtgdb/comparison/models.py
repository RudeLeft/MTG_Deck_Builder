"""Tk-free exact-printing comparison state."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from mtgdb.core.scryfall_json import json_list


MIN_COMPARISON_CARDS = 2
MAX_COMPARISON_CARDS = 7


@dataclass(frozen=True)
class ComparisonMutation:
    status: str
    card: dict | None
    count: int
    changed: bool


def comparison_card_id(card):
    if not isinstance(card, dict):
        return ""
    return str(
        card.get("id") or card.get("oracle_id") or card.get("name") or "")


class ComparisonCollection:
    """Insertion-ordered collection of two through seven exact printings."""

    def __init__(self, maximum=MAX_COMPARISON_CARDS):
        self.maximum = min(
            MAX_COMPARISON_CARDS, max(MIN_COMPARISON_CARDS, int(maximum)))
        self._cards = OrderedDict()

    def __len__(self):
        return len(self._cards)

    def __contains__(self, card_id):
        return str(card_id or "") in self._cards

    def cards(self):
        return tuple(self._cards.values())

    def items(self):
        return tuple(self._cards.items())

    def add(self, card, toggle=False):
        card_id = comparison_card_id(card)
        if not card_id:
            return ComparisonMutation("invalid", None, len(self), False)
        existing = self._cards.get(card_id)
        if existing is not None:
            if toggle:
                del self._cards[card_id]
                return ComparisonMutation("removed", existing, len(self), True)
            return ComparisonMutation("duplicate", existing, len(self), False)
        if len(self) >= self.maximum:
            return ComparisonMutation("full", None, len(self), False)
        self._cards[card_id] = card
        return ComparisonMutation("added", card, len(self), True)

    def remove(self, card_id):
        card = self._cards.pop(str(card_id or ""), None)
        return ComparisonMutation(
            "removed" if card is not None else "missing",
            card, len(self), card is not None)

    def clear(self):
        changed = bool(self._cards)
        self._cards.clear()
        return ComparisonMutation(
            "cleared" if changed else "unchanged", None, 0, changed)


def comparison_json_list(value):
    """Parse a Scryfall JSON list field (e.g. card_faces) into a Python list."""
    return json_list(value)
