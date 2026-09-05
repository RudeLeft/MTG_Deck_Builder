"""Search-layer ownership, projection, controller, and responsiveness contracts."""

import os
import tempfile
from dataclasses import fields
import time
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.search.controller import SearchController
from mtgdb.search.models import SearchCriteria
from mtgdb.database.search_queries import SearchQueryBuilder
from mtgdb.database.schema import _CARD_COLUMN_NAMES
from mtgdb.search.repository import SEARCH_RESULT_COLUMNS, SearchRepository
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.results import SearchResultsMixin
from mtgdb.ui.search import (
    CONTENT_TRAIT_KEYS, TRAIT_CHOICES, SearchFeatureMixin,
)
from mtgdb.ui.search_checklist import SearchChecklistDialog
from mtgdb.ui.search_filters import (
    CATEGORY_ORDER, FILTER_BY_KEY, FILTER_DEFINITIONS, PINNED_FILTER_TOOLTIPS,
    PINNED_FILTERS, STANDARD_FILTERS, advanced_filter_keys, advanced_filters,
    is_standard,
)
from mtgdb.ui.components import format_display_name
from mtgdb.search.catalogs import SearchCatalogController


def _card(card_id, name, keyword="Flying"):
    return {
        "id": card_id,
        "name": name,
        "type_line": "Creature — Bird",
        "mana_cost": "{1}{W}",
        "cmc": 2,
        "colors": ["W"],
        "color_identity": ["W"],
        "power": "2",
        "toughness": "2",
        "rarity": "common",
        "set": "tst",
        "set_name": "Test Set",
        "set_type": "expansion",
        "collector_number": card_id,
        "lang": "en",
        "released_at": "2026-01-01",
        "games": ["paper"],
        "keywords": [keyword],
        # Text follows the keyword so rules-text gates have a card that does
        # not mention flying; every card sharing one string made a negated
        # rules-text search indistinguishable from an empty result.
        "oracle_text": keyword,
        "legalities": {"modern": "legal"},
        "image_uris": {"normal": "normal", "png": "png"},
    }


class _ResultOwner(SearchResultsMixin):
    def __init__(self, repository, rows):
        self.search_repository = repository
        self._result_store = SearchResultStore.from_rows(rows)


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _LoadingSearchOwner:
    def __init__(self):
        self._search_catalog_loading = True
        self._pending_search_request = False
        self.results_count_lbl = _FakeLabel()
        self.status = ""

    def _update_search_filter_summary(self):
        pass

    def _status(self, text):
        self.status = text


class _PendingSearchOwner:
    def __init__(self, *, catalog_loading=False, running=False):
        self._pending_search_request = True
        self._search_catalog_loading = catalog_loading
        self.search_controller = type("Controller", (), {"running": running})()
        self.calls = 0
        self.count_restores = 0

    def _set_result_count(self):
        self.count_restores += 1

    def _do_search(self):
        self.calls += 1


def _method_body(source, name):
    """Source of one method, ending at the next method definition.

    Searching the whole module for a call finds it in any method, which lets a
    deleted call in one place pass because the same call exists in another.
    """
    start = source.index(f"def {name}(")
    remainder = source[start:]
    end = remainder.find("\n    def ", 1)
    return remainder if end == -1 else remainder[:end]


