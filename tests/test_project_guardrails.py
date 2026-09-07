"""Enforce agent-rule syntax, architecture boundaries, and release blocking."""

import ast
import re
import sys
import tempfile
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import package_release as P


RULE_START = re.compile(
    r"^- \*\*([A-Z]+-\d{3}) — "
    r"(MUST NOT|SHOULD NOT|MUST|SHOULD|MAY):\*\*")
VERIFICATION = re.compile(
    r"_Verification:_ \*\*(AUTO|WINDOWS|REVIEW|MANUAL|USER)\*\*\.")
AMBIGUOUS_PHRASES = (
    "as appropriate",
    "as needed",
    "if appropriate",
    "if needed",
    "when possible",
    "where possible",
    "significant change",
    "normally",
    "generally",
    "etc.",
)
REQUIRED_RULE_FAMILIES = {
    "GOV", "DOC", "CHG", "LAYER", "NAM", "UI", "CLR", "TYP", "SIZ", "LAY",
    "BEH", "DATA", "PORT", "BLD", "VER", "REL", "SRCH",
    "TBL", "IMG", "DECK", "DUI", "WSP",
    "DBS", "DBI", "PRN", "CMP", "BGJ", "WIN",
}
REQUIRED_VERIFICATION_CLASSES = {"AUTO", "WINDOWS", "REVIEW", "MANUAL", "USER"}


def _rule_blocks(text):
    blocks = []
    current = []
    for line in text.splitlines():
        if line.startswith("- "):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif line.startswith("#"):
            if current:
                blocks.append("\n".join(current))
                current = []
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _raises_runtime_error(callback):
    try:
        callback()
    except RuntimeError:
        return True
    return False


def _import_roots(source):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _import_paths(source):
    """Return complete imported module paths instead of lossy top-level roots."""
    paths = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            paths.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            paths.add(node.module)
    return paths


def _imports_prefix(paths, prefix):
    return any(path == prefix or path.startswith(prefix + ".") for path in paths)


