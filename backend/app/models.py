"""
TravelGuideMaster — core data models.

Single source of truth for data exchanged between agents, tools, the
FastAPI backend, and the frontend. Built bottom-up:

    Location -> Attraction / Hotel / Meal -> DayPlan -> TripPlan

Design decisions worth knowing before editing this file:

1. The LLM never emits factual data. It selects and arranges candidates by
   ID; coordinates, addresses, and costs are filled in server-side from the
   POI cache and the budget rules. `LLMItineraryItem` / `LLMDayPlan` /
   `LLMTripPlan` at the bottom are the *narrow* shapes the model is asked
   to produce; the rich models above are what the backend assembles.

2. Every enum is a closed `Literal`, and the prompt must enumerate the
   permitted values verbatim. Phase 1 benchmarking showed all three
   candidate LLMs inventing a fourth time slot ("late afternoon") when a
   day was activity-dense; describing the field by type alone is not
   enough (see experiments/week1_llm_selection.md).

3. Coordinates are validated against a New Zealand bounding box, so a
   hallucinated or mis-parsed coordinate fails fast rather than surfacing
   as a marker in the wrong hemisphere.
"""

from __future__ import annotations

from datetime import date, datetime
from datetime import date as DateType
from typing import Literal, Optional, get_args
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------
# Enums / controlled vocabularies
#
# IMPORTANT: any value added here must also be added verbatim to the
# PlannerAgent prompt's allowed-value list.
# --------------------------------------------------------------------------

TimeSlot = Literal["morning", "afternoon", "evening"]

# Every destination is in New Zealand, so "today" means today there. A
# traveller planning from London must not be told their first day has
# already passed because it is still yesterday where they are sitting —
# and the reverse, a date already gone in Auckland, must not be accepted
# because it is still today somewhere else.
LOCAL_TZ = ZoneInfo("Pacific/Auckland")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()

AttractionCategory = Literal[
    "nature", "culture", "geothermal", "museum", "viewpoint",
    "beach", "park", "adventure", "family", "other",
]

AccommodationType = Literal["hotel", "motel", "hostel", "guest_house"]

MealType = Literal["breakfast", "lunch", "dinner"]

BudgetLevel = Literal["budget", "mid_range", "premium"]

TravelPreference = Literal[
    "nature", "culture", "food", "family", "adventure", "relaxation",
]

# Destinations supported in the MVP, selected in Phase 1 on OSM data
# coverage. `search_radius_m` is per-destination by design: a fixed radius
# under-samples resort towns whose attractions spread beyond the centre
# (see experiments/week2_destination_selection.md).
SupportedDestination = Literal["Auckland", "Rotorua", "Wellington"]

DESTINATION_CONFIG: dict[str, dict] = {
    "Auckland":   {"lat": -36.8521, "lon": 174.7632, "search_radius_m": 15000, "tier": 2},
    "Rotorua":    {"lat": -38.1361, "lon": 176.2525, "search_radius_m": 15000, "tier": 3},
    "Wellington": {"lat": -41.2888, "lon": 174.7772, "search_radius_m": 15000, "tier": 2},
}

# New Zealand bounding box (generous: covers Stewart Island to the
# Far North, and the Chathams are deliberately excluded as out of scope).
NZ_LAT_MIN, NZ_LAT_MAX = -47.5, -34.0
NZ_LON_MIN, NZ_LON_MAX = 166.0, 179.0


# --------------------------------------------------------------------------
# Level 1 — Location
# --------------------------------------------------------------------------

class Location(BaseModel):
    """A validated point in New Zealand."""

    lat: float = Field(description="Latitude in decimal degrees")
    lon: float = Field(description="Longitude in decimal degrees")
    address: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def within_new_zealand(self) -> "Location":
        if not (NZ_LAT_MIN <= self.lat <= NZ_LAT_MAX):
            raise ValueError(
                f"latitude {self.lat} is outside New Zealand "
                f"({NZ_LAT_MIN}..{NZ_LAT_MAX})"
            )
        if not (NZ_LON_MIN <= self.lon <= NZ_LON_MAX):
            raise ValueError(
                f"longitude {self.lon} is outside New Zealand "
                f"({NZ_LON_MIN}..{NZ_LON_MAX})"
            )
        return self


# --------------------------------------------------------------------------
# Level 2 — POIs and cost estimates
# --------------------------------------------------------------------------

