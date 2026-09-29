"""PlannerAgent.

Turns the three data agents' outputs into a day-by-day itinerary.

This is the only place in the system where the model does something a rule
could not: deciding which of the shortlisted attractions belong together on
a day, in what order, and what to say about each. Everything factual around
that decision is supplied or resolved — the model receives ids and emits
ids, and the backend turns them back into places.

Two safeguards do the real work:

  * The model's schema (`LLMTripPlan`) permits only ids and prose. It has
    no field in which to invent a coordinate, a price, or an opening time.
  * Every id it returns is resolved against the candidate pool. An id that
    is not there is dropped, and a day left empty by that is dropped with
    it, rather than being rendered as a place that does not exist.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional, get_args

from ..budget import (
    compute_budget,
    estimate_attraction_entry,
    estimate_hotel_nightly,
    meal_share,
)
from ..llm import LLMClient, LLMError, get_llm
from ..geo import area_label
from ..prompt_utils import fence_free_text
from ..models import (
    DESTINATION_CONFIG,
    Attraction,
    CostEstimate,
    DayPlan,
    Hotel,
    ItineraryItem,
    Location,
    Meal,
    LLMTripPlan,
    TimeSlot,
    TripPlan,
    TripRequest,
    WeatherInfo,
    name_key,
)
from .weather_agent import coverage, itinerary_notes

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the itinerary planner for a New Zealand travel planner.

You arrange a day-by-day plan from a fixed shortlist of places. You do not
invent places, and you do not state facts about them beyond what you are
given — no opening hours, prices, access arrangements or policies.

Output a single JSON object and nothing else: no markdown fences, no
commentary before or after."""

# Derived from the type rather than restated, so the fallback path cannot
# drift from the schema the model is held to.
TIME_SLOTS: tuple[TimeSlot, ...] = get_args(TimeSlot)

# Reasoning is off by default across the system because it cost 10x the
# latency for no benefit on selection tasks. Planning is the one call where
# it might earn its keep — arranging days under several simultaneous
# constraints is closer to deduction than recall — so it is a parameter
# here rather than a constant, to be settled by measurement.
DEFAULT_THINKING = False


class PlanningFailed(RuntimeError):
    """The planner could not produce a usable itinerary."""


def _format_shortlist(attractions: list[Attraction], destination: str) -> str:
    """One line per candidate: id, name, category, locality, opening hours."""
    config = DESTINATION_CONFIG.get(destination)
    centre = (
        Location(lat=config["lat"], lon=config["lon"]) if config else None
    )

    lines = []
    for a in attractions:
        parts = [f"{a.id} | {a.name} | {a.category}"]
        if centre:
            parts.append(area_label(a.location, centre))
        if a.opening_hours:
            parts.append(f"hours: {a.opening_hours}")
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


def _format_weather(weather: dict[int, WeatherInfo], num_days: int) -> str:
    if not weather:
        return "  No forecast available; plan without weather adjustment."
    lines = []
    for day in range(1, num_days + 1):
        info = weather.get(day)
        if info is None or not info.available:
            lines.append(f"  Day {day}: no forecast — plan without weather adjustment")
        elif info.is_wet:
            lines.append(
                f"  Day {day}: {info.temp_min_c}-{info.temp_max_c}C, "
                f"{info.precipitation_probability}% rain — WET, favour indoor places"
            )
        else:
            lines.append(
                f"  Day {day}: {info.temp_min_c}-{info.temp_max_c}C, "
                f"{info.precipitation_probability}% rain — fine"
            )
    return "\n".join(lines)


