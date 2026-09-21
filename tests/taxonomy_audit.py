"""Audit an entire Scryfall Default Cards bulk file.

This is deliberately separate from fast regressions. It streams the source,
builds a real temporary CardDB, compares discovery output with source truth, and
optionally writes a Markdown evidence report.

Usage:
  python tests/taxonomy_audit.py PATH_TO_DEFAULT_CARDS [--online-catalogs]
  python tests/taxonomy_audit.py --current [--report REPORT.md]
  python tests/taxonomy_audit.py PATH --online-catalogs --report REPORT.md

``--current`` discovers and downloads Scryfall's current Default Cards export and
refreshes all trusted catalogs before auditing it.
"""
import argparse
import collections
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mtgdb.database.bulk_import import iter_card_objects
from mtgdb.database.constants import ART_LAYOUTS
from mtgdb.database.db import CardDB
from mtgdb.database.semantics import _BFM_COMPLETE_TYPE_LINE
from mtgdb.database import semantics as S
from mtgdb.database.schema import RULES_SUPERTYPES_META_KEY
from mtgdb.database.sync import API_BASE, DatabaseSyncService
import mtgdb.core.net as net


def download_current_default_cards(directory):
    """Discover and download Scryfall's current Default Cards bulk export."""
    metadata = net.get_json(f"{API_BASE}/bulk-data")
    items = metadata.get("data", []) if isinstance(metadata, dict) else []
    item = next(
        (value for value in items
         if isinstance(value, dict) and value.get("type") == "default_cards"),
        None,
    )
    if not item:
        raise RuntimeError("Scryfall bulk-data response has no default_cards item")
    url = item.get("jsonl_download_uri") or item.get("download_uri")
    if not url:
        raise RuntimeError("Scryfall default_cards metadata has no download URI")
    basename = os.path.basename(str(url).split("?", 1)[0]) or "default-cards.json"
    path = os.path.join(directory, basename)
    net.download(str(url), path)
    return path, item


def source_inventory(path):
    counts = collections.Counter()
    ids = set()
    sets = {}
    discoverable_sets = set()
    keywords = set()
    printing_keys = collections.Counter()
    duplicate_ids = 0
    started = time.monotonic()
    for card in iter_card_objects(path):
        counts["objects"] += 1
        card_id = str(card.get("id") or "")
        name = str(card.get("name") or "")
        set_code = str(card.get("set") or "")
        set_name = str(card.get("set_name") or "")
        set_type = str(card.get("set_type") or "")
        collector = str(card.get("collector_number") or "")
        lang = str(card.get("lang") or "")
        if not card_id:
            counts["missing_id"] += 1
        elif card_id in ids:
            duplicate_ids += 1
        else:
            ids.add(card_id)
        for field, value in (("name", name), ("set_code", set_code),
                             ("set_name", set_name), ("set_type", set_type),
                             ("collector", collector)):
            if not value:
                counts[f"missing_{field}"] += 1
        if set_code:
            sets.setdefault(set_code, (set_name, set_type))
            if card.get("layout") not in ART_LAYOUTS:
                discoverable_sets.add(set_code)
        keywords.update(str(value) for value in (card.get("keywords") or [])
                        if str(value))
        if name and set_code and collector:
            printing_keys[(name.casefold(), set_code.casefold(), collector,
                           lang.casefold())] += 1
        if card.get("card_faces"):
            counts["multi_face_objects"] += 1
        if card.get("all_parts"):
            counts["related_objects"] += 1
    exact_collisions = sum(1 for value in printing_keys.values() if value > 1)
    return {
        "counts": counts, "ids": ids, "sets": sets,
        "discoverable_sets": discoverable_sets, "keywords": keywords,
        "duplicate_ids": duplicate_ids, "exact_printing_collisions": exact_collisions,
        "seconds": time.monotonic() - started,
    }


