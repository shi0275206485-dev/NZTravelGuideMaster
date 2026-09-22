"""Deterministic budget estimation.

Every figure the user sees comes from `budget_rules.json` via a lookup and
a multiplication — never from the LLM. This is what makes the budget
reproducible (the same trip always costs the same), auditable (the rules
are a readable file, not a black box), and cheap to recompute after an
edit without another model call (FR8).

Estimates are always ranges and are always labelled as estimates.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import (
    DESTINATION_CONFIG,
    Attraction,
    Budget,
    BudgetLevel,
    CostEstimate,
    DayPlan,
    Hotel,
)

RULES_PATH = Path(__file__).parent / "budget_rules.json"


@lru_cache
def load_rules() -> dict:
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def destination_tier(destination: str) -> str:
    """Tier as a string key, since JSON object keys are strings."""
    tier = DESTINATION_CONFIG.get(destination, {}).get("tier", 2)
    return str(tier)


def seasonal_multiplier(start: date) -> float:
    """Accommodation-only seasonal adjustment, keyed on the arrival month."""
    return load_rules()["seasonal_multipliers"].get(str(start.month), 1.0)


def accommodation_cost(
    destination: str,
    nights: int,
    hotel: Optional[Hotel],
    start_date: Optional[date] = None,
) -> CostEstimate:
    rules = load_rules()["accommodation_per_night"][destination_tier(destination)]

    if hotel is None:
        key = "hotel_default"
        label = "unspecified accommodation"
    elif hotel.accommodation_type == "hotel":
        key = f"hotel_{hotel.stars}" if hotel.stars and f"hotel_{hotel.stars}" in rules else "hotel_default"
        label = f"{hotel.stars}-star hotel" if hotel.stars else "hotel"
    else:
        key = hotel.accommodation_type
        label = hotel.accommodation_type.replace("_", " ")

    low, high = rules[key]
    multiplier = seasonal_multiplier(start_date) if start_date else 1.0
    season_note = f", seasonal x{multiplier:g}" if multiplier != 1.0 else ""

    return CostEstimate(
        low_nzd=round(low * nights * multiplier, 2),
        high_nzd=round(high * nights * multiplier, 2),
        basis=f"tier {destination_tier(destination)} {label}, "
              f"{nights} night{'s' if nights != 1 else ''}{season_note}",
    )


def meals_cost(destination: str, days: int, budget_level: BudgetLevel,
                travellers: int = 1) -> CostEstimate:
    low, high = load_rules()["meals_per_person_per_day"][destination_tier(destination)][budget_level]
    per = f" x {travellers} travellers" if travellers != 1 else ""
    return CostEstimate(
        low_nzd=round(low * days * travellers, 2),
        high_nzd=round(high * days * travellers, 2),
        basis=f"{budget_level.replace('_', ' ')} dining, {days} day{'s' if days != 1 else ''}{per}",
    )


def transport_cost(destination: str, days: int) -> CostEstimate:
    low, high = load_rules()["transport_per_day"][destination_tier(destination)]
    return CostEstimate(
        low_nzd=round(low * days, 2),
        high_nzd=round(high * days, 2),
        basis=f"local transport within {destination}, {days} day{'s' if days != 1 else ''}",
    )


def attraction_cost(attractions: list[Attraction], travellers: int = 1) -> CostEstimate:
    """Sum per-category entry estimates across every scheduled attraction."""
    rules = load_rules()["attraction_entry"]
    low = high = 0.0
    free = 0
    for a in attractions:
        a_low, a_high = rules.get(a.category, rules["other"])
        if a_high == 0:
            free += 1
        low += a_low
        high += a_high
    paid = len(attractions) - free
    return CostEstimate(
        low_nzd=round(low * travellers, 2),
        high_nzd=round(high * travellers, 2),
        basis=f"{paid} paid + {free} free attraction{'s' if len(attractions) != 1 else ''}"
              + (f" x {travellers} travellers" if travellers != 1 else ""),
    )


def estimate_attraction_entry(attraction: Attraction) -> CostEstimate:
    """Per-attraction estimate, for display on an itinerary card."""
    rules = load_rules()["attraction_entry"]
    low, high = rules.get(attraction.category, rules["other"])
    return CostEstimate(
        low_nzd=float(low), high_nzd=float(high),
        basis=f"typical {attraction.category} entry" if high else "free entry",
    )


def estimate_hotel_nightly(destination: str, hotel: Hotel,
                            start_date: Optional[date] = None) -> CostEstimate:
    """Per-night estimate, for display on an accommodation card."""
    return accommodation_cost(destination, nights=1, hotel=hotel, start_date=start_date)


def compute_budget(
    destination: str,
    days: list[DayPlan],
    hotel: Optional[Hotel] = None,
    budget_level: BudgetLevel = "mid_range",
    start_date: Optional[date] = None,
    travellers: int = 1,
) -> Budget:
    """Assemble the full budget for a trip.

    Nights is days - 1: a 3-day trip needs 2 nights of accommodation.
    """
    num_days = len(days)
    nights = max(num_days - 1, 1)
    attractions = [item.attraction for day in days for item in day.items]

    return Budget(
        accommodation=accommodation_cost(destination, nights, hotel, start_date),
        meals=meals_cost(destination, num_days, budget_level, travellers),
        transport=transport_cost(destination, num_days),
        attractions=attraction_cost(attractions, travellers),
    )
