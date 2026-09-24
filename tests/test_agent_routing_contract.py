"""Agent-facing Module-map routing coverage and implementation-alignment checks."""

import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

# Stable implementation landmarks. These do not describe the whole module; they
# ensure the task-oriented routing row still points at the implementation owner
# it claims to describe instead of surviving a responsibility transfer silently.
SOURCE_MARKERS = {
    "mtgdb/__main__.py": ("from mtgdb.main import main", "main()"),
    "mtgdb/main.py": ("def acquire_single_instance(", "def resolve_data_dir("),
    "mtgdb/ui/app.py": ("class DeckBuilderApp(", "def _build_menu("),
    "mtgdb/core/cache_names.py": ("def cache_path(", "def readable_stem("),
    "mtgdb/core/net.py": ("def fetch_bytes(", "def download("),
    "mtgdb/core/scryfall_json.py": ("def json_list(", "def card_faces("),
    "mtgdb/core/format_names.py": (
        "FORMAT_WORD_LABELS = {", "def format_display_name("),
    "mtgdb/core/background_jobs.py": ("class GenerationalWorker", "def check_cancel("),
    "mtgdb/core/atomic_files.py": (
        "def sweep_abandoned_writes(", "def temp_prefix("),
    "mtgdb/core/version.py": ("def app_version(", "_DISTRIBUTION"),
    "mtgdb/search/models.py": ("class SearchCriteria", "def query_arguments("),
    "mtgdb/search/repository.py": ("SEARCH_RESULT_COLUMNS", "class SearchRepository"),
    "mtgdb/search/controller.py": ("class SearchController", "def invalidate("),
    "mtgdb/search/results.py": ("class SearchResultStore", "class ResultPreparationWorker"),
    "mtgdb/search/catalogs.py": ("class SearchCatalogController", "class SearchCatalogSnapshot"),
    "mtgdb/search/context.py": ("class SearchContextController", "class SearchContextSnapshot"),
    "mtgdb/search/facet_index.py": ("class FacetIndex", "def filter_bitset("),
    "mtgdb/deck/model.py": ("class Deck", "def move("),
    "mtgdb/deck/io.py": ("def deck_to_text(", "def save_deck_text(", "def deck_from_text("),
    "mtgdb/deck/file_jobs.py": ("def submit_deck_file_job(", "spawn_daemon("),
    "mtgdb/deck/analysis.py": ("def deck_stats(", "def sample_hand("),
    "mtgdb/deck/legality.py": ("_FORMAT_RULES =", "def legality_problems("),
    "mtgdb/deck/sessions.py": ("class DeckSession", "class DeckSessionManager"),
    "mtgdb/database/authorities.py": ("CATALOG_AUTHORITIES =", "SUBTYPE_AUTHORITIES ="),
    "mtgdb/database/constants.py": ("CONTENT_TYPES =", "PLAYABLE_LEGALITY_STATUSES"),
    "mtgdb/database/db.py": ("class CardDB(", "def load_cards("),
    "mtgdb/database/schema.py": ("_SCHEMA_VERSION", "def initialize_schema("),
    "mtgdb/database/semantics.py": ("def _normalize_rules_text(", "def _card_has_subtype("),
    "mtgdb/database/bulk_import.py": ("def iter_card_objects(", "class ScryfallBulkImporter"),
    "mtgdb/database/queries.py": ("class CardQueryMixin", "def get_by_name("),
    "mtgdb/database/search_queries.py": ("class SearchQueryBuilder", "class CardSearchQueryMixin"),
    "mtgdb/database/taxonomy.py": ("class CardTaxonomyMixin", "def keyword_catalog("),
    "mtgdb/database/sync.py": ("class DatabaseSyncService", "class DatabaseSyncController"),
    "mtgdb/comparison/models.py": ("class ComparisonCollection", "MAX_COMPARISON_CARDS"),
    "mtgdb/images/service.py": ("class CardImageService", "def _worker_loop("),
    "mtgdb/printing/renderer.py": ("CARD_W", "def render_print_template("),
    "mtgdb/printing/service.py": ("class PrintTemplateService", "class PrintController"),
    "mtgdb/workspace/repository.py": ("WORKSPACE_VERSION", "class WorkspaceRepository"),
    "mtgdb/preferences/repository.py": ("TABLE_COLUMNS_VERSION", "class UIPreferencesRepository"),
    "mtgdb/ui/tokens.py": ("PALETTE =", "FONT_BODY"),
    "mtgdb/ui/styles.py": ("def install_ui_styles(", "def _configure_button("),
    "mtgdb/ui/assets.py": ("APP_ICON_FILE", "def _asset_path("),
    "mtgdb/ui/components.py": ("class AppButton", "class ClassicCheckbutton", "class ToolTip"),
    "mtgdb/ui/autocomplete.py": ("class _AutocompletePopupBehavior", "class AutocompleteEntry"),
    "mtgdb/ui/tables.py": ("TABLE_COLUMNS =", "class TableInfrastructureMixin"),
    "mtgdb/ui/card_detail.py": ("class CardDetailMixin", "def _show_card("),
    "mtgdb/ui/search.py": ("class SearchFeatureMixin", "def _do_search(", "def _capture_search_workspace_state("),
    "mtgdb/ui/search_filters.py": ("FILTER_DEFINITIONS =", "def advanced_filters("),
    "mtgdb/ui/search_printings.py": ("class SearchPrintingFilter", "def _update_summary("),
    "mtgdb/ui/search_checklist.py": ("class SearchChecklistDialog", "def open_search_checklist("),
    "mtgdb/ui/set_filters.py": ("def set_type_label(", "class PrintingFilter", "class SetFilterSupportMixin"),
    "mtgdb/ui/table_filters.py": ("class TableFilterMixin", "def _show_table_filter("),
    "mtgdb/ui/results.py": ("RESULT_LIVE_ROW_LIMIT = 128", "class SearchResultsMixin"),
    "mtgdb/ui/comparison.py": ("class CardComparisonWindow", "def _build_cards("),
    "mtgdb/ui/comparison_controls.py": ("class ComparisonFeatureMixin", "def _open_comparison_window("),
    "mtgdb/ui/deck.py": ("class DeckEditorMixin", "def _build_deck_pane("),
    "mtgdb/ui/deck_files.py": ("class DeckFileWorkflowMixin", "def _open_deck("),
    "mtgdb/ui/deck_stats.py": ("class DeckStatsMixin", "def _render_legality("),
    "mtgdb/ui/workspace.py": ("WORKSPACE_AUTOSAVE_MS", "class WorkspaceMixin"),
    "mtgdb/ui/database_sync.py": ("class DatabaseSyncMixin", "def _poll_sync_events("),
    "mtgdb/ui/printing.py": ("class PrintingMixin", "def _poll_print_events("),
    "mtgdb/ui/mana.py": ("class ManaSymbolsMixin", "def _cost_image("),
    "mtgdb/ui/window.py": ("class WindowServicesMixin", "def _center_popup_on_screen("),
}