def build_prompt(
    request: TripRequest,
    attractions: list[Attraction],
    hotel: Optional[Hotel],
    weather: dict[int, WeatherInfo],
) -> str:
    """Assemble the planner's user turn.

    The JSON example below mirrors `LLMTripPlan` by hand — change one and
    change the other. Generating it from the model instead was tried and is
    worse: `model_json_schema()` emits `$defs`, `title` and `anyOf` noise
    that costs tokens and reads less clearly to the model than a filled-in
    example does.
    """
    num_days = request.num_days
    hotel_line = (
        f"\nAccommodation (already chosen): {hotel.id} | {hotel.name}\n"
        if hotel
        else ""
    )

    return f"""Arrange a {num_days}-day itinerary for {request.destination}.

Traveller preferences: {', '.join(request.preferences)}
Budget level: {request.budget_level.replace('_', ' ')}
Dates: {request.start_date} to {request.end_date}
{fence_free_text(request.free_text, 'the shortlist')}{hotel_line}
Weather by day:
{_format_weather(weather, num_days)}

Shortlisted places — use only these, by id:
{_format_shortlist(attractions, request.destination)}

Rules:
- Output ONLY a JSON object in exactly this shape:
{{
  "destination": "{request.destination}",
  "hotel_id": "{hotel.id if hotel else 'H01'}",
  "days": [
    {{
      "day": 1,
      "summary": "One sentence describing the shape of the day.",
      "items": [
        {{"attraction_id": "A01", "time_slot": "morning", "note": "What the traveller does here."}},
        {{"attraction_id": "A04", "time_slot": "afternoon", "note": "What the traveller does here."}}
      ],
      "meals": [
        {{"meal_type": "lunch", "suggestion": "Cafes near the lakefront, close to the morning stop"}},
        {{"meal_type": "dinner", "suggestion": "Restaurants in the town centre, a short walk from the hotel"}}
      ]
    }}
  ]
}}
- Exactly {num_days} day{'s' if num_days != 1 else ''}, numbered 1 to {num_days}.
- Prefer 3 items per day where the shortlist allows it, 2 at minimum and 4
  at most. Never repeat a place to reach that number: a day with two stops
  is better than a trip that sends someone somewhere twice. If the
  shortlist is short, fewer items per day is the correct answer.
- time_slot must be EXACTLY one of "morning", "afternoon", "evening". These
  are the ONLY valid values — never "late afternoon", "midday" or anything
  else. If a day has 4 items, put two of them in the same slot.
- Use only ids from the shortlist. Never schedule the same place twice
  across the whole trip, and never schedule two ids that carry the same
  name: some are separate branches of one business, and an itinerary that
  lists a name twice reads as a mistake. Pick one and use a different
  place for the other slot.
- On days marked WET, prefer indoor places (museums, galleries, thermal
  pools) and leave exposed walks and viewpoints for the fine days.
- Group each day by locality, using the area label on each candidate:
  put "central" places together, and keep places sharing a direction and
  distance (e.g. "N 6km" and "N 7km") on the same day. A day that runs from
  one edge of the region to the other spends its time in the car.
- Each note is one short sentence about what the traveller does there.
  Describe the place; do not state opening times, prices, or access
  arrangements — you have not been given them.
- The summary is one sentence describing the day as a whole.
- Give 1 to 3 meals per day, using only "breakfast", "lunch" or "dinner".
  Suggest an AREA and a style of food near that day's stops — never name a
  specific restaurant or cafe. You have not been given any, and inventing
  one sends the traveller somewhere that may not exist.
- Never state what a meal costs, what it includes, or that it comes with an
  attraction's ticket. Those are commercial terms you have not been given;
  a traveller who arrives expecting an included meal pays for it twice."""


def _first_of_each_name(attractions: list[Attraction]) -> list[Attraction]:
    """The list with later entries repeating an earlier name removed.

    Keeps the first, which preserves the significance order the ids encode.
    """
    seen: set[str] = set()
    kept: list[Attraction] = []
    for attraction in attractions:
        key = name_key(attraction.name)
        if key in seen:
            continue
        seen.add(key)
        kept.append(attraction)
    return kept


def _resolve_days(
    llm_plan: LLMTripPlan,
    by_id: dict[str, Attraction],
    request: TripRequest,
) -> tuple[list[DayPlan], list[str]]:
    """Turn the model's ids into real days, discarding what cannot be resolved."""
    days: list[DayPlan] = []
    unknown_ids: list[str] = []
    used: set[str] = set()
    used_names: set[str] = set()

    for day_index, llm_day in enumerate(llm_plan.days):
        items: list[ItineraryItem] = []
        for llm_item in llm_day.items:
            attraction = by_id.get(llm_item.attraction_id)
            if attraction is None:
                unknown_ids.append(llm_item.attraction_id)
                continue
            if attraction.id in used:
                # The schema forbids repeats, but a repeat that slipped
                # through would read as a mistake to the traveller.
                continue
            # And a name already scheduled is a repeat as far as the reader
            # is concerned, even when the ids differ. Auckland's shortlist
            # holds two Gow Langsford Gallery branches eight kilometres
            # apart: distinct places, but a sheet listing the name twice
            # looks like a bug, and nothing the traveller can see tells
            # them which is which.
            if name_key(attraction.name) in used_names:
                logger.info("dropping %s (%s): name already scheduled",
                            attraction.name, attraction.id)
                continue
            used.add(attraction.id)
            used_names.add(name_key(attraction.name))
            items.append(ItineraryItem(
                attraction=attraction.model_copy(update={
                    "estimated_cost": estimate_attraction_entry(attraction)
                }),
                time_slot=llm_item.time_slot,
                note=llm_item.note,
            ))

        if not items:
            logger.warning("day %d had no resolvable attractions; dropped", llm_day.day)
            continue

        per_meal = meal_share(
            request.destination, request.budget_level, len(llm_day.meals)
        )
        meals = [
            Meal(
                meal_type=m.meal_type,
                suggestion=m.suggestion,
                estimated_cost=per_meal,
            )
            for m in llm_day.meals
        ]

        days.append(DayPlan(
            day=len(days) + 1,
            date=request.start_date + timedelta(days=day_index),
            summary=llm_day.summary,
            items=items,
            meals=meals,
        ))

    return days, unknown_ids