def _literal_assignment(source, name):
    """Return one top-level literal assignment from a source module."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name
                   for target in node.targets):
                return ast.literal_eval(node.value)
        elif (isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name)
              and node.target.id == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing literal assignment: {name}")


def _internal_import_allowed(module, imported):
    """Mirror the dependency-direction rows in AGENTS.md for mtgdb imports."""
    if not imported.startswith("mtgdb."):
        return True
    if module.startswith("mtgdb/ui/"):
        return True
    if module == "mtgdb/__main__.py":
        return imported == "mtgdb.main"
    if module == "mtgdb/main.py":
        return imported in {"mtgdb.database.db", "mtgdb.ui.app"}
    if module.startswith("mtgdb/core/"):
        return False
    if module.startswith("mtgdb/search/"):
        return imported.startswith(("mtgdb.search.", "mtgdb.database.", "mtgdb.core."))
    if module.startswith("mtgdb/deck/"):
        return imported.startswith(("mtgdb.deck.", "mtgdb.core."))
    if module in {"mtgdb/database/authorities.py", "mtgdb/database/constants.py"}:
        return False
    if module == "mtgdb/database/db.py":
        return imported in {
            "mtgdb.database.schema", "mtgdb.database.queries",
            "mtgdb.database.taxonomy", "mtgdb.database.semantics",
            "mtgdb.database.bulk_import", "mtgdb.database.search_queries",
        }
    if module == "mtgdb/database/sync.py":
        return imported in {
            "mtgdb.core.net", "mtgdb.core.background_jobs",
            "mtgdb.database.authorities", "mtgdb.database.bulk_import",
            "mtgdb.database.schema",
        }
    if module.startswith("mtgdb/database/"):
        return imported in {
            "mtgdb.database.authorities", "mtgdb.database.constants",
            "mtgdb.database.semantics", "mtgdb.database.schema",
        }
    if module == "mtgdb/images/service.py":
        return imported in {
            "mtgdb.core.net", "mtgdb.core.cache_names",
            "mtgdb.core.background_jobs", "mtgdb.core.scryfall_json",
        }
    if module == "mtgdb/printing/service.py":
        return imported in {
            "mtgdb.core.net", "mtgdb.core.cache_names",
            "mtgdb.core.background_jobs", "mtgdb.printing.renderer",
        }
    if module == "mtgdb/printing/renderer.py":
        return False
    if module == "mtgdb/workspace/repository.py":
        return imported in {
            "mtgdb.core.background_jobs", "mtgdb.core.atomic_files",
            "mtgdb.deck.model", "mtgdb.deck.sessions"}
    if module == "mtgdb/deck/sessions.py":
        return imported == "mtgdb.deck.model"
    if module.startswith("mtgdb/comparison/"):
        return imported == "mtgdb.core.scryfall_json"
    if module.startswith("mtgdb/preferences/"):
        # The shared atomic-write helper is the one dependency: preferences
        # writes the same way every other durable writer does.
        return imported == "mtgdb.core.atomic_files"
    # Package __init__ files are intentionally empty and have no internal deps.
    if module.endswith("/__init__.py") or module == "mtgdb/__init__.py":
        return False
    return False


UI_CLUSTER_EXEMPT = {"mtgdb/ui/app.py", "mtgdb/ui/__init__.py"}


def _ui_clusters(agent_text):
    """Parse the UI feature-cluster table so the contract stays authoritative.

    Returns {module path: allowed logic prefixes}. Reading the table instead of
    restating it here means a cluster edit cannot leave the gate enforcing a
    stale map.
    """
    section = re.search(
        r"### UI feature clusters\n(.*?)(?=\n- \*\*LAYER-001)",
        agent_text, re.S)
    if section is None:
        return {}
    clusters = {}
    for line in section.group(1).splitlines():
        if not line.startswith("|") or line.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("Cluster",):
            continue
        modules = re.findall(r"`([^`]+\.py)`", cells[1])
        prefixes = tuple(re.findall(r"`([^`]+)`", cells[2]))
        for module in modules:
            clusters[f"mtgdb/ui/{module}"] = prefixes
    return clusters


def _ui_cluster_violations(agent_text, ui_directory):
    """LAYER-006 offenders, plus any UI module the table forgot to place."""
    clusters = _ui_clusters(agent_text)
    if not clusters:
        return ["the UI feature-cluster table is missing or unparsable"]
    offenders = []
    for path in sorted(ui_directory.glob("*.py")):
        name = f"mtgdb/ui/{path.name}"
        if name in UI_CLUSTER_EXEMPT:
            continue
        if name not in clusters:
            offenders.append(f"{name} (not placed in any cluster)")
            continue
        allowed = clusters[name] + ("mtgdb.core.",)
        imported = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for module in sorted(imported):
            if not module.startswith("mtgdb.") or module.startswith("mtgdb.ui"):
                continue
            if not any(module == prefix.rstrip(".") or module.startswith(prefix)
                       for prefix in allowed):
                offenders.append(f"{name} -> {module}")
    return offenders


SEARCH_UI_MODULES = (
    "mtgdb/ui/search.py", "mtgdb/ui/search_printings.py",
    "mtgdb/ui/search_checklist.py", "mtgdb/ui/results.py",
    "mtgdb/ui/table_filters.py", "mtgdb/ui/tables.py",
)


def _search_ui_carddb_importers(sources):
    """Return search-UI modules that import CardDB instead of the repository.

    The import matrix lets `mtgdb/ui/**` import any owning mtgdb API, but the
    search-UI row overrides that: these modules MUST reach card data through
    `search/repository.py` rather than `mtgdb.database.db` directly. Checked
    on the AST so every import form counts, including
    `from mtgdb.database import db`.

    A name listed here that no longer exists is reported too, so a renamed
    module cannot silently drop out of the row it is meant to be covered by.
    """
    offenders = []
    for name in SEARCH_UI_MODULES:
        source = sources.get(name)
        if source is None:
            offenders.append(f"{name} (listed in the matrix row but missing)")
            continue
        for node in ast.walk(ast.parse(source)):
            imported = None
            if isinstance(node, ast.Import):
                if any(alias.name == "mtgdb.database.db"
                       for alias in node.names):
                    imported = name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "mtgdb.database.db":
                    imported = name
                elif (module == "mtgdb.database"
                      and any(alias.name == "db" for alias in node.names)):
                    imported = name
            if imported is not None:
                offenders.append(imported)
                break
    return offenders


def _unmanaged_temp_directories():
    """Return test files whose tempfile.mkdtemp() has no registered cleanup.

    The suite runs on every local build, every cloud build, and every source
    release, so an unmanaged directory leaks a database per run. A managed call
    either uses tempfile.TemporaryDirectory or registers rmtree cleanup.
    """
    offenders = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "tempfile.mkdtemp(" not in source:
            continue
        managed = (
            "atexit.register(shutil.rmtree" in source
            or "shutil.rmtree(" in source
            or "TemporaryDirectory(" in source)
        if not managed:
            offenders.append(path.name)
    return offenders


def main():
    members = P.source_members()
    unauthorized = P.unauthorized_documents(members)
    agent_path = ROOT / "AGENTS.md"
    agent_text = agent_path.read_text(encoding="utf-8") if agent_path.exists() else ""
    rule_blocks = _rule_blocks(agent_text)
    rule_matches = [RULE_START.match(block) for block in rule_blocks]
    rule_ids = [match.group(1) for match in rule_matches if match]
    rule_families = {rule_id.split("-", 1)[0] for rule_id in rule_ids}
    verification_classes = {
        match.group(1)
        for block in rule_blocks
        for match in [VERIFICATION.search(block)]
        if match
    }
    ambiguous_rules = [
        block.splitlines()[0]
        for block in rule_blocks
        if any(phrase in block.casefold() for phrase in AMBIGUOUS_PHRASES)
    ]
    lowercase_normative_rules = [
        block.splitlines()[0]
        for block in rule_blocks
        if re.search(r"\b(must|must not|should|should not|may)\b", block)
    ]

    build_script = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
    spec_source = (ROOT / "MTGDeckBuilder.spec").read_text(encoding="utf-8")
    smoke_source = (
        ROOT / "windows_tests" / "smoke_packaged_windows.py").read_text(
            encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(
        encoding="utf-8")
    geometry_test = (
        ROOT / "windows_tests" / "test_ui_geometry_windows.py"
    ).read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    architecture_sources = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in (
            "mtgdb/ui/app.py", "mtgdb/ui/deck_files.py", "mtgdb/ui/mana.py",
            "mtgdb/ui/set_filters.py", "mtgdb/ui/autocomplete.py",
            "mtgdb/search/models.py", "mtgdb/search/repository.py",
            "mtgdb/search/controller.py", "mtgdb/search/results.py",
            "mtgdb/search/catalogs.py", "mtgdb/ui/search.py",
            "mtgdb/ui/search_printings.py", "mtgdb/ui/search_checklist.py",
            "mtgdb/ui/table_filters.py", "mtgdb/ui/results.py", "mtgdb/ui/tables.py",
            "mtgdb/preferences/repository.py",
            "mtgdb/images/service.py", "mtgdb/ui/card_detail.py", "mtgdb/ui/comparison.py",
            "mtgdb/comparison/models.py", "mtgdb/ui/comparison_controls.py",
            "mtgdb/deck/model.py", "mtgdb/deck/io.py", "mtgdb/deck/analysis.py",
            "mtgdb/deck/legality.py", "mtgdb/ui/deck.py", "mtgdb/ui/deck_stats.py",
            "mtgdb/deck/sessions.py", "mtgdb/workspace/repository.py", "mtgdb/ui/workspace.py",
            "mtgdb/database/sync.py", "mtgdb/ui/database_sync.py", "mtgdb/database/db.py",
            "mtgdb/database/authorities.py", "mtgdb/database/constants.py", "mtgdb/database/schema.py",
            "mtgdb/database/semantics.py",
            "mtgdb/database/bulk_import.py", "mtgdb/database/queries.py",
            "mtgdb/database/search_queries.py", "mtgdb/database/taxonomy.py",
            "mtgdb/printing/service.py", "mtgdb/printing/renderer.py",
            "mtgdb/ui/printing.py",
        )
    }
    architecture_imports = {
        name: _import_roots(source)
        for name, source in architecture_sources.items()
    }
    gui_tree = ast.parse(architecture_sources["mtgdb/ui/app.py"])
    gui_class = next(
        node for node in gui_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DeckBuilderApp")
    gui_methods = {
        node.name for node in gui_class.body if isinstance(node, ast.FunctionDef)
    }
    comparison_controls_tree = ast.parse(
        architecture_sources["mtgdb/ui/comparison_controls.py"])
    comparison_controls_class = next(
        node for node in comparison_controls_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ComparisonFeatureMixin")
    comparison_control_methods = {
        node.name for node in comparison_controls_class.body
        if isinstance(node, ast.FunctionDef)
    }
    production_sources = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in (ROOT / "mtgdb").rglob("*.py")
    }
    production_import_paths = {
        name: _import_paths(source) for name, source in production_sources.items()
    }
    production_files = set(production_sources)

    repository_match = re.search(
        r"## Repository map\n(.*?)(?=\n## Feature map)",
        agent_text, re.S)
    repository_section = repository_match.group(1) if repository_match else ""
    repository_code_match = re.search(
        r"```\n(.*?)\n```", repository_section, re.S)
    repository_paths = set()
    if repository_code_match:
        directory_stack = []
        for raw_line in repository_code_match.group(1).splitlines():
            content = raw_line.split("#", 1)[0].rstrip()
            if not content.strip():
                continue
            indent = len(content) - len(content.lstrip(" "))
            token = content.strip()
            if token.endswith("/"):
                while directory_stack and directory_stack[-1][0] >= indent:
                    directory_stack.pop()
                parent = directory_stack[-1][1] if directory_stack else ""
                path = f"{parent}/{token[:-1]}" if parent else token[:-1]
                directory_stack.append((indent, path))
                continue
            while directory_stack and directory_stack[-1][0] >= indent:
                directory_stack.pop()
            parent = directory_stack[-1][1] if directory_stack else ""
            path = f"{parent}/{token}" if parent else token
            repository_paths.add(path)

    ownership_match = re.search(
        r"## Module ownership\n(.*?)(?=\n## Module map)",
        agent_text, re.S)
    owned_modules = set(re.findall(
        r"^\| `([^`]+\.py)` \|", ownership_match.group(1), re.M
    )) if ownership_match else set()
    expected_owned_modules = {
        name for name in production_files if not name.endswith("/__init__.py")
    }
    feature_match = re.search(
        r"## Feature map\n(.*?)(?=\n\n## Module ownership)", agent_text, re.S)
    feature_references = set(re.findall(
        r"`([^`]+(?:\.py|/\*))`", feature_match.group(1)
    )) if feature_match else set()

    module_map_match = re.search(
        r"## Module map\n(.*?)(?=\n## Import allow/deny matrix)",
        agent_text, re.S)
    module_map_rows = []
    if module_map_match:
        module_map_rows = re.findall(
            r"^\| `([^`]+\.py)` \| (.*?) \| (.*?) \| (.*?) \|$",
            module_map_match.group(1), re.M)
    module_map_modules = {row[0] for row in module_map_rows}
    module_map_duplicate_free = (
        len(module_map_rows) == len(module_map_modules))
    module_map_examples_complete = all(
        row[1].count("<br>") >= 1 and all(
            part.strip() for part in row[1].split("<br>"))
        for row in module_map_rows)
    module_map_boundaries_complete = all(row[3].strip() for row in module_map_rows)
    module_map_collaborators = {
        reference
        for _module, _examples, inspect_with, _boundary in module_map_rows
        for reference in re.findall(r"`(mtgdb/[^`*]+\.py)`", inspect_with)
    }

    def feature_reference_exists(reference):
        if reference.endswith("/*"):
            directory = ROOT / reference[:-2]
            return directory.is_dir() and any(directory.iterdir())
        return (ROOT / reference).is_file()
    package_discovery = (
        pyproject.get("tool", {}).get("setuptools", {})
        .get("packages", {}).get("find", {}).get("include", []))
    packaged_ok = package_discovery == ["mtgdb*"]

    linux_commands = P.release_gate_commands(platform="linux")
    windows_commands = P.release_gate_commands(platform="win32")
    linux_command_targets = {Path(command[-1]).as_posix() for _, command in linux_commands}
    windows_command_targets = {Path(command[-1]).as_posix() for _, command in windows_commands}
    discovered_tests = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").glob("test_*.py")
    }
    required_windows = {path.as_posix() for path in P.WINDOWS_SOURCE_GATES}

    attempted = []

    def failing_runner(command, cwd):
        attempted.append((command, cwd))
        return SimpleNamespace(returncode=23)

    automatic_failure_blocks = _raises_runtime_error(
        lambda: P.run_release_gates(platform="linux", runner=failing_runner))

    prohibited_document_blocks = _raises_runtime_error(
        lambda: P.validate_members([*members, Path("UNAUTHORIZED.txt")]))

    with tempfile.TemporaryDirectory() as temporary_directory:
        output = Path(temporary_directory) / "release.zip"
        original = b"previous verified release"
        output.write_bytes(original)
        with mock.patch.object(
                P, "run_release_gates",
                side_effect=RuntimeError("deliberate gate failure")):
            archive_failure_blocks = _raises_runtime_error(
                lambda: P.build_archive(output))
        failed_build_preserves_output = output.read_bytes() == original

    local_compile = build_script.find(
        "python -m compileall -q mtgdb tests windows_tests package_release.py")
    local_cleanup = build_script.find('rmdir /s /q "mtg_deck_builder.egg-info"')
    local_tests = build_script.find("tests\\test_*.py")
    local_single = build_script.find("test_single_instance_windows.py")
    local_geometry = build_script.find("test_ui_geometry_windows.py")
    local_startup = build_script.find("test_app_startup_windows.py")
    local_build = build_script.find("pyinstaller MTGDeckBuilder.spec")
    local_smoke = build_script.find("smoke_packaged_windows.py")

    cloud_compile = workflow.find(
        "python -m compileall -q mtgdb tests windows_tests package_release.py")
    cloud_cleanup = workflow.find(
        'Remove-Item -Recurse -Force "mtg_deck_builder.egg-info"')
    cloud_tests = workflow.find('Filter "test_*.py"')
    cloud_single = workflow.find("test_single_instance_windows.py")
    cloud_geometry = workflow.find("test_ui_geometry_windows.py")
    cloud_startup = workflow.find("test_app_startup_windows.py")
    cloud_build = workflow.find("pyinstaller MTGDeckBuilder.spec")
    cloud_smoke = workflow.find("smoke_packaged_windows.py")
    cloud_upload = workflow.find("actions/upload-artifact")

    table_columns = _literal_assignment(
        production_sources["mtgdb/ui/tables.py"], "TABLE_COLUMNS")
    search_result_columns = set(_literal_assignment(
        production_sources["mtgdb/search/results.py"],
        "SEARCH_RESULT_FIELDS"))
    result_column_fields = {
        "cost": {"cmc"},
        "name": {"name"},
        "type": {"type_line"},
        "set": {"set_name", "set_code"},
        "collector": {"collector_number"},
        "year": {"released_at"},
        "ability": {"keywords"},
        "rarity": {"rarity"},
        "cmc": {"cmc"},
        "power": {"power"},
        "toughness": {"toughness"},
        "colors": {"colors"},
        "rules": {"oracle_text"},
    }
    configured_result_fields = set().union(*(
        result_column_fields[key]
        for key, spec in table_columns.items()
        if "results" in spec["views"] and key != "qty"
    ))

    checks = {
        "AGENTS.md exists": agent_path.is_file(),
        "AGENTS.md is the only project documentation": not unauthorized,
        "every bullet is an identified normative rule": (
            bool(rule_blocks) and all(rule_matches)),
        "rule identifiers are unique": len(rule_ids) == len(set(rule_ids)),
        "all required rule families are present": (
            REQUIRED_RULE_FAMILIES <= rule_families),
        "every rule has one verification class": all(
            len(VERIFICATION.findall(block)) == 1 for block in rule_blocks),
        "all verification classes are represented": (
            REQUIRED_VERIFICATION_CLASSES <= verification_classes),
        "rules avoid known ambiguous phrases": not ambiguous_rules,
        "rules avoid lowercase normative language": not lowercase_normative_rules,
        "single-document authority is explicit": (
            re.search(
                r"one and only project\s+documentation file", agent_text)
            is not None),
        "changelog use is explicitly prohibited": (
            "MUST NOT:** Turn this file into a changelog" in agent_text),
        "requirements.txt is absent": not (ROOT / "requirements.txt").exists(),
        "search core is Tk-free": all(
            "tkinter" not in architecture_imports[name]
            for name in (
                "mtgdb/search/models.py", "mtgdb/search/repository.py",
                "mtgdb/search/controller.py", "mtgdb/search/results.py",
                "mtgdb/search/catalogs.py")),
        "every UI module stays inside its feature cluster": (
            _ui_cluster_violations(agent_text, ROOT / "mtgdb" / "ui") == []),
        "search UI reaches card data only through the search repository": (
            _search_ui_carddb_importers(production_sources) == []),
        "search UI is SQLite-free": all(
            "sqlite3" not in architecture_imports[name]
            for name in (
                "mtgdb/ui/search.py", "mtgdb/ui/search_printings.py",
                "mtgdb/ui/search_checklist.py", "mtgdb/ui/table_filters.py",
                "mtgdb/ui/results.py")),
        "search UI has no direct CardDB access": (
            "self.db." not in architecture_sources["mtgdb/ui/search.py"]),
        "DeckBuilderApp delegates extracted search ownership": (
            {"SearchFeatureMixin", "TableFilterMixin", "SearchResultsMixin"}
            <= {base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_do_search", "_render_results", "_show_table_filter",
                "_open_search_multi_picker",
            } & gui_methods),
        "search printing component exclusively owns printing selector state": (
            "class SearchPrintingFilter"
            in architecture_sources["mtgdb/ui/search_printings.py"]
            and "self._search_printings = SearchPrintingFilter(self, parent, row=row)"
            in architecture_sources["mtgdb/ui/search.py"]
            and all(
                marker not in architecture_sources["mtgdb/ui/search.py"]
                for marker in (
                    "self.set_type_vars", "self._set_vars",
                    "self._eligible_sets", "self._present_set_types",
                ))),
        "deck alternate-printing and batch search use Search feature contracts": (
            "_apply_cards_search_preset(cards)"
            in architecture_sources["mtgdb/ui/deck.py"]
            and all(
                marker not in architecture_sources["mtgdb/ui/deck.py"]
                for marker in (
                    "self.q_name", "self.q_rules", "self.card_type_vars",
                    "self.property_vars", "self.content_vars",
                    "self._selected_subtypes", "self._search_printings",
                ))),
        "Search owns its workspace control-state translation": (
            "def _capture_search_workspace_state("
            in architecture_sources["mtgdb/ui/search.py"]
            and "def _restore_search_workspace_state("
            in architecture_sources["mtgdb/ui/search.py"]
            and all(
                marker not in architecture_sources["mtgdb/ui/workspace.py"]
                for marker in (
                    "self.q_name", "self.q_rules", "self.card_type_vars",
                    "self._selected_subtypes", "self._search_printings",
                ))),
        "Results owns virtualization and deferred workspace selection": (
            "RESULT_LIVE_ROW_LIMIT = 128"
            in architecture_sources["mtgdb/ui/results.py"]
            and "class SearchResultStore"
            in architecture_sources["mtgdb/search/results.py"]
            and "self._restore_pending_result_selection()"
            in architecture_sources["mtgdb/ui/results.py"]
            and "def _restore_pending_result_selection("
            in architecture_sources["mtgdb/ui/search.py"]
            and "selected_result_id"
            not in architecture_sources["mtgdb/ui/workspace.py"]),
        "autocomplete entry owns the shared popup implementation": (
            "class _AutocompletePopupBehavior"
            in architecture_sources["mtgdb/ui/autocomplete.py"]
            and "class AutocompleteEntry(_AutocompletePopupBehavior, ClassicEntry)"
            in architecture_sources["mtgdb/ui/autocomplete.py"]
            and "AutocompleteCombobox" not in architecture_sources["mtgdb/ui/autocomplete.py"]),
        "superseded private compatibility fossils stay removed": all(
            marker not in "\n".join(production_sources.values())
            for marker in (
                "def _center_print_popup(", "def _center_sync_popup(",
                "def _schedule_titlebar_refreshes(", "_result_render_generation",
                "_set_row_widgets", "_set_visible_row_widgets",
                "_set_render_generation", "def _workspace_payload(",
                "def _restore_workspace_session(", "def _comparison_card_id(",
                "def _comparison_source_boards(", "def _needs_rotation(",
                "def _sort_table(", "def _available_columns(",
                "def _apply_card_search_preset(", "AutocompleteCombobox",
                "SINGLETON_FORMATS", "_PRIMARY_BUTTON_ROLES",
            )),
        "PrintingFilter centrally owns catalog control enablement": (
            "def _set_catalog_controls_enabled("
            in architecture_sources["mtgdb/ui/set_filters.py"]
            and "def _set_catalog_controls_enabled("
            not in architecture_sources["mtgdb/ui/search_printings.py"]
            and "def _set_catalog_controls_enabled("
            not in architecture_sources["mtgdb/ui/deck_files.py"]),
        "preference repository is Tk-free": (
            "tkinter" not in architecture_imports["mtgdb/preferences/repository.py"]),
        "table UI is database-free": (
            "sqlite3" not in architecture_imports["mtgdb/ui/tables.py"]
            and "scryfall_db" not in architecture_imports["mtgdb/ui/tables.py"]
            and "self.db." not in architecture_sources["mtgdb/ui/tables.py"]),
        "DeckBuilderApp delegates shared table ownership": (
            "TableInfrastructureMixin" in {
                base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_load_ui_preferences", "_save_ui_preferences",
                "_table_value", "_table_sort_key", "_update_table_headings",
                "_setup_table_columns", "_apply_table_columns",
                "_toggle_column_popup", "_show_column_popup",
                "_hide_column_popup", "_create_column_popup",
                "_columns_changed", "_reset_columns", "_display_column_at",
                "_column_boundaries", "_drop_index_for_x",
                "_bind_column_drag",
            } & gui_methods
            and "_ui_prefs_path" not in architecture_sources["mtgdb/ui/app.py"]),
        "interactive image service is Tk-free": (
            "tkinter" not in architecture_imports["mtgdb/images/service.py"]),
        "main preview image scheduling is latest-wins and prioritized": (
            "queue.PriorityQueue()"
            in architecture_sources["mtgdb/images/service.py"]
            and "_channel_requests"
            in architecture_sources["mtgdb/images/service.py"]
            and "_request_consumers"
            in architecture_sources["mtgdb/images/service.py"]
            and 'channel="main-preview"'
            in architecture_sources["mtgdb/ui/card_detail.py"]),
        "preview ready deferral cannot occupy the poll scheduling slot": (
            "self._image_ready_after = None"
            in architecture_sources["mtgdb/ui/card_detail.py"]
            and "def _defer_image_ready("
            in architecture_sources["mtgdb/ui/card_detail.py"]
            and '"_image_ready_after"'
            in architecture_sources["mtgdb/ui/app.py"]),
        "card image views own no image transport or decode": all(
            forbidden not in architecture_sources[name]
            for name in ("mtgdb/ui/card_detail.py", "mtgdb/ui/comparison.py")
            for forbidden in (
                "import mtgdb.core.net", "import mtgdb.core.cache_names", "threading.Thread",
                "Image.open(", "_load_image_pil")),
        "DeckBuilderApp delegates card-detail ownership": (
            "CardDetailMixin" in {
                base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_build_card_pane", "_show_card",
                "_rotate_card_preview", "_open_card_zoom",
                "_sync_preview_zoom", "_set_card_preview_actions",
                "_card_summary_text", "_legal_summary",
                "_start_image_event_pump", "_poll_image_events",
                "_cancel_image_ready_retry", "_defer_image_ready",
                "_image_ready", "_image_failed",
            } & gui_methods
            and "self.card_image_service.shutdown()"
            in architecture_sources["mtgdb/ui/app.py"]),
        "comparison model is Tk SQLite UI and network free": (
            not ({"tkinter", "sqlite3", "net"}
                 & architecture_imports["mtgdb/comparison/models.py"])
            and not any(
                root.startswith("ui_")
                for root in architecture_imports["mtgdb/comparison/models.py"])),
        "comparison model owns ordered exact-printing state": (
            all(
                marker in architecture_sources["mtgdb/comparison/models.py"]
                for marker in (
                    "class ComparisonCollection", "class ComparisonMutation",
                    "MIN_COMPARISON_CARDS = 2",
                    "MAX_COMPARISON_CARDS = 7",
                    "def comparison_card_id(", "def add(", "def remove(",
                    "def clear("))),
        "comparison model is collection-only without display logic": (
            "def comparison_json_list(" in architecture_sources[
                "mtgdb/comparison/models.py"]
            and "class ComparisonCollection" in architecture_sources[
                "mtgdb/comparison/models.py"]
            and all(
                marker not in architecture_sources["mtgdb/comparison/models.py"]
                for marker in (
                    "def comparison_row_state(", "def format_mana_value(",
                    "def printing_short(", "def visible_symbol_text(",
                    "def color_tokens(", "def power_toughness(",
                    "def legality_text(", "def join_face_field(",
                    "def type_parts(", "def comparison_key("))),
        "comparison controls own application coordination": (
            {
                "_build_comparison_bar", "_layout_comparison_actions",
                "_selected_comparison_candidates",
                "_add_selected_to_comparison",
                "_add_to_comparison", "_add_cards_to_comparison",
                "_add_selected_results_to_comparison", "_remove_from_comparison",
                "_clear_comparison", "_comparison_changed",
                "_update_comparison_bar", "_show_comparison_notice",
                "_open_comparison_window", "_show_result_context_menu",
                "_comparison_source_record", "_comparison_source_info",
                "_remove_comparison_source_from_board",
                "_comparison_card", "_add_comparison_card_to_board",
                "_open_card_grid_window", "_update_card_grid_window",
                "_close_card_grid_window",
            } <= comparison_control_methods),
        "DeckBuilderApp delegates comparison ownership": (
            "ComparisonFeatureMixin" in {
                base.id for base in gui_class.bases
                if isinstance(base, ast.Name)}
            and "ComparisonCollection()"
            in architecture_sources["mtgdb/ui/app.py"]
            and not {
                "_build_comparison_bar", "_layout_comparison_actions",
                "_selected_comparison_candidates",
                "_add_selected_to_comparison",
                "_add_to_comparison", "_add_cards_to_comparison",
                "_add_selected_results_to_comparison", "_remove_from_comparison",
                "_clear_comparison", "_comparison_changed",
                "_update_comparison_bar", "_show_comparison_notice",
                "_open_comparison_window", "_show_result_context_menu",
                "_comparison_source_record", "_comparison_source_info",
                "_remove_comparison_source_from_board",
                "_comparison_card", "_add_comparison_card_to_board",
                "_open_card_grid_window", "_update_card_grid_window",
                "_close_card_grid_window",
            } & gui_methods
            and all(
                "_comparison_cards" not in source
                for source in production_sources.values())),
        "comparison view reads but does not mutate collection state": (
            "self.app.comparison.cards()"
            in architecture_sources["mtgdb/ui/comparison.py"]
            and all(
                marker not in architecture_sources["mtgdb/ui/comparison.py"]
                for marker in (
                    "self.app.comparison.add(",
                    "self.app.comparison.remove(",
                    "self.app.comparison.clear("))),
        "comparison view supports source-board actions and read-only grids": (
            "self.app._comparison_source_info("
            in architecture_sources["mtgdb/ui/comparison.py"]
            and "self.app._remove_comparison_source_from_board("
            in architecture_sources["mtgdb/ui/comparison.py"]
            and "def show_cards("
            in architecture_sources["mtgdb/ui/comparison.py"]
            and "self.app._add_comparison_card_to_board("
            in architecture_sources["mtgdb/ui/comparison.py"]
            and "def _open_card_grid_window("
            in architecture_sources["mtgdb/ui/comparison_controls.py"]),
        "comparison view delegates normalization": all(
            marker not in architecture_sources["mtgdb/ui/comparison.py"]
            for marker in (
                "def _comparison_json_list(",
                "def _comparison_json_dict(", "def _comparison_key(",
                "def _format_mana_value(", "def _printing_short(",
                "def _visible_symbol_text(", "def _color_tokens(",
                "def _power_toughness(", "def _legality_text(",
                "def _join_face_field(")),
        "app composes comparison without dead view imports": (
            "from mtgdb.comparison.models import ComparisonCollection"
            in architecture_sources["mtgdb/ui/app.py"]
            and "from mtgdb.ui.comparison_controls import ComparisonFeatureMixin"
            in architecture_sources["mtgdb/ui/app.py"]
            and "CardComparisonWindow" not in architecture_sources["mtgdb/ui/app.py"]
            and "MAX_COMPARISON_CARDS" not in architecture_sources["mtgdb/ui/app.py"]
            and "MIN_COMPARISON_CARDS" not in architecture_sources["mtgdb/ui/app.py"]
            and "_comparison_json_dict" not in architecture_sources[
                "mtgdb/ui/app.py"]),
        "comparison modules exist and package is discovered": (
            {"mtgdb/comparison/models.py", "mtgdb/ui/comparison_controls.py"}
            <= production_files and packaged_ok),
        "deck domain is Tk, SQLite, and UI free": all(
            not ({"tkinter", "sqlite3"} & architecture_imports[name])
            and not any(root.startswith("ui_") for root in architecture_imports[name])
            for name in (
                "mtgdb/deck/model.py", "mtgdb/deck/io.py", "mtgdb/deck/analysis.py",
                "mtgdb/deck/legality.py")),
        "deck model owns no parser, analysis, or legality imports": (
            not ({"re", "json", "math", "random"}
                 & architecture_imports["mtgdb/deck/model.py"])),
        "production code imports responsible deck modules": (
            "from mtgdb.deck.model import Deck" in architecture_sources["mtgdb/ui/app.py"]
            and "from mtgdb.deck.io import deck_from_text, save_deck_text"
            in architecture_sources["mtgdb/ui/deck_files.py"]
            and "from mtgdb.deck.model import Deck"
            in architecture_sources["mtgdb/ui/deck.py"]
            and "from mtgdb.deck.analysis import"
            in architecture_sources["mtgdb/ui/deck_stats.py"]
            and "from mtgdb.deck.legality import legality_problems"
            in architecture_sources["mtgdb/ui/deck_stats.py"]
            and "from mtgdb.deck.io import" not in architecture_sources["mtgdb/ui/app.py"]
            and "from mtgdb.deck.analysis import" not in architecture_sources["mtgdb/ui/app.py"]
            and "from mtgdb.deck.legality import" not in architecture_sources["mtgdb/ui/app.py"]),
        "DeckBuilderApp delegates deck-editor ownership": (
            {"DeckEditorMixin", "DeckFileWorkflowMixin", "DeckStatsMixin"}
            <= {base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_build_deck_pane", "_render_deck_tabs", "_deck_qty",
                "_deck_remove", "_deck_move", "_refresh_deck_views",
                "_build_stats_panel", "_refresh_stats", "_draw_curve",
                "_draw_hand", "_view_hand", "_render_legality",
            } & gui_methods),
        "sample hand uses shared read-only card grid": (
            'text="View hand"' in architecture_sources["mtgdb/ui/deck_stats.py"]
            and "self._open_card_grid_window("
            in architecture_sources["mtgdb/ui/deck_stats.py"]
            and 'self._close_card_grid_window("sample_hand")'
            in architecture_sources["mtgdb/ui/deck_stats.py"]),
        "deck UI ownership excludes persistence and SQL": (
            all(
                forbidden not in architecture_sources[name]
                for name in ("mtgdb/ui/deck.py", "mtgdb/ui/deck_stats.py")
                for forbidden in (
                    "import sqlite3", "sqlite3.", "deck_to_text(",
                    "deck_from_text(", "json.dump", "json.loads",
                    "print_template.",
                ))),
        "workspace core is Tk, SQLite, and UI free": all(
            not ({"tkinter", "sqlite3"} & architecture_imports[name])
            and not any(root.startswith("ui_")
                        for root in architecture_imports[name])
            for name in ("mtgdb/deck/sessions.py", "mtgdb/workspace/repository.py")),
        "DeckBuilderApp delegates workspace ownership": (
            "WorkspaceMixin" in {
                base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_capture_search_workspace_state",
                "_restore_search_workspace_state", "_workspace_snapshot",
                "_save_workspace_session", "_schedule_workspace_autosave",
                "_restore_workspace_session_async", "_restore_workspace_geometry",
            } & gui_methods),
        "workspace UI delegates Search state translation": (
            "self._capture_search_workspace_state()"
            in architecture_sources["mtgdb/ui/workspace.py"]
            and "self._restore_search_workspace_state("
            in architecture_sources["mtgdb/ui/workspace.py"]),
        "workspace UI performs no direct persistence": (
            all(
                forbidden not in architecture_sources[name]
                for name in ("mtgdb/ui/app.py", "mtgdb/ui/deck.py", "mtgdb/ui/workspace.py")
                for forbidden in (
                    '"session.json"', '"workspace_recovery"',
                    "_write_json_atomic", "_read_workspace_candidate",
                    "_write_recovery_snapshot", "_load_workspace_payload",
                ))),
        "typed session manager replaces legacy dictionaries": (
            "DeckSessionManager" in architecture_sources["mtgdb/ui/app.py"]
            and "DeckSession" in architecture_sources["mtgdb/ui/app.py"]
            and all(
                legacy not in source
                for source in production_sources.values()
                for legacy in ("_deck_sessions", "_active_deck_session"))),
        "database synchronization core is Tk-free": (
            "tkinter" not in architecture_imports["mtgdb/database/sync.py"]),
        "CardDB owns no network synchronization orchestration": all(
            marker not in architecture_sources["mtgdb/database/db.py"]
            for marker in ("import mtgdb.core.net", "def sync(", "def refresh_catalogs(")),
        "database sync UI owns no transport or worker lifecycle": all(
            marker not in architecture_sources["mtgdb/ui/database_sync.py"]
            for marker in ("import mtgdb.core.net", "threading.Thread", "self.db.sync(")),
        "DeckBuilderApp delegates database synchronization UI": (
            "DatabaseSyncMixin" in {
                base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and not {
                "_initialize_database_sync", "_maybe_auto_sync", "_sync_db",
                "_show_sync_popup", "_close_sync_popup", "_poll_sync_events",
                "_apply_sync_progress", "_sync_done", "_sync_error",
                "_shutdown_database_sync",
            } & gui_methods),
        "database sync modules exist and package is discovered": (
            {"mtgdb/database/sync.py", "mtgdb/ui/database_sync.py"}
            <= production_files and packaged_ok),
        "CardDB is a small composed database facade": (
            "class CardDB(CardQueryMixin, CardSearchQueryMixin, CardTaxonomyMixin):"
            in architecture_sources["mtgdb/database/db.py"]
            and all(
                marker not in architecture_sources["mtgdb/database/db.py"]
                for marker in (
                    "CREATE TABLE", "sqlite3.connect", "def search(",
                    "def get_card(", "def subtype_catalog(",
                    "def iter_card_objects(", "def _extract_row(",
                ))
            and len(architecture_sources["mtgdb/database/db.py"].splitlines()) < 150),
        "database schema exclusively owns schema and connections": (
            "CREATE TABLE IF NOT EXISTS cards"
            in architecture_sources["mtgdb/database/schema.py"]
            and "CREATE INDEX IF NOT EXISTS"
            in architecture_sources["mtgdb/database/schema.py"]
            and "sqlite3.connect"
            in architecture_sources["mtgdb/database/schema.py"]
            and all(
                "sqlite3.connect" not in architecture_sources[name]
                for name in (
                    "mtgdb/database/db.py", "mtgdb/database/bulk_import.py",
                    "mtgdb/database/queries.py", "mtgdb/database/search_queries.py",
                    "mtgdb/database/taxonomy.py"))),
        "database import owns projection parsing and transactions": (
            "def iter_card_objects("
            in architecture_sources["mtgdb/database/bulk_import.py"]
            and "def _extract_row("
            in architecture_sources["mtgdb/database/bulk_import.py"]
            and "BEGIN IMMEDIATE"
            in architecture_sources["mtgdb/database/bulk_import.py"]
            and "minimum_count"
            in architecture_sources["mtgdb/database/bulk_import.py"]
            and "def search("
            not in architecture_sources["mtgdb/database/bulk_import.py"]),
        "database semantics owns shared type interpretation": (
            "def _complete_type_line("
            in architecture_sources["mtgdb/database/semantics.py"]
            and "def _card_has_type("
            in architecture_sources["mtgdb/database/semantics.py"]
            and "def _card_has_subtype("
            in architecture_sources["mtgdb/database/semantics.py"]
            and not ({"sqlite3", "tkinter", "net"}
                     & architecture_imports["mtgdb/database/semantics.py"])),
        "database lookup search and taxonomy responsibilities are separated": (
            all(
                marker in architecture_sources["mtgdb/database/queries.py"]
                for marker in (
                    "def get_card(", "def get_by_name(",
                    "def name_suggestions(",
                ))
            and "def search("
            not in architecture_sources["mtgdb/database/queries.py"]
            and all(
                marker in architecture_sources["mtgdb/database/search_queries.py"]
                for marker in (
                    "class SearchQueryBuilder", "class CardSearchQueryMixin",
                    "def search(",
                ))
            and all(
                marker in architecture_sources["mtgdb/database/taxonomy.py"]
                for marker in (
                    "def sets(", "def formats(", "def subtype_catalog(",
                    "def keyword_catalog(",
                ))
            and "def search("
            not in architecture_sources["mtgdb/database/taxonomy.py"]),
        "database internals have no facade cycle or UI network imports": all(
            "scryfall_db" not in architecture_imports[name]
            and "tkinter" not in architecture_imports[name]
            and "net" not in architecture_imports[name]
            for name in (
                "mtgdb/database/authorities.py", "mtgdb/database/constants.py", "mtgdb/database/schema.py",
            "mtgdb/database/semantics.py",
                "mtgdb/database/bulk_import.py", "mtgdb/database/queries.py",
                "mtgdb/database/search_queries.py", "mtgdb/database/taxonomy.py")),
        "database sync imports responsible internal owners": (
            "from mtgdb.database.authorities import SCRYFALL_CATALOGS"
            in architecture_sources["mtgdb/database/sync.py"]
            and "from mtgdb.database.bulk_import import iter_card_objects"
            in architecture_sources["mtgdb/database/sync.py"]
            and "RULES_SUPERTYPES_META_KEY"
            in architecture_sources["mtgdb/database/sync.py"]
            and "from mtgdb.database.db import"
            not in architecture_sources["mtgdb/database/sync.py"]),
        "upstream compatibility diagnostics stay out of normal UI": all(
            "upstream_compatibility" not in source and "compatibility:last_report" not in source
            for name, source in architecture_sources.items() if name.startswith("mtgdb/ui/")),
        "database internal modules exist and package is discovered": (
            {
                "mtgdb/database/bulk_import.py", "mtgdb/database/queries.py",
                "mtgdb/database/search_queries.py",
                "mtgdb/database/authorities.py", "mtgdb/database/constants.py", "mtgdb/database/schema.py",
            "mtgdb/database/semantics.py",
                "mtgdb/database/taxonomy.py",
            } <= production_files and packaged_ok),
        "print renderer exclusively owns physical PDF construction": (
            "def render_print_template("
            in architecture_sources["mtgdb/printing/renderer.py"]
            and "canvas.Canvas("
            in architecture_sources["mtgdb/printing/renderer.py"]
            and all(
                marker not in architecture_sources["mtgdb/printing/renderer.py"]
                for marker in (
                    "import mtgdb.core.net", "import mtgdb.core.cache_names", "threading",
                    "tkinter", "def expanded_cards("))),
        "print service owns snapshots images and worker lifecycle": (
            all(
                marker in architecture_sources["mtgdb/printing/service.py"]
                for marker in (
                    "class PrintJob", "class PrintTemplateService",
                    "class PrintController(GenerationalWorker)", "def ensure_png(",
                    "self._spawn(",
                    "from mtgdb.core.background_jobs import"))
            and "tkinter" not in architecture_imports["mtgdb/printing/service.py"]
            and "reportlab" not in architecture_imports["mtgdb/printing/service.py"]),
        "print UI owns presentation without transport or workers": (
            all(
                marker in architecture_sources["mtgdb/ui/printing.py"]
                for marker in (
                    "class PrintingMixin", "def _create_print_template(",
                    "def _show_print_popup(", "def _poll_print_events(",
                    "def _shutdown_printing("))
            and not ({"net", "cache_names", "threading", "queue", "reportlab"}
                     & architecture_imports["mtgdb/ui/printing.py"])),
        "DeckBuilderApp delegates the printing workflow": (
            "PrintingMixin" in {
                base.id for base in gui_class.bases if isinstance(base, ast.Name)}
            and "PrintController(PrintTemplateService())"
            in architecture_sources["mtgdb/ui/app.py"]
            and 'command=self._create_print_template'
            in architecture_sources["mtgdb/ui/app.py"]
            and "self._shutdown_printing()"
            in architecture_sources["mtgdb/ui/app.py"]
            and not {
                "_initialize_printing", "_show_print_popup",
                "_create_print_template", "_poll_print_events",
                "_update_print_progress", "_shutdown_printing",
            } & gui_methods
            and all(
                marker not in architecture_sources["mtgdb/ui/app.py"]
                for marker in (
                    "import mtgdb.printing.service", "threading.Thread",
                    "_print_event_queue"))),
        "print service owns the synchronous entry point": (
            "def create_print_template("
            in architecture_sources["mtgdb/printing/service.py"]
            and "PrintTemplateService().create("
            in architecture_sources["mtgdb/printing/service.py"]
            and "class PrintController(GenerationalWorker)"
            in architecture_sources["mtgdb/printing/service.py"]),
        "printing modules exist and package is discovered": (
            {
                "mtgdb/printing/renderer.py", "mtgdb/printing/service.py",
                "mtgdb/ui/printing.py",
            } <= production_files and packaged_ok),
        "AGENTS module ownership covers every production module": (
            owned_modules == expected_owned_modules),
        "Repository map names every production module": (
            expected_owned_modules <= repository_paths),
        "Repository map names both workflow owners": (
            {
                ".github/workflows/build-windows.yml",
                ".github/workflows/taxonomy-audit.yml",
            } <= repository_paths),
        "every Feature-map path resolves to current source": (
            bool(feature_references)
            and all(feature_reference_exists(ref) for ref in feature_references)),
        "Module map covers every owned production module exactly once": (
            module_map_duplicate_free
            and module_map_modules == expected_owned_modules),
        "every Module-map owner has multiple concrete routing examples": (
            bool(module_map_rows) and module_map_examples_complete),
        "every Module-map row has a placement boundary cue": (
            bool(module_map_rows) and module_map_boundaries_complete),
        "every exact Module-map collaborator path exists": all(
            (ROOT / reference).is_file()
            for reference in module_map_collaborators),
        "new DB-backed Search fields route through schema and bulk import": (
            "SRCH-017" in agent_text
            and "Data-shape routing:" in agent_text
            and "`mtgdb/database/schema.py`" in agent_text
            and "`mtgdb/database/bulk_import.py`" in agent_text),
        "configurable Results fields exist in the narrow Search projection": (
            configured_result_fields <= search_result_columns
            and "TBL-009" in agent_text),
        "every internal import follows the documented dependency direction": all(
            _internal_import_allowed(name, imported)
            for name, paths in production_import_paths.items()
            for imported in paths if imported.startswith("mtgdb.")),
        "only UI modules import tkinter": all(
            not _imports_prefix(paths, "tkinter") or name.startswith("mtgdb/ui/")
            for name, paths in production_import_paths.items()),
        "non-UI feature packages never import UI": all(
            not _imports_prefix(paths, "mtgdb.ui")
            for name, paths in production_import_paths.items()
            if name.startswith((
                "mtgdb/core/", "mtgdb/database/", "mtgdb/search/",
                "mtgdb/deck/", "mtgdb/workspace/", "mtgdb/images/",
                "mtgdb/printing/", "mtgdb/comparison/",
                "mtgdb/preferences/"))),
        "UI modules never import sqlite3": all(
            not _imports_prefix(paths, "sqlite3")
            for name, paths in production_import_paths.items()
            if name.startswith("mtgdb/ui/")),
        "database internals never depend on search or UI": all(
            not _imports_prefix(production_import_paths[name], "mtgdb.search")
            and not _imports_prefix(production_import_paths[name], "mtgdb.ui")
            for name in production_import_paths
            if name.startswith("mtgdb/database/")
            and not name.endswith("/__init__.py")),
        "workspace repository only reaches background jobs and deck state": (
            not _imports_prefix(
                production_import_paths["mtgdb/workspace/repository.py"],
                "mtgdb.ui")
            and not _imports_prefix(
                production_import_paths["mtgdb/workspace/repository.py"],
                "mtgdb.database")
            and {path for path in production_import_paths[
                    "mtgdb/workspace/repository.py"]
                 if path.startswith("mtgdb.")}
            <= {
                "mtgdb.core.background_jobs", "mtgdb.core.atomic_files",
                "mtgdb.deck.model", "mtgdb.deck.sessions"}),
        "print renderer has no Pillow network cache or Tk dependency": all(
            not _imports_prefix(
                production_import_paths["mtgdb/printing/renderer.py"], prefix)
            for prefix in ("PIL", "mtgdb.core.net", "mtgdb.core.cache_names",
                           "tkinter")),
        "shared cancellation helper is used by sync and printing": (
            "check_cancel(" in architecture_sources["mtgdb/database/sync.py"]
            and "check_cancel(" in architecture_sources["mtgdb/printing/service.py"]),
        "shared set-filter UI has one owner": (
            all(marker in architecture_sources["mtgdb/ui/set_filters.py"]
                for marker in (
                    "def set_type_label(",
                    "def _create_set_filter_shell(",
                    "def _build_set_type_controls("))
            and all(marker not in architecture_sources["mtgdb/ui/set_filters.py"]
                    for marker in ("SET_TYPE_DEFAULT_ON", "SET_TYPE_GROUPS", "Recommended"))
            and "from mtgdb.ui.search import"
                not in architecture_sources["mtgdb/ui/deck_files.py"]
            and "SetFilterSupportMixin"
                in architecture_sources["mtgdb/ui/app.py"]),
        "pyproject.toml is the dependency source": (
            bool(pyproject["project"]["dependencies"])
            and bool(pyproject["project"]["optional-dependencies"]["build"])),
        "Python minimum and build instructions agree on 3.11+": (
            pyproject["project"].get("requires-python") == ">=3.11"
            and "Python 3.11+" in build_script
            and "python-version: '3.14'" in workflow),
        "BLD-007 packaged build ships the program without runtime data": (
            "shutil.rmtree(data_dir" in smoke_source
            and "!dist/MTGDeckBuilder/data/" in workflow),
        "BLD-008 packaged smoke test performs no network refresh": (
            "def seed_current_database(" in smoke_source
            and "due_reason()" in smoke_source
            and "last_successful_sync_epoch" in smoke_source
            and '"*.download"' in smoke_source
            and "not downloaded" in smoke_source),
        "BLD-009 built exe carries pyproject version metadata, UPX off": (
            "VSVersionInfo" in spec_source
            and "version=version_info" in spec_source
            and 'pyproject.toml"' in spec_source
            and 'StringStruct("FileVersion", _version)' in spec_source
            and 'StringStruct("ProductVersion", _version)' in spec_source
            and "upx=True" not in spec_source
            and spec_source.count("upx=False") == 2),
        "REL-007 release membership has a ceiling, not only a floor": (
            bool(P.ALLOWED_ROOT_FILES)
            and bool(P.ALLOWED_TOP_LEVEL_DIRECTORIES)
            and P.unauthorized_root_files({Path("stray_probe.py")})
            and not P.unauthorized_root_files({Path("package_release.py")})
            and P.unauthorized_directories({Path("nope/x.py")})
            and not P.unauthorized_directories({Path("mtgdb/main.py")})),
        "local build rebuilds the dependency environment": (
            'rmdir /s /q "build-venv"' in build_script
            # rmdir reports "Access is denied" and carries on when a file is
            # locked, which left the old environment in place and built
            # against it silently -- exactly what removing it was for.
            and build_script.count('if exist "build-venv"') >= 2
            and "Could not remove build-venv" in build_script),
        "VER-011 tests clean up every temporary directory they create": (
            _unmanaged_temp_directories() == []),
        "source archive uses stable root and exact membership validation": (
            P.ARCHIVE_ROOT == "MTG_Deck_Builder"
            and "actual != expected" in (ROOT / "package_release.py").read_text(encoding="utf-8")
            and "expected_members=members" in (ROOT / "package_release.py").read_text(encoding="utf-8")),
        "PORT verification map covers portable storage and user paths": (
            "PORT-001 through PORT-005" in agent_text
            and "tests/test_portability_contract.py" in agent_text
            and "tests/test_hardening_regressions.py" in agent_text),
        "local build installs the pyproject build group": (
            'python -m pip install ".[build]"' in build_script),
        "cloud build installs the pyproject build group": (
            'python -m pip install ".[build]"' in workflow),
        "build paths do not reference requirements.txt": (
            "requirements.txt" not in build_script
            and "requirements.txt" not in workflow),
        "generated dependency metadata is removed before local gates": (
            -1 not in {local_cleanup, local_compile}
            and local_cleanup < local_compile),
        "generated dependency metadata is removed before cloud gates": (
            -1 not in {cloud_cleanup, cloud_compile}
            and cloud_cleanup < cloud_compile),
        "source gates cover every cross-platform test": (
            discovered_tests <= linux_command_targets),
        "Windows source gates include every pre-build test": (
            required_windows <= windows_command_targets),
        "geometry test uses exactly one hidden Tk root": (
            geometry_test.count("tk.Tk()") == 1
            and 'root.state() == "withdrawn"' in geometry_test
            and "not root.winfo_viewable()" in geometry_test
            and 'root.attributes("-alpha", 0.0)' in geometry_test),
        "geometry test supplies checklist owner scroll registration": (
            "def _install_geometry_owner_contract(root):" in geometry_test
            and "root._register_scrollable" in geometry_test
            and "_install_geometry_owner_contract(root)" in geometry_test
            and geometry_test.find("_install_geometry_owner_contract(root)",
                                   geometry_test.find("root = tk.Tk()"))
                > geometry_test.find("root = tk.Tk()")
            and geometry_test.find("_install_geometry_owner_contract(root)",
                                   geometry_test.find("root = tk.Tk()"))
                < geometry_test.find('root.geometry("1x1-32000-32000")')),
        "geometry test keeps aligned Content chips inside one grid-managed holder": (
            'advanced_holder.pack(fill="x")' in geometry_test
            and 'content_holder.grid(row=0, column=1, sticky="w", pady=2)' in geometry_test
            and "chip.grid(" in geometry_test
            and "content_holder.pack()" not in geometry_test
            and "*content_chips" not in geometry_test[
                geometry_test.find("widgets = ["):
                geometry_test.find("for widget in widgets:")
            ]),
        "geometry test retains all three display scales": all(
            scale in geometry_test for scale in (
                "(100, 96 / 72)",
                "(125, 120 / 72)",
                "(150, 144 / 72)",
            )),
        "geometry text-fit check understands intentional multiline labels": (
            "text.splitlines()" in geometry_test
            and 'text="Remove from\\nMainboard"' in geometry_test
            and 'text="Remove from\\nSideboard"' in geometry_test
            and "comparison side rail fits source-board actions" in geometry_test),
        "geometry gate is labeled simulated Tk scaling, not DPI certification": (
            "simulated Tk scaling" in geometry_test
            and "not certification of Windows OS or" in geometry_test
            and "simulated-Tk-scaling geometry" in agent_text),
        "a failed automatic gate raises": automatic_failure_blocks and bool(attempted),
        "a prohibited document blocks validation": prohibited_document_blocks,
        "a failed gate cannot replace the target archive": (
            archive_failure_blocks and failed_build_preserves_output),
        "local Windows gates precede build and smoke follows": (
            -1 not in {
                local_compile, local_tests, local_single, local_geometry,
                local_startup, local_build, local_smoke,
            }
            and max(
                local_compile, local_tests, local_single, local_geometry,
                local_startup) < local_build < local_smoke),
        "cloud Windows gates precede build, smoke, and upload": (
            -1 not in {
                cloud_compile, cloud_tests, cloud_single, cloud_geometry,
                cloud_startup, cloud_build, cloud_smoke, cloud_upload,
            }
            and max(
                cloud_compile, cloud_tests, cloud_single, cloud_geometry,
                cloud_startup) < cloud_build < cloud_smoke < cloud_upload),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    if unauthorized:
        print("  Unauthorized documentation:")
        for path in unauthorized:
            print(f"    - {path.as_posix()}")
    if ambiguous_rules:
        print("  Ambiguous rule wording:")
        for rule in ambiguous_rules:
            print(f"    - {rule}")
    if lowercase_normative_rules:
        print("  Lowercase normative wording:")
        for rule in lowercase_normative_rules:
            print(f"    - {rule}")
    print(f"  Parsed enforceable rules: {len(rule_ids)}")
    print("\nPROJECT GUARDRAILS:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
