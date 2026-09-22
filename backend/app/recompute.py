"""Recomputing an itinerary after the traveller edits it.

Editing must not cost a model call. Adding, removing or reordering stops
changes what the routes and the costs are, and both of those are arithmetic.
So an edit that took a second to make should take about a second to settle,
not the several seconds a fresh plan does.

What the client sends back is treated as a statement of intent, not as data.
Every attraction is re-resolved against the POI cache by id, so the names,
coordinates and categories used for routing and costing are the ones the server
already held. A client that edits a price, moves a coordinate or invents
a place changes nothing but its own copy.
"""

from __future__ import annotations

import logging
from typing import Optional
from .budget import compute_budget, estimate_attraction_entry, meal_share
from .cache import Cache
from .models import Attraction, DayPlan, Hotel, ItineraryItem, Meal, TripPlan
from .poi_repository import PoiUnavailableError, attractions_by_id, hotels_by_id
from .routing import add_routes

logger = logging.getLogger(__name__)

class RecomputeError(RuntimeError):
    """The edited plan could not be made into a valid itinerary."""

def _resolve_hotel(plan: TripPlan) -> Optional[Hotel]:
    """The plan's hotel, or None if it has none or the id is invalid."""
    # `plan.hotel`, not `plan.hotel.id`: Hotel.id is a required field, so the
    # id can never be None, and the original test raised AttributeError on
    # exactly the case it was written to handle — a plan with no hotel at
    # all, which the pipeline produces whenever accommodation data is
    # missing. Editing such a plan returned a 500.
    if plan.hotel is None:
        return None
    try:
        known = hotels_by_id(plan.destination)
    except PoiUnavailableError:
        return plan.hotel
    return known.get(plan.hotel.id, plan.hotel)

def _rebuild_meals(plan: TripPlan, day: DayPlan) -> list[Meal]:
    """Re-split the day's food budget across whatever meals remain.

    Meals are not editable, but their per-meal share is derived from the
    day's total, so it has to be recalculated whenever a day is rebuilt.
    """
    if not day.meals:
        return []
    # The level comes off the plan. It used to be the literal "mid_range",
    # which silently restated a premium trip's meals one band down on the
    # first edit — a 200, plausible figures, and a basis line in the
    # summary that changed from "premium dining" to "mid range dining".
    per_meal = meal_share(plan.destination, plan.budget_level, len(day.meals))

    return [
        Meal(meal_type=m.meal_type, suggestion=m.suggestion, estimated_cost= per_meal)
        for m in day.meals
    ]

def _costed(attraction: Attraction) -> Attraction:
    return attraction.model_copy(
        update={"estimated_cost": estimate_attraction_entry(attraction)}
    )

def recompute(plan: TripPlan, cache: Optional[Cache] = None) -> TripPlan:
    """Return the edited plan with routes and costs brought back into line.
    Raises RecomputeError if the edit leaves nothing to plan:
    an itinerary with no stops at all is not a trip.
    """
    try:
        known = attractions_by_id(plan.destination)
    except PoiUnavailableError as exc:
        raise RecomputeError(str(exc)) from exc

    days: list[DayPlan] = []
    dropped: list[str] = []
    seen: set[str] = set()

    for index, day in enumerate(plan.days):
        items: list[ItineraryItem] = []
        for item in day.items:
            attraction = known.get(item.attraction.id)
            if attraction is None:
                dropped.append(item.attraction.id)
                continue
            if attraction.id in seen:
                dropped.append(attraction.id)
                continue
            seen.add(attraction.id)
            items.append(
                ItineraryItem(
                    attraction=_costed(attraction),
                    time_slot=item.time_slot,
                    note=item.note,
                )
            )
        # An emptied day is kept rather than dropped: the traveller cleared
        # it deliberately, and renumbering the days beneath it would move
        # everything else they were looking at.
        days.append(
            DayPlan(
                day=index + 1,
                date=day.date,
                summary=day.summary,
                items=items,
                meals=_rebuild_meals(plan, day),
                weather=day.weather,
            )
        )
    if not any(day.items for day in days):
        raise RecomputeError("no attractions remain in the plan")
    if dropped:
        logger.warning("recompute dropped %d attractions: %s", 
                       len(dropped), ", ".join(dropped))
    hotel = _resolve_hotel(plan)
    scheduled = {item.attraction.id for day in days for item in day.items}

    updated = TripPlan(
        destination= plan.destination,
        start_date=plan.start_date,
        end_date=plan.end_date,
        days=days,
        hotel=hotel,
        budget=compute_budget(
            # Every day, including any the traveller emptied. Filtering to
            # the days with stops priced a cleared day as if the trip had
            # got shorter — one fewer night of accommodation and one fewer
            # day of food — but they still sleep there and still eat. The
            # attraction line falls on its own, because an empty day
            # contributes no entries.
            plan.destination, days,
            hotel=hotel,
            budget_level=plan.budget_level,
            start_date=plan.start_date,
        ),
        weather_available=plan.weather_available,
        # The planner's shortlist, re-resolved and filtered — not the whole
        # cache, which is what this used to return. Editing is meant to
        # offer the candidates the traveller's preferences already selected
        # for; drawing from every cached POI quietly widened the list from
        # around nine to fifty-one on the first edit, and put back the
        # places the AttractionSearchAgent had ruled out. Unknown ids are
        # dropped rather than trusted, on the same grounds as the stops.
        alternatives=[
            _costed(known[a.id])
            for a in plan.alternatives
            if a.id in known and a.id not in scheduled
        ],
        # Carried through, not re-derived: recompute has no request to read
        # it from, and leaving it to the field default would restore the
        # same silent downgrade one level up.
        budget_level=plan.budget_level,
        notes=plan.notes,
    )
    return add_routes(updated, cache=cache)