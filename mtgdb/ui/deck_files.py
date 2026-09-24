"""Deck open/save/import and JSON-export presentation workflows."""

import json
import logging
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox

from mtgdb.deck.file_jobs import submit_deck_file_job
from mtgdb.deck.io import deck_from_text, read_deck_text, save_deck_text
from mtgdb.deck.model import Deck

log = logging.getLogger("mtg")


class DeckFileWorkflowMixin:
    """Own deck-file dialogs while parsing/serialization/durability stay off Tk."""

    def _detached_deck_copy(self, deck):
        clone = Deck(name=deck.name, fmt=deck.fmt)
        for card, quantity, board in deck.snapshot_entries():
            if quantity > 0:
                clone.add(card, board, quantity)
        return clone

    def _await_deck_file_job(self, future, on_success, *, error_title):
        """Block until one deck-file job finishes and report whether it wrote.

        Used only where the caller destroys state on the strength of the
        result -- closing a dirty deck session. The disk work still runs on the
        deck-file worker (DUI-017); only the caller waits for its outcome, so a
        failed save can never be mistaken for a completed one.
        """
        try:
            result = future.result()
        except Exception as exc:
            log.exception("%s", error_title)
            messagebox.showerror(error_title, str(exc))
            return False
        on_success(result)
        return True

    def _poll_deck_file_job(self, future, on_success, *, error_title):
        if not future.done():
            try:
                self.after(20, lambda: self._poll_deck_file_job(
                    future, on_success, error_title=error_title))
            except tk.TclError:
                pass
            return
        try:
            result = future.result()
        except Exception as exc:
            log.exception("%s", error_title)
            messagebox.showerror(error_title, str(exc))
            return
        on_success(result)

    def _open_deck(self):
        path = filedialog.askopenfilename(
            title="Open Deck",
            filetypes=[("Text Decklists", "*.txt"), ("All files", "*.*")])
        if not path:
            return

        def load_and_resolve():
            # Deck TXT import is intentionally independent of every interactive
            # Search/Printings filter. Resolve names against the complete local
            # card database so an active filter can never hide a valid deck card.
            # Explicit [SET] / [SET:COLLECTOR] tags remain authoritative inside
            # deck_from_text; untagged names use the resolver's normal ranking
            # across every stored platform, language, set type, and set.
            raw = read_deck_text(path)
            return deck_from_text(
                raw, self.db,
                name=os.path.splitext(os.path.basename(path))[0],
                allowed_set_types=None, allowed_set_codes=None,
                paper_only=False, lang=None)

        def opened(result):
            deck, missing = result
            # Opening a deck never replaces the current workspace deck; it gets
            # its own session/tab and becomes the active deck.
            self._append_deck_session(deck, path=path, dirty=False)
            log.info(
                "Opened deck %r from %s: %d mainboard, %d sideboard, "
                "%d unresolved", deck.name, os.path.basename(path),
                deck.total("main"), deck.total("side"), len(missing))
            if missing:
                messagebox.showwarning(
                    "Some cards not found",
                    "Couldn't match these names:\n\n" + "\n".join(missing[:30]))

        self._status("Opening deck…")
        future = submit_deck_file_job(
            load_and_resolve, name="mtg-deck-import")
        self._poll_deck_file_job(future, opened, error_title="Open failed")

    def _paste_deck(self):
        """Import a decklist from the clipboard using the Open Deck resolver."""
        try:
            raw = self.clipboard_get()
        except tk.TclError:
            raw = ""
        if not str(raw).strip():
            messagebox.showinfo(
                "Paste Decklist",
                "The clipboard is empty. Copy a decklist first, then paste it.")
            return

        def load_and_resolve():
            # Same unrestricted resolution as Open Deck (independent of every
            # interactive Search/Printings filter); a leading "// Name (format)"
            # header still names the deck and explicit [SET]/[SET:COLLECTOR] tags
            # stay authoritative inside deck_from_text.
            return deck_from_text(
                raw, self.db, name="Pasted Deck",
                allowed_set_types=None, allowed_set_codes=None,
                paper_only=False, lang=None)

        def opened(result):
            deck, missing = result
            # A pasted deck has no file of its own yet, so it opens in a new tab
            # as an unsaved (dirty) session.
            self._append_deck_session(deck, path=None, dirty=True)
            log.info(
                "Pasted deck %r: %d mainboard, %d sideboard, %d unresolved",
                deck.name, deck.total("main"), deck.total("side"), len(missing))
            if missing:
                messagebox.showwarning(
                    "Some cards not found",
                    "Couldn't match these names:\n\n" + "\n".join(missing[:30]))

        self._status("Importing pasted deck…")
        future = submit_deck_file_job(load_and_resolve, name="mtg-deck-paste")
        self._poll_deck_file_job(future, opened, error_title="Paste failed")


    def _save_session_as(self, index, *, wait=False):
        """Save one deck session without losing the currently active tab.

        With ``wait=True`` the returned value reflects the completed write
        rather than a successful submission, so a caller may discard the
        session only once the deck is durably on disk.
        """
        if not self.deck_sessions.is_valid_index(index):
            return False

        active_before = self.deck_sessions.active_index
        if index == active_before:
            self._sync_deck_meta()
        session = self.deck_sessions[index]
        deck = session.deck

        path = filedialog.asksaveasfilename(
            title="Save Deck As", defaultextension=".txt",
            initialfile=f"{deck.name}.txt",
            filetypes=[("Text Decklists", "*.txt")])
        if not path:
            return False
        detached = self._detached_deck_copy(deck)

        def saved(_result):
            session.path = path
            session.dirty = False
            self._render_deck_tabs()
            log.info("Saved deck %r to %s", getattr(deck, "name", "?"), path)
            self._status(f"Saved {os.path.basename(path)}")

        future = submit_deck_file_job(
            save_deck_text, path, detached, name="mtg-deck-save")
        if wait:
            return self._await_deck_file_job(
                future, saved, error_title="Save failed")
        self._poll_deck_file_job(future, saved, error_title="Save failed")
        return True


    def _save_deck(self):
        return self._save_session_as(self.deck_sessions.active_index)


    def _json_safe_value(self, value):
        """Recursively convert stored deck/card values into JSON-safe data."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._json_safe_value(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(v) for v in value]
        return str(value)


    def _json_object_field(self, value):
        """Decode a DB JSON-object text field when possible."""
        if not value:
            return {}
        if isinstance(value, dict):
            return self._json_safe_value(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return self._json_safe_value(parsed)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return {}


    def _json_list_field(self, value, compact_colors=False):
        """Decode DB list fields, including compact WUBRG color strings."""
        if value is None or value == "":
            return []
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(v) for v in value]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [self._json_safe_value(v) for v in parsed]
                if isinstance(parsed, str) and parsed:
                    value = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

            if compact_colors:
                return [c for c in value.upper() if c in "WUBRGC"]

            parts = [p.strip() for p in re.split(r"[|,;]", value) if p.strip()]
            return parts or [value]
        return [self._json_safe_value(value)]


    def _deck_json_payload(self, deck):
        """Build a comprehensive JSON representation of one deck."""
        def export_entry(entry):
            card = dict(entry["card"])
            return {
                "quantity": int(entry.get("qty", 0)),
                "board": entry.get("board") or "",
                "name": card.get("name"),
                "mana_cost": card.get("mana_cost"),
                "mana_value": card.get("cmc"),
                "power": card.get("power"),
                "toughness": card.get("toughness"),
                "type_line": card.get("type_line"),
                "rules_text": card.get("oracle_text"),
                "abilities": self._json_list_field(card.get("keywords")),
                "colors": self._json_list_field(
                    card.get("colors"), compact_colors=True),
                "color_identity": self._json_list_field(
                    card.get("color_identity"), compact_colors=True),
                "legalities": self._json_object_field(card.get("legalities")),
                "rarity": card.get("rarity"),
                "set_code": card.get("set_code") or card.get("set"),
                "set_name": card.get("set_name"),
                "collector_number": card.get("collector_number"),
                "released_at": card.get("released_at"),
                "layout": card.get("layout"),
                "language": card.get("lang"),
                "oracle_id": card.get("oracle_id"),
                "scryfall_id": card.get("id"),
                # Preserve every card field retained by the app's local DB.
                "card": self._json_safe_value(card),
            }

        return {
            "deck_name": deck.name or "Untitled Deck",
            "format": deck.fmt,
            "summary": {
                "mainboard_cards": deck.total("main"),
                "sideboard_cards": deck.total("side"),
                "mainboard_unique_cards": deck.unique("main"),
                "sideboard_unique_cards": deck.unique("side"),
            },
            "stats": self._json_safe_value(deck.stats()),
            "mainboard": [export_entry(e) for e in deck.entries("main")],
            "sideboard": [export_entry(e) for e in deck.entries("side")],
        }


    def _safe_json_filename(self, name, used):
        """Return a Windows-safe, path-length-conscious, duplicate-safe filename."""
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_",
                      (name or "Untitled Deck"))
        safe = re.sub(r"\s+", " ", safe).strip().rstrip(". ")
        safe = safe or "Untitled Deck"

        reserved = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if safe.upper() in reserved:
            safe += "_"

        # Leave ample room for the selected folder path and duplicate suffixes.
        safe = safe[:120].rstrip(". ") or "Untitled Deck"
        candidate = f"{safe}.json"
        n = 2
        while candidate.casefold() in used:
            suffix = f" ({n})"
            stem = safe[:max(1, 120 - len(suffix))].rstrip(". ")
            candidate = f"{stem}{suffix}.json"
            n += 1
        used.add(candidate.casefold())
        return candidate


    def _export_all_decks_json(self):
        """Export every currently open deck tab, one JSON file per deck."""
        self._capture_active_session_state()
        if not len(self.deck_sessions):
            messagebox.showinfo("Export JSON", "There are no open decks to export.")
            return

        folder = filedialog.askdirectory(title="Export All Decks as JSON")
        if not folder:
            return

        detached = tuple(self._detached_deck_copy(session.deck)
                         for session in self.deck_sessions)

        def export_all():
            try:
                used_names = {name.casefold() for name in os.listdir(folder)}
            except OSError:
                used_names = set()
            exported = []
            failures = []
            for deck in detached:
                filename = self._safe_json_filename(deck.name, used_names)
                path = os.path.join(folder, filename)
                try:
                    with open(path, "w", encoding="utf-8") as handle:
                        json.dump(
                            self._deck_json_payload(deck), handle,
                            indent=2, ensure_ascii=False)
                        handle.write("\n")
                    exported.append(filename)
                except Exception as exc:
                    failures.append(f"{deck.name or 'Untitled Deck'}: {exc}")
            return exported, failures

        def exported(result):
            exported_names, failures = result
            if failures:
                details = "\n".join(failures[:12])
                if len(failures) > 12:
                    details += f"\n… and {len(failures) - 12} more"
                messagebox.showwarning(
                    "Export JSON",
                    f"Exported {len(exported_names)} deck(s), but "
                    f"{len(failures)} failed.\n\n{details}")
            else:
                messagebox.showinfo(
                    "Export JSON",
                    f"Exported {len(exported_names)} deck"
                    f"{'s' if len(exported_names) != 1 else ''} as JSON to:\n\n{folder}")

        self._status("Exporting deck JSON…")
        future = submit_deck_file_job(export_all, name="mtg-deck-export")
        self._poll_deck_file_job(future, exported, error_title="Export JSON failed")

