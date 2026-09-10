"""Exact-printing lookup, import resolution, and name suggestions."""

import re

from mtgdb.database.constants import ART_LAYOUTS, NON_CARD_LAYOUTS


# --- decklist name normalization -------------------------------------------
# Moxfield / Arena / MTGO exports write multi-face names several ways
# ("A // B", "A / B", "A/B", or just the front face "A"), and the Arena format
# appends "(SET) 123" plus foil markers. These helpers build the exact-name
# candidates to try, most-specific first.
_ARENA_SUFFIX_RE = re.compile(
    r"\s*\([A-Za-z0-9]{2,6}\)\s+[\w-]+(?:\s*\*[A-Za-z]+\*)?\s*$")
_SLASH_RE = re.compile(r"\s*/+\s*")
# Trailing site tags in square brackets, e.g. "[EVG:1]" or "[foil]" (mtg.wtf and
# similar). Card names never contain square brackets, so a trailing run of these
# is always a vendor annotation, never part of the real name.
_BRACKET_TAGS_RE = re.compile(r"(?:\s*\[[^\]]*\])+\s*$")

# Import ranking is intentionally metadata-driven rather than maintained as a
# crossover set-code blacklist. Universes Beyond is derived set-wide after each
# bulk load from Scryfall's triangle security-stamp metadata, so commons and
# uncommons in the same crossover set inherit the classification even when they
# do not carry a security stamp themselves.

# Product tiers used only for TXT deck import resolution. Search/manual card
# selection is unaffected. Lower values are preferred.
_PRIMARY_MAGIC_SET_TYPES = ("core", "expansion", "masters", "draft_innovation")
_SUPPLEMENTAL_MAGIC_SET_TYPES = (
    "commander", "starter", "duel_deck", "premium_deck", "arsenal",
    "from_the_vault", "spellbook", "box", "planechase", "archenemy",
)
# A collector number carrying another set's prefix ("CLB-187", "OTC-280")
# marks a booster insert: physically a reprint of that other set's card,
# packaged into a different product. The List is the whole of this in practice
# -- 5,584 cards, every one prefixed, continuously updated, and typed by
# Scryfall as "masters", which would otherwise make it a main Magic release
# that wins on recency for anything it has ever carried. Among paper printings
# the marker is 99.8% The List; the remainder are Secret Lair and promo cards
# that this rule deliberately leaves alone, because it only ever demotes a
# printing that would otherwise rank as a main release.
_BOOSTER_INSERT_COLLECTOR = "collector_number GLOB '*[A-Z]*-*'"

_SPECIAL_SET_TYPES = (
    "promo", "masterpiece", "funny", "memorabilia", "token", "alchemy",
    "treasure_chest", "minigame", "vanguard",
)


def _name_candidates(name):
    cands = []

    def add(n):
        n = (n or "").strip()
        if n and n not in cands:
            cands.append(n)

    add(name)
    add(_ARENA_SUFFIX_RE.sub("", name))          # strip "(SET) 123 [*F*]"
    # Also accept trailing "[SET:123]" / "[foil]" tags that some sites append.
    bracketless = _BRACKET_TAGS_RE.sub("", name)
    add(bracketless)
    add(_ARENA_SUFFIX_RE.sub("", bracketless))   # tolerate both styles together
    for base in list(cands):
        if "/" in base:
            add(_SLASH_RE.sub(" // ", base))     # "A / B" or "A/B" -> "A // B"
    return cands


