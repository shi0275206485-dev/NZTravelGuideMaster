"""Routing between a day's stops, via OSRM.

Distances elsewhere in the system are straight-line: good enough to rank
hotels against each other, useless for telling a traveller how long a day
takes. This module returns road distances and driving times, which is what
the map draws and what the itinerary can be honest about.

One request per day covering all its stops, rather than one per pair.
OSRM returns a leg between each consecutive waypoint from a single call,
so a five-stop day costs one round trip instead of four.

Routing is an enrichment, not a requirement. If OSRM is unreachable the
itinerary keeps its places, its costs and its schedule, and simply has no
line on the map — so every failure here returns empty rather than raising.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from .cache import Cache, cached_fetch
from .config import get_settings
from .models import DayPlan, Location, RouteLeg, TripPlan

logger = logging.getLogger(__name__)

ROUTE_NAMESPACE = "route"

# Road networks change slowly and this is a demonstration system; a week
# is long enough to make the cache worth having and short enough that a
# closed road is not remembered indefinitely.
ROUTE_TTL_S = 7 * 24 * 3600

# OSRM's public instance rejects very long coordinate lists, and a day with
# more stops than this is not an itinerary anyone would follow.
MAX_WAYPOINTS = 10

REQUEST_TIMEOUT_S = 20.0


def _coordinate_string(points: list[Location]) -> str:
    """OSRM wants lon,lat — the reverse of every other API here."""
    return ";".join(f"{p.lon:.6f},{p.lat:.6f}" for p in points)


def _cache_key(points: list[Location]) -> str:
    """Rounded to about 11 m, so trivially different requests share a hit."""
    return "|".join(f"{p.lat:.4f},{p.lon:.4f}" for p in points)


def _fetch_route(points: list[Location]) -> dict:
    settings = get_settings()
    url = (f"{settings.osrm_url.rstrip('/')}/route/v1/driving/"
           f"{_coordinate_string(points)}")
    response = httpx.get(
        url,
        params={"overview": "full", "geometries": "polyline"},
        timeout=REQUEST_TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def route_day(
    day: DayPlan,
    start_from: Optional[Location] = None,
    cache: Optional[Cache] = None,
) -> list[RouteLeg]:
    """Legs between a day's stops, in visiting order.

    `start_from` prepends the accommodation, so the first leg is the drive
    out from where the traveller wakes up rather than an invisible jump to
    the first attraction.
    """
    stops = [item.attraction for item in day.items]
    if not stops:
        return []

    # Ids identify each leg's endpoints. The hotel has no attraction id, so
    # it gets a reserved one the frontend can recognise.
    ids = [a.id for a in stops]
    points = [a.location for a in stops]
    if start_from is not None:
        ids.insert(0, "HOTEL")
        points.insert(0, start_from)

    if len(points) < 2:
        return []
    if len(points) > MAX_WAYPOINTS:
        logger.warning("day %d has %d waypoints; routing first %d",
                        day.day, len(points), MAX_WAYPOINTS)
        ids, points = ids[:MAX_WAYPOINTS], points[:MAX_WAYPOINTS]

    store = cache or Cache(get_settings().cache_db_path)
    try:
        payload = cached_fetch(
            store, ROUTE_NAMESPACE, _cache_key(points),
            lambda: _fetch_route(points), ttl_s=ROUTE_TTL_S,
        )
    except Exception as exc:
        logger.warning("routing failed for day %d: %s", day.day, exc)
        return []

    if payload.get("code") != "Ok" or not payload.get("routes"):
        logger.warning("OSRM returned %s for day %d",
                        payload.get("code"), day.day)
        return []

    route = payload["routes"][0]
    legs = route.get("legs", [])
    if len(legs) != len(points) - 1:
        # A mismatch means the response does not describe the journey that
        # was asked for; drawing it anyway would mislabel the map.
        logger.warning("day %d: expected %d legs, got %d",
                        day.day, len(points) - 1, len(legs))
        return []

    # The geometry covers the whole day as one polyline. Splitting it per
    # leg would need the step-level detail that `overview` omits, so the
    # full line is attached to the first leg and the rest carry distance
    # and duration only. The map draws one line per day, which is what it
    # wants anyway.
    geometry = route.get("geometry")

    return [
        RouteLeg(
            from_id=ids[i],
            to_id=ids[i + 1],
            distance_m=leg.get("distance", 0.0),
            duration_s=leg.get("duration", 0.0),
            geometry=geometry if i == 0 else None,
        )
        for i, leg in enumerate(legs)
    ]


def add_routes(plan: TripPlan, cache: Optional[Cache] = None) -> TripPlan:
    """Attach routes to every day of a plan, in place.

    Days are routed independently: a traveller returns to their
    accommodation each night, so there is no leg spanning midnight.
    """
    hotel_location = plan.hotel.location if plan.hotel else None
    for day in plan.days:
        day.route_legs = route_day(day, start_from=hotel_location, cache=cache)
    return plan