class CostEstimate(BaseModel):
    """An estimated NZD cost range.

    Always a range, never a point value, and always labelled as an estimate
    in the UI — the project does not claim real-time commercial pricing.
    """

    low_nzd: float = Field(ge=0)
    high_nzd: float = Field(ge=0)
    basis: str = Field(
        max_length=200,
        description="Human-readable explanation of how this was derived, "
                    "e.g. 'tier 3 motel, 2 nights'",
    )

    @model_validator(mode="after")
    def low_not_above_high(self) -> "CostEstimate":
        if self.low_nzd > self.high_nzd:
            raise ValueError(f"low_nzd ({self.low_nzd}) exceeds high_nzd ({self.high_nzd})")
        return self

    @property
    def midpoint_nzd(self) -> float:
        return (self.low_nzd + self.high_nzd) / 2


class Attraction(BaseModel):
    """A candidate or scheduled attraction, sourced from OpenStreetMap.

    `id` is the short handle the LLM uses to refer to this attraction
    (e.g. "A07"); it is assigned when the candidate pool is built, not by
    the model.
    """

    id: str = Field(pattern=r"^A\d{2,3}$")
    osm_id: Optional[str] = Field(default=None, description="e.g. 'node/1234567'")
    name: str = Field(min_length=1, max_length=200)
    category: AttractionCategory
    location: Location
    opening_hours: Optional[str] = Field(
        default=None, max_length=200,
        description="Raw OSM opening_hours string when tagged; coverage is "
                    "inconsistent, so this is advisory only",
    )
    has_wikidata: bool = Field(
        default=False,
        description="Proxy for landmark significance; used to rank the "
                    "candidate pool before it is shown to the LLM",
    )
    estimated_cost: Optional[CostEstimate] = None


class Hotel(BaseModel):
    """A candidate or selected accommodation, sourced from OpenStreetMap."""

    id: str = Field(pattern=r"^H\d{2,3}$")
    osm_id: Optional[str] = None
    name: str = Field(min_length=1, max_length=200)
    accommodation_type: AccommodationType
    location: Location
    stars: Optional[int] = Field(
        default=None, ge=1, le=5,
        description="From the OSM 'stars' tag where present; sparsely tagged",
    )
    estimated_cost_per_night: Optional[CostEstimate] = None


class Meal(BaseModel):
    """A meal slot in the itinerary.

    Restaurants are not individually recommended in the MVP — the meal
    carries a suggested area and an estimated cost, keeping the LLM out of
    the business of naming specific venues it cannot verify.
    """

    meal_type: MealType
    suggestion: str = Field(min_length=3, max_length=200)
    estimated_cost: Optional[CostEstimate] = None


# --------------------------------------------------------------------------
# Level 3 — Daily plan
# --------------------------------------------------------------------------

class RouteLeg(BaseModel):
    """One OSRM-computed leg between two consecutive stops."""

    from_id: str
    to_id: str
    distance_m: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    geometry: Optional[str] = Field(
        default=None, description="Encoded polyline for the map layer"
    )


class ItineraryItem(BaseModel):
    """A scheduled attraction within a day."""

    attraction: Attraction
    time_slot: TimeSlot
    note: str = Field(min_length=5, max_length=300)


class WeatherInfo(BaseModel):
    """Forecast for a single day, when within the forecast window.

    Open-Meteo returns a 16-day window (verified in Phase 1); trips beyond
    that are planned without weather adjustment and `available` is False.
    """

    available: bool = True
    temp_min_c: Optional[float] = None
    temp_max_c: Optional[float] = None
    precipitation_probability: Optional[int] = Field(default=None, ge=0, le=100)
    summary: Optional[str] = Field(default=None, max_length=200)
    is_wet: bool = Field(
        default=False,
        description="Drives indoor-activity weighting in the PlannerAgent prompt",
    )

    @model_validator(mode="after")
    def unavailable_has_no_data(self) -> "WeatherInfo":
        if not self.available and any(
            v is not None for v in (self.temp_min_c, self.temp_max_c,
                                     self.precipitation_probability)
        ):
            raise ValueError("weather marked unavailable but carries forecast data")
        return self


