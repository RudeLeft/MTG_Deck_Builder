"""Declarative upstream taxonomy authorities and compatibility metadata keys.

This module defines which *upstream sources* the application understands.  It
never defines the values allowed inside those sources: Card Types, Subtypes,
Mechanics, Sets, Set Types, Formats, and Rarities remain data-driven.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogAuthority:
    """One Scryfall catalog whose semantics are understood by this build."""

    name: str
    role: str
    label: str
    applies_to: tuple[str, ...] = ()


CATALOG_AUTHORITIES = (
    CatalogAuthority("card-types", "card_type", "Card type"),
    CatalogAuthority("artifact-types", "subtype", "Artifact", ("Artifact",)),
    CatalogAuthority("battle-types", "subtype", "Battle", ("Battle",)),
    CatalogAuthority("creature-types", "subtype", "Creature", ("Creature",)),
    CatalogAuthority("enchantment-types", "subtype", "Enchantment", ("Enchantment",)),
    CatalogAuthority("land-types", "subtype", "Land", ("Land",)),
    CatalogAuthority("planeswalker-types", "subtype", "Planeswalker", ("Planeswalker",)),
    CatalogAuthority("spell-types", "subtype", "Spell", ("Instant", "Sorcery")),
    CatalogAuthority("keyword-abilities", "mechanic", "Keyword ability"),
    CatalogAuthority("keyword-actions", "mechanic", "Keyword action"),
    CatalogAuthority("ability-words", "mechanic", "Ability word"),
)

SCRYFALL_CATALOGS = {authority.name: authority.label for authority in CATALOG_AUTHORITIES}
SUBTYPE_AUTHORITIES = tuple(
    authority for authority in CATALOG_AUTHORITIES if authority.role == "subtype")
MECHANIC_AUTHORITIES = tuple(
    authority for authority in CATALOG_AUTHORITIES if authority.role == "mechanic")

# Internal-only upstream compatibility metadata.  These values are diagnostics,
# not picker vocabulary and not a reason to reject a database refresh.
UPSTREAM_FINGERPRINT_META_KEY = "compatibility:upstream_fingerprint"
UPSTREAM_REPORT_META_KEY = "compatibility:last_report"
