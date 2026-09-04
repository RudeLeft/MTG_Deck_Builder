"""Regression checks for cascading Search Printings scope without popup rebuilds."""

from __future__ import annotations

import sys
import tkinter as tk
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.ui.search import SearchFeatureMixin
from mtgdb.ui.search_printings import SearchPrintingFilter


class _Repo:
    def __init__(self):
        self.calls = []

    def set_types(self, content_types, paper_only):
        content = set(content_types or ())
        self.calls.append(("types", tuple(content_types), bool(paper_only)))
        result = []
        if "card" in content:
            result.extend([("expansion", 3), ("commander", 1)])
        if "art" in content:
            result.append(("memorabilia", 1))
        return result

    def sets(self, allowed_types=None, content_types=None, paper_only=False):
        allowed = None if allowed_types is None else tuple(sorted(allowed_types))
        content = set(content_types or ())
        self.calls.append(("sets", allowed, tuple(content_types or ()), bool(paper_only)))
        rows = [
            ("exp", "Paper Expansion", "expansion", True, "card"),
            ("exp2", "Second Expansion", "expansion", True, "card"),
            ("cmd", "Commander Product", "commander", True, "card"),
            ("dig", "Digital Expansion", "expansion", False, "card"),
            ("art", "Art Series Product", "memorabilia", True, "art"),
        ]
        result = []
        for code, name, set_type, is_paper, content_kind in rows:
            if content_kind not in content:
                continue
            if paper_only and not is_paper:
                continue
            if allowed is not None and set_type not in allowed:
                continue
            result.append((code, name))
        return result


class _Button:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _Popup:
    def __init__(self):
        self.destroyed = 0

    def winfo_exists(self):
        return True

    def destroy(self):
        self.destroyed += 1



def _fixture():
    owner = tk.Tk(useTk=False)
    owner.search_repository = _Repo()
    owner._content = {"card"}
    owner._selected_content_types = lambda: set(owner._content)
    owner._update_search_filter_summary = lambda: None

    printing = SearchPrintingFilter.__new__(SearchPrintingFilter)
    printing.owner = owner
    printing.repository = owner.search_repository
    printing._content_types_getter = owner._selected_content_types
    printing._change_callback = owner._update_search_filter_summary
    printing._scope_change_callback = None
    printing._english_variable = None
    printing._english_change_callback = None
    printing.paper_only = tk.BooleanVar(master=owner, value=True)
    printing.set_type_vars = {}
    printing._present_set_types = set()
    printing._set_vars = {}
    printing._eligible_sets = []
    printing._popup = _Popup()
    printing._set_type_frame = None
    printing._status_label = None
    printing._set_search_var = None
    printing._set_checklist = None
    printing._set_checklist_catalog = ()
    printing._visible_set_codes = []
    printing.button = _Button()
    printing._pending_restore_types = None
    printing._pending_restore_codes = None

    def refresh(selected_set_types=None):
        selected = set(selected_set_types or ())
        set_types = owner.search_repository.set_types(
            owner._selected_content_types(), printing.paper_only.get())
        sets = owner.search_repository.sets(
            allowed_types=selected or None,
            content_types=owner._selected_content_types(),
            paper_only=printing.paper_only.get())
        snapshot = SimpleNamespace(set_types=set_types, sets=sets)
        printing.apply_snapshot(snapshot)

    owner._refresh_search_catalogs = refresh
    printing._scope_change_callback = owner._refresh_search_catalogs
    return printing, owner


