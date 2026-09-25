"""Pure deck statistics, probability, curve, mana, and hand calculations."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import re


_TYPE_ORDER = (
    ("Land", "Lands"),
    ("Creature", "Creatures"),
    ("Planeswalker", "Planeswalkers"),
    ("Battle", "Battles"),
    ("Instant", "Instants"),
    ("Sorcery", "Sorceries"),
    ("Artifact", "Artifacts"),
    ("Enchantment", "Enchantments"),
)
_PIP_RE = re.compile(r"\{([^}]+)\}")
_COLOR_LETTERS = ("W", "U", "B", "R", "G")



@dataclass(frozen=True)
class DeckAnalysisSnapshot:
    generation: int
    stats: dict
    average_mana_value: float
    color_pips: dict
    color_sources: dict
    mana_source_cards: int
    opening_land_stats: tuple


def analyze_deck(deck):
    """Compute the common stats-panel aggregates in one mainboard traversal."""
    curve = [0] * 8
    colors = {color: 0 for color in _COLOR_LETTERS}
    pips = {color: 0 for color in _COLOR_LETTERS}
    sources = {color: 0 for color in _COLOR_LETTERS}
    colorless = 0
    types = {}
    main_total = 0
    main_unique = 0
    lands = 0
    total_mana_value = 0.0
    spell_count = 0
    mana_source_cards = 0
    for entry in deck.iter_entries("main"):
        main_unique += 1
        card, quantity = entry["card"], int(entry["qty"])
        main_total += quantity
        type_line = card.get("type_line", "") or ""
        label = classify_type(type_line)
        types[label] = types.get(label, 0) + quantity
        cost = card.get("mana_cost") or ""
        for token in _PIP_RE.findall(cost):
            for color in _COLOR_LETTERS:
                if color in token:
                    pips[color] += quantity
        produced = card.get("produced_mana") or ""
        if isinstance(produced, list):
            produced = ",".join(produced)
        if produced:
            mana_source_cards += quantity
            for color in _COLOR_LETTERS:
                if color in produced:
                    sources[color] += quantity
        if is_land(card):
            lands += quantity
            continue
        mana_value = float(card.get("cmc") or 0)
        total_mana_value += mana_value * quantity
        spell_count += quantity
        curve[min(int(mana_value), 7)] += quantity
        identity = color_identity(card)
        if identity:
            for color in identity:
                if color in colors:
                    colors[color] += quantity
        else:
            colorless += quantity
    side_total = deck.total("side")
    average = total_mana_value / spell_count if spell_count else 0.0
    land_average, land_probability = _land_odds(main_total, lands)
    stats = {
        "curve": curve, "colors": colors, "colorless": colorless,
        "types": types, "main_total": main_total, "side_total": side_total,
        "main_unique": main_unique,
    }
    return DeckAnalysisSnapshot(
        generation=getattr(deck, "generation", 0), stats=stats,
        average_mana_value=average, color_pips=pips, color_sources=sources,
        mana_source_cards=mana_source_cards,
        opening_land_stats=(main_total, lands, land_average, land_probability),
    )

def front_face(type_line):
    """Return the type line of the face a card is played from.

    A double-faced card stores both faces in one string, so a card whose back
    is a land reads as "Sorcery // Land" or "Legendary Enchantment //
    Legendary Land". Deck statistics ask what a card is while it sits in your
    hand, and only the front face answers that: Growing Rites of Itlimoc is a
    three-mana enchantment that can never be played as a land, however its
    back face reads. Matching the whole string counted 82 cards -- every
    transforming permanent with a land back, and every Zendikar Rising modal
    card -- as lands, which removed them from the curve and inflated the
    opening-hand land figures.
    """
    value = type_line or ""
    head, separator, _back = value.partition("//")
    return head.strip() if separator else value


def is_land(card):
    """Return whether a card occupies a land slot in deck statistics."""
    return "Land" in front_face(card.get("type_line"))


def color_identity(card):
    value = card.get("color_identity") or ""
    if isinstance(value, list):
        return [color for color in value if color]
    return [color for color in value.split(",") if color]


def classify_type(type_line):
    value = front_face(type_line)
    for needle, label in _TYPE_ORDER:
        if needle in value:
            return label
    return "Other"


def deck_stats(deck):
    """Return the legacy aggregate statistics mapping for one deck."""
    curve = [0] * 8
    colors = {color: 0 for color in _COLOR_LETTERS}
    colorless = 0
    types = {}
    for entry in deck.iter_entries("main"):
        card, quantity = entry["card"], entry["qty"]
        type_line = card.get("type_line") or ""
        label = classify_type(type_line)
        types[label] = types.get(label, 0) + quantity
        if is_land(card):
            continue
        # float() first: a mana value may arrive as "3.0" from a JSON round
        # trip, and int("3.0") raises where int(float("3.0")) does not.
        mana_value = float(card.get("cmc") or 0)
        curve[min(int(mana_value), 7)] += quantity
        identity = color_identity(card)
        if identity:
            for color in identity:
                if color in colors:
                    colors[color] += quantity
        else:
            colorless += quantity
    return {
        "curve": curve,
        "colors": colors,
        "colorless": colorless,
        "types": types,
        "main_total": deck.total("main"),
        "side_total": deck.total("side"),
        "main_unique": deck.unique("main"),
    }


def average_mana_value(deck):
    """Return the quantity-weighted mana value of mainboard nonland cards."""
    total_mana_value = 0.0
    spell_count = 0
    for entry in deck.iter_entries("main"):
        card = entry["card"]
        if is_land(card):
            continue
        total_mana_value += float(card.get("cmc") or 0) * entry["qty"]
        spell_count += entry["qty"]
    return total_mana_value / spell_count if spell_count else 0.0


def card_draw_odds(deck, card_name):
    """Return mainboard copy counts and legacy opening/draw probabilities."""
    deck_size = deck.total("main")
    copies = sum(
        entry["qty"] for entry in deck.iter_entries("main")
        if entry["card"].get("name") == card_name
    )
    return {
        "deck_size": deck_size,
        "copies": copies,
        "opening": hyper_at_least(deck_size, copies, min(7, deck_size)),
        "turn_3": hyper_at_least(deck_size, copies, min(9, deck_size)),
        "turn_6": hyper_at_least(deck_size, copies, min(12, deck_size)),
    }


def color_pips(deck):
    """Return colored symbols across mainboard casting costs by quantity."""
    pips = {color: 0 for color in _COLOR_LETTERS}
    for entry in deck.iter_entries("main"):
        cost = entry["card"].get("mana_cost") or ""
        for token in _PIP_RE.findall(cost):
            for color in _COLOR_LETTERS:
                if color in token:
                    pips[color] += entry["qty"]
    return pips


def color_sources(deck):
    """Return colored mana-source counts and all producing-card quantities."""
    sources = {color: 0 for color in _COLOR_LETTERS}
    any_source = 0
    for entry in deck.iter_entries("main"):
        produced = entry["card"].get("produced_mana") or ""
        if isinstance(produced, list):
            produced = ",".join(produced)
        if produced:
            any_source += entry["qty"]
            for color in _COLOR_LETTERS:
                if color in produced:
                    sources[color] += entry["qty"]
    return sources, any_source


def hyper_at_least(deck_size, copies, draws, want=1):
    """Return the probability of drawing at least ``want`` successes."""
    if deck_size <= 0 or copies <= 0 or draws <= 0:
        return 0.0
    draws = min(draws, deck_size)
    total = math.comb(deck_size, draws)
    misses = 0
    for count in range(0, min(want, copies + 1)):
        # count may exceed draws when a caller asks for more successes than it
        # draws; math.comb rejects the negative second term, so skip it here.
        if (count <= draws and copies >= count
                and deck_size - copies >= draws - count):
            misses += (
                math.comb(copies, count)
                * math.comb(deck_size - copies, draws - count))
    return 1.0 - misses / total


def hyper_between(deck_size, copies, draws, lower, upper):
    """Return the probability of drawing between two inclusive bounds."""
    if deck_size <= 0 or draws <= 0:
        return 0.0
    draws = min(draws, deck_size)
    total = math.comb(deck_size, draws)
    favorable = 0
    for count in range(lower, upper + 1):
        if (0 <= count <= copies and draws - count <= deck_size - copies
                and count <= draws):
            favorable += (
                math.comb(copies, count)
                * math.comb(deck_size - copies, draws - count))
    return favorable / total


def _land_odds(deck_size, lands):
    """Return (average lands in an opening 7, P(two to four lands)).

    The one shared formula behind both analyze_deck's inline opening-hand
    figures (computed from its own single mainboard traversal) and
    opening_land_stats below (which does its own separate traversal) --
    kept in one place so a future change to the draw-count clamp or
    probability bounds cannot land in one copy and not the other.
    """
    if deck_size <= 0:
        return 0.0, 0.0
    draws = min(7, deck_size)
    average = draws * lands / deck_size
    probability = hyper_between(deck_size, lands, draws, 2, 4)
    return average, probability


def opening_land_stats(deck):
    """Return size, lands, average lands in seven, and P(two to four lands)."""
    deck_size = deck.total("main")
    lands = sum(
        entry["qty"] for entry in deck.iter_entries("main")
        if is_land(entry["card"]))
    if deck_size == 0:
        return 0, 0, 0.0, 0.0
    average, probability = _land_odds(deck_size, lands)
    return deck_size, lands, average, probability


def sample_hand(deck, n=7):
    """Return a random mainboard hand as a list of card mappings."""
    pool = []
    for entry in deck.iter_entries("main"):
        pool.extend([entry["card"]] * entry["qty"])
    if not pool:
        return []
    return random.sample(pool, min(n, len(pool)))


def curve_breakdown(deck, mode):
    """Return labels and eight nonland mana-value buckets by type or color."""
    if mode == "type":
        labels = [
            "Creatures", "Instants", "Sorceries", "Artifacts",
            "Enchantments", "Planeswalkers", "Battles", "Other"]

        def segment(card):
            label = classify_type(card.get("type_line", ""))
            return label if label in labels else "Other"
    else:
        labels = ["W", "U", "B", "R", "G", "Multi", "Colorless"]

        def segment(card):
            identity = color_identity(card)
            if not identity:
                return "Colorless"
            if len(identity) > 1:
                return "Multi"
            return identity[0]

    buckets = [{label: 0 for label in labels} for _ in range(8)]
    for entry in deck.iter_entries("main"):
        card = entry["card"]
        if is_land(card):
            continue
        bucket = min(int(float(card.get("cmc") or 0)), 7)
        buckets[bucket][segment(card)] += entry["qty"]
    return labels, buckets
