"""Shared-table ownership, preferences, migration, and behavior contracts."""

import ast
import json
import tempfile
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.preferences.repository import TABLE_COLUMNS_VERSION, UIPreferencesRepository
from mtgdb.search.repository import SEARCH_RESULT_COLUMNS
from mtgdb.ui.tables import (
    TABLE_COLUMNS, TABLE_DEFAULTS, TableInfrastructureMixin,
    available_columns, column_popup_position, normalized_visible_columns,
    table_sort_key, table_value,
)




class _Var:
    def __init__(self, value=False):
        self.value = bool(value)

    def set(self, value):
        self.value = bool(value)

    def get(self):
        return self.value


class _FakeTree:
    def __init__(self):
        self.columns = {"#0": {"width": 999}}
        self.config = {}

    def column(self, key, option=None, **kwargs):
        state = self.columns.setdefault(key, {})
        if kwargs:
            state.update(kwargs)
        if option is not None:
            return state.get(option)
        return dict(state)

    def configure(self, **kwargs):
        self.config.update(kwargs)

    def heading(self, *_args, **_kwargs):
        return None


class _ResetHarness(TableInfrastructureMixin):
    def __init__(self):
        self.tree = _FakeTree()
        self._visible_columns = {
            "results": ["cost", "rules", "collector"],
            "main": list(TABLE_DEFAULTS["main"]),
            "side": list(TABLE_DEFAULTS["side"]),
        }
        self._column_vars = {
            "results": {key: _Var(key in self._visible_columns["results"])
                        for key in available_columns("results")}
        }
        self.saved = 0
        for key in available_columns("results"):
            physical = "#0" if key == "cost" else key
            self.tree.column(physical, width=999, minwidth=1, stretch=False)

    def _table_widget(self, view):
        return self.tree if view == "results" else None

    def _update_table_headings(self, _view, _tv):
        return None

    def _save_ui_preferences(self):
        self.saved += 1


def _class_methods(source, class_name):
    tree = ast.parse(source)
    target = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {
        node.name for node in target.body if isinstance(node, ast.FunctionDef)
    }, target