def main():
    criteria = SearchCriteria.from_mapping({
        "name": "Bird", "colors": ["W"], "card_types": ["Creature"],
        "set_codes": {"tst"}, "content_types": ["card"],
    })
    same = SearchCriteria.from_mapping({
        "name": "Bird", "colors": ("W",), "card_types": ("Creature",),
        "set_codes": ("tst",), "content_types": ("card",),
    })

    with tempfile.TemporaryDirectory() as temporary_directory:
        db = CardDB(os.path.join(temporary_directory, "cards.db"))
        # SRCH-033. Produced mana is a different axis from colour identity,
        # and the stored encoding is alphabetical while COLORS is WUBRG, so an
        # exact match has to sort. Model the three cards that make the
        # distinction real.
        db.load_cards([
            _card("1", "First Bird"),
            _card("2", "Second Bird", "Vigilance"),
            dict(_card("3", "Birds of Paradise"), color_identity=["G"],
                 colors=["G"], type_line="Creature — Bird",
                 produced_mana=["W", "U", "B", "R", "G"]),
            dict(_card("4", "Command Tower"), color_identity=[], colors=[],
                 type_line="Land", mana_cost="", cmc=0,
                 produced_mana=["W", "U", "B", "R", "G"]),
            dict(_card("5", "Sol Ring"), color_identity=[], colors=[],
                 type_line="Artifact", produced_mana=["C"]),
            dict(_card("6", "Azorius Signet"), color_identity=[], colors=[],
                 type_line="Artifact", produced_mana=["W", "U"]),
            # Identity supplied in WUBRG order, stored alphabetically: an exact
            # match only works if both import and query agree on the ordering.
            dict(_card("7", "Azorius Charm"), color_identity=["W", "U"],
                 colors=["W", "U"], type_line="Instant", produced_mana=[]),
            # Released mid-year, so an upper bound of 2026 only includes it if
            # that bound resolves to 31 December rather than 1 January.
            dict(_card("8", "Midyear Hawk"), released_at="2026-06-15"),
            # A colourless card with a coloured identity: the only fixture that
            # can tell the two colour columns apart.
            dict(_card("9", "Ghostfire Owl"), colors=[],
                 color_identity=["R"], type_line="Instant"),
            # A digital-only printing in its own set, so platform scoping has
            # something to tell apart from the paper vocabulary.
            dict(_card("10", "Alchemy Owl"), games=["arena"], set="ana",
                 set_name="Arena Set", set_type="alchemy"),
            # A Saga has one face and a two-part cost; the old multi-face
            # layout list called every Saga multi-faced.
            dict(_card("11", "Sagacious Owl"), type_line="Enchantment — Saga",
                 layout="saga", mana_cost="{G}{G}"),
            # Two faces, so the trait must find it through card_faces rather
            # than through a layout this build happens to know.
            dict(_card("12", "Owl Adventure"), layout="adventure",
                 mana_cost="{G/W}{G/W}",
                 card_faces=[{"name": "Owl Adventure"}, {"name": "Off We Go"}]),
            # One card, two printings: the only fixture that can tell a
            # reprint from a card printed once.
            dict(_card("13", "Reprinted Owl"), oracle_id="shared-bird"),
            dict(_card("14", "Reprinted Owl"), oracle_id="shared-bird",
                 set="tst2", set_name="Second Test Set"),
        ])

        # SRCH-033/034 query coverage for the optional filters.
        def _opt(**kwargs):
            return {row["name"] for row in db.search(
                columns=("id", "name"), **kwargs)}

        traits_narrow_the_query = (
            _opt(traits=["multi_faced"]) == {"Owl Adventure"}
            and _opt(traits=["single_faced"]) >= {"First Bird"}
            and _opt(traits=["top_heavy"]) == set())
        release_bounds_are_inclusive = (
            # Whole-year inclusion: the mid-year card only appears when the
            # upper bound resolves to the end of the year.
            "Midyear Hawk" in _opt(released_from=2026, released_to=2026)
            and "Midyear Hawk" in _opt(released_to=2026)
            and _opt(released_from=2027) == set()
            and _opt(released_to=2025) == set())
        # An identity cannot be both white and colourless, so the query has
        # always ignored the C. Ticking it alongside a colour therefore looked
        # like a filter that did nothing.
        # SRCH-040/041. Shape, pips and print count.
        shape_is_exclusive = (
            _opt(layouts=["saga"]) == {"Sagacious Owl"}
            and _opt(layouts=["saga"], layout_mode="none")
            == _opt() - {"Sagacious Owl"}
            # All would always find nothing, so the control must not offer it.
            and ("All", "all") not in SearchFeatureMixin.ANY_NONE_CHOICES)
        faces_decide_multi_faced = (
            # The Saga is single-faced despite a layout the old list called
            # multi-faced; the Adventure is multi-faced because it has faces.
            "Sagacious Owl" in _opt(traits=["single_faced"])
            and "Sagacious Owl" not in _opt(traits=["multi_faced"])
            and "Owl Adventure" in _opt(traits=["multi_faced"])
            and _opt(traits=["multi_faced"]) | _opt(traits=["single_faced"])
            == _opt())
        pips_count_per_colour = (
            "Sagacious Owl" in _opt(pips=["G"], pip_min=2)
            and "Sagacious Owl" not in _opt(pips=["G"], pip_min=3)
            # A hybrid symbol counts for both of its colours, which is what
            # devotion does and what "costs two green" is asked to mean.
            and "Owl Adventure" in _opt(pips=["G"], pip_min=2)
            and "Owl Adventure" in _opt(pips=["W"], pip_min=2)
            # Two colours at once is an AND, not a colour-identity question.
            and _opt(pips=["G", "W"], pip_min=2) == {"Owl Adventure"})
        print_count_is_stored_not_derived = (
            _opt(print_min=2) == {"Reprinted Owl"}
            and _opt(print_min=1, print_max=1) == _opt() - {"Reprinted Owl"}
            and "print_sets" in _CARD_COLUMN_NAMES)
        colorless_adds_nothing_beside_a_colour = (
            _opt(colors=["W", "C"], color_mode="exact")
            == _opt(colors=["W"], color_mode="exact"))
        colour_scope_selects_the_column = (
            # Ghostfire Bird is colourless by card colours and red by identity,
            # so each scope must reach it through a different query.
            "Ghostfire Owl" in _opt(
                colors=["C"], color_mode="exact", color_scope="colors")
            and "Ghostfire Owl" not in _opt(
                colors=["C"], color_mode="exact", color_scope="identity")
            and "Ghostfire Owl" in _opt(
                colors=["R"], color_mode="includes", color_scope="identity")
            and "Ghostfire Owl" not in _opt(
                colors=["R"], color_mode="includes", color_scope="colors"))
        unknown_trait_is_ignored_not_widening = (
            _opt(traits=["not_a_real_trait"]) == _opt())

        # DATA-010: a printing may exist on several platforms at once, which
        # the old paper boolean could not express.
        games_select_platforms = (
            _opt(games=["paper"]) >= {"First Bird"}
            and _opt(games=["arena"]) == {"Alchemy Owl"}
            and _opt(games=["paper", "arena"]) >= {"First Bird"}
            # Selecting every platform, or none, is no restriction.
            and _opt(games=["paper", "mtgo", "arena"]) == _opt()
            and _opt(games=[]) == _opt())

        # DATA-010: picking a platform must re-scope the Printings vocabulary,
        # not merely the result rows. Paper and Arena sets differ.
        paper_types = {value for value, _c in db.set_types(("card",), False, games=("paper",))}
        arena_types = {value for value, _c in db.set_types(("card",), False, games=("arena",))}
        paper_sets = {code for code, _c in db.sets(None, content_types=("card",), games=("paper",))}
        arena_sets = {code for code, _c in db.sets(None, content_types=("card",), games=("arena",))}
        platform_scopes_the_vocabulary = (
            paper_sets and arena_sets and paper_sets != arena_sets
            and paper_types != arena_types)

        # Traits combine with Any by default: requiring all of them made two
        # selections return nothing.
        trait_mode_widens = (
            len(_opt(traits=["reserved", "single_faced"], trait_mode="any"))
            >= len(_opt(traits=["reserved", "single_faced"], trait_mode="all")))

        # SRCH-037. Negation was the largest remaining gap: nothing could ask
        # for a green non-creature, which is why hand-built negative traits
        # existed. "none" must be the exact complement of "any".
        creatures = _opt(card_types=["Creature"], card_type_mode="any")
        non_creatures = _opt(card_types=["Creature"], card_type_mode="none")
        negation_is_complementary = (
            creatures and non_creatures
            and not (creatures & non_creatures)
            and creatures | non_creatures == _opt())
        keyword_negation = (
            "Second Bird" in _opt(keywords=["Flying"], keyword_mode="none")
            and "First Bird" not in _opt(keywords=["Flying"], keyword_mode="none"))
        trait_negation = (
            _opt(traits=["single_faced"], trait_mode="none")
            == {"Owl Adventure"})
        # Rules text builds its clauses on its own path, so "none" has to be
        # implemented there separately from the shared term helper. Without it
        # "cards that never mention flying" was unaskable.
        flyers = _opt(text=("flying",), text_mode="any")
        non_flyers = _opt(text=("flying",), text_mode="none")
        rules_text_negation = (
            flyers and non_flyers
            and not (flyers & non_flyers)
            and flyers | non_flyers == _opt()
            # Two chips must be excluded independently: NOT (a OR b), not
            # NOT (a AND b), which would have returned every card here.
            and _opt(text=("flying", "vigilance"), text_mode="none") == set())

        # SRCH-038. Playable is legal-or-restricted; banned and restricted are
        # the states a deck check asks about.
        legality_states_are_distinct = (
            "First Bird" in _opt(fmt="modern", fmt_status="playable")
            and _opt(fmt="modern", fmt_status="banned") == set()
            and _opt(fmt="modern", fmt_status="restricted") == set())

        def _produces(values, mode):
            return {row["name"] for row in db.search(
                produces=list(values), produces_mode=mode,
                columns=("id", "name"))}

        produces_ignores_identity = (
            "Birds of Paradise" in _produces(("W",), "includes")
            and "Command Tower" in _produces(("W",), "includes")
            and "First Bird" not in _produces(("W",), "includes"))
        produces_exact_sorts_the_needle = (
            _produces(("W", "U"), "exact") == {"Azorius Signet"})
        produces_within_excludes_wider_sources = (
            "Azorius Signet" in _produces(("W", "U"), "within")
            and "Birds of Paradise" not in _produces(("W", "U"), "within")
            and "First Bird" not in _produces(("W", "U"), "within"))
        produces_treats_colorless_as_a_member = (
            _produces(("C",), "includes") == {"Sol Ring"})
        empty_produces_filters_nothing = (
            len(_produces((), "includes")) == 13)
        # The same helper serves colour identity, where an exact multi-colour
        # request previously built "W,U" against stored "U,W" and matched none.
        identity_exact_multicolor = {row["name"] for row in db.search(
            colors=["W", "U"], color_mode="exact", columns=("id", "name"))}

        repository = SearchRepository(db)
        reader = repository.open_reader()
        try:
            summaries = repository.search(criteria, reader)
        finally:
            reader.close()
        full_ids = [row["id"] for row in db.search(**criteria.query_arguments())]
        summary_ids = [row["id"] for row in summaries]
        exact_batch = SearchCriteria.from_mapping({
            "names": ["First Bird", "Second Bird"],
            "content_types": ["card"],
        })
        batch_ids = [row["id"] for row in db.search(**exact_batch.query_arguments())]

        owner = _ResultOwner(repository, list(summaries))
        full = owner._full_result_at(0)

        controller = SearchController(repository)
        started = controller.start(criteria)
        deadline = time.monotonic() + 2.0
        event = None
        while event is None and time.monotonic() < deadline:
            event = controller.poll_latest()
            if event is None:
                time.sleep(0.005)
        accepted = bool(event and controller.accept(event))
        cached = controller.start(criteria)
        db.close()

    checklist_source = (ROOT / "mtgdb/ui/search_checklist.py").read_text(encoding="utf-8")
    table_filter_source = (ROOT / "mtgdb/ui/table_filters.py").read_text(encoding="utf-8")
    results_source = (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8")
    search_source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    search_query_source = (
        ROOT / "mtgdb/database/search_queries.py").read_text(encoding="utf-8")
    bulk_import_source = (
        ROOT / "mtgdb/database/bulk_import.py").read_text(encoding="utf-8")
    printings_source = (
        (ROOT / "mtgdb/ui/search_printings.py").read_text(encoding="utf-8")
        + (ROOT / "mtgdb/ui/set_filters.py").read_text(encoding="utf-8"))
    mechanic_ten_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Mechanic {index}" for index in range(10)}, "Any", "mechanics",
        max_visible=10)
    mechanic_eleven_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Mechanic {index}" for index in range(11)}, "Any", "mechanics",
        max_visible=10)
    rarity_summary = SearchFeatureMixin._picker_button_text(
        None, {"common", "uncommon", "rare", "mythic"}, "Any", "rarities")
    subtype_ten_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(10)},
        "Any", "subtypes", max_visible=10, single_line=True)
    subtype_eleven_summary = SearchFeatureMixin._picker_button_text(
        None, {f"Subtype {index}" for index in range(11)},
        "Any", "subtypes", max_visible=10, single_line=True)

    loading_owner = _LoadingSearchOwner()
    SearchFeatureMixin._do_search(loading_owner)
    ready_owner = _PendingSearchOwner()
    resumed = SearchFeatureMixin._resume_pending_search_request(ready_owner)
    blocked_owner = _PendingSearchOwner(catalog_loading=True)
    blocked = SearchFeatureMixin._resume_pending_search_request(blocked_owner)

    # A multi-name batch ("Search for these cards") pins the Name filter to a
    # generated display string. Editing that box must drop the batch, or the
    # visible Name text and the actual search scope silently disagree.
    from mtgdb.ui.search import SearchFeatureMixin as _SearchMixin

    class _NameEntry:
        def __init__(self, text):
            self.text = text

        def get(self):
            return self.text

    class _NameBatchProbe:
        def __init__(self, batch, display, typed):
            self._search_name_batch = tuple(batch)
            self._search_name_batch_display = display
            self.q_name = _NameEntry(typed)
            self.summaries = 0

        def _update_search_filter_summary(self):
            self.summaries += 1

    def _edit(batch, display, typed):
        probe = _NameBatchProbe(batch, display, typed)
        _SearchMixin._on_name_filter_edited(probe)
        return probe._search_name_batch, probe._search_name_batch_display

    _batch = ("Forest", "Island")
    _display = "2 cards"
    _name_batch_ok = (
        # Untouched text keeps the batch active.
        _edit(_batch, _display, _display) == (_batch, _display)
        # Surrounding whitespace is still the same intent.
        and _edit(_batch, _display, f"  {_display}  ") == (_batch, _display)
        # Any real edit drops the batch so scope matches what is shown.
        and _edit(_batch, _display, "Forest") == ((), "")
        and _edit(_batch, _display, "") == ((), "")
        # With no batch active there is nothing to clear.
        and _edit((), "", "Forest") == ((), ""))

    # SRCH-034/035 registry contracts. These hold without Tk, because the
    # registry is deliberately data rather than widgets.
    catalog = advanced_filters()
    catalog_keys = [entry["key"] for _c, entries in catalog for entry in entries]
    tooltips = {entry["key"]: entry["tooltip"] for entry in FILTER_DEFINITIONS}
    trait_keys = {key for key, _label in TRAIT_CHOICES}

    # SRCH-034. The trusted-catalog refresh runs on every startup and does not
    # know which optional rows exist. Reading a picker it does not own crashed
    # the real app with AttributeError while every headless gate stayed green,
    # because a guard written as `if self._rarity_btn is not None` raises just
    # as readily as the call it protects when the attribute was never created.
    catalog_refresh_touches_no_missing_widget = all(
        f"self._set_picker_text(self.{name}" in search_source
        or f"self._set_picker_text(\n            self.{name}" in search_source
        for name in ("_rarity_btn", "_keyword_btn", "_subtype_btn", "_format_btn")
    ) and "def _set_picker_text(" in search_source

    optional_handles_start_as_none = all(
        f"self.{name} = None" in _method_body(
            search_source, "_initialize_search_filter_state")
        for name in ("q_rules", "_format_btn", "_rarity_btn", "_subtype_btn",
                     "_keyword_btn", "_traits_btn", "_property_chip_frame")
    )

    # SRCH-034. Every reader must survive a filter that has not been built.
    # Two shipped crashes came from this: the catalog refresh, and workspace
    # autosave, both of which run without knowing which rows exist. hasattr is
    # not a guard here -- the handles exist as None from the start.
    no_hasattr_guards_on_optional_handles = not any(
        f'hasattr(self, "{name}")' in search_source
        for name in ("q_rules", "q_cmc_min", "_rarity_btn",
                     "_subtype_btn", "_keyword_btn", "_format_btn"))
    workspace_capture_reads_through_helpers = (
        '"rules": self._rules_text_values(commit_pending=False),' in search_source
        and '"rules_pending": self._rules_pending_text(),' in search_source
        and "self.q_rules.values(" not in _method_body(
            search_source, "_capture_search_workspace_state"))
    removal_clears_the_query_contribution = (
        "return []" in _method_body(search_source, "_rules_text_values")
        and "self._rules_text_shadow = []" in _method_body(
            search_source, "_reset_filter_rules_text"))
    entry_restore_tolerates_absence = (
        "if widget is None:" in _method_body(
            search_source, "_set_search_entry_text"))

    checks = {
        "Supertypes has a visible mode row and defaults to Any": (
            # It defaulted to All with no control to change it, so selecting
            # two supertypes silently reduced the search to the 17 cards that
            # carry both -- with no way for the user to correct it.
            'self.q_supertype_mode = tk.StringVar(value="any")' in search_source
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_filter_supertypes")),
        "every Any/All/None row comes from one builder": (
            # Card Type hand-built its row and silently kept only Any and All
            # when None was added everywhere else. One construction point means
            # a new mode reaches every row at once.
            ("None", "none") in SearchFeatureMixin.MODE_ROW_CHOICES
            and ("None", "none") in SearchChecklistDialog.MODE_CHOICES
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_card_type_filters")
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_filter_supertypes")
            # The triple appears exactly once: as MODE_ROW_CHOICES itself.
            and search_source.count(
                '(("Any", "any"), ("All", "all"), ("None", "none"))') == 1),
        "none mode is the exact complement of any": (
            negation_is_complementary),
        "mechanics and traits can be negated too": (
            keyword_negation and trait_negation),
        "rules text can be negated too": (
            rules_text_negation
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_rules_text_filter")),
        "format legality states are separately reachable": (
            legality_states_are_distinct),
        "printing type re-scopes the set vocabulary": (
            platform_scopes_the_vocabulary),
        "card traits combine with Any by default": (
            trait_mode_widens
            and 'self.q_trait_mode = tk.StringVar(value="any")' in search_source),
        "Search Clear returns Results to the first row": (
            "self._reset_results_viewport()" in _method_body(
                search_source, "_clear_search")),
        "Clear empties every filter without taking any away": (
            "self._reset_advanced_filter_values()" in _method_body(
                search_source, "_clear_search")
            and "advanced_filter_keys()" in _method_body(
                search_source, "_reset_advanced_filter_values")
            # Standard rows are never rebuilt, so Clear has to empty them in
            # place; the advanced sweep cannot reach them.
            and "self._reset_filter_stats()" in _method_body(
                search_source, "_clear_search")
            and "_set_search_entry_text" in _method_body(
                search_source, "_reset_filter_stats")),
        "platform is part of the taxonomy cache key": (
            # Without it, the snapshot that arrived after an Arena toggle
            # described paper and overwrote the Arena set list.
            len(SearchCatalogController._key(("card",), True, (), ("arena",)))
            == 4
            and SearchCatalogController._key(("card",), True, (), ("arena",))
            != SearchCatalogController._key(("card",), True, (), ("paper",))
            and "games" in _method_body(
                search_source, "_refresh_search_catalogs")),
        "every filter explains itself in the same voice": (
            all(entry.get("tooltip", "").strip().endswith(".")
                for entry in FILTER_DEFINITIONS)
            # A tooltip that names where the data came from spends the user's
            # attention on something that cannot change their search.
            and not any(
                word in text.casefold()
                for text in ([entry["tooltip"] for entry in FILTER_DEFINITIONS]
                             + list(PINNED_FILTER_TOOLTIPS.values()))
                for word in ("scryfall", "database", "snapshot"))
            # The always-present filters are explained too, not skipped.
            and set(PINNED_FILTER_TOOLTIPS) == set(PINNED_FILTERS)
            and "_add_pinned_filter_tooltip" in printings_source),
        "format names are spelled out rather than run together": (
            format_display_name("paupercommander") == "Pauper Commander"
            and format_display_name("standardbrawl") == "Standard Brawl"
            and format_display_name("modern") == "Modern"
            # An unknown key still reaches the user rather than disappearing,
            # and a multi-word one is spaced rather than only capitalized.
            and format_display_name("neoformat") == "Neoformat"
            and format_display_name("neo_format") == "Neo Format"
            # An abbreviated key is spelled out too: "Tlr" reads as a typo and
            # "TLR" tells the reader nothing about what the format is.
            and format_display_name("tlr") == "Tarkir Dragonstorm Limited"
            and "capitalize()" not in _method_body(
                search_source, "_set_format_filter")),
        "the Format list follows the chosen legality": (
            # Almost no format restricts anything, so offering all of them
            # under Restricted offered a guaranteed-empty search.
            "mode_command=self._rescope_format_choices" in _method_body(
                search_source, "_choose_format")
            and "self._format_catalog_for_status()" in _method_body(
                search_source, "_choose_format")
            # The unscoped list must not reach the picker's values: the guard
            # that drops an impossible selection is not a substitute for
            # listing only the formats the legality can produce.
            and "for fmt in self._format_catalog]" not in _method_body(
                search_source, "_choose_format")
            and "mode_command=mode_command" in checklist_source
            # The dialog builds itself through show(), so a mode_command the
            # constructor forgets to pass on is a dead control until the
            # picker is closed and opened a second time.
            and "mode_command=mode_command" in _method_body(
                checklist_source[
                    checklist_source.index("class SearchChecklistDialog"):],
                "__init__")),
        "printing type selects platforms rather than a paper flag": (
            games_select_platforms),
        "content kinds come from traits and add no clause": (
            set(CONTENT_TRAIT_KEYS) & set(SearchQueryBuilder.TRAIT_CLAUSES)
            == set()),
        "no reader guards an optional handle with hasattr": (
            no_hasattr_guards_on_optional_handles),
        "workspace capture survives unbuilt filter rows": (
            workspace_capture_reads_through_helpers),
        "removing a filter clears what it contributed": (
            removal_clears_the_query_contribution),
        "restoring text tolerates a row that is not built": (
            entry_restore_tolerates_absence),
        "the catalog refresh never touches an unbuilt picker": (
            catalog_refresh_touches_no_missing_widget),
        "optional widget handles exist as None before any row is built": (
            optional_handles_start_as_none),
        "every filter is declared with a category and tooltip": (
            all(entry["category"] in CATEGORY_ORDER for entry in FILTER_DEFINITIONS)
            and all(len(entry["tooltip"]) >= 60 for entry in FILTER_DEFINITIONS)
            and len(catalog_keys) == len(set(catalog_keys))),
        "the standard set is on the form in the order a search is built": (
            STANDARD_FILTERS
            == ("name", "card_type", "colors", "stats", "printings")
            # Power/Toughness is a standard row now, so it is built by the
            # form rather than reached through the advanced panel.
            and "self._build_standard_stats_filter(form, row=4)" in _method_body(
                search_source, "_build_search_pane")
            and all(is_standard(key) for key in STANDARD_FILTERS)),
        "advanced holds every other filter, grouped and in registry order": (
            set(catalog_keys) | set(STANDARD_FILTERS)
            == set(FILTER_BY_KEY) | {"name", "colors", "card_type", "printings"}
            and not (set(catalog_keys) & set(STANDARD_FILTERS))
            # Category order is the registry's, so a filter is always in the
            # same place rather than wherever it was opened first.
            and [category for category, _entries in catalog]
            == [c for c in CATEGORY_ORDER]
            and advanced_filter_keys()[:3]
            == ("mana_value", "produces", "mana_pips")),
        "advanced rows are built once and only hidden": (
            "def _build_advanced_filter_rows(" in search_source
            and "self._advanced_host.pack_forget()" in _method_body(
                search_source, "_toggle_advanced_filters")
            # Rebuilding on expand would make the first click the slowest.
            and "self._build_advanced_filter_rows()" in _method_body(
                search_source, "_build_advanced_filter_zone")
            and "_build_advanced_filter_rows" not in _method_body(
                search_source, "_toggle_advanced_filters")),
        "collapsing advanced returns Results to the first row": (
            "self._reset_results_viewport()" in _method_body(
                search_source, "_toggle_advanced_filters")),
        "whether advanced is open survives the session": (
            '"advanced_expanded": bool(getattr(self, "_advanced_expanded", False)),'
            in _method_body(search_source, "_capture_search_workspace_state")
            and 'state.get("advanced_expanded", False)' in _method_body(
                search_source, "_restore_search_workspace_state")),
        "tooltips say what is matched, not what the control is": (
            # Each of these names the boundary its filter is confused with:
            # Produces against colour, Mechanics against rules text, Rarity
            # against the card rather than the printing.
            "not the same as its colour" in tooltips["produces"]
            and "rules text" in tooltips["mechanics"]
            and "rules text" in tooltips["rules_text"]
            and "planeswalker" in tooltips["loyalty"]
            and "printing" in tooltips["rarity"]
            # Loyalty and Defense are separate because no card has both;
            # the tooltip has to say so or the split looks arbitrary.
            and "no card has both" in tooltips["defense"]),
        "every card trait has a query clause or selects content": (
            # Content traits choose which objects the search covers instead of
            # adding a clause, so they are satisfied by content_types.
            trait_keys <= (
                set(SearchQueryBuilder.TRAIT_CLAUSES)
                | {"multi_faced", "single_faced"}
                | set(CONTENT_TRAIT_KEYS))),
        "card traits narrow the query and unknown keys are ignored": (
            traits_narrow_the_query
            and unknown_trait_is_ignored_not_widening),
        "release bounds are inclusive years": release_bounds_are_inclusive,
        "including tokens rebuilds the vocabulary it widens": (
            # The results were always right; the pickers kept describing cards
            # only, because the callback that rescopes them had no caller.
            "self._on_content_filter_change()" in _method_body(
                search_source, "_choose_traits")
            and "self._content_types_from_traits()" in _method_body(
                search_source, "_choose_traits")),
        "scope traits are grouped away from the traits the mode row governs": (
            # Include Tokens cannot be negated by None: it chooses what the
            # search covers rather than adding a condition.
            '"Scope · "' in _method_body(search_source, "_choose_traits")
            and '"Trait · "' in _method_body(search_source, "_choose_traits")),
        "Produces is restored after its row exists": (
            # Its checkboxes belong to an optional row, so a restore that runs
            # before the rebuild is discarded with the widgets that held it.
            _method_body(
                search_source, "_restore_search_workspace_state").index(
                    "wanted_produces")
            > _method_body(
                search_source, "_restore_search_workspace_state").index(
                    "_restore_optional_filter_values(")),
        "the platform selection is saved and restored": (
            '"games": list(self._search_printings.selected_games()),'
            in _method_body(search_source, "_capture_search_workspace_state")
            and "games=(list(saved_games)" in _method_body(
                search_source, "_restore_search_workspace_state")
            # The Search subclass overrides restore_selection and used to drop
            # the games argument its own base class accepts.
            and "games=None):" in _method_body(
                printings_source, "restore_selection")
            and "self.game_vars.items()" in _method_body(
                printings_source, "restore_selection")),
        "every range of bounds is validated, not only the first three": (
            all(f'"{label}"' in _method_body(search_source, "_do_search")
                for label in ("Mana value", "Power", "Toughness",
                              "Loyalty", "Defense", "Released"))
            and _method_body(search_source, "_do_search").count(
                "self._validate_search_range(") >= 4),
        "Colors can look at either colour column": (
            colour_scope_selects_the_column
            and "self.q_color_scope = tk.StringVar(value=\"identity\")"
            in search_source
            and "color_scope=self.q_color_scope.get()," in _method_body(
                search_source, "_do_search")
            and '"color_scope": self.q_color_scope.get(),' in _method_body(
                search_source, "_capture_search_workspace_state")
            # The mode row names the column it compares, so "Color identity:"
            # cannot sit above a search of the card's own colours.
            and "COLOR_SCOPE_LABELS" in search_source),
        "colorless releases itself instead of being dropped in silence": (
            # W plus Colorless returned exactly the mono-white result: an
            # identity cannot be both, so the query ignored the C.
            colorless_adds_nothing_beside_a_colour
            and "def _sync_colorless_availability(" in search_source
            # Both the per-click wiring and the initial sync: a restored
            # workspace can arrive with colours already ticked, and the box
            # has to come up released.
            and _method_body(
                search_source, "_build_color_filters").count(
                    "_sync_colorless_availability") >= 2),
        "the search API has one spelling per criterion": (
            # keyword/creature_type/characteristics/rarity/set_code were a
            # second way to say things SearchCriteria already says, with no
            # caller anywhere; characteristics silently aliased supertypes.
            not any(
                f"{name}=" in _method_body(search_query_source, "search")
                for name in ("keyword", "creature_type", "characteristics",
                             "characteristic_mode", "rarity", "set_code",
                             "exclude_art", "show_tokens", "limit"))),
        "card shape is a filter, and only Any or None can apply to it": (
            shape_is_exclusive
            and FILTER_BY_KEY["card_shape"]["category"] == "Card"
            and "choices=self.ANY_NONE_CHOICES" in _method_body(
                search_source, "_build_filter_card_shape")),
        "multi-faced is read from the faces, not from a layout list": (
            # The list called Saga, Class, Case, Leveler, Prototype, Mutate
            # and Meld multi-faced: 761 paper printings with one face.
            faces_decide_multi_faced
            and "MULTI_FACE_LAYOUTS" not in search_query_source
            and "HAS_FACES_CLAUSE" in search_query_source),
        "colored pips are counted at import, once per colour": (
            pips_count_per_colour
            and all(f"pips_{c}" in _CARD_COLUMN_NAMES for c in "wubrgc")
            # Counting them in SQL cannot use an index and cannot see the
            # hybrid halves; the parser at import can do both.
            and "_mana_pips" in bulk_import_source
            and "LENGTH(mana_cost)" not in search_query_source),
        "print count is stored per row rather than aggregated per query": (
            print_count_is_stored_not_derived
            # As a correlated subquery this question took over two minutes.
            and "UPDATE cards SET print_sets" in bulk_import_source
            and "COUNT(DISTINCT set_code)" in bulk_import_source
            and "GROUP BY" not in _method_body(
                search_query_source, "add_print_count_filters")),
        "no criterion outlives the control that reaches it": (
            # Artist kept a working query path, a criterion and a passing gate
            # after its row was removed -- a feature no user could run, proved
            # by a test. A criterion with no control is either wired up or
            # taken out.
            "artist" not in {field.name for field in fields(SearchCriteria)}
            and "add_artist_filter" not in search_query_source
            and "artist" not in SearchCriteria().query_arguments()),
        "colour scope chooses colors or color_identity": (
            colour_scope_selects_the_column),
        "Produces is captured, cleared, and restored with the other criteria": (
            "produces_mode=self.q_produces_mode.get()," in search_source
            and "produces=[value for value, variable in self.produces_vars.items()"
                in search_source
            and 'self.q_produces_mode.set("includes")' in search_source
            and "for variable in self.produces_vars.values():" in search_source
            and '"produces_mode": self.q_produces_mode.get(),' in search_source
            and '"produces": [key for key, variable in self.produces_vars.items()'
                in search_source
            and 'wanted_produces = {str(value) for value in state.get("produces", [])}'
                in search_source),
        "produced mana is filtered independently of colour identity": (
            produces_ignores_identity),
        "exact produced mana sorts the requested set": (
            produces_exact_sorts_the_needle),
        "within produced mana excludes wider sources": (
            produces_within_excludes_wider_sources),
        "colourless produced mana is an explicit member": (
            produces_treats_colorless_as_a_member),
        "an empty produces selection filters nothing": (
            empty_produces_filters_nothing),
        "exact multicolour identity matches stored ordering": (
            identity_exact_multicolor == {"Azorius Charm"}),
        "editing the Name field drops a stale exact-name batch": _name_batch_ok,
        "criteria signatures normalize equivalent snapshots": (
            criteria.signature() == same.signature()),
        "repository projection is intentionally narrow": (
            set(summaries[0]) == set(SEARCH_RESULT_COLUMNS)
            and "legalities" not in summaries[0]
            and "image_normal" not in summaries[0]),
        "narrow and full searches return identical printing IDs": (
            summary_ids == full_ids == ["1", "2"]),
        "exact-name batches return every selected deck-card name": (
            batch_ids == ["1", "2"]),
        "result action hydrates by exact printing ID without inflating compact rows": (
            full["id"] == "1" and "legalities" in full
            and owner._result_store.row_at_source(0).get("id") == "1"
            and not hasattr(owner._result_store.row_at_source(0), "__dict__")),
        "controller delivers and caches a Tk-free result": (
            started.kind == "started" and accepted
            and cached.kind == "unchanged"),
        "search checklist maps only through hidden-first popup presenter": (
            "owner._create_hidden_popup(" in checklist_source
            and "owner._present_hidden_popup(" in checklist_source
            and "VirtualChecklistView" in checklist_source),
        "table filter maps only after withdrawal and geometry": (
            table_filter_source.index("pop.withdraw()")
            < table_filter_source.rindex("pop.geometry(")
            < table_filter_source.rindex("pop.deiconify()")),
        "large UI collections use fixed row pools and Results virtualization": (
            "ROW_POOL = 32" in checklist_source
            and 'style="ListChoice.TRadiobutton"' in checklist_source
            and "VirtualChecklistView" in table_filter_source
            and "RESULT_LIVE_ROW_LIMIT = 128" in results_source
            and "SearchResultStore" in results_source
            and "BooleanVar" not in table_filter_source[
                table_filter_source.index("def _build_values_filter_editor"):
                table_filter_source.index("def _build_numeric_filter_editor")]),
        "result count is independent of physical viewport rows": (
            'text=f"0 /' not in results_source
            and "live_tk_rows" in results_source
            and "logical_rows" in results_source),
        "Search Clear resets subtype mechanic and rarity picker presentation": all(
            marker in search_source for marker in (
                'def _reset_filter_subtype(', 'def _reset_filter_mechanics(',
                'def _reset_filter_rarity(',
            )),
        "Search Clear also releases highlights and every Results column filter": (
            'clear_highlights = getattr(self, "_clear_source_highlights", None)'
                in search_source
            and 'if callable(clear_highlights):' in search_source
            and 'clear_highlights()' in search_source
            and 'self._clear_table_filter("results")' in search_source),
        "Search requested during trusted-filter loading is queued and resumes": (
            loading_owner._pending_search_request
            and loading_owner.results_count_lbl.text == "RESULTS | Trusted filters are loading…"
            and "automatically" in loading_owner.status
            and resumed and ready_owner.calls == 1
            and ready_owner.count_restores == 1
            and not ready_owner._pending_search_request
            and not blocked and blocked_owner.calls == 0
            and blocked_owner._pending_search_request
            and 'self._resume_pending_search_request()' in search_source
            and 'if start.kind == "unchanged":\n            self._set_result_count()'
                in search_source),
        "Search Clear cancels a queued trusted-filter Search": (
            search_source.index('def _clear_search(self):')
            < search_source.index('self._pending_search_request = False',
                                  search_source.index('def _clear_search(self):'))
            < search_source.index('def _apply_cards_search_preset',
                                  search_source.index('def _clear_search(self):'))),
        "trusted-filter failure cannot strand the Results loading label": (
            'self._pending_search_request = False' in search_source
            and 'set_count = getattr(self, "_set_result_count", None)' in search_source
            and 'text="RESULTS | Trusted filters unavailable"' in search_source),
        "the standard core is built and every other filter is in Advanced": (
            'text="Active Filters"' not in search_source
            and "self._build_name_filter(form)" in search_source
            and "self._build_card_type_filters(form)" in search_source
            and "self._build_color_filters(form)" in search_source
            and "self._build_printing_filter(form, row=6)" in search_source
            and "self._build_advanced_filter_zone(parent)" in search_source
            # Every registry filter still has a builder: Advanced is where the
            # rows live now, not a second way of declaring them.
            and all(f"def _build_filter_{key}(" in search_source
                    for key in FILTER_BY_KEY)),
        "picker summaries expose ten values before remainder count": (
            'max_visible=10' in search_source
            and 'PICKER_SUMMARY_PER_LINE = 5' in search_source
            and 'f" · +{len(vals) - max_visible}"' in search_source),
        "Mechanics summaries show ten then count the remainder": (
            "+" not in mechanic_ten_summary
            and mechanic_eleven_summary.endswith("+1")
            and len(mechanic_ten_summary.replace("\n", " · ").split(" · ")) == 10),
        "Rarity summary lists every selected rarity": (
            all(value in rarity_summary for value in (
                "common", "uncommon", "rare", "mythic"))
            and "+" not in rarity_summary),
        "Subtype summary stays horizontal through ten selections": (
            "\n" not in subtype_ten_summary
            and "\n" not in subtype_eleven_summary
            and len(subtype_ten_summary.split(" · ")) == 10
            and subtype_eleven_summary.endswith("+1")
            and "single_line=True" in search_source),
        "Card Name spans the primary row and English only lives in Printings": (
            'text="Card Name"' in search_source
            and 'row=0, column=1, columnspan=3, sticky="ew"' in search_source
            and 'text="English only"' not in search_source
            and 'text="English only"' in printings_source
            and 'english_variable=owner.english_only' in printings_source),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nSEARCH ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
