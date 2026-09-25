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
from mtgdb.database.semantics import _mana_cost_symbol_match
from mtgdb.database.schema import _CARD_COLUMN_NAMES
from mtgdb.search.repository import SEARCH_RESULT_COLUMNS, SearchRepository
from mtgdb.search.results import SearchResultStore
from mtgdb.ui.results import SearchResultsMixin
from mtgdb.ui.search import (
    CONTENT_TRAIT_KEYS, TRAIT_CHOICES, SearchFeatureMixin,
)
from mtgdb.ui.search_checklist import SearchChecklistDialog
from mtgdb.ui.search_filters import (
    CATEGORY_ORDER, FILTER_BY_KEY, FILTER_DEFINITIONS, STANDARD_FILTER_TOOLTIPS,
    STANDARD_FILTERS, advanced_filter_keys, advanced_filters, filter_tooltip,
    is_standard,
)
from mtgdb.ui.components import format_display_name
from mtgdb.ui.set_filters import (
    ENGLISH_HELP, EXACT_SET_HELP, PLATFORM_HELP, PLATFORM_SECTION_HELP,
    SET_TYPE_DESCRIPTIONS, SET_TYPE_HELP,
)
from mtgdb.search.catalogs import SearchCatalogController
from mtgdb.search.context import (
    SearchContextController, SearchContextSnapshot, _PredictiveMembershipCounts,
    _predict_colors, _predict_content, _predict_games, _predict_pips,
)


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


class _FakeTree:
    """Enough of a Treeview to watch which pooled row shows which card."""

    def __init__(self):
        self.order = []
        self.values = {}

    def insert(self, _parent, _index, iid=None, text="", values=(), **_kw):
        self.order.append(iid)
        self.values[iid] = tuple(values)

    def item(self, iid, **kwargs):
        if "values" in kwargs:
            self.values[iid] = tuple(kwargs["values"])
        return {"values": self.values.get(iid, ())}

    def move(self, iid, _parent, index):
        # Tk moves by removing and re-inserting, and takes "end" as a position.
        self.order.remove(iid)
        if index == "end":
            self.order.append(iid)
        else:
            self.order.insert(int(index), iid)

    def delete(self, iid):
        if iid in self.order:
            self.order.remove(iid)
        self.values.pop(iid, None)

    def exists(self, iid):
        return iid in self.values

    def get_children(self, _parent=""):
        return tuple(self.order)

    def yview_moveto(self, _fraction):
        return None

    def selection_set(self, *_args):
        return None

    def selection(self):
        return ()


class _ScrollOwner(SearchResultsMixin):
    """A Results table driven without Tk, to watch the row pool rotate."""

    CAPACITY = 20

    def __init__(self, rows):
        self.results_tv = _FakeTree()
        self._initialize_search_results()
        self._result_store = SearchResultStore.from_rows(rows)
        self._result_selected_ids = set()
        self._table_filters = {"results": {}}
        self._sort_col = None
        self._sort_desc = False
        self._results_vsb = None

    # Tk-dependent pieces the ring does not need to be tested.
    def _result_visible_capacity(self):
        return self.CAPACITY

    def _cost_image(self, _cost, height=18):
        return None

    def _table_value(self, card, key, qty=None):
        return card.get(key, "")

    def _begin_result_selection_sync(self):
        return None

    def _end_result_selection_sync_later(self):
        return None

    def _apply_result_native_selection(self):
        return None

    def _update_result_scrollbar(self):
        return None

    # What a user would see, top row first.
    def visible_names(self):
        column = self._result_ordinary_columns().index("name")
        return [self.results_tv.values[iid][column]
                for iid in self.results_tv.order[:self.CAPACITY]]

    def expected_names(self):
        store = self._result_store
        names = []
        for offset in range(self.CAPACITY):
            position = self._result_top + offset
            if position >= store.visible_count:
                break
            names.append(store.row_at_source(
                store.source_index_at_view(position)).get("name"))
        return names

    def pool_is_consistent(self):
        """Every pooled row shows its ring position, or shows nothing.

        The pool is bigger than the viewport, so this covers the rows waiting
        below the fold as well. A row that keeps an old card down there is one
        rotation away from being at the top.
        """
        store = self._result_store
        column = self._result_ordinary_columns().index("name")
        for index, slot in enumerate(self._result_live_slots):
            position = self._result_top + index
            expected = ""
            if position < store.visible_count:
                expected = store.row_at_source(
                    store.source_index_at_view(position)).get("name")
            if self.results_tv.values[slot][column] != expected:
                return False
        return list(self.results_tv.order) == self._result_live_slots


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class _FakeContextControl:
    def __init__(self, value=""):
        self.value = value
        self.disabled = False
        self._mtg_context_available = True

    def get(self):
        return self.value

    def state(self, spec):
        for item in spec:
            if item == "disabled":
                self.disabled = True
            elif item == "!disabled":
                self.disabled = False


class _FakeBoolVar:
    def __init__(self, value=False):
        self.value = value

    def get(self):
        return self.value


class _FakeStatus:
    """Stand-in for a PulseStatus / ActivitySource: records what was asked of it."""
    def __init__(self):
        self.starts = []
        self.stops = 0
        self.cancels = 0

    def start(self, text):
        self.starts.append(text)

    def stop(self):
        self.stops += 1

    def cancel(self):
        self.cancels += 1


class _LoadingSearchOwner:
    def __init__(self):
        self._search_catalog_loading = True
        self._pending_search_request = False
        self.results_count_lbl = _FakeLabel()
        self.results_count_lbl.text = "RESULTS | 0 CARDS"
        self._results_status = _FakeStatus()
        self._search_status = _FakeStatus()
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