class CardQueryMixin:
    """Provide stable exact-card lookup, import resolution, and suggestions."""

    def get_card(self, card_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(row) if row else None

    def get_cards(self, card_ids):
        """Batch exact-printing hydration while preserving caller order."""
        ordered = [str(card_id) for card_id in card_ids if card_id]
        if not ordered:
            return []
        unique = list(dict.fromkeys(ordered))
        found = {}
        # Stay below conservative SQLite variable limits for general callers.
        for start in range(0, len(unique), 800):
            chunk = unique[start:start + 800]
            placeholders = ",".join("?" for _ in chunk)
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT * FROM cards WHERE id IN ({placeholders})", chunk
                ).fetchall()
            found.update((str(row["id"]), dict(row)) for row in rows)
        return [found.get(card_id) for card_id in ordered]

    def get_by_name(self, name, allowed_set_types=None, allowed_set_codes=None,
                    allowed_collector_numbers=None, *, paper_only=True, lang=None):
        """
        Resolve an imported decklist name to the best conventional printing
        available in the local database within the requested printings scope.

        Import preference hierarchy (manual Search selection is unaffected):
          1. Main Magic releases: core / expansion / masters / draft innovation.
          2. Normal Magic supplemental products: Commander, Duel Decks, starter,
             Planechase, From the Vault, etc.
          3. Other in-universe paper printings.
          4. Special/promotional products (Secret Lair, promos, funny, etc.).
          5. Universes Beyond / crossover sets.

        Within the best available tier, the newest release date wins and base
        treatments are preferred over promo/showcase/extended-art treatments.
        The ranking is soft: if a card exists only in a special or crossover
        product, it still imports rather than becoming unresolvable. Basic lands
        and split/DFC/Room front-face lookups use the same rules.
        allowed_set_types, when supplied, restricts candidates to those
        Scryfall set_type values before this ranking is applied.
        allowed_set_codes, when supplied, further restricts candidates to the
        exact selected sets.
        allowed_collector_numbers, when supplied, preserves an exact printing
        even when two physical cards share a name and set (notably B.F.M.).
        ``paper_only`` and ``lang`` apply to untagged-card resolution; callers
        may disable them for an explicit authoritative [SET] tag.
        """
        placeholders = ",".join("?" * len(NON_CARD_LAYOUTS))
        importable = f"(layout IS NULL OR layout NOT IN ({placeholders}))"
        import_params = list(NON_CARD_LAYOUTS)
        if paper_only:
            importable += " AND paper = 1"
        if lang:
            importable += " AND lang = ? COLLATE NOCASE"
            import_params.append(str(lang))

        # Deck import can optionally constrain resolution to the set types the
        # user selected in the import picker. None preserves the normal resolver.
        if allowed_set_types is not None:
            allowed = sorted({str(st) for st in allowed_set_types if st})
            if not allowed:
                return None
            allowed_ph = ",".join("?" * len(allowed))
            importable += f" AND set_type IN ({allowed_ph})"
            import_params.extend(allowed)

        if allowed_set_codes is not None:
            allowed_codes = sorted({str(code).lower()
                                    for code in allowed_set_codes if code})
            if not allowed_codes:
                return None
            codes_ph = ",".join("?" * len(allowed_codes))
            importable += f" AND lower(set_code) IN ({codes_ph})"
            import_params.extend(allowed_codes)

        if allowed_collector_numbers is not None:
            allowed_collectors = sorted({str(number) for number in
                                         allowed_collector_numbers
                                         if str(number)})
            if not allowed_collectors:
                return None
            collector_ph = ",".join("?" * len(allowed_collectors))
            importable += f" AND collector_number IN ({collector_ph})"
            import_params.extend(allowed_collectors)

        candidates = _name_candidates(name)

        primary = ",".join("'%s'" % t for t in _PRIMARY_MAGIC_SET_TYPES)
        supplemental = ",".join("'%s'" % t for t in _SUPPLEMENTAL_MAGIC_SET_TYPES)
        special = ",".join("'%s'" % t for t in _SPECIAL_SET_TYPES)

        # Tier is evaluated before release date on purpose. A 2025 conventional
        # Magic printing should beat a 2026 Secret Lair/crossover printing.
        product_tier = (
            "CASE "
            "WHEN universes_beyond = 1 THEN 4 "
            f"WHEN promo = 0 AND set_type IN ({primary}) "
            f"AND {_BOOSTER_INSERT_COLLECTOR} THEN 1 "
            f"WHEN promo = 0 AND set_type IN ({primary}) THEN 0 "
            f"WHEN promo = 0 AND set_type IN ({supplemental}) THEN 1 "
            f"WHEN set_type IN ({special}) OR promo = 1 THEN 3 "
            "ELSE 2 END"
        )

        # Prefer the ordinary/base treatment inside the chosen product tier.
        # Scryfall's frame_effects/promo_types arrays are stored as JSON text;
        # checking the known treatment labels avoids selecting showcase or
        # extended-art variants when a regular printing from the same release is
        # available. Collector number remains a deterministic final tie-breaker.
        treatment_rank = (
            "CASE WHEN promo = 1 "
            "OR frame_effects LIKE '%showcase%' "
            "OR frame_effects LIKE '%extendedart%' "
            "OR frame_effects LIKE '%inverted%' "
            "OR frame_effects LIKE '%etched%' "
            "OR COALESCE(promo_types, '[]') NOT IN ('[]','') "
            "THEN 1 ELSE 0 END"
        )

        newest_order = (
            f"{product_tier} ASC, "
            "released_at DESC, "
            f"{treatment_rank} ASC, "
            "set_code COLLATE NOCASE DESC, "
            "CAST(collector_number AS INTEGER) ASC, "
            "collector_number ASC, id ASC"
        )

        with self._lock:
            for cand in candidates:
                row = self.conn.execute(
                    f"SELECT * FROM cards WHERE name = ? COLLATE NOCASE "
                    f"AND {importable} "
                    f"ORDER BY {newest_order} LIMIT 1",
                    (cand, *import_params),
                ).fetchone()
                if row:
                    return dict(row)

            for cand in candidates:
                front = _SLASH_RE.split(cand)[0].strip()
                if not front:
                    continue
                row = self.conn.execute(
                    f"SELECT * FROM cards WHERE name LIKE ? COLLATE NOCASE "
                    f"AND {importable} "
                    f"ORDER BY {newest_order}, length(name) LIMIT 1",
                    (front + " //%", *import_params),
                ).fetchone()
                if row:
                    return dict(row)

        return None

    def name_suggestions(self, query, limit=20):
        """Ranked card-name suggestions for the editable Name field.

        Prefix matches come first because they are normally the most useful while
        typing. Once at least three characters are present, any remaining slots
        are filled with substring matches so autocomplete agrees with the main
        Name search, which itself accepts text anywhere in the card name.
        """
        query = (query or "").strip()
        if not query:
            return []

        limit = max(1, int(limit))
        placeholders = ",".join("?" * len(ART_LAYOUTS))
        visible = f"(layout IS NULL OR layout NOT IN ({placeholders}))"

        # Treat LIKE metacharacters as literal user input.
        escaped = (query.replace("\\", "\\\\")
                         .replace("%", "\\%")
                         .replace("_", "\\_"))
        prefix_pattern = escaped + "%"

        with self._lock:
            rows = self.conn.execute(
                f"SELECT DISTINCT name FROM cards "
                f"WHERE name LIKE ? ESCAPE '\\' AND {visible} "
                f"ORDER BY name COLLATE NOCASE LIMIT ?",
                (prefix_pattern, *ART_LAYOUTS, limit),
            ).fetchall()
            names = [r["name"] for r in rows]

            # A contains scan is more expensive, so only do it for meaningful
            # input and only when prefix matches have not already filled the list.
            if len(query) >= 3 and len(names) < limit:
                remaining = limit - len(names)
                contains_pattern = "%" + escaped + "%"
                rows = self.conn.execute(
                    f"SELECT DISTINCT name FROM cards "
                    f"WHERE name LIKE ? ESCAPE '\\' "
                    f"AND name NOT LIKE ? ESCAPE '\\' AND {visible} "
                    f"ORDER BY length(name), name COLLATE NOCASE LIMIT ?",
                    (contains_pattern, prefix_pattern, *ART_LAYOUTS, remaining),
                ).fetchall()
                names.extend(r["name"] for r in rows)

        return names