class DayPlan(BaseModel):
    day: int = Field(ge=1, le=7)
    # Annotated with the aliased import: a field named `date` shadows the
    # `date` type inside the class body, and Pydantic then reads the
    # annotation as "must be None" — which silently rejected every real
    # date until something first tried to set one.
    date: Optional[DateType] = None
    summary: str = Field(min_length=10, max_length=500)
    # No minimum: the planner is told to fill every day, and its own
    # schema (LLMDayPlan) enforces that — but a traveller who removes the
    # last stop from a day has done something reasonable, and refusing to
    # represent it would mean rejecting their edit.
    items: list[ItineraryItem] = Field(default_factory=list, max_length=5)
    meals: list[Meal] = Field(default_factory=list, max_length=3)
    weather: Optional[WeatherInfo] = None
    route_legs: list[RouteLeg] = Field(default_factory=list)

    @computed_field
    @property
    def travel_distance_m(self) -> float:
        return sum(leg.distance_m for leg in self.route_legs)

    @computed_field
    @property
    def travel_duration_s(self) -> float:
        return sum(leg.duration_s for leg in self.route_legs)

    @computed_field
    @property
    def travel_summary(self) -> Optional[str]:
        """How much of the day is spent moving, or None if unrouted.

        Surfaced rather than logged. Routing runs after planning, so real
        road distances cannot feed back into the schedule — the planner
        sees only coarse straight-line locality labels, and in Auckland
        that put a beach 5 km away as the crow flies but 21 km by road at
        the end of a day. Saying so lets the traveller judge, and edit.
        """
        if not self.route_legs:
            return None
        km = self.travel_distance_m / 1000
        minutes = round(self.travel_duration_s / 60)
        return f"{km:.1f} km of travel, about {minutes} min in total"

    @model_validator(mode="after")
    def items_run_in_clock_order(self) -> "DayPlan":
        """Keep a day's stops in morning → afternoon → evening order.

        Sorted rather than rejected: a day whose stops arrive out of order
        is a client that appended rather than inserted, not a malformed
        request, and refusing the edit would be a strange way to say so.

        This is load-bearing, not tidiness. `add_routes` feeds `items` to
        OSRM in list order, so an interleaved day was routed morning →
        evening → afternoon — a real journey, drawn on the map, that no
        screen ever showed. The per-stop numbering and the "from the
        previous stop" distances read the same order.

        Stable, so stops sharing a slot keep the order they were given in.
        """
        rank = {slot: i for i, slot in enumerate(get_args(TimeSlot))}
        self.items = sorted(self.items, key=lambda i: rank[i.time_slot])
        return self

    @model_validator(mode="after")
    def no_repeated_attractions_within_day(self) -> "DayPlan":
        ids = [i.attraction.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"day {self.day} repeats an attraction: {ids}")
        return self


# --------------------------------------------------------------------------
# Level 4 — Trip
# --------------------------------------------------------------------------

class Budget(BaseModel):
    """Deterministically computed from the heuristic rules — never by the LLM.

    Recomputed client-side after edits without an LLM call (FR8).
    """

    accommodation: CostEstimate
    meals: CostEstimate
    transport: CostEstimate
    attractions: CostEstimate

    @property
    def total(self) -> CostEstimate:
        parts = [self.accommodation, self.meals, self.transport, self.attractions]
        return CostEstimate(
            low_nzd=sum(p.low_nzd for p in parts),
            high_nzd=sum(p.high_nzd for p in parts),
            basis="sum of accommodation, meals, transport and attraction estimates",
        )