def markdown(result):
    source = result["source"]
    checks = result["checks"]
    lines = [
        "# Full Scryfall Taxonomy & Printing Audit",
        "",
        f"Bulk source: `{result['bulk_path']}`  ",
        f"Scryfall bulk updated: `{result.get('bulk_updated_at') or 'local file / unknown'}`  ",
        f"Audited: {result['audited_at']}  ",
        f"Result: **{'PASS' if all(checks.values()) else 'REVIEW REQUIRED'}**",
        "",
        "## Coverage",
        "",
        "| Area | Count |",
        "|---|---:|",
        f"| Card printing objects | {source['objects']:,} |",
        f"| Unique printing IDs | {result['db_cards']:,} |",
        f"| Source set codes | {result['source_sets']:,} |",
        f"| Searchable non-art set codes shown | {result['sets']:,} |",
        f"| Art-series-only set codes intentionally hidden | {result['art_only_sets']:,} |",
        f"| Set types | {result['set_types']:,} |",
        f"| Trusted Card Type names | {result['card_types']:,} |",
        f"| Rules-defined observed Supertype names | {result['supertypes']:,} |",
        f"| Trusted catalog-backed subtype names | {result['subtypes']:,} |",
        f"| Trusted Mechanics labels | {result['keywords']:,} |",
        f"| Multi-face objects | {source['multi_face_objects']:,} |",
        f"| Objects with related parts | {source['related_objects']:,} |",
        "",
        "## Category breakdown",
        "",
        "### Subtypes",
        "",
    ]
    for name, count in sorted(result["subtype_categories"].items()):
        lines.append(f"- {name}: {count:,}")
    lines += ["", "### Mechanics / keyword catalogs", ""]
    for name, count in sorted(result["keyword_categories"].items()):
        lines.append(f"- {name}: {count:,}")
    compatibility = result.get("compatibility") or {}
    lines += [
        "", "## Upstream compatibility", "",
        f"- Fingerprint: `{compatibility.get('fingerprint') or 'unavailable'}`",
        "- Unknown layouts: " + (", ".join(compatibility.get("unknown_layouts", [])) or "none"),
        "- Unknown legality statuses: " + (
            ", ".join(compatibility.get("unknown_legality_statuses", [])) or "none"),
        "- Subtype authority gaps (diagnostic only): " + (
            ", ".join(compatibility.get("subtype_authority_gaps", [])) or "none"),
        "- Mechanic authority gaps (diagnostic only): " + (
            ", ".join(compatibility.get("mechanic_authority_gaps", [])) or "none"),
        "", "## Integrity checks", "",
    ]
    for label, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'} — {label}")
    lines += [
        "", "## Notes", "",
        "- B.F.M. collector numbers 28 and 29 remain separate printing IDs, "
        "but both receive the reconstructed official hyphenated creature subtype.",
        "- `raw_type_line`, `related_parts`, and `card_faces` preserve Scryfall's "
        "source representation for future reparsing.",
        "- Card Type, Subtype, and Mechanics picker vocabulary is the intersection "
        "of cached Scryfall catalogs and values actually observed in the local card "
        "snapshot; Supertypes instead use the strictly parsed current Wizards "
        "Comprehensive Rules taxonomy plus observed Scryfall type lines.",
        "- Uncataloged words remain available to explicit query callers but are not "
        "invented as interactive picker vocabulary.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("bulk_path", nargs="?")
    parser.add_argument("--current", action="store_true",
                        help="download and audit Scryfall's current Default Cards export")
    parser.add_argument("--online-catalogs", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if args.current and args.bulk_path:
        parser.error("provide either bulk_path or --current, not both")
    if not args.current and not args.bulk_path:
        parser.error("bulk_path is required unless --current is used")

    tmpdir = tempfile.mkdtemp(prefix="mtg-taxonomy-audit-")
    downloaded = False
    bulk_meta = {}
    if args.current:
        bulk_path, bulk_meta = download_current_default_cards(tmpdir)
        downloaded = True
    else:
        bulk_path = args.bulk_path

    inventory = source_inventory(bulk_path)
    db = CardDB(os.path.join(tmpdir, "cards.db"))
    if args.online_catalogs or args.current:
        refreshed = DatabaseSyncService(db).refresh_catalogs()
    else:
        refreshed = 0
    db_cards = db.load_cards(iter_card_objects(bulk_path), minimum_count=1000)
    compatibility = db.refresh_compatibility_diagnostics()

    discovered_sets = db.sets()
    set_types = db.set_types()
    card_types = db.card_types()
    supertypes = db.supertypes()
    subtypes = db.subtype_catalog()
    abilities = db.keyword_catalog()
    try:
        rules_supertypes = json.loads(
            db.get_meta(RULES_SUPERTYPES_META_KEY, "[]"))
    except (TypeError, ValueError):
        rules_supertypes = []
    if not isinstance(rules_supertypes, list):
        rules_supertypes = []
    rules_source_url = db.get_meta("rules:supertypes_source_url", "")
    rules_document_hash = db.get_meta("rules:supertypes_document_sha256", "")
    subtype_categories = collections.Counter(category for _value, category in subtypes)
    keyword_categories = collections.Counter(category for _value, category in abilities)
    bfm = db.search(name="B.F.M. (Big Furry Monster)", set_codes=["ugl"])
    bfm_subtype = (
        "The-Biggest-Baddest-Nastiest-Scariest-Creature-You'll-Ever-See")
    source = inventory["counts"]
    checks = {
        "every source object has a unique stored Scryfall ID": (
            inventory["duplicate_ids"] == 0 and db_cards == len(inventory["ids"])),
        "no source card is missing its required name": source["missing_name"] == 0,
        "every non-art set code is represented in discovery": (
            {code for code, _name in discovered_sets} ==
            inventory["discoverable_sets"]),
        "art-series-only set exclusions exactly explain hidden set codes": (
            set(inventory["sets"]) - inventory["discoverable_sets"] ==
            set(inventory["sets"]) - {code for code, _name in discovered_sets}),
        "set names and set types are populated": (
            source["missing_set_code"] == 0 and source["missing_set_name"] == 0
            and source["missing_set_type"] == 0),
        "collector numbers are populated": source["missing_collector"] == 0,
        "no name/set/collector/language printing identity collides": (
            inventory["exact_printing_collisions"] == 0),
        "every trusted Mechanic is an observed Scryfall card keyword": (
            {S._type_key(value) for value, _category in abilities}
            <= {S._type_key(value) for value in inventory["keywords"]}),
        "trusted Card Types are authorized by Scryfall card-types catalog": (
            bool(db.catalog("card-types"))
            and {S._type_key(value) for value in card_types}
                <= {S._type_key(value) for value in db.catalog("card-types")}),
        "trusted Supertypes are Wizards-rules-defined and observed": (
            bool(rules_supertypes)
            and {S._type_key(value) for value in supertypes}
                <= {S._type_key(value) for value in rules_supertypes}
            and all(db.search(supertypes=[value]) for value in supertypes)),
        "Supertype rules provenance is recorded from Wizards": (
            rules_source_url.startswith("https://")
            and len(rules_document_hash) == 64),
        "trusted Mechanics are authorized by Scryfall catalogs": (
            {S._type_key(value) for value, _category in abilities}
            <= {S._type_key(value) for name in (
                "keyword-abilities", "keyword-actions", "ability-words")
                for value in db.catalog(name)}),
        "trusted Subtypes are authorized by Scryfall subtype catalogs": (
            {S._type_key(value) for value, _category in subtypes}
            <= {S._type_key(value) for name in (
                "artifact-types", "battle-types", "creature-types",
                "enchantment-types", "land-types", "planeswalker-types",
                "spell-types") for value in db.catalog(name)}),
        "case-only source variants do not duplicate picker names": (
            len(card_types) == len({S._type_key(value) for value in card_types}) and
            len(subtypes) == len({S._type_key(value) for value, _ in subtypes}) and
            len(abilities) == len({S._type_key(value) for value, _ in abilities})),
        "B.F.M. remains two distinct collector-number printings": (
            {card["collector_number"] for card in bfm} == {"28", "29"}),
        "B.F.M. subtype follows catalog trust without fragment guessing": (
            ((bfm_subtype in db.catalog("creature-types")
              and dict(subtypes).get(bfm_subtype) == "Creature")
             or (bfm_subtype not in db.catalog("creature-types")
                 and bfm_subtype not in dict(subtypes)))
            and all(card["type_line"] == _BFM_COMPLETE_TYPE_LINE for card in bfm)),
        "B.F.M. fragment words do not leak into card-type names": (
            not ({"Scariest", "You'll", "Ever", "See"} & set(card_types))),
        "all trusted Mechanics receive a category": (
            len(abilities) == sum(keyword_categories.values())),
        "all parsed subtype labels receive a category": (
            len(subtypes) == sum(subtype_categories.values())),
        "current Scryfall layouts are understood or deliberately classified": (
            not compatibility.get("unknown_layouts")),
        "current Scryfall legality statuses are understood": (
            not compatibility.get("unknown_legality_statuses")),
    }

    result = {
        "bulk_path": os.path.basename(bulk_path),
        "bulk_updated_at": bulk_meta.get("updated_at", "") if bulk_meta else "",
        "bulk_download_uri": (bulk_meta.get("jsonl_download_uri")
                              or bulk_meta.get("download_uri", "")) if bulk_meta else "",
        "audited_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "source": dict(source), "db_cards": db_cards,
        "source_sets": len(inventory["sets"]), "sets": len(discovered_sets),
        "art_only_sets": len(set(inventory["sets"]) -
                             inventory["discoverable_sets"]),
        "set_types": len(set_types),
        "card_types": len(card_types), "supertypes": len(supertypes),
        "rules_supertypes": list(rules_supertypes),
        "rules_source_url": rules_source_url,
        "subtypes": len(subtypes),
        "keywords": len(abilities), "catalogs_refreshed": refreshed,
        "subtype_categories": dict(subtype_categories),
        "keyword_categories": dict(keyword_categories), "checks": checks,
        "inventory_seconds": round(inventory["seconds"], 2),
        # markdown() renders the Upstream compatibility section from this; without
        # it the report always showed "unavailable"/"none" even when a check for
        # an unknown layout or legality status failed.
        "compatibility": compatibility,
    }
    report = markdown(result)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            handle.write(report)
    print(report)
    db.close()
    if downloaded:
        shutil.rmtree(tmpdir, ignore_errors=True)
    else:
        # Local-file audits still use a temporary database only.
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
