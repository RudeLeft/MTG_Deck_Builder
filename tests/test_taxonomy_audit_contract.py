"""Static contract for the networked full-Scryfall taxonomy audit."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.authorities import (
    CATALOG_AUTHORITIES, MECHANIC_AUTHORITIES, SCRYFALL_CATALOGS, SUBTYPE_AUTHORITIES,
)
from mtgdb.database.schema import RULES_SUPERTYPES_META_KEY


def main():
    audit = (ROOT / "tests/taxonomy_audit.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/taxonomy-audit.yml").read_text(
        encoding="utf-8")
    constants = (ROOT / "mtgdb/database/constants.py").read_text(encoding="utf-8")
    taxonomy = (ROOT / "mtgdb/database/taxonomy.py").read_text(encoding="utf-8")
    semantics = (ROOT / "mtgdb/database/semantics.py").read_text(encoding="utf-8")
    checks = {
        "Scryfall card-types is a first-class cached catalog": (
            SCRYFALL_CATALOGS.get("card-types") == "Card type"),
        "taxonomy authority sources have one declarative registry": (
            len(CATALOG_AUTHORITIES) == len(SCRYFALL_CATALOGS)
            and {item.name for item in SUBTYPE_AUTHORITIES}
                == {name for name in SCRYFALL_CATALOGS if name.endswith("-types") and name != "card-types"}
            and {item.name for item in MECHANIC_AUTHORITIES}
                == {"keyword-abilities", "keyword-actions", "ability-words"}
            and "catalog_categories = (" not in taxonomy),
        "no hardcoded Card Type tuple remains": (
            "CARD_TYPES =" not in constants and "CARD_TYPES =" not in taxonomy
            and "CARD_TYPES =" not in semantics),
        "Card Type picker is catalog intersect local occurrence": (
            'catalog = self.catalog("card-types")' in taxonomy
            and "if not catalog:" in taxonomy
            and "_card_has_type(type_line, value)" in taxonomy),
        "Supertype picker uses verified Wizards metadata plus local occurrence": (
            RULES_SUPERTYPES_META_KEY == "rules:supertypes"
            and "_verified_rules_supertypes" in taxonomy
            and 'self.catalog("supertypes")' not in taxonomy
            and "_observed_left_phrase" in taxonomy
            and "SUPERTYPES =" not in constants),

        "current audit discovers Default Cards instead of hardcoding a dated file": (
            'net.get_json(f"{API_BASE}/bulk-data")' in audit
            and 'value.get("type") == "default_cards"' in audit
            and "jsonl_download_uri" in audit),
        "current audit refreshes Scryfall and Wizards taxonomy sources": (
            "if args.online_catalogs or args.current:" in audit
            and "refresh_catalogs()" in audit
            and "RULES_SUPERTYPES_META_KEY" in audit),
        "full audit proves Card Types are authorized by Scryfall": (
            "trusted Card Types are authorized by Scryfall card-types catalog" in audit),
        "full audit proves Supertypes stay inside parsed Wizards vocabulary": (
            "trusted Supertypes are Wizards-rules-defined and observed" in audit
            and "Supertype rules provenance is recorded from Wizards" in audit),
        "full audit records upstream compatibility fingerprints": (
            "refresh_compatibility_diagnostics()" in audit
            and "## Upstream compatibility" in audit
            and "current Scryfall layouts are understood or deliberately classified" in audit
            and "current Scryfall legality statuses are understood" in audit),
        "weekly/manual CI runs the current full audit": (
            "workflow_dispatch:" in workflow and "schedule:" in workflow
            and "tests/taxonomy_audit.py --current" in workflow),
        "CI retains Markdown audit evidence": (
            "taxonomy-audit.md" in workflow and "upload-artifact@v4" in workflow),
    }
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nTAXONOMY AUDIT CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
