"""Read access to the pre-fetched POI cache.

The agents never touch Overpass at runtime. Destination data is captured
and filtered once by scripts/prefetch_pois.py, so a request only ever
reads from SQLite — which is what keeps the deployed system independent of
rate-limited public geographic services.

This module is the single place that knows the cache key layout, so the
agents can ask for "Rotorua's attractions" without caring how that is
stored.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from .cache import Cache
from .config import get_settings
from .models import Attraction, Hotel

logger = logging.getLogger(__name__)

POI_NAMESPACE = "poi"


class PoiUnavailableError(RuntimeError):
    """Raised when a destination has no pre-fetched data.

    This is a deployment/setup problem, not a user error: it means the
    pre-fetch script has not been run for a destination the API advertises
    as supported.
    """


@lru_cache
def get_cache() -> Cache:
    return Cache(get_settings().cache_db_path)


def _load(destination: str, kind: str) -> list[dict]:
    cache = get_cache()
    raw = cache.get(POI_NAMESPACE, f"{destination}:{kind}")
    if not raw:
        raise PoiUnavailableError(
            f"No cached {kind} for {destination}. "
            f"Run: python scripts/prefetch_pois.py --dest {destination}"
        )
    return raw


def load_attractions(destination: str) -> list[Attraction]:
    """Candidate attractions, already ranked by the pre-fetch pipeline.

    Order is significant: ids are assigned by descending significance, so
    A01 is the most notable candidate. Preserving that order means the
    ranking work done offline still informs the model even before it
    applies the traveller's preferences.
    """
    records = _load(destination, "attractions")
    attractions = []
    for record in records:
        try:
            attractions.append(Attraction.model_validate(record))
        except Exception as exc:
            # One malformed cache entry should not sink the whole request.
            logger.warning("skipping malformed attraction in cache: %s", exc)
    return attractions


def load_hotels(destination: str) -> list[Hotel]:
    records = _load(destination, "hotels")
    hotels = []
    for record in records:
        try:
            hotels.append(Hotel.model_validate(record))
        except Exception as exc:
            logger.warning("skipping malformed hotel in cache: %s", exc)
    return hotels


def attractions_by_id(destination: str) -> dict[str, Attraction]:
    """Lookup table for resolving the ids the planner returns.

    The planner is only ever given ids and only ever returns ids; this is
    where they become real places again. An id that is absent here is a
    hallucination and is rejected rather than rendered.
    """
    return {a.id: a for a in load_attractions(destination)}


def hotels_by_id(destination: str) -> dict[str, Hotel]:
    return {h.id: h for h in load_hotels(destination)}