class TripRequest(BaseModel):
    """What the user submits (FR1)."""

    destination: SupportedDestination
    start_date: date
    end_date: date
    preferences: list[TravelPreference] = Field(min_length=1, max_length=6)
    budget_level: BudgetLevel = "mid_range"
    accommodation_type: Optional[AccommodationType] = None
    free_text: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional free-form constraints the structured fields "
                    "cannot express, e.g. 'travelling with a 5-year-old, "
                    "nothing too strenuous'. Treated as a soft preference: it "
                    "influences which candidates the planner picks, never what "
                    "the system is capable of.",
    )

    @field_validator("free_text")
    @classmethod
    def clean_free_text(cls, v: Optional[str]) -> Optional[str]:
        """Normalise whitespace and drop empty submissions.

        This text is interpolated into the planner prompt, so it is fenced
        and length-capped rather than trusted. Collapsing newlines matters:
        multi-line input is the easiest way to make injected text look like
        a new instruction block to the model. The schema validation on the
        planner's output is the real backstop — an injected instruction
        still has to produce a valid TripPlan referencing real candidate
        IDs — but there is no reason to make the attempt easy.
        """
        if v is None:
            return None
        cleaned = " ".join(v.split())
        return cleaned or None

    @model_validator(mode="after")
    def dates_are_sane(self) -> "TripRequest":
        if self.start_date < local_today():
            raise ValueError("Trips cannot start in the past.")
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if (self.end_date - self.start_date).days + 1 > 7:
            raise ValueError("trips longer than 7 days are not supported")
        return self

    @property
    def num_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class TripPlan(BaseModel):
    """The complete generated itinerary — the API's primary response type."""

    destination: SupportedDestination
    start_date: date
    end_date: date
    days: list[DayPlan] = Field(min_length=1, max_length=7)
    hotel: Optional[Hotel] = None
    budget: Optional[Budget] = None
    # The plan has to carry the level it was priced at. /api/recompute is
    # given the plan and nothing else, so without this the budget silently
    # reverts to the default after any edit: a premium trip comes back
    # priced for mid-range dining, with a 200 and figures that look
    # plausible. Defaulted rather than required because sessionStorage
    # holds plans written before the field existed.
    budget_level: BudgetLevel = "mid_range"
    weather_available: bool = Field(
        default=True,
        description="False when the trip falls outside the forecast window; "
                    "surfaced to the user as a notice",
    )
    alternatives: list[Attraction] = Field(
        default_factory=list,
        description="Shortlisted places the planner did not schedule. "
                    "Returned so editing can offer the same candidates the "
                    "planner chose from, rather than a second, unrelated "
                    "search — swapping one stop for another should stay "
                    "within what was already judged to suit the traveller.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="User-facing caveats, e.g. that costs are estimates",
    )

    @model_validator(mode="after")
    def day_numbers_are_sequential(self) -> "TripPlan":
        expected = list(range(1, len(self.days) + 1))
        actual = [d.day for d in self.days]
        if actual != expected:
            raise ValueError(f"day numbers must be sequential {expected}, got {actual}")
        return self


# --------------------------------------------------------------------------
# LLM-facing shapes
#
# These are what the PlannerAgent is asked to produce: IDs and prose only.
# Keeping them separate from the rich models above is what prevents the
# model from inventing coordinates, prices, or opening hours — the backend
# resolves every ID against the POI cache when assembling a TripPlan, and
# an unknown ID is a validation failure rather than a plausible-looking
# fabrication.
# --------------------------------------------------------------------------

class LLMItineraryItem(BaseModel):
    attraction_id: str = Field(pattern=r"^A\d{2,3}$")
    time_slot: TimeSlot
    note: str = Field(min_length=5, max_length=300)


class LLMMeal(BaseModel):
    """A meal slot as the model expresses it.

    Deliberately no restaurant name. OSM's dining data is thin and the
    model has not been given any, so naming a venue would be inventing
    one — and a traveller sent to a restaurant that closed last year is
    worse served than one told which part of town to eat in.
    """

    meal_type: MealType
    suggestion: str = Field(
        min_length=5,
        max_length=200,
        description="Where to eat and what to look for, e.g. 'Cafes along "
                    "Eat Streat, near the lakefront' — an area and a style, "
                    "never a specific restaurant",
    )


class LLMDayPlan(BaseModel):
    day: int = Field(ge=1, le=7)
    summary: str = Field(min_length=10, max_length=500)
    items: list[LLMItineraryItem] = Field(min_length=1, max_length=5)
    meals: list[LLMMeal] = Field(default_factory=list, max_length=3)


class LLMTripPlan(BaseModel):
    """Narrow schema the PlannerAgent must satisfy."""

    destination: str
    days: list[LLMDayPlan] = Field(min_length=1, max_length=7)
    hotel_id: Optional[str] = Field(default=None, pattern=r"^H\d{2,3}$")

    @field_validator("destination")
    @classmethod
    def destination_is_supported(cls, v: str) -> str:
        if v not in DESTINATION_CONFIG:
            raise ValueError(
                f"unsupported destination {v!r}; expected one of "
                f"{list(DESTINATION_CONFIG)}"
            )
        return v

    @model_validator(mode="after")
    def no_attraction_used_twice_across_trip(self) -> "LLMTripPlan":
        seen: set[str] = set()
        for day in self.days:
            for item in day.items:
                if item.attraction_id in seen:
                    raise ValueError(
                        f"attraction {item.attraction_id} scheduled more than once"
                    )
                seen.add(item.attraction_id)
        return self
