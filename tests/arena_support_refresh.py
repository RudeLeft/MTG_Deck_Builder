"""Regenerate assets/arena/supported_cards.json from the Proxic Arena card scripts.

Proxic Arena resolves a card by name alone, so the Deck Builder can only warn
about a card Arena will not find if it holds the list of names Arena has card
scripts for. The two applications are separate projects and neither reads the
other's tree at runtime, so that list ships as a generated asset and is
refreshed by this script whenever the Arena card scripts are updated
(DECK-012).

This is a maintenance tool, not a gate: it reads a tree outside this
repository and rewrites a source asset, so it is deliberately named outside the
`test_*.py` pattern the release suite runs. `tests/test_deck_architecture.py`
checks its contract without needing the Arena tree, the way
`test_taxonomy_audit_contract.py` does for the taxonomy audit.

Usage, from the project root:

    python tests/arena_support_refresh.py
    python tests/arena_support_refresh.py --cardsfolder <path to cardsfolder>
    python tests/arena_support_refresh.py --check

`--check` writes nothing and exits nonzero when the shipped asset no longer
matches the scripts, which is the question to ask after bumping Arena.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.deck.arena_support import (
    ASSET_VERSION, front_face_name, normalize_card_name,
)
from mtgdb.ui.assets import ARENA_SUPPORTED_CARDS_FILE

# Where the Arena card scripts sit when both projects are checked out side by
# side. Only a default: --cardsfolder overrides it, because nothing in this
# repository may depend on the other project's location.
DEFAULT_CARDSFOLDER = (
    ROOT.parent / "Proxic_Arena" / "vendor" / "forge" / "forge-gui" / "res"
    / "cardsfolder")

NAME_PREFIX = "Name:"
# A script Arena ships but marks as a variant it cannot play. The file exists,
# so a name test alone would call the card playable.
UNSUPPORTED_MARKER = "<Unsupported Variant>"
# A refresh that collapses to a handful of names means the tree was wrong or
# unreadable, and MUST NOT be allowed to replace a complete asset with it.
MINIMUM_CREDIBLE_NAMES = 10000


def collect_supported_keys(cardsfolder):
    """Return the lookup keys for every card name Arena ships a script for.

    Each script holds one `Name:` line per face, and a card is filed under its
    front face, so both faces' names are read and reduced to the same key the
    runtime uses. Reading the names rather than the filenames is deliberate:
    the filename is a lossy spelling of the name and cannot be reversed.
    """
    folder = Path(cardsfolder)
    if not folder.is_dir():
        raise SystemExit(f"Not a directory: {folder}")

    keys = set()
    scripts = 0
    skipped = 0
    for script in sorted(folder.rglob("*.txt")):
        try:
            text = script.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SystemExit(f"Cannot read {script}: {exc}") from exc
        scripts += 1
        if UNSUPPORTED_MARKER in text:
            skipped += 1
            continue
        for line in text.splitlines():
            if not line.startswith(NAME_PREFIX):
                continue
            key = normalize_card_name(
                front_face_name(line[len(NAME_PREFIX):].strip()))
            if key:
                keys.add(key)
    print(f"  scripts read:            {scripts}")
    print(f"  skipped as unsupported:  {skipped}")
    print(f"  distinct lookup keys:    {len(keys)}")
    if len(keys) < MINIMUM_CREDIBLE_NAMES:
        raise SystemExit(
            f"Found only {len(keys)} card names, below the credible minimum "
            f"of {MINIMUM_CREDIBLE_NAMES}; refusing to replace the shipped "
            "asset. Check --cardsfolder.")
    return keys


def build_payload(keys):
    """Return the asset payload for `keys`, sorted so a diff stays readable."""
    return {
        "version": ASSET_VERSION,
        "source": "Proxic Arena card scripts (Forge cardsfolder)",
        "name_count": len(keys),
        "names": sorted(keys),
    }


def write_asset(path, payload):
    """Write the asset with LF endings, which .gitattributes pins for this tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
        handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Regenerate the Proxic Arena playable-name asset.")
    parser.add_argument(
        "--cardsfolder", default=str(DEFAULT_CARDSFOLDER),
        help="Path to the Proxic Arena cardsfolder tree.")
    parser.add_argument(
        "--check", action="store_true",
        help="Compare only; write nothing and exit nonzero when stale.")
    args = parser.parse_args(argv)

    asset = ROOT / "assets" / ARENA_SUPPORTED_CARDS_FILE
    print(f"Arena card scripts: {args.cardsfolder}")
    print(f"Asset:              {asset.relative_to(ROOT).as_posix()}")
    payload = build_payload(collect_supported_keys(args.cardsfolder))

    previous = None
    if asset.is_file():
        try:
            previous = json.loads(asset.read_text(encoding="utf-8"))
        except ValueError:
            previous = None

    if previous == payload:
        print("\nThe shipped asset already matches the card scripts.")
        return 0

    if previous is None:
        print("\nNo readable existing asset; this refresh creates it.")
    else:
        was = set(previous.get("names") or ())
        now = set(payload["names"])
        print(f"\n  names added:   {len(now - was)}")
        print(f"  names removed: {len(was - now)}")

    if args.check:
        print("\nSTALE: the asset does not match the card scripts. Re-run "
              "without --check to refresh it.")
        return 1

    write_asset(asset, payload)
    print(f"\nWrote {payload['name_count']} names "
          f"({asset.stat().st_size / 1024:.0f} KB).")
    print("Run tests/test_deck_architecture.py to verify the shipped asset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