STOPWORDS = {
    "add", "change", "modify", "the", "a", "an", "to", "of", "and", "or",
    "in", "for", "with", "from", "into", "on", "its", "new", "shared",
    "behavior", "module", "ui", "mtgdb", "py", "class", "def", "card", "cards",
}


def _section(start, end):
    match = re.search(rf"## {re.escape(start)}\n(.*?)(?=\n## {re.escape(end)})", AGENTS, re.S)
    if not match:
        raise AssertionError(f"Missing AGENTS section: {start}")
    return match.group(1)


def _tokenize(text):
    text = re.sub(r"`[^`]*?\.py`", " ", text)
    text = text.replace("<br>", " ")
    return {
        token for token in re.findall(r"[a-z0-9_]+", text.casefold())
        if len(token) > 2 and token not in STOPWORDS
    }


def main():
    ownership_section = _section("Module ownership", "Module map")
    ownership = dict(re.findall(
        r"^\| `([^`]+\.py)` \| (.*?) \|$", ownership_section, re.M))

    map_section = _section("Module map", "Import allow/deny matrix")
    rows = re.findall(
        r"^\| `([^`]+\.py)` \| (.*?) \| (.*?) \| (.*?) \|$",
        map_section, re.M)
    module_map = {module: (examples, collaborators, boundary)
                  for module, examples, collaborators, boundary in rows}

    production_modules = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "mtgdb").rglob("*.py")
        if path.name != "__init__.py"
    }
    all_examples = [
        example.strip()
        for examples, _collaborators, _boundary in module_map.values()
        for example in examples.split("<br>")
    ]

    docs = {
        module: _tokenize(
            ownership[module] + " " + examples + " " + boundary)
        for module, (examples, _collaborators, boundary) in module_map.items()
    }
    document_count = len(docs)
    document_frequency = {}
    for tokens in docs.values():
        for token in tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    inverse_document_frequency = {
        token: math.log((document_count + 1) / (count + 1)) + 1
        for token, count in document_frequency.items()
    }

    routing_failures = []
    simulation_count = 0
    for expected_module, (examples, _collaborators, _boundary) in module_map.items():
        for example in examples.split("<br>"):
            simulation_count += 1
            query = _tokenize(example)
            scores = []
            for candidate_module, candidate_tokens in docs.items():
                score = sum(
                    inverse_document_frequency.get(token, 1.0)
                    for token in query & candidate_tokens)
                scores.append((score, candidate_module))
            scores.sort(key=lambda item: (-item[0], item[1]))
            if not scores or scores[0][1] != expected_module:
                routing_failures.append((example, expected_module, scores[:3]))

    exact_collaborators = {
        collaborator
        for _module, (_examples, collaborators, _boundary) in module_map.items()
        for collaborator in re.findall(r"`(mtgdb/[^`*]+\.py)`", collaborators)
    }

    markers_match = True
    marker_failures = []
    for module, markers in SOURCE_MARKERS.items():
        source = (ROOT / module).read_text(encoding="utf-8")
        missing = [marker for marker in markers if marker not in source]
        if missing:
            markers_match = False
            marker_failures.append((module, missing))

    checks = {
        "routing map and implementation cover the same production modules": (
            set(module_map) == set(ownership) == production_modules == set(SOURCE_MARKERS)),
        "every module supplies at least two concrete routing simulations": all(
            len(examples.split("<br>")) >= 2
            and all(len(_tokenize(example)) >= 2 for example in examples.split("<br>"))
            for examples, _collaborators, _boundary in module_map.values()),
        "all simulated changes are unique and do not reveal a file path": (
            len(all_examples) == len(set(all_examples))
            and all(".py" not in example for example in all_examples)),
        "task-only retrieval routes every simulation to its documented owner": (
            simulation_count >= 2 * len(production_modules) and not routing_failures),
        "each routing owner still contains its implementation landmarks": markers_match,
        "every exact collaborator named by the Module map exists": all(
            (ROOT / collaborator).is_file() for collaborator in exact_collaborators),
        "every routing row gives a nontrivial boundary cue": all(
            len(_tokenize(boundary)) >= 4
            for _examples, _collaborators, boundary in module_map.values()),
    }

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print(f"  Routing simulations executed: {simulation_count}")
    if routing_failures:
        print("  Routing failures:")
        for failure in routing_failures:
            print(f"    - {failure}")
    if marker_failures:
        print("  Implementation-marker failures:")
        for failure in marker_failures:
            print(f"    - {failure}")
    print("\nAGENT ROUTING CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
