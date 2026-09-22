"""HotelAgent.

Ranks the cached accommodation pool for a trip and attaches nightly cost
estimates.

Deliberately rule-based. What makes accommodation suitable here is
distance to the places the traveller is actually visiting, whether the
type matches how they want to travel, and what it costs — all of which are
arithmetic over data the system already holds. Handing that to a model
would add latency and a hallucination surface in exchange for nothing,
since the model has no information about these hotels beyond what it would
be given.

OSM carries no ratings and no prices for accommodation, so neither is
claimed: costs come from the published heuristic rules and are labelled as
estimates.
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Optional

from pydantic import BaseModel

from ..budget_v1 import estimate_hotel_nightly
from ..models import (
    DESTINATION_CONFIG,
    AccommodationType,
    Attraction,
    BudgetLevel,
    CostEstimate,
    Hotel,
    Location,
    TripRequest,
)
from ..poi_repository import load_hotels

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0

# How each budget level maps onto accommodation types, in preference order.
# A budget traveller is not refused a hotel if nothing else is available —
# the list is a ranking, not a filter.
BUDGET_TYPE_PREFERENCE: dict[BudgetLevel, list[AccommodationType]] = {
    "budget": ["hostel", "guest_house", "motel", "hotel"],
    "mid_range": ["motel", "hotel", "guest_house", "hostel"],
    "premium": ["hotel", "guest_house", "motel", "hostel"],
}

# Beyond this, a place stops being a base for the trip and starts being a
# commute. Scores taper to zero here rather than cutting off, so a sparse
# destination still returns something.
MAX_USEFUL_DISTANCE_KM = 12.0

# Weighting varies by budget level, because what "best" means varies with
# it. On a mid-range or premium trip proximity dominates: travel time is
# the scarce resource and a room an hour away costs more of it than it
# saves. On a budget trip that logic inverts — a fixed weighting ranked a
# NZD 332-570 five-star second for a budget traveller purely because it was
# close, which is not an answer to the question they asked.
WEIGHTS: dict[BudgetLevel, dict[str, float]] = {
    "budget": {"proximity": 0.35, "type": 0.60, "stars": 0.05},
    "mid_range": {"proximity": 0.60, "type": 0.30, "stars": 0.10},
    "premium": {"proximity": 0.50, "type": 0.30, "stars": 0.20},
}


class RankedHotel(BaseModel):
    hotel: Hotel
    distance_km: float
    nightly_cost: CostEstimate
    reason: str
    rank: int


def haversine_km(a: Location, b: Location) -> float:
    """Great-circle distance. Straight-line, not driving distance.

    Adequate for ranking candidates against each other; the itinerary's
    actual travel times come from the routing tool, which follows roads.
    """
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def centroid(attractions: list[Attraction]) -> Optional[Location]:
    """Mean position of the planned attractions.

    A centroid is crude — it can land between two clusters rather than in
    either — but it beats the destination's nominal centre, which ignores
    where the traveller is actually going. Sharper clustering is only worth
    it once itineraries show the failure mattering in practice.
    """
    if not attractions:
        return None
    return Location(
        lat=sum(a.location.lat for a in attractions) / len(attractions),
        lon=sum(a.location.lon for a in attractions) / len(attractions),
    )


def _proximity_score(distance_km: float) -> float:
    """1.0 next door, decaying to 0 at MAX_USEFUL_DISTANCE_KM.

    Decays on the square of the distance rather than linearly. A linear
    curve is almost flat across the range that actually matters — it
    separated 0.5 km from 3 km by 0.21, less than a single step of type
    preference, so distance stopped influencing the ranking inside a town.
    Squaring restores the difference a traveller feels between walking to
    dinner and driving to it, while still tapering gently further out where
    everything is a drive anyway.
    """
    if distance_km >= MAX_USEFUL_DISTANCE_KM:
        return 0.0
    return (1.0 - (distance_km / MAX_USEFUL_DISTANCE_KM)) ** 2


def _type_score(hotel: Hotel, budget_level: BudgetLevel,
                 requested: Optional[AccommodationType]) -> float:
    if requested:
        return 1.0 if hotel.accommodation_type == requested else 0.0
    order = BUDGET_TYPE_PREFERENCE[budget_level]
    try:
        position = order.index(hotel.accommodation_type)
    except ValueError:
        return 0.25
    return 1.0 - (position / len(order))


def _star_score(hotel: Hotel) -> float:
    """Sparsely tagged in OSM, so its weight is small and its absence neutral."""
    if hotel.stars is None:
        return 0.5
    return hotel.stars / 5.0


def _describe(hotel: Hotel, distance_km: float) -> str:
    kind = hotel.accommodation_type.replace("_", " ")
    star_text = f"{hotel.stars}-star " if hotel.stars else ""
    if distance_km < 1.0:
        where = f"{round(distance_km * 1000)} m from your planned stops"
    else:
        where = f"{distance_km:.1f} km from your planned stops"
    return f"{star_text}{kind}, {where}."


def rank_hotels(
    request: TripRequest,
    attractions: Optional[list[Attraction]] = None,
    hotels: Optional[list[Hotel]] = None,
    limit: int = 5,
) -> list[RankedHotel]:
    """Rank accommodation for this trip, best first."""
    pool = hotels if hotels is not None else load_hotels(request.destination)
    if not pool:
        return []

    # Rank against where the traveller is actually going; fall back to the
    # destination's nominal centre when attractions have not been chosen yet.
    anchor = centroid(attractions or [])
    if anchor is None:
        config = DESTINATION_CONFIG.get(request.destination)
        if config:
            anchor = Location(lat=config["lat"], lon=config["lon"])
        else:
            logger.warning("no anchor for %s; ranking on type alone", request.destination)

    weights = WEIGHTS[request.budget_level]
    scored: list[tuple[float, Hotel, float]] = []
    for hotel in pool:
        distance = haversine_km(anchor, hotel.location) if anchor else 0.0
        score = (
            weights["proximity"] * (_proximity_score(distance) if anchor else 0.5)
            + weights["type"] * _type_score(
                hotel, request.budget_level, request.accommodation_type
            )
            + weights["stars"] * _star_score(hotel)
        )
        scored.append((score, hotel, distance))

    scored.sort(key=lambda item: (-item[0], item[1].name))

    ranked: list[RankedHotel] = []
    for position, (_, hotel, distance) in enumerate(scored[:limit], start=1):
        ranked.append(
            RankedHotel(
                hotel=hotel,
                distance_km=round(distance, 2),
                nightly_cost=estimate_hotel_nightly(
                    request.destination, hotel, request.start_date
                ),
                reason=_describe(hotel, distance),
                rank=position,
            )
        )
    return ranked


def select_hotel(
    request: TripRequest,
    attractions: Optional[list[Attraction]] = None,
    hotels: Optional[list[Hotel]] = None,
) -> Optional[RankedHotel]:
    """The single recommendation, or None if the pool is empty."""
    ranked = rank_hotels(request, attractions, hotels, limit=1)
    return ranked[0] if ranked else None