def _fallback_days(
    request: TripRequest,
    attractions: list[Attraction],
) -> list[DayPlan]:
    """Deal the shortlist across the days in order.

    Loses the model's grouping and its sense of what belongs together, but
    keeps every other part of the trip intact — an itinerary without
    narrative beats no itinerary at all.
    """
    logger.warning("planner falling back to sequential day assignment")
    # Deduplicated before the chunking, not inside it: skipping an entry
    # mid-chunk would shorten that one day rather than pull the next place
    # forward, and the fallback's whole job is to fill the days it can.
    attractions = _first_of_each_name(attractions)
    per_day = max(2, min(3, len(attractions) // max(request.num_days, 1)))
    days: list[DayPlan] = []

    for day_index in range(request.num_days):
        chunk = attractions[day_index * per_day : (day_index + 1) * per_day]
        if not chunk:
            break
        days.append(DayPlan(
            day=len(days) + 1,
            date=request.start_date + timedelta(days=day_index),
            summary=f"Day {day_index + 1} in {request.destination}.",
            items=[
                ItineraryItem(
                    attraction=a.model_copy(update={
                        "estimated_cost": estimate_attraction_entry(a)
                    }),
                    time_slot=TIME_SLOTS[i % len(TIME_SLOTS)],
                    note=f"Visit {a.name}.",
                )
                for i, a in enumerate(chunk)
            ],
        ))
    return days


def plan_trip(
    request: TripRequest,
    attractions: list[Attraction],
    hotel: Optional[Hotel] = None,
    hotel_nightly: Optional[CostEstimate] = None,
    weather: Optional[dict[int, WeatherInfo]] = None,
    llm: Optional[LLMClient] = None,
    thinking: bool = DEFAULT_THINKING,
) -> TripPlan:
    """Assemble the finished itinerary.

    Raises PlanningFailed only when there is nothing to plan with; a model
    failure degrades to sequential assignment instead.
    """
    if not attractions:
        raise PlanningFailed(
            f"No attractions available for {request.destination}."
        )

    weather = weather or {}
    by_id = {a.id: a for a in attractions}
    days: list[DayPlan] = []
    used_fallback = False

    try:
        client = llm or get_llm()
        llm_plan = client.complete_structured(
            build_prompt(request, attractions, hotel, weather),
            LLMTripPlan,
            system=SYSTEM_PROMPT,
            max_tokens=2500,
            temperature=0.0,
            thinking=thinking,
        )
        days, unknown = _resolve_days(llm_plan, by_id, request)
        if unknown:
            logger.warning("planner returned %d unknown ids: %s", len(unknown), unknown)
    except LLMError as exc:
        logger.warning("planner model call failed: %s", exc)

    if not days:
        days = _fallback_days(request, attractions)
        used_fallback = True

    if not days:
        raise PlanningFailed("Could not build any days from the shortlist.")

    # Attach each day's forecast now that the days are final.
    for day in days:
        day.weather = weather.get(day.day)

    available, total = coverage(weather) if weather else (0, request.num_days)
    notes = itinerary_notes(weather) if weather else []
    notes.append(
        "All costs are estimates based on published rules, not live prices."
    )
    if used_fallback:
        notes.append(
            "Detailed day-by-day arrangement was unavailable, so places are "
            "listed in recommended order rather than grouped by area."
        )

    # The nightly estimate is computed by the hotel agent while ranking;
    # carry it onto the hotel so the itinerary can show what a night costs
    # rather than only the trip total.
    if hotel is not None:
        nightly = hotel_nightly or estimate_hotel_nightly(
            request.destination, hotel, request.start_date
        )
        hotel = hotel.model_copy(update={"estimated_cost_per_night": nightly})

    # Everything shortlisted but not scheduled, so the client can offer
    # swaps without another round of searching.
    #
    # Filtered by name as well as id, and for the same reason the itinerary
    # is: the "add a stop" list shows a name, a category and a distance, so
    # two entries sharing a name are two rows the traveller cannot choose
    # between. Keeping the plan name-unique end to end means no path —
    # model, fallback, or a manual add — can put one name on the sheet
    # twice.
    scheduled = {item.attraction.id for day in days for item in day.items}
    scheduled_names = {
        name_key(item.attraction.name) for day in days for item in day.items
    }
    alternatives = [
        a for a in _first_of_each_name(attractions)
        if a.id not in scheduled and name_key(a.name) not in scheduled_names
    ]

    return TripPlan(
        destination=request.destination,
        start_date=request.start_date,
        end_date=request.end_date,
        days=days,
        hotel=hotel,
        budget=compute_budget(
            request.destination,
            days,
            hotel=hotel,
            budget_level=request.budget_level,
            start_date=request.start_date,
        ),
        # Carried on the plan, not just used here: the recompute path sees
        # the plan alone and has no request to read it back from.
        budget_level=request.budget_level,
        weather_available=bool(weather) and available == total,
        alternatives=[
            a.model_copy(update={"estimated_cost": estimate_attraction_entry(a)})
            for a in alternatives
        ],
        notes=notes,
    )
