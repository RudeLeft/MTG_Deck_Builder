"""Immutable contracts shared by the search UI, controller, and repository."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping



def _tuple(value):
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(value, key=lambda item: str(item).casefold()))
    return tuple(value)


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    """Complete, Tk-free description of one semantic card search."""

    name: str = ""
    names: tuple[str, ...] = ()
    text: tuple[str, ...] = ()
    text_mode: str = "all"
    card_types: tuple[str, ...] = ()
    card_type_mode: str = "any"
    supertypes: tuple[str, ...] = ()
    supertype_mode: str = "all"
    subtypes: tuple[str, ...] = ()
    subtype_mode: str = "any"
    keywords: tuple[str, ...] = ()
    keyword_mode: str = "any"
    colors: tuple[str, ...] = ()
    color_mode: str = "within"
    color_scope: str = "identity"
    produces: tuple[str, ...] = ()
    produces_mode: str = "includes"
    traits: tuple[str, ...] = ()
    trait_mode: str = "any"
    cmc_min: float | None = None
    cmc_max: float | None = None
    power_min: float | None = None
    power_max: float | None = None
    toughness_min: float | None = None
    toughness_max: float | None = None
    loyalty_min: float | None = None
    loyalty_max: float | None = None
    defense_min: float | None = None
    defense_max: float | None = None
    released_from: float | None = None
    released_to: float | None = None
    games: tuple[str, ...] = ()
    rarities: tuple[str, ...] = ()
    fmt: str = ""
    fmt_status: str = "playable"
    set_codes: tuple[str, ...] | None = None
    set_types: tuple[str, ...] | None = None
    lang: str = ""
    paper_only: bool = False
    content_types: tuple[str, ...] = ("card",)

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SearchCriteria":
        """Normalize the GUI snapshot without changing search semantics."""
        data = dict(values)
        tuple_fields = {
            "names", "text", "card_types", "supertypes", "subtypes", "keywords",
            "colors", "produces", "traits", "rarities", "games",
            "content_types",
        }
        optional_tuple_fields = {"set_codes", "set_types"}
        for name in tuple_fields:
            data[name] = _tuple(data.get(name))
        for name in optional_tuple_fields:
            value = data.get(name)
            data[name] = None if value is None else _tuple(value)
        allowed = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})

    def signature(self):
        """Stable immutable cache key for these semantic criteria."""
        return tuple((field.name, getattr(self, field.name)) for field in fields(self))

    def query_arguments(self):
        """Return legacy-compatible arguments for ``CardDB.search``."""
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        for name in (
                "names", "text", "card_types", "supertypes", "subtypes", "keywords",
                "colors", "produces", "traits", "rarities", "games",
                "content_types"):
            values[name] = list(values[name])
        for name in ("set_codes", "set_types"):
            if values[name] is not None:
                values[name] = list(values[name])
        return values


@dataclass(frozen=True, slots=True)
class SearchEvent:
    """One terminal worker event delivered without any Tk dependency."""

    kind: str
    generation: int
    signature: tuple
    payload: object
    elapsed: float = 0.0


@dataclass(frozen=True, slots=True)
class SearchStart:
    """Immediate outcome from requesting a search."""

    kind: str
    generation: int
    signature: tuple
    results: object = ()
