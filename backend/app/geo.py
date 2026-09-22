from __future__ import annotations

import math

from .models import Location

EARTH_RADIUS_KM = 6371.0

SECTORS = ("N","NE","E","SE","S","SW","W","NW")

CENTRAL_RADIUS_KM = 1.5

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

def area_label(point: Location, centre: Location)->str:
    distance = haversine_km(centre, point)
    if distance < CENTRAL_RADIUS_KM:
        return "central"
    bearing = math.degrees(math.atan2(
        point.lon - centre.lon,
        point.lat - centre.lat,
    )) % 360
    sector = SECTORS[int((bearing * 22.5)% 360 // 45)]
    return f"{sector} {distance:.0f} km"

def centroid(points: list[Location]) -> Location | None:
    """Mean position of a set of places.
 
    Crude — with two clusters it lands between them rather than in either —
    but it beats a destination's nominal centre, which ignores where the
    traveller is actually going.
    """
    if not points:
        return None
    return Location(
        lat=sum(a.lat for a in points) / len(points),
        lon=sum(a.lon for a in points) / len(points),
    )