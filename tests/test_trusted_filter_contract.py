"""Trusted Search taxonomy/set vocabulary and scope contracts."""

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mtgdb.database.db import CardDB
from mtgdb.database.semantics import _card_content_kind
from mtgdb.database.schema import RULES_SUPERTYPES_META_KEY
from mtgdb.ui.search_filters import FILTER_BY_KEY, FILTER_DEFINITIONS
from mtgdb.search.models import SearchCriteria
from mtgdb.search.catalogs import SearchCatalogController
from mtgdb.search.repository import SearchRepository


def card(card_id, name, type_line, set_code, set_type, *, games=("paper",),
         keywords=(), layout="normal", rarity="common", legalities=None):
    return {
        "object": "card", "id": card_id, "name": name,
        "type_line": type_line, "cmc": 2, "colors": [], "color_identity": [],
        "rarity": rarity, "set": set_code, "set_name": set_code.upper(),
        "set_type": set_type, "collector_number": card_id,
        "lang": "en", "released_at": "2026-01-01", "games": list(games),
        "keywords": list(keywords), "legalities": legalities or {}, "layout": layout,
    }


def main():
    with tempfile.TemporaryDirectory() as temporary:
        db = CardDB(os.path.join(temporary, "cards.db"))
        db.load_cards([
            card("paper", "Trusted Goblin", "Legendary Creature — Goblin", "seta",
                 "expansion", keywords=("Lifelink",), rarity="rare",
                 legalities={"modern": "legal", "standard": "not_legal",
                             "vintage": "restricted", "legacy": "banned"}),
            card("digital", "Arena Equipment", "Snow Artifact — Equipment", "setb",
                 "alchemy", games=("arena",), keywords=("Ward",), rarity="mythic",
                 legalities={"alchemy": "legal"}),
            card("token", "Goblin Token", "Token Creature — Goblin", "tset", "token",
                 keywords=(), layout="token"),
            card("emblem", "Test Emblem", "Emblem", "tset", "token", layout="emblem"),
            card("art", "Trusted Art Series", "Card", "aset", "memorabilia",
                 layout="art_series"),
            card("plane", "Ravnica", "Plane — Ravnica", "pln", "planechase"),
            card("novelty", "Pig Latin", "Eaturecray — Igpay", "fun", "funny"),
        ])
        db.store_catalogs({
            "card-types": ["Artifact", "Creature", "Plane"],
            "artifact-types": ["Equipment"],
            "battle-types": [],
            "creature-types": ["Goblin"],
            "enchantment-types": [], "land-types": [],
            "planeswalker-types": [], "spell-types": [],
            "keyword-abilities": ["Lifelink", "Ward"],
            "keyword-actions": [], "ability-words": [],
        })
        db.set_meta(
            RULES_SUPERTYPES_META_KEY,
            json.dumps(["basic", "legendary", "ongoing", "snow", "world"]))
        # A legacy/broader Scryfall-style cache must have no authority over the
        # Supertype picker after the Wizards rules redesign.
        db.set_meta(
            "catalog:supertypes",
            json.dumps(["Basic", "Legendary", "Snow", "Token", "Elite", "Host"]))

        constants_source = (ROOT / "mtgdb/database/constants.py").read_text(encoding="utf-8")
        taxonomy_source = (ROOT / "mtgdb/database/taxonomy.py").read_text(encoding="utf-8")

        paper_cards = ("card",)

        # The snapshot carries both the playable list every screen reads and
        # the per-legality lists the Format picker offers. They come from one
        # scan: asking the database twice for the same answer cost a quarter
        # of the cold catalog load.
        catalogs = SearchCatalogController(SearchRepository(db))
        try:
            loaded_base = catalogs._load_base(paper_cards, True, ("paper",))
        finally:
            catalogs.shutdown()
        one_scan_serves_both_format_lists = (
            list(loaded_base["formats"]) == ["modern", "vintage"]
            and tuple(loaded_base["formats"])
            == loaded_base["formats_by_status"]["playable"]
            and "self.repository.formats(" not in
            (ROOT / "mtgdb/search/catalogs.py").read_text(encoding="utf-8"))

        # DATA-004 / DATA-005 fail-closed. The rows above are identical, but no
        # Scryfall catalog and no verified Wizards Supertype parse exist. Every
        # picker vocabulary must be empty rather than falling back to names the
        # application invented. Without this, replacing either "return []" with
        # a hardcoded list passes every gate -- and "never invent vocabulary" is
        # the claim the whole taxonomy architecture rests on.
        starved = CardDB(os.path.join(temporary, "starved.db"))
        starved.load_cards([
            card("paper", "Trusted Goblin", "Legendary Creature — Goblin",
                 "seta", "expansion", keywords=("Lifelink",), rarity="rare",
                 legalities={"modern": "legal"}),
        ])
        starved_vocabulary = {
            "card_types": starved.card_types(paper_cards, True),
            "supertypes": starved.supertypes(paper_cards, True),
            "subtypes": starved.subtypes(paper_cards, True),
            "mechanics": starved.keyword_catalog(paper_cards, True),
        }
        # Sets and raw row keywords come from observed data rather than
        # catalogs, so they stay available. That is the positive control: the
        # emptiness above is missing upstream authority, not an empty database.
        starved_still_observes_rows = (
            starved.count() == 1
            and {code for code, _ in starved.sets(None, paper_cards, True)}
                == {"seta"}
            and starved.keywords(paper_cards, True) == ["Lifelink"])
        starved.close()
        checks = {
            "content classifier has no inferred supplemental bucket": (
                _card_content_kind("normal", "Plane — Ravnica") == "card"
                and _card_content_kind("token", "Token Creature — Goblin") == "token"
                and _card_content_kind("emblem", "Emblem") == "emblem"
                and _card_content_kind("art_series", "Card") == "art"),
            "ordinary card scope excludes token and emblem rows": (
                {row["id"] for row in db.search(content_types=["card"])}
                == {"paper", "digital", "plane", "novelty"}),
            "token emblem and Art Series content are independently exact": (
                [row["id"] for row in db.search(content_types=["token"])] == ["token"]
                and [row["id"] for row in db.search(content_types=["emblem"])] == ["emblem"]
                and [row["id"] for row in db.search(content_types=["art"])] == ["art"]),
            "fully broadened content reaches every imported row": (
                len(db.search(content_types=["card", "token", "emblem", "art"]))
                == db.count() == 7),
            "Art Series Printings scope is data-driven and unions with Cards": (
                {value for value, _ in db.set_types(("art",), True)} == {"memorabilia"}
                and {code for code, _ in db.sets(None, ("art",), True)} == {"aset"}
                and {code for code, _ in db.sets(None, ("card", "art"), True)}
                    == {"seta", "aset", "pln", "fun"}),
            "missing upstream authority yields no invented vocabulary": (
                all(value == [] for value in starved_vocabulary.values())
                and starved_still_observes_rows),
            "Card Type vocabulary is catalog-authorized and observed": (
                set(db.card_types(paper_cards, True)) == {"Creature", "Plane"}
                and "Eaturecray" not in db.card_types(paper_cards, True)),
            "Supertypes are Wizards-rules constrained and locally observed": (
                db.supertypes(paper_cards, True) == ["Legendary"]
                and set(db.supertypes(paper_cards, False)) == {"Legendary", "Snow"}
                and db.supertypes(("token",), True) == []
                and not ({"Token", "Elite", "Host"} & set(db.supertypes(None, False)))),
            "Subtypes are catalog intersect local scope": (
                dict(db.subtype_catalog(paper_cards, True)) == {"Goblin": "Creature"}
                and "Igpay" not in db.subtypes(paper_cards, True)
                and "Ravnica" not in db.subtypes(paper_cards, True)),
            "Mechanics are card keywords intersect Scryfall catalogs": (
                db.keyword_catalog(paper_cards, True) == [("Lifelink", "Keyword ability")]
                and set(value for value, _ in db.keyword_catalog(paper_cards, False))
                    == {"Lifelink", "Ward"}),
            "Formats list only playable locally backed values": (
                db.formats(paper_cards, True) == ["modern", "vintage"]
                and set(db.formats(paper_cards, False))
                    == {"modern", "vintage", "alchemy"}
                and "standard" not in db.formats(paper_cards, True)),
            "each legality state lists only the formats that can satisfy it": (
                # The Format picker offers one list per legality. Offering
                # every format under Restricted offered a guaranteed-empty
                # search, since almost no format restricts anything.
                db.formats_by_status(paper_cards, True)
                == {"playable": ("modern", "vintage"),
                    "banned": ("legacy",),
                    "restricted": ("vintage",)}
                # An explicit platform replaces the paper scope, so Arena
                # offers Arena's formats and none of paper's.
                and db.formats_by_status(paper_cards, True, games=("arena",))
                    == {"playable": ("alchemy",), "banned": (),
                        "restricted": ()}),
            "one legality scan serves every format list": (
                one_scan_serves_both_format_lists),
            "Format picker has no hardcoded preferred vocabulary": (
                "FORMATS =" not in constants_source
                and "FORMATS" not in taxonomy_source),
            "set type vocabulary is observed and paper scoped": (
                {value for value, _ in db.set_types(paper_cards, True)}
                    == {"expansion", "planechase", "funny"}
                and {value for value, _ in db.set_types(paper_cards, False)}
                    == {"expansion", "alchemy", "planechase", "funny"}),
            "exact sets are observed independently of set type": (
                {code for code, _ in db.sets(None, paper_cards, True)}
                    == {"seta", "pln", "fun"}),
            "paper-only filtering uses Scryfall games metadata": (
                not db.search(set_codes=["setb"], paper_only=True, content_types=["card"])
                and [row["id"] for row in db.search(
                    set_codes=["setb"], paper_only=False, content_types=["card"])]
                    == ["digital"]),
            "deck resolver honors shared Paper and English scope": (
                db.get_by_name("Arena Equipment", paper_only=True) is None
                and (db.get_by_name(
                    "Arena Equipment", paper_only=False) or {}).get("id") == "digital"
                and db.get_by_name("Trusted Goblin", lang="fr") is None
                and (db.get_by_name(
                    "Trusted Goblin", lang="en") or {}).get("id") == "paper"),
            "set and set-type filters intersect rather than imply each other": (
                [row["id"] for row in db.search(
                    set_codes=["seta"], set_types=["expansion"], content_types=["card"])]
                    == ["paper"]
                and not db.search(
                    set_codes=["seta"], set_types=["funny"], content_types=["card"])),
            "SearchCriteria exposes current supertype and paper contracts": (
                SearchCriteria.from_mapping({
                    "supertypes": ["Legendary"], "paper_only": True,
                    "content_types": ["card"],
                }).query_arguments()["supertypes"] == ["Legendary"]),
            "trusted Supertypes are searchable through canonical Search SQL": (
                [row["id"] for row in db.search(
                    supertypes=["Legendary"], content_types=["card"], paper_only=True)]
                    == ["paper"]),
        }
        db.close()

    search_source = (ROOT / "mtgdb/ui/search.py").read_text(encoding="utf-8")
    printing_source = (ROOT / "mtgdb/ui/search_printings.py").read_text(encoding="utf-8")
    catalog_source = (ROOT / "mtgdb/search/catalogs.py").read_text(encoding="utf-8")
    set_source = (ROOT / "mtgdb/ui/set_filters.py").read_text(encoding="utf-8")
    combined = search_source + printing_source + set_source
    checks.update({
        "Search has no More Types or Characteristics UI": (
            "More Types" not in search_source and "Characteristics" not in search_source),
        "Abilities are presented as Mechanics": (
            FILTER_BY_KEY["mechanics"]["label"] == "Mechanics"
            and "Choose Abilities" not in search_source
            and "Abilities" not in FILTER_BY_KEY["mechanics"]["tooltip"]),
        "trusted type filters expose explicit authority failures": (
            FILTER_BY_KEY["supertypes"]["label"] == "Supertypes"
            # The control itself, not a summary string: the old assertion
            # named text that lived only in a dead code path.
            and "def _build_filter_supertypes(" in search_source
            and '"Selected supertypes:"' in search_source
            and not any(entry["label"] == "Properties"
                        for entry in FILTER_DEFINITIONS)
            and '"Properties: "' not in search_source
            and "Official Wizards Supertype taxonomy is unavailable" in search_source
            and "Scryfall Card Type taxonomy is unavailable" in search_source
            and "supertype_taxonomy_status" in catalog_source
            and "card_type_taxonomy_status" in catalog_source
            and "Last error:" in search_source),
        "taxonomy code has no hardcoded Supertype picker vocabulary": (
            "SUPERTYPES =" not in (ROOT / "mtgdb/database/constants.py").read_text(
                encoding="utf-8")
            and 'self.catalog("supertypes")' not in (
                ROOT / "mtgdb/database/taxonomy.py").read_text(encoding="utf-8")),

        "a standard set on the form, everything else behind one button": (
            # The Add filter menu made a real search several menu trips before
            # it could be run, and re-added the same filters every session.
            "def _build_advanced_filter_zone(" in search_source
            and "_add_filter_menu" not in search_source
            and "def _add_optional_filter(" not in search_source
            and {"rules_text", "subtype", "format", "rarity"}
            <= set(FILTER_BY_KEY)
            # Content is expressed as Card traits now, not its own filter.
            and "content" not in FILTER_BY_KEY
            and "printings" not in FILTER_BY_KEY),
        "advanced filter labels match primary Search field typography": (
            'label = ttk.Label(frame, text=entry["label"])' in search_source
            and 'printings_label = ttk.Label(parent, text="Printings")'
            in printing_source
            and 'text="Content", style="Section.TLabel"' not in search_source
            and 'text="Subtype", style="Section.TLabel"' not in search_source
            and 'text="Format", style="Section.TLabel"' not in search_source
            and 'text="Rarity", style="Section.TLabel"' not in search_source
            and 'text="Printings", style="Section.TLabel"' not in printing_source),
        "content kinds are chosen through Card traits": (
            '"include_tokens": "token"' in search_source
            and '"include_emblems": "emblem"' in search_source
            and '"include_art_series": "art"' in search_source
            and '("include_art_series", "Include Art Series")' in search_source
            # Cards are always searched; the other kinds are opt-in traits, so
            # Art Series stays out until explicitly asked for (DATA-008).
            and 'kinds = {"card"}' in search_source
            and "Supplemental" not in search_source),
        "Printings default to Paper only and Any set": (
            'text="Paper only · Any set type · Any set"' in printing_source
            and "SET_TYPE_DEFAULT_ON" not in combined
            and "SET_TYPE_GROUPS" not in combined
            and "Recommended" not in combined),
        "Printings Exact Set picker cascades from selected Set Types in place": (
            "def _on_set_type_change(" in set_source
            and "allowed_types = sorted(self.selected_set_types()) or None" in set_source
            and "self.repository.sets(" in set_source
            and "self._refresh_exact_set_catalog(selected_codes)" in set_source
            and "after_idle(self._show_popup)" not in set_source),
        "set-type UI is flat observed vocabulary only": (
            "def _build_set_type_controls(" in set_source
            and "sorted({str(value) for value in present" in set_source
            and "SET_TYPE_GROUPS" not in set_source
            and "SET_TYPE_DEFAULT_ON" not in set_source),
        "Search Format Any is the explicit empty radio value": (
            '[("", "Any")] + [' in search_source
            and 'selected = {current}' in search_source
            and 'value = "" if "" in normalized else next(iter(normalized), "")'
                in search_source
            and 'single_select=True' in search_source),
        "Mechanics and Subtype use the requested concise picker instructions": (
            'help_text="Choose one or several card mechanics."' in search_source
            and 'help_text="Choose one or several card subtypes."' in search_source),
        "Search Printings has no title-level instruction while shared popup supports it": (
            'intro_text=""' in printing_source
            and 'if self._intro_text:' in set_source
            and 'if intro_text is None else intro_text' in set_source),
        "workspace restoration cannot insert taxonomy vocabulary": (
            'set(pending["subtypes"]).intersection(valid_subtypes)' in search_source
            and 'set(pending["keywords"]).intersection(valid_keywords)' in search_source
            and 'set(pending["rarities"]).intersection(' in search_source
            and "selected_types=selected_types" in set_source
            and "selected_codes=selected_codes" in set_source),
    })

    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok &= bool(passed)
    print("\nTRUSTED FILTER CONTRACT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