def _catalog_reader_and_warm_check():
    """Off-lock catalog reads must match the primary, and warming must fill the
    cache in the background without a UI event.

    The trusted-catalog build was moved onto an independent WAL reader (so it no
    longer blocks interactive reads) and given a warm queue (so common scope
    switches are instant). Prove both: identical results inside/outside a reader
    session, and that a warmed scope becomes a cache hit.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = CardDB(os.path.join(tmp, "cards.db"))
        db.load_cards([
            _card("1", "Alpha"),
            dict(_card("2", "Beta"), set="two", set_type="masters", rarity="rare"),
            dict(_card("3", "Tok"), layout="token",
                 type_line="Token Creature — Elf"),
        ])
        ct = ("card",)
        methods = (
            lambda: db.set_types(ct, False), lambda: db.rarities(ct, False),
            lambda: db.layouts(ct, False), lambda: db.formats_by_status(ct, False),
            lambda: db._type_lines(ct, False))
        outside = [fn() for fn in methods]
        with db.reader_session():
            inside = [fn() for fn in methods]
        parity = inside == outside

        controller = SearchCatalogController(SearchRepository(db))
        try:
            controller.request(("token",), False, (), ("paper",))
            controller.warm(("card",), False, (), ("paper",))
            warmed = False
            for _ in range(250):
                time.sleep(0.02)
                if controller.cache_info().get("warmed", 0) >= 1:
                    warmed = True
                    break
            cached = controller.request(
                ("card",), False, (), ("paper",)).kind == "cached"
        finally:
            controller.invalidate()
            db.close()
        return parity and warmed and cached


def _progressive_delivery_check():
    """Broad searches deliver a first screen before the full store.

    Regression guard for SearchController two-phase delivery: a result larger
    than FIRST_SCREEN emits a partial (first-screen) event then a terminal
    'done' carrying the full store, and the partial rows are the full store's
    first FIRST_SCREEN in the same order (no reshuffle when the full set lands).
    A result that fits in one screen emits only 'done'.
    """
    import queue as _queue
    from mtgdb.search.controller import SearchController
    from mtgdb.search.results import SearchResultStore

    n = SearchController.FIRST_SCREEN

    class _Repo:
        def __init__(self, total):
            self._rows = [{"id": str(i), "name": "%06d" % i} for i in range(total)]

        def open_reader(self):
            return object()

        def search_result_store(self, _criteria, _reader, limit=None):
            rows = self._rows if limit is None else self._rows[:limit]
            return SearchResultStore.from_rows(rows)

    def drain(controller):
        events = []
        while True:
            try:
                event = controller.events.get(timeout=2.0)
            except _queue.Empty:
                break
            events.append(event)
            if event.kind in ("done", "error"):
                break
        return events

    big = SearchController(_Repo(n * 3))
    big.start(SearchCriteria(content_types=("card",)))
    big_events = drain(big)
    partial = next((e for e in big_events if e.kind == "partial"), None)
    done = next((e for e in big_events if e.kind == "done"), None)
    big_ok = (
        [e.kind for e in big_events] == ["partial", "done"]
        and partial.payload.logical_count == n
        and done.payload.logical_count == n * 3
        and partial.payload.rows == done.payload.rows[:n])

    small = SearchController(_Repo(n // 2))
    small.start(SearchCriteria(content_types=("card",)))
    small_ok = [e.kind for e in drain(small)] == ["done"]
    return big_ok and small_ok


def _activity_indicator_behaviour():
    """Drive the real ActivityIndicator on a hidden Tk root with short timings.

    The centered busy cue is shared by three independent activities, so what it
    shows when they overlap, and that it never flashes or sticks, is behaviour to
    execute rather than to grep for.  Returns one bool per behaviour.
    """
    import tkinter as tk
    import tkinter.font as tkfont
    from tkinter import ttk
    from mtgdb.ui.components import ActivityIndicator
    from mtgdb.ui.tokens import FONT_ACTIVITY

    threshold, dwell, pulse = 40, 160, 50
    root = tk.Tk()
    root.withdraw()
    # A ttk label rejects a style whose layout does not exist, which would end
    # the pulse before it starts.
    for style_name in ("A.TLabel", "B.TLabel"):
        ttk.Style(root).configure(style_name, foreground="#ffffff")

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            root.update()
            time.sleep(0.004)

    def make():
        label = ttk.Label(root, text="")
        indicator = ActivityIndicator(
            label, working_styles=("A.TLabel", "B.TLabel"),
            threshold_ms=threshold, dwell_ms=dwell, pulse_ms=pulse)
        return (label, indicator, indicator.source("search", priority=3),
                indicator.source("filters", priority=2),
                indicator.source("context", priority=1))

    def text(label):
        return str(label.cget("text"))

    try:
        results = {}

        label, _ind, _search, _filters, context = make()
        starts_empty = text(label) == ""
        context.start("Updating…")
        pump(threshold // 4)
        context.stop()
        pump(threshold * 4)
        results["work that finishes inside the threshold never flashes"] = (
            starts_empty and text(label) == "")

        label, _ind, _search, filters, _context = make()
        filters.start("Loading filters…")
        pump(threshold * 3)
        shown_font = tkfont.Font(root=root, font=label.cget("font")).actual()
        results["a cue that outlives the threshold appears at the activity size"] = (
            text(label) == "Loading filters…"
            and str(label.cget("style")) in ("A.TLabel", "B.TLabel")
            and shown_font["size"] == FONT_ACTIVITY[1]
            and shown_font["weight"] == FONT_ACTIVITY[2])

        label, _ind, search, filters, context = make()
        context.start("Updating…")
        filters.start("Loading filters…")
        pump(threshold * 3)
        names = [text(label)]
        search.start("Searching…")
        names.append(text(label))
        search.stop()
        names.append(text(label))
        filters.stop()
        names.append(text(label))
        results["overlapping activities name the highest priority, then fall back"] = (
            names == ["Loading filters…", "Searching…", "Loading filters…",
                      "Updating…"])

        label, indicator, _s, _f, _c = make()
        first = indicator.source("first", priority=1)
        second = indicator.source("second", priority=1)
        first.start("First")
        second.start("Second")
        pump(threshold * 3)
        tie_names = [text(label)]
        first.start("First again")
        tie_names.append(text(label))
        results["equal priorities show the most recently started"] = (
            tie_names == ["Second", "First again"])

        label, _ind, _search, _filters, context = make()
        context.start("Updating…")
        pump(threshold * 3)
        context.stop()
        still_up = text(label) == "Updating…"
        pump(dwell * 3)
        results["a shown cue keeps its minimum dwell, then clears"] = (
            still_up and text(label) == "")

        label, _ind, _search, _filters, context = make()
        context.start("Updating…")
        pump(threshold * 3)
        context.stop()
        context.start("Updating…")
        pump(dwell * 3)
        results["restarting during the dwell keeps the cue up"] = (
            text(label) == "Updating…")

        label, _ind, _search, _filters, context = make()
        context.start("Updating…")
        pump(threshold * 3)
        context.cancel()
        cleared_at_once = text(label) == ""
        pump(dwell * 2)
        results["cancel clears a shown cue at once, without the dwell"] = (
            cleared_at_once and text(label) == "")

        label, _ind, _search, _filters, context = make()
        context.start("Updating…")
        context.cancel()
        pump(threshold * 4)
        results["cancel before the threshold means it never appears"] = (
            text(label) == "")

        label, _ind, _search, filters, context = make()
        context.start("Updating…")
        filters.start("Loading filters…")
        pump(threshold * 3)
        filters.cancel()
        results["cancelling one activity falls back to the next"] = (
            text(label) == "Updating…")
        return results
    finally:
        root.destroy()


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
        mana_symbol_total_semantics = (
            # One hybrid can represent both selected colors for Match All, but
            # is still only one physical symbol toward the total Minimum.
            _mana_cost_symbol_match("{W/B}", "W,B", "all", 1) == 1
            and _mana_cost_symbol_match("{W/B}", "W,B", "all", 2) == 0
            and _mana_cost_symbol_match("{W/B}{W/B}", "W,B", "all", 2) == 1
            and _mana_cost_symbol_match("{W}{B}", "W,B", "all", 2) == 1
            and _mana_cost_symbol_match("{W}{W}", "W,B", "all", 2) == 0
            # Any permits either selected color while the total can be supplied
            # by multiple qualifying symbols. None ignores Minimum and excludes.
            and _mana_cost_symbol_match("{W}{W}", "W,B", "any", 2) == 1
            and _mana_cost_symbol_match("{W}{B}", "W,B", "none", 9) == 0
            and _mana_cost_symbol_match("{U}{U}", "W,B", "none", 9) == 1
            # Generic symbols never count; hybrid/Phyrexian symbols represent
            # their selected color but each brace-delimited symbol counts once.
            and _mana_cost_symbol_match("{W/B}{1}", "W,B", "all", 2) == 0
            and _mana_cost_symbol_match("{W/P}{B/P}", "W,B", "all", 2) == 1
            # The canonical SQL path uses the same semantics. With G+W and a
            # total minimum of two, the all-hybrid Adventure satisfies All;
            # the mono-green Saga only appears under Any.
            and "Owl Adventure" in _opt(
                pips=["G", "W"], pip_mode="all", pip_min=2)
            and "Sagacious Owl" not in _opt(
                pips=["G", "W"], pip_mode="all", pip_min=2)
            and {"Owl Adventure", "Sagacious Owl"} <= _opt(
                pips=["G", "W"], pip_mode="any", pip_min=2)
            # Live candidate prediction uses the same physical-symbol total.
            # Under Any, Black must occur itself; selected White cannot revive
            # a Black candidate that never appears on a card.
            and _predict_pips([
                {"mana_cost": "{W/B}"}, {"mana_cost": "{W}{B}"},
                {"mana_cost": "{W}{W}"}, {"mana_cost": "{U}{U}"},
            ], ["W"], 2, "any")["B"] == 1
            and _predict_pips([
                {"mana_cost": "{W/B}"}, {"mana_cost": "{W}{B}"},
                {"mana_cost": "{W}{W}"},
            ], ["W"], 2, "all")["B"] == 1
            and "pip_mode" in {field.name for field in fields(SearchCriteria)})
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

        # SRCH-034/045. Interactive property families are separate facets.
        # Their groups AND with each other while each group owns its own mode.
        independent_property_groups = (
            _opt(mana_features=["hybrid_mana"]) == {"Owl Adventure"}
            and _opt(special_properties=["multi_faced"]) == {"Owl Adventure"}
            and _opt(
                mana_features=["hybrid_mana"],
                special_properties=["multi_faced"]) == {"Owl Adventure"}
            and _opt(
                special_properties=["multi_faced"],
                special_property_mode="none") == _opt(traits=["single_faced"])
            and _opt(
                special_properties=["multi_faced", "top_heavy"],
                special_property_mode="any") == {"Owl Adventure"}
            and _opt(
                special_properties=["multi_faced", "top_heavy"],
                special_property_mode="all") == set()
            and _opt(
                special_properties=["multi_faced", "top_heavy"],
                special_property_mode="none") == _opt() - {"Owl Adventure"})

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

        # Context analysis is a separate latest-wins worker: the Results query
        # must not wait for facet counting or zero-result diagnostics.
        context_controller = SearchContextController(repository)
        try:
            context_criteria = SearchCriteria.from_mapping({
                "card_types": ["Creature"], "content_types": ["card"],
            })
            first_generation = context_controller.request(
                context_criteria,
                card_types=["Artifact", "Creature", "Enchantment", "Instant", "Land"],
                supertypes=["Legendary"],
                subtypes=[("Bird", "Creature"), ("Saga", "Enchantment")],
                keywords=[("Flying", "Keyword Ability"),
                          ("Vigilance", "Keyword Ability")],
                layouts=[("normal", 1), ("saga", 1), ("adventure", 1)],
                rarities=["common"], formats=["modern"],
                set_types=["expansion", "alchemy"],
                sets=[("tst", "Test Set"), ("ana", "Arena Set"),
                      ("tst2", "Second Test Set")])
            deadline = time.monotonic() + 5.0
            context_event = None
            while time.monotonic() < deadline and context_event is None:
                context_event = context_controller.poll_latest()
                if context_event is None:
                    time.sleep(0.01)
            expected_artifact_compatibility = len(db.search(
                card_types=["Artifact"], card_type_mode="any",
                content_types=["card"], columns=("id",)))
            expected_white_creature = len(db.search(
                card_types=["Creature"], colors=["W"], color_mode="within",
                content_types=["card"], columns=("id",)))
            expected_arena_creature = len(db.search(
                card_types=["Creature"], games=["arena"],
                content_types=["card"], columns=("id",)))
            context_worker_prepares_facets = (
                context_event is not None
                and context_event.generation == first_generation
                and context_event.kind == "done"
                and isinstance(context_event.payload, SearchContextSnapshot)
                and context_event.payload.result_count == 8
                and context_event.payload.card_type_counts.get("Artifact")
                    == expected_artifact_compatibility
                and context_event.payload.subtype_counts.get("Bird") == 8
                and context_event.payload.keyword_counts.get("Flying", 0) >= 1
                and context_event.payload.color_counts.get("W")
                    == expected_white_creature
                and context_event.payload.game_counts.get("arena")
                    == expected_arena_creature
                and context_event.payload.pip_counts.get("W") == 8
                and context_event.payload.content_counts.get("card") == 8
                and context_event.payload.rarity_counts.get("common") == 8
                and context_event.payload.set_type_counts.get("expansion") == 7
                and context_event.payload.format_counts.get("modern") == 8
                and context_event.payload.numeric_ranges.get("cmc") == (2.0, 2.0)
                and context_event.payload.release_years == ("2026",)
                and context_event.payload.english_count == 8
                and context_event.payload.all_language_count == 8)

            # A selected Any/OR value must not donate its matching rows to
            # every peer.  That made a zero-result Card Type turn available as
            # soon as another type (for example a Sorcery face) was selected.
            any_predictor = _PredictiveMembershipCounts(
                ("Creature", "Sorcery", "Artifact"), ("Sorcery",), "any")
            any_predictor.add(("Sorcery",))
            any_predictor.add(("Creature",))
            any_counts = any_predictor.finish()
            any_peer_does_not_inflate_zero = (
                any_counts == {"Creature": 1, "Sorcery": 1, "Artifact": 0})

            union_facets_do_not_inflate_zero = (
                _predict_games(
                    [{"games": ["paper"]}, {"games": ["arena"]}],
                    ("paper",),
                ) == {"paper": 1, "arena": 1, "mtgo": 0}
                and _predict_content(
                    [
                        {"layout": "normal", "type_line": "Creature — Bird"},
                        {"layout": "token", "type_line": "Token Creature — Bird"},
                    ],
                    ("card",),
                ).get("emblem") == 0
                and _predict_colors(
                    [
                        {"color_identity": "W"},
                        {"color_identity": "U"},
                        {"color_identity": "U,W"},
                        {"color_identity": ""},
                    ],
                    "color_identity", ("W", "U", "B", "R", "G", "C"),
                    ("W",), "within", produced=False,
                ).get("G") == 0
            )

            # Independent property facets must stay active while a peer facet
            # is relaxed for predictive counts. Owl Adventure is both hybrid
            # and multi-faced; there is no top-heavy card in this fixture.
            grouped_criteria = SearchCriteria.from_mapping({
                "mana_features": ["hybrid_mana"],
                "special_properties": ["multi_faced"],
                "special_property_mode": "all",
                "content_types": ["card"],
            })
            grouped_generation = context_controller.request(grouped_criteria)
            deadline = time.monotonic() + 5.0
            grouped_event = None
            while time.monotonic() < deadline and grouped_event is None:
                grouped_event = context_controller.poll_latest()
                if grouped_event is None:
                    time.sleep(0.01)
            independent_property_context = (
                grouped_event is not None
                and grouped_event.generation == grouped_generation
                and grouped_event.kind == "done"
                and grouped_event.payload.result_count == 1
                and grouped_event.payload.mana_feature_counts.get("hybrid_mana") == 1
                and grouped_event.payload.special_property_counts.get("multi_faced") == 1
                and grouped_event.payload.special_property_counts.get("top_heavy") == 0
                and grouped_event.payload.status_property_counts.get(
                    "not_universes_beyond") == 1)

            context_controller.request(
                context_criteria, subtypes=[("Bird", "Creature")])
            latest_criteria = SearchCriteria.from_mapping({
                "name": "First Bird", "content_types": ["card"],
            })
            latest_generation = context_controller.request(
                latest_criteria, subtypes=[("Bird", "Creature")],
                keywords=[("Flying", "Keyword Ability")])
            deadline = time.monotonic() + 5.0
            latest_event = None
            while time.monotonic() < deadline and latest_event is None:
                latest_event = context_controller.poll_latest()
                if latest_event is None:
                    time.sleep(0.01)
            context_worker_is_latest_wins = (
                latest_event is not None
                and latest_event.generation == latest_generation
                and latest_event.signature == latest_criteria.signature()
                and latest_event.kind == "done"
                and latest_event.payload.result_count == 1)
        finally:
            context_controller.shutdown(timeout=2.0)

        # SRCH-045. Dungeon is a compact real-world applicability probe: its
        # rules-derived mana value is 0, but it has no meaningful mana cost, so
        # the interactive Mana Value range is inapplicable. It also has no P/T,
        # loyalty, defense, mana production, or mana symbols. It is colorless as
        # a card, so the Mana Color C candidate remains compatible even though
        # Mana Produced C does not. Keeping this in a separate database avoids
        # perturbing the broad fixture counts above.
        dungeon_db = CardDB(os.path.join(temporary_directory, "dungeon.db"))
        dungeon_db.load_cards([dict(
            _card("d1", "Lost Mine of Phandelver"),
            type_line="Dungeon", layout="dungeon", mana_cost="", cmc=0,
            colors=[], color_identity=[], produced_mana=[],
            power=None, toughness=None, loyalty=None, defense=None,
        )])
        dungeon_context = SearchContextController(SearchRepository(dungeon_db))
        try:
            dungeon_criteria = SearchCriteria.from_mapping({
                "card_types": ["Dungeon"], "content_types": ["card"],
            })
            dungeon_generation = dungeon_context.request(
                dungeon_criteria, card_types=["Dungeon"])
            deadline = time.monotonic() + 5.0
            dungeon_event = None
            while time.monotonic() < deadline and dungeon_event is None:
                dungeon_event = dungeon_context.poll_latest()
                if dungeon_event is None:
                    time.sleep(0.01)
            dungeon_snapshot = (
                dungeon_event.payload
                if dungeon_event is not None
                and dungeon_event.generation == dungeon_generation
                and dungeon_event.kind == "done"
                else None
            )
            dungeon_dynamic_applicability = bool(
                dungeon_snapshot is not None
                and dungeon_snapshot.result_count == 1
                and dungeon_snapshot.numeric_ranges.get("cmc") == (0.0, 0.0)
                and dungeon_snapshot.numeric_applicability.get("cmc") == 0
                and dungeon_snapshot.numeric_applicability.get("power") == 0
                and dungeon_snapshot.numeric_applicability.get("toughness") == 0
                and dungeon_snapshot.numeric_applicability.get("loyalty") == 0
                and dungeon_snapshot.numeric_applicability.get("defense") == 0
                and dungeon_snapshot.release_years == ("2026",)
                and dungeon_snapshot.color_counts.get("C") == 1
                and all(dungeon_snapshot.color_counts.get(c, 0) == 0
                        for c in "WUBRG")
                and all(dungeon_snapshot.produces_counts.get(c, 0) == 0
                        for c in "WUBRGC")
                and all(dungeon_snapshot.pip_counts.get(c, 0) == 0
                        for c in "WUBRGC")
            )
        finally:
            dungeon_context.shutdown(timeout=2.0)
            dungeon_db.close()

        mana_value_db = CardDB(os.path.join(temporary_directory, "mana-value-applicability.db"))
        mana_value_db.load_cards([
            dict(_card("z0", "Zero Cost Spell"), type_line="Artifact",
                 mana_cost="{0}", cmc=0, colors=[], color_identity=[]),
            dict(_card("dfc", "Front // Back"), type_line="Creature",
                 mana_cost="", cmc=2, colors=["U"], color_identity=["U"],
                 card_faces=[{"name": "Front", "mana_cost": "{1}{U}"},
                             {"name": "Back", "mana_cost": ""}]),
            dict(_card("land0", "No-Cost Land"), type_line="Land",
                 mana_cost="", cmc=0, colors=[], color_identity=[]),
        ])
        mana_value_context = SearchContextController(SearchRepository(mana_value_db))
        try:
            mana_value_generation = mana_value_context.request(
                SearchCriteria.from_mapping({"content_types": ["card"]}))
            deadline = time.monotonic() + 5.0
            mana_value_event = None
            while time.monotonic() < deadline and mana_value_event is None:
                mana_value_event = mana_value_context.poll_latest()
                if mana_value_event is None:
                    time.sleep(0.01)
            mana_value_snapshot = (
                mana_value_event.payload
                if mana_value_event is not None
                and mana_value_event.generation == mana_value_generation
                and mana_value_event.kind == "done"
                else None
            )
            meaningful_mana_value_applicability = bool(
                mana_value_snapshot is not None
                and mana_value_snapshot.numeric_ranges.get("cmc") == (0.0, 2.0)
                and mana_value_snapshot.numeric_applicability.get("cmc") == 2
            )
        finally:
            mana_value_context.shutdown(timeout=2.0)
            mana_value_db.close()

        # A property clause over a NULL column (power/toughness on a
        # non-creature) is NULL, and `NOT NULL` is NULL not TRUE, so a bare
        # `NOT (group)` silently dropped every card with no stats from a
        # "None" search -- a Land vanished from "top-heavy: None".
        null_stat_db = CardDB(os.path.join(temporary_directory, "null-stat.db"))
        null_stat_db.load_cards([
            dict(_card("ns-land", "Null Stat Land"), type_line="Land",
                 mana_cost="", cmc=0, colors=[], color_identity=[],
                 power=None, toughness=None),
            dict(_card("ns-topheavy", "Top Heavy Beast"),
                 power="4", toughness="1"),
            dict(_card("ns-variable", "Variable Beast"),
                 power="*", toughness="*"),
        ])
        try:
            def _ns(**kwargs):
                return {row["name"] for row in null_stat_db.search(
                    columns=("id", "name"), content_types=["card"], **kwargs)}
            everything = _ns()
            property_none_includes_null_stats = (
                "Null Stat Land" in _ns(
                    special_properties=["top_heavy"],
                    special_property_mode="none")
                and "Null Stat Land" in _ns(
                    special_properties=["variable_stats"],
                    special_property_mode="none")
                and _ns(special_properties=["top_heavy"],
                        special_property_mode="none")
                    == everything - _ns(special_properties=["top_heavy"])
                and _ns(special_properties=["variable_stats"],
                        special_property_mode="none")
                    == everything - _ns(special_properties=["variable_stats"])
                and _ns(special_properties=["top_heavy"]) == {"Top Heavy Beast"}
                and _ns(special_properties=["variable_stats"])
                    == {"Variable Beast"})
        finally:
            null_stat_db.close()
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

    # Searching before the card database has anything in it must tell the
    # truth about WHY: a first-launch/refresh sync already populating it
    # right now (has_cards() is False only because it hasn't finished) is a
    # different, temporary situation from a genuinely empty, no-sync-running
    # database -- telling the user to go trigger an update while one is
    # already in flight is actively wrong advice.
    from mtgdb.ui import search as search_module

    class _RecordingMessagebox:
        def __init__(self):
            self.infos = []

        def showinfo(self, title, message):
            self.infos.append((title, message))

    class _EmptyDatabaseSearchOwner:
        def __init__(self, *, syncing):
            self._search_catalog_loading = False
            self._pending_search_request = False
            self.search_controller = type("Controller", (), {"running": False})()
            self.search_repository = type(
                "Repo", (), {"has_cards": staticmethod(lambda: False)})()
            self._syncing = syncing

        def _update_search_filter_summary(self):
            pass

        def _database_sync_is_running(self):
            return self._syncing

    original_messagebox = search_module.messagebox
    syncing_recorder = _RecordingMessagebox()
    idle_recorder = _RecordingMessagebox()
    try:
        search_module.messagebox = syncing_recorder
        SearchFeatureMixin._do_search(_EmptyDatabaseSearchOwner(syncing=True))
        search_module.messagebox = idle_recorder
        SearchFeatureMixin._do_search(_EmptyDatabaseSearchOwner(syncing=False))
    finally:
        search_module.messagebox = original_messagebox

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

        def _update_search_filter_summary_typing(self):
            # Name editing coalesces on the longer typing debounce.
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

    # SRCH-042. Scroll a pool of rows across a result set and compare every
    # visible row against the store at every stop. The pool is larger than the
    # viewport, so near the end some slots have no row to show; leaving those
    # alone let the ring carry stale rows back to the top, and the rows nearest
    # the end of a long result set stopped moving while the rest scrolled.
    scroll_owner = _ScrollOwner([
        {"id": str(index), "name": "Card %04d" % index, "mana_cost": ""}
        for index in range(400)
    ])
    scroll_owner._populate_result_window(force=True)
    scroll_stops = []

    def _record_scroll_stop():
        scroll_stops.append(
            scroll_owner.visible_names() == scroll_owner.expected_names()
            and scroll_owner.pool_is_consistent())

    for _step in range(60):
        scroll_owner._result_scroll(3, "units")
        _record_scroll_stop()
    for _step in range(40):
        scroll_owner._result_scroll(-5, "units")
        _record_scroll_stop()
    scroll_owner._set_result_top(10 ** 6)
    _record_scroll_stop()
    for _step in range(20):
        scroll_owner._result_scroll(-1, "units")
        _record_scroll_stop()
    for _step in range(30):
        scroll_owner._result_scroll(1, "units")
        _record_scroll_stop()
    every_scroll_stop_matches_the_store = all(scroll_stops)
    pool_order_follows_the_ring = (
        list(scroll_owner.results_tv.order) == scroll_owner._result_live_slots)

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
                     "_keyword_btn", "_search_scope_btn", "_card_form_btn",
                     "_mana_cost_features_btn", "_special_properties_btn",
                     "_status_properties_btn",
                     "_property_chip_frame")
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

    context_owner = object.__new__(SearchFeatureMixin)
    empty_low = _FakeContextControl()
    empty_high = _FakeContextControl()
    context_owner._set_context_field_pair_availability(
        (empty_low, empty_high), False)
    filled_low = _FakeContextControl("3")
    filled_high = _FakeContextControl()
    context_owner._set_context_field_pair_availability(
        (filled_low, filled_high), False)
    unavailable_pip = _FakeContextControl()
    selected_pip = _FakeContextControl()
    SearchFeatureMixin._set_context_check_availability(
        unavailable_pip, _FakeBoolVar(False), False)
    SearchFeatureMixin._set_context_check_availability(
        selected_pip, _FakeBoolVar(True), False)

    components_source = (ROOT / "mtgdb/ui/components.py").read_text(encoding="utf-8")
    styles_source = (ROOT / "mtgdb/ui/styles.py").read_text(encoding="utf-8")
    tokens_source = (ROOT / "mtgdb/ui/tokens.py").read_text(encoding="utf-8")

    activity_behaviour = _activity_indicator_behaviour()

    # The owner-side wiring of the centered cue, driven through the real methods
    # on minimal duck-typed owners.
    class _ContextEndOwner:
        def __init__(self):
            self.events = []
            events = self.events
            self._context_status = type(
                "Status", (), {"stop": lambda _self: events.append("stop")})()

        def _render_context_notice(self):
            self.events.append("render")

    context_end_owner = _ContextEndOwner()
    SearchFeatureMixin._end_context_update(context_end_owner)
    context_end_events = context_end_owner.events

    class _FiltersOwner:
        def __init__(self):
            self._filters_status = _FakeStatus()

    filters_end_owner = _FiltersOwner()
    SearchFeatureMixin._end_filters_loading(filters_end_owner)
    filters_cancel_owner = _FiltersOwner()
    SearchFeatureMixin._end_filters_loading(filters_cancel_owner, cancel=True)
    try:
        # Before the Search pane has built its cue there is nothing to lower.
        SearchFeatureMixin._end_filters_loading(object())
        filters_missing_ok = True
    except AttributeError:
        filters_missing_ok = False

    class _InvalidateOwner:
        def __init__(self, *, running):
            self.search_controller = type(
                "Controller", (), {"running": running,
                                   "invalidate": lambda _self: None})()
            self._search_btn = type("Button", (), {"state": lambda _s, _v: None})()
            self._active_search_signature = "old"
            self._search_status = _FakeStatus()

    invalidate_running_owner = _InvalidateOwner(running=True)
    SearchFeatureMixin._invalidate_search_cache(invalidate_running_owner)
    invalidate_idle_owner = _InvalidateOwner(running=False)
    SearchFeatureMixin._invalidate_search_cache(invalidate_idle_owner)

    class _ResultCountOwner:
        def __init__(self):
            self._results_status = _FakeStatus()
            self._search_status = _FakeStatus()

    result_count_owner = _ResultCountOwner()
    SearchResultsMixin._set_result_count(result_count_owner)

    class _BareResultCountOwner:
        renders = 0

        def _render_results_count(self):
            self.renders += 1

    bare_count_owner = _BareResultCountOwner()
    SearchResultsMixin._set_result_count(bare_count_owner)

    # Staged startup warm-ups: the facet index only after the trusted catalogs
    # have landed, the other-scope catalogs only after the first live count.
    class _WarmupOwner:
        def __init__(self, *, with_controller=True):
            self._search_warmup_stage = 0
            self.events = []
            events = self.events
            if with_controller:
                self.search_context_controller = type(
                    "Controller", (),
                    {"warm_facet_index": lambda _s: events.append("index")})()

        def _warm_common_search_catalogs(self):
            self.events.append("catalogs")

    warmup_owner = _WarmupOwner()
    warmup_trace = []
    for landed in ("context", "catalogs", "catalogs", "context", "context",
                   "catalogs"):
        SearchFeatureMixin._advance_search_warmups(warmup_owner, landed)
        warmup_trace.append(list(warmup_owner.events))
    controllerless_owner = _WarmupOwner(with_controller=False)
    SearchFeatureMixin._advance_search_warmups(controllerless_owner, "catalogs")
    startup_body = _method_body(
        (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8"),
        "_start_post_paint_initialization")

    checks = {
        "catalog loads off-lock give identical results and warming caches": (
            _catalog_reader_and_warm_check()),
        "inline working status is standardized through PulseStatus": (
            # One reusable threshold + minimum-dwell + gold-pulse controller.  The
            # RESULTS header uses it directly for its own table-view recompute;
            # the centered activity cue wraps it (below), so no status flashes for
            # a frame or reads as the error red.
            "class PulseStatus" in components_source
            and "def start(self" in components_source
            and "self._results_status = PulseStatus(" in search_source
            and 'working_styles=("SectionWorking.TLabel", "SectionWorkingDim.TLabel")'
                in search_source
            and all(name in styles_source for name in (
                "SectionWorking.TLabel", "SectionWorkingDim.TLabel",
                "ActivityWorking.TLabel", "ActivityWorkingDim.TLabel"))
            and all(name in tokens_source for name in (
                "STATUS_THRESHOLD_MS", "STATUS_MIN_DWELL_MS", "STATUS_PULSE_MS",
                "ACTIVITY_MIN_DWELL_MS", "FONT_ACTIVITY"))
            # Gold, never the red reserved for errors/unavailable.
            and '"working": "#E4C36A"' in tokens_source),
        "one large centered activity cue is shared by three prioritized sources": (
            "class ActivityIndicator" in components_source
            and "class ActivitySource" in components_source
            and "self._pulse = PulseStatus(" in components_source
            and "label.configure(font=FONT_ACTIVITY)" in components_source
            and "self._activity = ActivityIndicator(" in search_source
            and 'working_styles=("ActivityWorking.TLabel", "ActivityWorkingDim.TLabel")'
                in search_source
            and 'self._search_status = self._activity.source("search", priority=3)'
                in search_source
            and 'self._filters_status = self._activity.source("filters", priority=2)'
                in search_source
            and 'self._context_status = self._activity.source("context", priority=1)'
                in search_source
            # Nothing else may still own a second, competing copy of the cue.
            and "self._context_status = PulseStatus(" not in search_source),
        "activity cue sits between the Search and deck-add groups and yields first": (
            _method_body(search_source, "_build_search_actions").index(
                'deck_actions.pack(side="right")')
            < _method_body(search_source, "_build_search_actions").index(
                'search_actions.pack(side="left")')
            < _method_body(search_source, "_build_search_actions").index(
                "self._activity_label.pack(")
            # Packed last with expand so it takes only leftover space, and a
            # constant one-character request so showing text cannot resize the row.
            and 'text="", width=1, anchor="center"'
                in _method_body(search_source, "_build_search_actions")
            and 'side="left", fill="x", expand=True'
                in _method_body(search_source, "_build_search_actions")),
        "trusted-filter loading raises the cue and every exit path lowers it": (
            'status.start("Loading filters…")' in _method_body(
                search_source, "_mark_search_catalogs_loading")
            and "self._end_filters_loading()" in _method_body(
                search_source, "_apply_search_catalog_snapshot")
            and "self._end_filters_loading(cancel=True)" in _method_body(
                search_source, "_poll_search_catalogs")),
        "Search and live-context work use the centered cue, not the header or notice": (
            'self._search_status.start("Searching…")' in _method_body(
                search_source, "_do_search")
            and 'self._search_status.start("Searching…")' in _method_body(
                search_source, "_poll_search_events")
            and 'status.start("Updating…")' in _method_body(
                search_source, "_schedule_live_search_context")
            and "RESULTS | Searching…" not in search_source
            and "Trusted filters are loading…" not in search_source
            and "_show_context_notice_working" not in search_source
            and "MutedWorking" not in search_source + styles_source),
        "the cue no longer hides while trusted filters load": (
            # This early return used to stop the "Updating…" cue during startup,
            # the one window where the filters are disabled and the app most
            # needs to look busy.
            "self._end_context_update()" in _method_body(
                search_source, "_prepare_live_search_context")
            and "status.stop()" not in _method_body(
                search_source, "_prepare_live_search_context")),
        "abandoned or failed Search lowers the cue": (
            "search_status.cancel()" in _method_body(
                search_source, "_invalidate_search_cache")
            and "self._search_status.cancel()" in _method_body(
                search_source, "_poll_search_events")
            and "search_status.stop()" in _method_body(
                (ROOT / "mtgdb/ui/results.py").read_text(encoding="utf-8"),
                "_set_result_count")),
        **activity_behaviour,
        "startup warm-ups run in order: filters, then the index, then other scopes": (
            # Before the first catalog lands nothing starts; then only the index;
            # only after the first live count do the other-scope catalogs load;
            # and each stage runs once.
            warmup_trace == [
                [], ["index"], ["index"], ["index", "catalogs"],
                ["index", "catalogs"], ["index", "catalogs"]]
            and warmup_owner._search_warmup_stage == 2
            and controllerless_owner._search_warmup_stage == 1),
        "startup no longer launches the CPU-bound warm-ups beside the catalog load": (
            "warm_facet_index" not in startup_body
            and "_warm_common_search_catalogs" not in startup_body
            and "self._refresh_search_catalogs()" in startup_body
            and "self._search_warmup_stage = 0" in (
                ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
            and 'self._advance_search_warmups("catalogs")' in _method_body(
                search_source, "_apply_search_catalog_snapshot")
            and 'self._advance_search_warmups("context")' in _method_body(
                search_source, "_poll_search_context")),
        "context recompute ends by painting the notice before lowering the cue": (
            context_end_events == ["render", "stop"]
            and filters_end_owner._filters_status.stops == 1
            and filters_end_owner._filters_status.cancels == 0
            and filters_cancel_owner._filters_status.cancels == 1
            and filters_missing_ok
            and invalidate_running_owner._search_status.cancels == 1
            and invalidate_idle_owner._search_status.cancels == 0
            and result_count_owner._results_status.stops == 1
            and result_count_owner._search_status.stops == 1
            and bare_count_owner.renders == 1),
        "Clear coalesces catalog refresh and skips it when scope is unchanged": (
            # Clearing filters must not rebuild the trusted catalog (and its
            # chips) when the scope did not change -- that redundant rebuild was
            # most of the Clear hang.
            'if getattr(self, "_suppress_catalog_refresh", False):' in search_source
            and "def _catalog_request_signature(" in search_source
            and "clear_scope_before = self._catalog_request_signature()" in search_source
            and "self._suppress_catalog_refresh = True" in search_source
            and "self._catalog_request_signature() != clear_scope_before" in search_source),
        "Empty Type Line scopes show a labelled placeholder, not a blank gap": (
            # Emblems have no supertype; Art Series has neither supertype nor
            # card type. When the taxonomy is authoritative but the scope is
            # genuinely empty, the row shows one disabled ✕ placeholder chip
            # instead of an odd empty gap.
            "empty_label" in search_source
            and "_mtg_empty_scope_placeholder" in search_source
            and 'empty_label="No Supertypes in this scope"' in search_source
            and 'empty_label="No Card Types in this scope"' in search_source
            and 'f"✕ {empty_label}"' in search_source),
        "Dungeon live context treats no-cost mana value 0 as inapplicable": (
            dungeon_dynamic_applicability),
        "Mana Value applicability accepts literal zero costs and face-derived costs": (
            meaningful_mana_value_applicability),
        "live context grays inapplicable ranges and mana choices without trapping selections": (
            empty_low.disabled and empty_high.disabled
            and not filled_low.disabled and not filled_high.disabled
            and unavailable_pip.disabled and not selected_pip.disabled
            and not unavailable_pip._mtg_context_available
            and not selected_pip._mtg_context_available
            and 'if key in {"cmc", "power", "toughness", "loyalty", "defense"}' in search_source
            and 'bool(snapshot.release_years)' in search_source
            and 'snapshot.color_counts' in search_source
            and 'snapshot.produces_counts' in search_source
            and 'snapshot.pip_counts' in search_source),
        "Supertypes has a visible mode row and defaults to Any": (
            # It defaulted to All with no control to change it, so selecting
            # two supertypes silently reduced the search to the 17 cards that
            # carry both -- with no way for the user to correct it.
            'self.q_supertype_mode = tk.StringVar(value="any")' in search_source
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_standard_type_line_filters")),
        "every Any/All/None row comes from one compact Match builder": (
            # Card Type once hand-built its row and silently kept only Any and
            # All when None was added elsewhere. One construction point still
            # owns the modes, but the Search form now clusters them instead of
            # stretching three radio buttons across the full control width.
            ("None", "none") in SearchFeatureMixin.MODE_ROW_CHOICES
            and ("None", "none") in SearchChecklistDialog.MODE_CHOICES
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_card_type_filters")
            and "self._build_mode_row(" in _method_body(
                search_source, "_build_standard_type_line_filters")
            and "mode_var=self.q_mana_feature_mode" in _method_body(
                search_source, "_build_filter_mana_cost_features")
            and "mode_var=self.q_special_property_mode" in _method_body(
                search_source, "_build_filter_special_properties")
            and "mode_var=self.q_status_property_mode" in _method_body(
                search_source, "_build_filter_status_properties")
            and 'text=MATCH_MODE_LABEL' in _method_body(
                search_source, "_build_mode_row")
            and 'minsize=MATCH_MODE_LABEL_WIDTH' in _method_body(
                search_source, "_build_mode_row")
            and 'MATCH_MODE_CHOICE_GAP' in _method_body(
                search_source, "_build_mode_row")
            and 'uniform="search-mode-choice"' not in _method_body(
                search_source, "_build_mode_row")
            and 'mode_box.grid(row=1, column=0, sticky="ew"' in _method_body(
                search_source, "_build_rules_text_filter")
            # Picker dialogs use the same compact helper hierarchy rather than
            # stretching their mode choices across the popup.
            and 'HELPER_LABEL_WIDTH = 42' in (
                ROOT / "mtgdb/ui/search_checklist.py").read_text(encoding="utf-8")
            and 'uniform="picker-mode-choice"' not in (
                ROOT / "mtgdb/ui/search_checklist.py").read_text(encoding="utf-8")
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
        "property groups combine internally with Any by default": (
            trait_mode_widens
            and 'self.q_mana_feature_mode = tk.StringVar(value="any")' in search_source
            and 'self.q_special_property_mode = tk.StringVar(value="any")' in search_source
            and 'self.q_status_property_mode = tk.StringVar(value="any")' in search_source),
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
                             + list(STANDARD_FILTER_TOOLTIPS.values()))
                for word in ("scryfall", "database", "snapshot", "api", "trusted vocabulary"))
            # Every standard filter is explained too, whether its wording
            # comes from the registry or from the hand-built dict, and one
            # lookup serves both so neither can be described twice.
            and all(filter_tooltip(key) for key in STANDARD_FILTERS)
            and set(STANDARD_FILTER_TOOLTIPS) <= set(STANDARD_FILTERS)
            and "_add_standard_filter_tooltip" in printings_source),
        "tooltips belong only to Search filters, not Search actions": (
            "_add_tooltip(" not in _method_body(search_source, "_build_search_actions")
            and "_add_tooltip(" not in _method_body(search_source, "_build_advanced_filter_zone")
            and "_add_tooltip(" in _method_body(search_source, "_build_color_filters")
            and "_add_tooltip(" in _method_body(search_source, "_build_mode_row")
            # Shared Printings presentation defaults tooltips off; Search is the
            # interactive filter caller that explicitly enables them.
            and "tooltips_enabled=False" in printings_source
            and "if not self._tooltips_enabled:" in printings_source
            and "tooltips_enabled=True" in printings_source),
        "Search filter tooltip controls do not expose implementation provenance": (
            not any(
                forbidden in body.casefold()
                for body in (
                    _method_body(search_source, "_build_color_filters"),
                    _method_body(search_source, "_build_produces_filter"),
                    _method_body(search_source, "_build_filter_mana_pips"),
                    _method_body(search_source, "_numeric_pair"),
                    _method_body(search_source, "_build_mode_row"),
                    _method_body(search_source, "_context_choice_text"),
                    _method_body(search_source, "_context_range_text"),
                )
                for forbidden in (
                    "scryfall", "database", "snapshot", " api ",
                    "trusted vocabulary", "storage field", "query name",
                )
            )),
        "live tooltip context explains counts and unavailable choices": (
            'tooltip_key="card_type"' in search_source
            and 'tooltip_key="supertypes"' in search_source
            and "_context_choice_text" in search_source
            and "match this" in _method_body(search_source, "_context_choice_text")
            and "Not available with the current filters" in search_source
            and "It stays available so you can deselect it" in search_source
            and "cards have numeric" in _method_body(search_source, "_context_range_text")),
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
            == ("name", "supertypes", "card_type", "subtype",
                "colors", "stats")
            # Power/Toughness is a standard row now, so it is built by the
            # form rather than reached through the advanced panel.
            and "self._build_standard_type_line_filters(form)" in _method_body(
                search_source, "_build_search_pane")
            and "self._build_standard_stats_filter(form, row=6)" in _method_body(
                search_source, "_build_search_pane")
            and all(is_standard(key) for key in STANDARD_FILTERS)),
        "advanced holds every other filter, grouped and in registry order": (
            set(catalog_keys) | set(STANDARD_FILTERS)
            == set(FILTER_BY_KEY) | {"name", "colors", "card_type"}
            and not (set(catalog_keys) & set(STANDARD_FILTERS))
            # Category order is the registry's, so a filter is always in the
            # same place rather than wherever it was opened first.
            and [category for category, _entries in catalog]
            == [c for c in CATEGORY_ORDER]
            and advanced_filter_keys()[:4]
            == ("search_scope", "mana_value", "produces", "mana_pips")
            and any(
                category == "Printing & Status"
                and entries and entries[0]["key"] == "printings"
                for category, entries in catalog)),
        "advanced rows are built once and only hidden": (
            "def _build_advanced_filter_rows(" in search_source
            and "self._advanced_host.pack_forget()" in _method_body(
                search_source, "_toggle_advanced_filters")
            # Rebuilding on expand would make the first click the slowest.
            and "self._build_advanced_filter_rows()" in _method_body(
                search_source, "_build_advanced_filter_zone")
            and "_build_advanced_filter_rows" not in _method_body(
                search_source, "_toggle_advanced_filters")),
        "the advanced panel does not depend on a frame built after it": (
            # The zone is built before the actions row it packs above, so a
            # missing anchor must not be an AttributeError only a real window
            # can reveal -- the class of bug that shipped as _rarity_btn.
            'getattr(self, "_search_actions_frame", None)' in _method_body(
                search_source, "_toggle_advanced_filters")),
        "clearing advanced rows resets them in place without rebuilding": (
            # Clear no longer destroys and rebuilds ~15 controls (the bulk of the
            # old Clear cost); each row's reset clears state in place and the
            # shared caption pass refreshes the picker buttons.
            "child.destroy()" not in _method_body(
                search_source, "_reset_advanced_filter_values")
            and "_build_filter_" not in _method_body(
                search_source, "_reset_advanced_filter_values")
            and "_refresh_split_trait_button_texts()" in _method_body(
                search_source, "_reset_advanced_filter_values")),
        "collapsing advanced returns Results to the first row": (
            "self._reset_results_viewport()" in _method_body(
                search_source, "_toggle_advanced_filters")),
        "a workspace saved before the split still restores": (
            # The values never changed, only the key naming them. Dropping the
            # old key would have emptied every advanced row on first launch.
            'state.get("advanced_values", state.get("optional_values", {}))'
            in _method_body(search_source, "_restore_search_workspace_state")),
        "whether advanced is open survives the session": (
            '"advanced_expanded": bool(getattr(self, "_advanced_expanded", False)),'
            in _method_body(search_source, "_capture_search_workspace_state")
            and 'state.get("advanced_expanded", False)' in _method_body(
                search_source, "_restore_search_workspace_state")),
        "the control a user operates explains itself, not only its label": (
            # People hover the picker or the box they are about to use, not
            # the word beside it, so a tooltip only on the label is one most
            # of them never see.
            # Controls are tooltipped once when the rows are built and are never
            # rebuilt, so the tooltips persist across a Clear.
            "def _tooltip_row_controls(" in search_source
            and "self._tooltip_row_controls(frame, entry[\"tooltip\"])"
            in _method_body(search_source, "_build_advanced_filter_rows")
            # Two tooltips on one widget both fire, so bulk tagging has to
            # know which controls already carry their own wording.
            and 'getattr(widget, "_mtg_tooltip", None) is not None'
            in _method_body(search_source, "_tooltip_row_controls")
            and "widget._mtg_tooltip = tip" in (
                ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")),
        "the Printings popup explains the scope it sets": (
            # It decides what every other filter has to offer, and had no
            # explanation of any kind on any control.
            all(name in printings_source for name in (
                "PLATFORM_HELP", "PLATFORM_SECTION_HELP", "SET_TYPE_HELP",
                "EXACT_SET_HELP", "ENGLISH_HELP", "SET_TYPE_DESCRIPTIONS"))
            # Set-type names come from the publisher: Arsenal, Box and
            # Memorabilia are not categories anyone would guess.
            and all(key in printings_source for key in (
                '"memorabilia":', '"masterpiece":', '"draft_innovation":'))),
        "range boxes and pip counts say how they combine": (
            "RANGE_BOUNDS_HELP" in search_source
            and "PIP_SELECTION_HELP" in search_source
            and "Min and Max are inclusive" in search_source
            and "Minimum is the total number" in search_source
            and "counts as one symbol toward Minimum" in search_source
            and 'self.q_pip_mode' in search_source),
        "tooltips say what is matched, not what the control is": (
            # Confusable filter families explain their boundaries explicitly.
            "separate from" in tooltips["produces"]
            and "Rules Text" in tooltips["mechanics"]
            and "rules text" in tooltips["rules_text"]
            and "planeswalkers" in tooltips["loyalty"]
            and "qualifying printing" in tooltips["rarity"]
            and "separate characteristics" in tooltips["defense"]
            # Mana Color explains both secondary control rows and the colorless distinction.
            and "Color Identity" in filter_tooltip("colors")
            and "Card Colors" in filter_tooltip("colors")
            and "Within" in filter_tooltip("colors")
            and "Contains" in filter_tooltip("colors")
            and "Exactly" in filter_tooltip("colors")
            # Multi-faced type-line behavior and negative rules-text matching are explicit.
            and "either face" in tooltips["supertypes"]
            and "None excludes" in tooltips["rules_text"]
            and "Tokens" in tooltips["search_scope"]
            # The labels on the controls are American; the tooltips beside them cannot be British.
            and not any(
                "colour" in filter_tooltip(key).casefold()
                for key in STANDARD_FILTERS + advanced_filter_keys())
            # Search Printings help follows the same behavior-only rule.
            and not any(
                forbidden in text.casefold()
                for text in (
                    tuple(PLATFORM_HELP.values())
                    + (PLATFORM_SECTION_HELP, SET_TYPE_HELP, EXACT_SET_HELP, ENGLISH_HELP)
                    + tuple(SET_TYPE_DESCRIPTIONS.values())
                )
                for forbidden in (
                    "scryfall", "database", "snapshot", " api ",
                    "trusted vocabulary", "storage field", "query name",
                )
            )),
        "every card trait has a query clause or selects content": (
            # Content traits choose which objects the search covers instead of
            # adding a clause, so they are satisfied by content_types.
            trait_keys <= (
                set(SearchQueryBuilder.TRAIT_CLAUSES)
                | {"multi_faced", "single_faced"}
                | set(CONTENT_TRAIT_KEYS))),
        "None of a stat property includes cards with no power/toughness":
            property_none_includes_null_stats,
        "card traits narrow the query and unknown keys are ignored": (
            traits_narrow_the_query
            and unknown_trait_is_ignored_not_widening),
        "property families are independent facets with local modes": (
            independent_property_groups
            and "mana_features" in SearchCriteria.__dataclass_fields__
            and "special_properties" in SearchCriteria.__dataclass_fields__
            and "status_properties" in SearchCriteria.__dataclass_fields__
            and "builder.add_property_filters(mana_features, mana_feature_mode)"
                in search_query_source
            and "builder.add_property_filters(special_properties, special_property_mode)"
                in search_query_source
            and "builder.add_property_filters(status_properties, status_property_mode)"
                in search_query_source),
        "release bounds are inclusive years": release_bounds_are_inclusive,
        "the content scope can narrow as well as widen": (
            # While Cards could not be turned off, asking to see the tokens
            # meant adding 3,000 of them to 100,000 cards and hunting.
            'DEFAULT_CONTENT_TRAITS = ("content_cards",)' in search_source
            and '("content_cards", "Cards")' in search_source
            and "kind for key, kind in CONTENT_TRAIT_KEYS.items() if key in selected"
            in search_source
            # Every scope off would search nothing at all, which is not a
            # search anybody meant to run.
            and 'return kinds or {"card"}' in search_source
            and "set(DEFAULT_CONTENT_TRAITS)" in _method_body(
                search_source, "_reset_filter_search_scope")),
        "a saved content scope survives a workspace written without it": (
            # The saved traits list used to overwrite the scope derived from
            # the saved content, so an older workspace lost its tokens.
            "restored_content_traits" in _method_body(
                search_source, "_restore_search_workspace_state")
            and 'str(value) not in CONTENT_TRAIT_KEYS' in _method_body(
                search_source, "_restore_search_workspace_state")),
        "including tokens rebuilds the vocabulary it widens": (
            # The results were always right; the pickers kept describing cards
            # only, because the callback that rescopes them had no caller.
            "self._on_content_filter_change()" in _method_body(
                search_source, "_choose_search_scope")
            and "self._content_types_from_traits()" in _method_body(
                search_source, "_choose_search_scope")),
        "search scope is separate from property match modes": (
            # Tokens/Emblems/Art Series choose the searched universe and never
            # enter any of the independent boolean-property facets.
            "q_mana_feature_mode" not in _method_body(
                search_source, "_choose_search_scope")
            and "q_special_property_mode" not in _method_body(
                search_source, "_choose_search_scope")
            and "q_status_property_mode" not in _method_body(
                search_source, "_choose_search_scope")
            and 'traits=(), trait_mode="any"' in _method_body(
                search_source, "_capture_search_criteria")),
        "Produces is restored after its row exists": (
            # Its checkboxes belong to an optional row, so a restore that runs
            # before the rebuild is discarded with the widgets that held it.
            _method_body(
                search_source, "_restore_search_workspace_state").index(
                    "wanted_produces")
            > _method_body(
                search_source, "_restore_search_workspace_state").index(
                    "_restore_advanced_filter_values(")),
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
            all(f'"{label}"' in _method_body(
                    search_source, "_capture_search_criteria")
                for label in ("Mana value", "Power", "Toughness",
                              "Loyalty", "Defense", "Released"))
            and _method_body(search_source, "_capture_search_criteria").count(
                "self._validate_search_range(") >= 4),
        "Mana Color can look at either color column": (
            colour_scope_selects_the_column
            and "self.q_color_scope = tk.StringVar(value=\"identity\")"
            in search_source
            and "color_scope=self.q_color_scope.get()" in _method_body(
                search_source, "_capture_search_criteria")
            and '"color_scope": self.q_color_scope.get(),' in _method_body(
                search_source, "_capture_search_workspace_state")
            # The Search form uses one stable secondary hierarchy regardless
            # of which color field is active: Use chooses the field and Match
            # chooses Within / Contains / Exactly.
            and 'COLOR_SCOPE_LABEL = "Use"' in search_source
            and 'text=COLOR_SCOPE_LABEL' in _method_body(
                search_source, "_build_color_filters")
            and 'text=MATCH_MODE_LABEL' in _method_body(
                search_source, "_build_color_filters")
            and 'COLOR_SCOPE_LABELS' not in search_source),
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
        "card form preserves the layout query and only Any or None applies": (
            shape_is_exclusive
            and FILTER_BY_KEY["card_form"]["category"] == "Card"
            and "choices=self.ANY_NONE_CHOICES" in _method_body(
                search_source, "_build_filter_card_form")
            and "Card Shape" not in FILTER_BY_KEY
            and "Card Traits" not in FILTER_BY_KEY),
        "multi-faced is read from the faces, not from a layout list": (
            # The list called Saga, Class, Case, Leveler, Prototype, Mutate
            # and Meld multi-faced: 761 paper printings with one face.
            faces_decide_multi_faced
            and "MULTI_FACE_LAYOUTS" not in search_query_source
            and "HAS_FACES_CLAUSE" in search_query_source),
        "mana symbols use physical-symbol totals without double-counting hybrids": (
            mana_symbol_total_semantics
            and all(f"pips_{c}" in _CARD_COLUMN_NAMES for c in "wubrgc")
            # Import-time per-color counts remain cheap presence prefilters,
            # while the cost parser owns the physical-symbol total semantics.
            and "_mana_pips" in bulk_import_source
            and "MANA_COST_SYMBOL_MATCH" in search_query_source),
        "a removed filter leaves nothing of itself behind": (
            # Printed in was built and then not wanted. A criterion with no
            # control is the orphan SRCH-039 forbids, and a stored column with
            # no criterion is the same waste one layer down: it cost 1.9s of
            # every import and a column on every row.
            "print_sets" not in _CARD_COLUMN_NAMES
            and "print_sets" not in bulk_import_source
            and "print_count" not in search_source
            and "print_min" not in {field.name for field in fields(SearchCriteria)}
            and "print_count" not in FILTER_BY_KEY),
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
        "controller streams a first screen before the full result store": (
            _progressive_delivery_check()),
        "context worker prepares data-derived facets off the Search path": (
            context_worker_prepares_facets),
        "Any facet compatibility does not let a selected OR peer revive zero options": (
            any_peer_does_not_inflate_zero),
        "union-style facets do not let selected peers revive zero options": (
            union_facets_do_not_inflate_zero),
        "independent property facets preserve cross-filter predictive counts": (
            independent_property_context),
        "context worker publishes only the newest requested generation": (
            context_worker_is_latest_wins),
        "live draft context uses the same criteria adapter as manual Search": (
            "def _capture_search_criteria(" in search_source
            and "criteria = self._capture_search_criteria(commit_rules=False)"
                in _method_body(search_source, "_prepare_live_search_context")
            and "criteria = self._capture_search_criteria(commit_rules=True)"
                in _method_body(search_source, "_do_search")
            and "int(delay_ms), self._prepare_live_search_context"
                in _method_body(search_source, "_schedule_live_search_context")),
        "discrete controls debounce shorter than free-text/numeric typing": (
            # Clicking a checkbox/picker refreshes context snappily; typing in a
            # text or numeric field coalesces on a longer debounce.
            "_CONTEXT_DEBOUNCE_DISCRETE_MS" in search_source
            and "_CONTEXT_DEBOUNCE_TYPING_MS" in search_source
            and SearchFeatureMixin._CONTEXT_DEBOUNCE_DISCRETE_MS
                < SearchFeatureMixin._CONTEXT_DEBOUNCE_TYPING_MS
            and "self._CONTEXT_DEBOUNCE_DISCRETE_MS" in _method_body(
                search_source, "_update_search_filter_summary")
            and "self._CONTEXT_DEBOUNCE_TYPING_MS" in _method_body(
                search_source, "_update_search_filter_summary_typing")
            and "def _update_search_filter_summary_typing(" in search_source
            # Free-text / numeric typing routes to the longer debounce.
            and "self._update_search_filter_summary_typing()" in _method_body(
                search_source, "_on_name_filter_edited")
            and "change_command=self._update_search_filter_summary_typing"
                in search_source),
        "live context covers every existing search dimension without adding one": (
            all(name in SearchContextSnapshot.__dataclass_fields__ for name in (
                "card_type_counts", "supertype_counts", "subtype_counts",
                "keyword_counts", "color_counts", "produces_counts",
                "layout_counts", "rarity_counts", "numeric_ranges",
                "release_years", "trait_counts", "mana_feature_counts",
                "special_property_counts", "status_property_counts", "pip_counts",
                "content_counts", "game_counts", "set_type_counts",
                "set_counts", "format_counts", "english_count"))),
        "broad live totals use canonical SQL count rather than result materialization": (
            "COUNT(*) AS match_count" in search_query_source
            and "def count_search(" in search_query_source
            and "search_unordered" in (ROOT / "mtgdb/search/repository.py").read_text(encoding="utf-8")),
        "zero-result filter-window choices show a red X and cannot be newly selected": (
            'metadata.get("zero_count")' in checklist_source
            and '"Unavailable.ListChoice.TRadiobutton"' in checklist_source
            and 'f"✕ {shown}" if zero_count else shown' in checklist_source
            and 'key in self._zero_count_keys and key not in self._selected' in checklist_source
            and 'selectable = keys - self._zero_count_keys' in checklist_source
            and 'set_context_availability' in search_source
            and '{"zero_count": count <= 0}' in search_source),
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
            # The centered "Loading filters…" cue already names the wait: the
            # queued click neither rewrites the RESULTS header nor fabricates a
            # "Searching…" cue for a query that has not started.
            and loading_owner.results_count_lbl.text == "RESULTS | 0 CARDS"
            and not loading_owner._results_status.starts
            and not loading_owner._search_status.starts
            and "automatically" in loading_owner.status
            and resumed and ready_owner.calls == 1
            and ready_owner.count_restores == 1
            and not ready_owner._pending_search_request
            and not blocked and blocked_owner.calls == 0
            and blocked_owner._pending_search_request
            and 'self._resume_pending_search_request()' in search_source
            and 'if start.kind == "unchanged":\n            self._set_result_count()'
                in search_source),
        "empty-database Search message tells the truth about an in-flight sync": (
            len(syncing_recorder.infos) == 1
            and syncing_recorder.infos[0][0] == "Card database is being prepared"
            and "already" not in syncing_recorder.infos[0][1].casefold()
            and "update database" not in syncing_recorder.infos[0][1].casefold()
            and len(idle_recorder.infos) == 1
            and idle_recorder.infos[0][0] == "No cards yet"
            and "update database" in idle_recorder.infos[0][1].casefold()),
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
        "every scroll shows the rows the store says it should": (
            every_scroll_stop_matches_the_store
            # The physical order has to keep following the ring, or the next
            # rotation moves the wrong rows.
            and pool_order_follows_the_ring),
        "the standard core is built and every other filter is in Advanced": (
            'text="Active Filters"' not in search_source
            and "self._build_name_filter(form)" in search_source
            and "self._build_standard_type_line_filters(form)" in search_source
            and "self._build_color_filters(form, row=5)" in search_source
            and "self._build_printing_filter(form, row=8)" not in search_source
            and "def _build_filter_printings(" in search_source
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
            'form, "Card Name", row=0, pady=SEARCH_ROW_PADY, tooltip_key="name"'
            in search_source
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
