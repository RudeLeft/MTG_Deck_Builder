"""Create a source release while enforcing the project's package rules."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE_ROOT = "MTG_Deck_Builder"

DOCUMENT_SUFFIXES = {
    ".md", ".markdown", ".mdown", ".mkd", ".txt", ".rst", ".adoc",
    ".asciidoc", ".doc", ".docx", ".odt", ".pdf", ".rtf",
}
ALLOWED_DOCUMENT = Path("AGENTS.md")
EXCLUDED_DIRECTORY_NAMES = {
    ".git", ".pytest_cache", "__pycache__", "build", "build-venv",
    "data", "dist",
}
EXCLUDED_FILE_SUFFIXES = {".pyc", ".pyo", ".log", ".sqlite", ".db"}
REQUIRED_RELEASE_MEMBERS = {
    Path(".gitattributes"),
    Path(".gitignore"),
    Path("AGENTS.md"),
    Path("MTGDeckBuilder.spec"),
    Path("assets/magic_icon.ico"),
    Path("build_windows.bat"),
    Path("mtgdb/__init__.py"),
    Path("mtgdb/__main__.py"),
    Path("mtgdb/ui/app.py"),
    Path("mtgdb/comparison/__init__.py"),
    Path("mtgdb/comparison/models.py"),
    Path("mtgdb/core/__init__.py"),
    Path("mtgdb/core/background_jobs.py"),
    Path("mtgdb/core/cache_names.py"),
    Path("mtgdb/core/net.py"),
    Path("mtgdb/database/__init__.py"),
    Path("mtgdb/database/authorities.py"),
    Path("mtgdb/database/bulk_import.py"),
    Path("mtgdb/database/constants.py"),
    Path("mtgdb/database/db.py"),
    Path("mtgdb/database/queries.py"),
    Path("mtgdb/database/search_queries.py"),
    Path("mtgdb/database/schema.py"),
    Path("mtgdb/database/semantics.py"),
    Path("mtgdb/database/sync.py"),
    Path("mtgdb/database/taxonomy.py"),
    Path("mtgdb/deck/__init__.py"),
    Path("mtgdb/deck/analysis.py"),
    Path("mtgdb/deck/file_jobs.py"),
    Path("mtgdb/deck/io.py"),
    Path("mtgdb/deck/legality.py"),
    Path("mtgdb/deck/model.py"),
    Path("mtgdb/deck/sessions.py"),
    Path("mtgdb/images/__init__.py"),
    Path("mtgdb/images/service.py"),
    Path("mtgdb/main.py"),
    Path("mtgdb/preferences/__init__.py"),
    Path("mtgdb/preferences/repository.py"),
    Path("mtgdb/printing/__init__.py"),
    Path("mtgdb/printing/renderer.py"),
    Path("mtgdb/printing/service.py"),
    Path("mtgdb/search/__init__.py"),
    Path("mtgdb/search/catalogs.py"),
    Path("mtgdb/search/controller.py"),
    Path("mtgdb/search/models.py"),
    Path("mtgdb/search/repository.py"),
    Path("mtgdb/search/results.py"),
    Path("mtgdb/ui/__init__.py"),
    Path("mtgdb/ui/assets.py"),
    Path("mtgdb/ui/autocomplete.py"),
    Path("mtgdb/ui/card_detail.py"),
    Path("mtgdb/ui/comparison.py"),
    Path("mtgdb/ui/comparison_controls.py"),
    Path("mtgdb/ui/components.py"),
    Path("mtgdb/ui/database_sync.py"),
    Path("mtgdb/ui/deck.py"),
    Path("mtgdb/ui/deck_files.py"),
    Path("mtgdb/ui/deck_stats.py"),
    Path("mtgdb/ui/search_checklist.py"),
    Path("mtgdb/ui/search_printings.py"),
    Path("mtgdb/ui/table_filters.py"),
    Path("mtgdb/ui/mana.py"),
    Path("mtgdb/ui/printing.py"),
    Path("mtgdb/ui/results.py"),
    Path("mtgdb/ui/search.py"),
    Path("mtgdb/ui/set_filters.py"),
    Path("mtgdb/ui/styles.py"),
    Path("mtgdb/ui/tables.py"),
    Path("mtgdb/ui/tokens.py"),
    Path("mtgdb/ui/window.py"),
    Path("mtgdb/ui/workspace.py"),
    Path("mtgdb/workspace/__init__.py"),
    Path("mtgdb/workspace/repository.py"),
    Path("package_release.py"),
    Path("pyproject.toml"),
    Path("tests/test_background_jobs_architecture.py"),
    Path("tests/test_comparison_architecture.py"),
    Path("tests/test_deck_ui_architecture.py"),
    Path("tests/test_integrity_regressions.py"),
    Path("tests/test_hardening_regressions.py"),
    Path("tests/test_portability_contract.py"),
    Path("tests/test_performance_architecture.py"),
    Path("tests/test_printing_architecture.py"),
    Path("tests/test_project_guardrails.py"),
    Path("tests/test_workspace_architecture.py"),
    Path("windows_tests/smoke_packaged_windows.py"),
    Path("windows_tests/test_app_startup_windows.py"),
    Path("windows_tests/test_single_instance_windows.py"),
    Path("windows_tests/test_ui_geometry_windows.py"),
}
# Membership ceiling.  REQUIRED_RELEASE_MEMBERS is only a floor: it proves the
# release is complete, not that it is clean.  Without these two limits any
# scratch file left at the project root -- a debug script, a one-off probe --
# silently ships.  Directories are limited to the known project tree, and root
# level files to this exact set.
ALLOWED_ROOT_FILES = {
    Path(".gitattributes"),
    Path(".gitignore"),
    Path("AGENTS.md"),
    Path("MTGDeckBuilder.spec"),
    Path("build_windows.bat"),
    Path("package_release.py"),
    Path("pyproject.toml"),
}
ALLOWED_TOP_LEVEL_DIRECTORIES = {
    ".github", "assets", "mtgdb", "tests", "windows_tests",
}
WINDOWS_SOURCE_GATES = (
    Path("windows_tests/test_single_instance_windows.py"),
    Path("windows_tests/test_ui_geometry_windows.py"),
    Path("windows_tests/test_app_startup_windows.py"),
)


def is_excluded(relative_path: Path, output_path: Path | None = None) -> bool:
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative_path.parts):
        return True
    if relative_path.suffix.casefold() in EXCLUDED_FILE_SUFFIXES:
        return True
    if relative_path.suffix.casefold() == ".zip":
        return True
    if output_path is not None:
        try:
            if (ROOT / relative_path).resolve() == output_path.resolve():
                return True
        except OSError:
            pass
    return False


def unauthorized_root_files(paths):
    """Return root-level files that are not on the release allow-list."""
    return sorted(
        (path for path in paths
         if len(path.parts) == 1 and path not in ALLOWED_ROOT_FILES),
        key=lambda path: path.as_posix())


def unauthorized_directories(paths):
    """Return members outside the known top-level project directories."""
    return sorted(
        (path for path in paths
         if len(path.parts) > 1
         and path.parts[0] not in ALLOWED_TOP_LEVEL_DIRECTORIES),
        key=lambda path: path.as_posix())


def unauthorized_documents(paths):
    failures = []
    for path in paths:
        if path.suffix.casefold() not in DOCUMENT_SUFFIXES:
            continue
        if path.as_posix() != ALLOWED_DOCUMENT.as_posix():
            failures.append(path)
    return failures


def source_members(output_path: Path | None = None):
    members = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if is_excluded(relative, output_path):
            continue
        members.append(relative)
    return sorted(members, key=lambda path: path.as_posix().casefold())


def validate_members(members):
    members = set(members)
    failures = unauthorized_documents(members)
    if failures:
        names = "\n".join(f"  - {path.as_posix()}" for path in failures)
        raise RuntimeError(
            "AGENTS.md must be the only documentation file. Remove:\n" + names)
    if ALLOWED_DOCUMENT not in members:
        raise RuntimeError("AGENTS.md is required in every release.")
    if Path("pyproject.toml") not in members:
        raise RuntimeError("pyproject.toml is required in every release.")
    if Path("requirements.txt") in members:
        raise RuntimeError("requirements.txt is prohibited.")
    stray_root = unauthorized_root_files(members)
    if stray_root:
        names = "\n".join(f"  - {path.as_posix()}" for path in stray_root)
        raise RuntimeError(
            "Unexpected root-level files in the release. Remove them or add "
            "them to ALLOWED_ROOT_FILES:\n" + names)
    stray_dirs = unauthorized_directories(members)
    if stray_dirs:
        names = "\n".join(f"  - {path.as_posix()}" for path in stray_dirs)
        raise RuntimeError(
            "Release members outside the known project directories:\n" + names)
    missing = sorted(REQUIRED_RELEASE_MEMBERS - members)
    if missing:
        names = "\n".join(f"  - {path.as_posix()}" for path in missing)
        raise RuntimeError("Required release members are missing:\n" + names)


def release_gate_commands(platform=None):
    """Return every deterministic gate required before source packaging."""
    platform = sys.platform if platform is None else platform
    commands = [
        ("Python compilation", [
            sys.executable, "-m", "compileall", "-q", "mtgdb", "tests",
            "windows_tests", "package_release.py"]),
    ]
    for test_path in sorted((ROOT / "tests").glob("test_*.py")):
        relative = test_path.relative_to(ROOT)
        commands.append((relative.as_posix(), [sys.executable, str(relative)]))
    if platform == "win32":
        for relative in WINDOWS_SOURCE_GATES:
            commands.append((relative.as_posix(), [sys.executable, str(relative)]))
    return commands


def run_release_gates(platform=None, runner=None):
    """Run all source-release gates and raise on the first failure."""
    runner = subprocess.run if runner is None else runner
    for label, command in release_gate_commands(platform):
        print(f"GATE RUN: {label}")
        completed = runner(command, cwd=ROOT)
        if completed.returncode != 0:
            raise RuntimeError(
                f"Mandatory release gate failed ({completed.returncode}): "
                f"{label}")


def validate_archive(archive_path, expected_members=None):
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise RuntimeError(f"Archive verification failed at {bad_member}.")
        archived = []
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            path = Path(name)
            if not path.parts or path.parts[0] != ARCHIVE_ROOT:
                raise RuntimeError(f"Invalid archive root for member: {name}")
            archived.append(Path(*path.parts[1:]))
    validate_members(archived)
    expected = set(
        source_members(Path(archive_path))
        if expected_members is None else expected_members)
    actual = set(archived)
    if actual != expected:
        missing = sorted(expected - actual, key=lambda path: path.as_posix())
        extra = sorted(actual - expected, key=lambda path: path.as_posix())
        details = []
        if missing:
            details.append("Missing archive members:\n" + "\n".join(
                f"  - {path.as_posix()}" for path in missing))
        if extra:
            details.append("Unexpected archive members:\n" + "\n".join(
                f"  - {path.as_posix()}" for path in extra))
        raise RuntimeError("Archive source membership mismatch.\n" + "\n".join(details))
    return archived


def build_archive(output_path: Path):
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    members = source_members(output_path)
    validate_members(members)
    run_release_gates()

    # Re-scan after tests so test-created files cannot bypass content checks.
    members = source_members(output_path)
    validate_members(members)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".partial",
        dir=output_path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        with zipfile.ZipFile(
                temporary_path, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=9) as archive:
            for relative in members:
                archive.write(
                    ROOT / relative,
                    arcname=(Path(ARCHIVE_ROOT) / relative).as_posix())
        archived = validate_archive(temporary_path, expected_members=members)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path, len(archived)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        output, count = build_archive(args.output)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"PACKAGE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"PACKAGE PASS: {output} ({count} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