def main():
    gui_source = (ROOT / "mtgdb/ui/app.py").read_text(encoding="utf-8")
    table_source = (ROOT / "mtgdb/ui/tables.py").read_text(encoding="utf-8")
    filter_source = (ROOT / "mtgdb/ui/table_filters.py").read_text(encoding="utf-8")
    search_source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    deck_source = (ROOT / "mtgdb/ui/deck.py").read_text(encoding="utf-8")
    preference_source = (
        ROOT / "mtgdb/preferences/repository.py").read_text(encoding="utf-8")
    gui_methods, gui_class = _class_methods(gui_source, "DeckBuilderApp")
    table_methods, _table_class = _class_methods(
        table_source, "TableInfrastructureMixin")
    schema_owners = []
    for production_path in sorted((ROOT / "mtgdb").rglob("*.py")):
        source = production_path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                    isinstance(target, ast.Name)
                    and target.id in {"TABLE_COLUMNS", "TABLE_DEFAULTS"}
                    for target in targets):
                schema_owners.append(
                    production_path.relative_to(ROOT).as_posix())

    card = {
        "name": "B.F.M. (Big Furry Monster)", "cmc": 15,
        "type_line": "Creature — The-Biggest-Baddest-Nastiest-Scariest-Creature-You'll-Ever-See",
        "set_name": "Unglued", "set_code": "ugl",
        "collector_number": "28a", "keywords": '["Fear", "Trample"]',
        "rarity": "rare", "power": "99", "toughness": "99",
        "colors": "B", "oracle_text": "Fear\nTrample", "released_at": "1998-08-11",
    }

    legacy = {
        "table_columns_version": 1,
        "table_columns": {
            "results": ["name", "rarity", "bogus", "name", "cost"],
            "main": ["qty", "name", "rarity"],
            "side": [],
        },
    }
    migrated = normalized_visible_columns(legacy)

    with tempfile.TemporaryDirectory() as temporary_directory:
        path = Path(temporary_directory) / "ui_preferences.json"
        path.write_text(json.dumps({"unrelated": {"keep": True}}), encoding="utf-8")
        repository = UIPreferencesRepository(path)
        repository.save_table_columns(migrated)
        stored = json.loads(path.read_text(encoding="utf-8"))
        no_temporary_file = not path.with_name(path.name + ".tmp").exists()
        path.write_text("not valid json", encoding="utf-8")
        invalid_fallback = repository.load()

    result_column_fields = {
        "cost": {"cmc"}, "name": {"name"}, "type": {"type_line"},
        "set": {"set_name", "set_code"}, "collector": {"collector_number"},
        "year": {"released_at"}, "ability": {"keywords"},
        "rarity": {"rarity"}, "cmc": {"cmc"}, "power": {"power"},
        "toughness": {"toughness"}, "colors": {"colors"},
        "rules": {"oracle_text"},
    }
    required_result_fields = set().union(*(
        result_column_fields[key]
        for key, spec in TABLE_COLUMNS.items()
        if "results" in spec["views"] and key != "qty"
    ))

    reset_harness = _ResetHarness()
    reset_harness._reset_columns("results")
    reset_geometry_ok = (
        reset_harness._visible_columns["results"] == TABLE_DEFAULTS["results"]
        and reset_harness.tree.config.get("displaycolumns")
            == tuple(key for key in TABLE_DEFAULTS["results"] if key != "cost")
        and all(
            reset_harness.tree.columns["#0" if key == "cost" else key].get("width")
                == TABLE_COLUMNS[key]["width"]
            for key in available_columns("results"))
        and reset_harness.saved == 1
    )

    extracted = {
        "_load_ui_preferences", "_save_ui_preferences", "_table_value",
        "_table_sort_key",
        "_update_table_headings", "_setup_table_columns",
        "_apply_table_columns", "_toggle_column_popup",
        "_show_column_popup", "_hide_column_popup", "_create_column_popup",
        "_columns_changed", "_reset_columns", "_display_column_at",
        "_column_boundaries", "_drop_index_for_x", "_bind_column_drag",
    }
    bases = {
        base.id for base in gui_class.bases if isinstance(base, ast.Name)
    }
    monitor_popup = column_popup_position(
        anchor_x=3700, anchor_y=980, anchor_height=28,
        popup_width=320, popup_height=360,
        work_area=(1920, 0, 1920, 1040), margin=8)
    negative_monitor_popup = column_popup_position(
        anchor_x=-1500, anchor_y=900, anchor_height=28,
        popup_width=320, popup_height=360,
        work_area=(-1920, 0, 1920, 1040), margin=8)

    checks = {
        "table schema has one production owner": (
            schema_owners == ["mtgdb/ui/tables.py", "mtgdb/ui/tables.py"]),
        "all configurable table views use one schema": (
            set(TABLE_DEFAULTS) == {"results", "main", "side"}
            and all(TABLE_DEFAULTS[view] for view in TABLE_DEFAULTS)
            and all(
                key in available_columns(view)
                for view, columns in TABLE_DEFAULTS.items() for key in columns)),
        "Results card-data columns are present in the narrow Search projection": (
            required_result_fields <= set(SEARCH_RESULT_COLUMNS)),
        "existing table labels and widths are unchanged": (
            {key: (value["label"], value["width"])
             for key, value in TABLE_COLUMNS.items()} == {
                "cost": ("Cost", 105), "qty": ("Qty", 48),
                "name": ("Name", 240), "type": ("Type", 230),
                "set": ("Set", 180), "collector": ("Collector #", 92),
                "year": ("Year", 62), "ability": ("Ability", 170),
                "rarity": ("Rarity", 78), "cmc": ("Mana Value", 92),
                "power": ("Power", 68), "toughness": ("Toughness", 82),
                "colors": ("Colors", 90), "rules": ("Rules Text", 360),
            }),
        "shared formatting preserves exact printing identity": (
            table_value(card, "collector") == "28a"
            and table_value(card, "set") == "Unglued (UGL)"
            and table_value(card, "ability") == "Fear, Trample"),
        "shared sorting handles numeric and suffixed values": (
            table_sort_key(card, "collector")[:2] == (28, "28a")
            and table_sort_key(card, "power")[:2] == (0, 99.0)),
        "legacy layouts migrate and discard unsafe columns": (
            migrated["results"][0] == "cost"
            and migrated["results"].count("name") == 1
            and "bogus" not in migrated["results"]
            and "collector" not in TABLE_DEFAULTS["results"]
            and all(key in migrated["results"] for key in (
                "power", "toughness"))
            and migrated["side"] == TABLE_DEFAULTS["side"]),
        "preference writes are atomic and preserve unrelated values": (
            stored["unrelated"] == {"keep": True}
            and stored["table_columns_version"] == TABLE_COLUMNS_VERSION
            and stored["table_columns"] == migrated
            and no_temporary_file),
        "invalid preference files safely fall back": invalid_fallback == {},
        "preference persistence is Tk-free": "tkinter" not in preference_source,
        "column popup is built hidden before mapping": (
            "popup.withdraw()" in table_source
            and table_source.index("popup = self._create_column_popup(view)")
            < table_source.index("popup.geometry(")
            < table_source.index("popup.deiconify()")),
        "column popup clamps to the anchor monitor work area": (
            1928 <= monitor_popup[0] <= 3512
            and 8 <= monitor_popup[1] <= 672
            and -1912 <= negative_monitor_popup[0] <= -328
            and 8 <= negative_monitor_popup[1] <= 672
            and "self._work_area_for_widget(anchor)" in table_source),
        "Cost remains the protected tree column": (
            'column == "cost"' in table_source
            and migrated["results"][0] == "cost"),
        "column Reset restores default order visibility and widths in one click": reset_geometry_ok,
        "column popup clear is scoped while header clear removes all view filters": (
            'text="Clear table filters"' not in filter_source
            and 'text="Clear this"' in filter_source
            and 'self._clear_table_filter(view, key)' in filter_source
            and 'self._clear_table_filter("results")' in search_source
            and 'self._clear_table_filter("main")' in deck_source
            and 'self._clear_table_filter("side")' in deck_source),
        "DeckBuilderApp delegates shared table ownership": (
            "TableInfrastructureMixin" in bases
            and extracted <= table_methods
            and not extracted & gui_methods),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nTABLE ARCHITECTURE:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
