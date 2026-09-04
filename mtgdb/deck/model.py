"""Mutable deck state and board operations with no UI or database dependency."""

from __future__ import annotations


BOARDS = ("main", "side")


class Deck:
    """A collection of exact card printings, quantities, and board locations."""

    def __init__(self, name="Untitled Deck", fmt="commander"):
        self.name = name
        self.fmt = fmt
        self._entries = {}
        self._generation = 0

    @property
    def generation(self):
        return self._generation

    def _changed(self):
        self._generation += 1

    def add(self, card, board="main", qty=1):
        if board not in BOARDS:
            raise ValueError(board)
        try:
            quantity = int(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid quantity: {qty!r}") from exc
        if quantity <= 0:
            raise ValueError("Quantity must be greater than zero.")
        card_id = card.get("id") if isinstance(card, dict) else None
        if not card_id:
            raise ValueError("Card must include a Scryfall printing id.")
        key = (card_id, board)
        if key in self._entries:
            self._entries[key]["qty"] += quantity
        else:
            self._entries[key] = {
                "card": card, "qty": quantity, "board": board}
        self._changed()

    def set_qty(self, card_id, board, qty):
        if board not in BOARDS:
            raise ValueError(board)
        try:
            quantity = int(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid quantity: {qty!r}") from exc
        key = (card_id, board)
        if key in self._entries:
            old = self._entries[key]["qty"]
            if quantity <= 0:
                del self._entries[key]
                self._changed()
            elif old != quantity:
                self._entries[key]["qty"] = quantity
                self._changed()

    def change_qty(self, card_id, board, delta):
        if board not in BOARDS:
            raise ValueError(board)
        try:
            change = int(delta)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid quantity change: {delta!r}") from exc
        key = (card_id, board)
        if key in self._entries:
            self.set_qty(card_id, board, self._entries[key]["qty"] + change)

    def remove(self, card_id, board):
        if board not in BOARDS:
            raise ValueError(board)
        if self._entries.pop((card_id, board), None) is not None:
            self._changed()

    def move(self, card_id, from_board, to_board):
        if from_board not in BOARDS:
            raise ValueError(from_board)
        if to_board not in BOARDS:
            raise ValueError(to_board)
        if from_board == to_board:
            return
        key = (card_id, from_board)
        if key in self._entries:
            entry = self._entries[key]
            # add() records one generation; collapse move to one logical mutation.
            before = self._generation
            self.add(entry["card"], to_board, entry["qty"])
            del self._entries[key]
            self._generation = before + 1

    def clear(self):
        if self._entries:
            self._entries.clear()
            self._changed()

    def iter_entries(self, board=None):
        """Unsorted internal iteration for statistics/persistence hot paths."""
        if board is None:
            return iter(self._entries.values())
        return (entry for entry in self._entries.values() if entry["board"] == board)

    def entries(self, board=None, *, sorted_by_name=True):
        items = list(self.iter_entries(board))
        if not sorted_by_name:
            return items
        return sorted(items, key=lambda entry: entry["card"].get("name", ""))

    def snapshot_entries(self):
        """Cheap detached structural snapshot; card records are treated as immutable."""
        return tuple(
            (entry["card"], int(entry["qty"]), entry["board"])
            for entry in self._entries.values()
        )

    def total(self, board="main"):
        return sum(
            entry["qty"] for entry in self._entries.values()
            if entry["board"] == board)

    def unique(self, board="main"):
        return sum(
            1 for entry in self._entries.values()
            if entry["board"] == board)

    def stats(self):
        """Compatibility method delegating calculations to deck_analysis."""
        from mtgdb.deck.analysis import deck_stats
        return deck_stats(self)

    def to_text(self):
        """Compatibility method delegating TXT serialization to deck_io."""
        from mtgdb.deck.io import deck_to_text
        return deck_to_text(self)

    @classmethod
    def from_text(cls, text, db, name="Imported Deck", fmt="commander",
                  allowed_set_types=None, allowed_set_codes=None,
                  paper_only=True, lang=None):
        """Compatibility constructor delegating TXT parsing to deck_io."""
        from mtgdb.deck.io import deck_from_text
        return deck_from_text(
            text, db, name=name, fmt=fmt,
            allowed_set_types=allowed_set_types,
            allowed_set_codes=allowed_set_codes,
            paper_only=paper_only,
            lang=lang,
            deck_class=cls)