def main():
    printing, owner = _fixture()
    popup = printing._popup

    printing.refresh_catalog()
    initial_all_paper = printing.eligible_set_codes == {"exp", "exp2", "cmd"}
    popup_survives_initial_refresh = printing._popup is popup and popup.destroyed == 0

    # Select one exact Expansion set, then restrict Set Type to Expansion.
    printing._set_vars["exp"].set(True)
    printing.set_type_vars["expansion"].set(True)
    printing._on_set_type_change()
    expansion_only = printing.eligible_set_codes == {"exp", "exp2"}
    compatible_selection_survives = printing.selected_set_codes() == {"exp"}

    # Adding Commander broadens the picker by union and keeps the compatible set.
    printing.set_type_vars["commander"].set(True)
    printing._on_set_type_change()
    union_of_selected_types = printing.eligible_set_codes == {"exp", "exp2", "cmd"}
    compatible_selection_survives_union = printing.selected_set_codes() == {"exp"}

    # Narrowing to Commander removes an incompatible Exact Set selection.
    printing.set_type_vars["expansion"].set(False)
    printing._on_set_type_change()
    commander_only = printing.eligible_set_codes == {"cmd"}
    incompatible_selection_removed = printing.selected_set_codes() == set()

    # Clearing Set Types restores all sets in the current Paper scope.
    printing._clear_set_types()
    clear_restores_all_paper = printing.eligible_set_codes == {"exp", "exp2", "cmd"}

    # Paper-only changes refresh in place and can expose digital sets without
    # destroying/reopening the Printings window.
    printing.paper_only.set(False)
    printing._on_scope_change()
    digital_appears = printing.eligible_set_codes == {"exp", "exp2", "cmd", "dig"}
    popup_survives_scope_change = printing._popup is popup and popup.destroyed == 0


    # Content scope is authoritative for Printings. Art Series alone exposes
    # only Art Series set vocabulary; combining it with Cards exposes the union.
    printing.paper_only.set(True)
    owner._content = {"art"}
    printing.refresh_catalog()
    art_only_scope = (
        printing.present_set_types == {"memorabilia"}
        and printing.eligible_set_codes == {"art"}
    )
    owner._content = {"card", "art"}
    printing.refresh_catalog()
    card_art_union = (
        printing.present_set_types == {"expansion", "commander", "memorabilia"}
        and printing.eligible_set_codes == {"exp", "exp2", "cmd", "art"}
    )
    popup_survives_content_change = printing._popup is popup and popup.destroyed == 0

    # The repository receives selected observed Set Types rather than a hardcoded
    # set-family mapping.
    set_calls = [call for call in owner.search_repository.calls if call[0] == "sets"]
    observed_type_scoping_used = (
        any(call[1] == ("expansion",) for call in set_calls)
        and any(call[1] == ("commander",) for call in set_calls)
        and any(call[1] == ("commander", "expansion") for call in set_calls)
        and any(call[1] is None for call in set_calls)
    )

    subtype_ten = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(10)},
        "Any", "subtypes", max_visible=10, single_line=True)
    subtype_eleven = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(11)},
        "Any", "subtypes", max_visible=10, single_line=True)
    subtype_single_line = "\n" not in subtype_ten and "\n" not in subtype_eleven
    subtype_ten_visible = len(subtype_ten.split(" · ")) == 10
    subtype_remainder = subtype_eleven.endswith("+1")

    checks = {
        "initial Exact Sets include every paper set": initial_all_paper,
        "catalog refresh keeps the same Printings popup": popup_survives_initial_refresh,
        "Expansion Set Type narrows Exact Sets": expansion_only,
        "compatible Exact Set selection survives narrowing": compatible_selection_survives,
        "multiple Set Types expose their union": union_of_selected_types,
        "compatible Exact Set survives Set Type union": compatible_selection_survives_union,
        "Commander-only narrows Exact Sets": commander_only,
        "incompatible Exact Set selection is removed": incompatible_selection_removed,
        "clearing Set Types restores all scoped sets": clear_restores_all_paper,
        "Paper-only off exposes digital scoped sets": digital_appears,
        "Paper-only refresh does not destroy/reopen popup": popup_survives_scope_change,
        "Art Series-only Content narrows Printings vocabulary": art_only_scope,
        "Cards plus Art Series exposes Printings union": card_art_union,
        "Content-scope refresh does not destroy/reopen popup": popup_survives_content_change,
        "Exact Set scope is driven by observed Set Type values": observed_type_scoping_used,
        "Subtype summary never inserts a second line": subtype_single_line,
        "Subtype summary lists ten values on one line": subtype_ten_visible,
        "Subtype summary appends remainder count after ten": subtype_remainder,
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nSEARCH PRINTINGS CASCADE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
