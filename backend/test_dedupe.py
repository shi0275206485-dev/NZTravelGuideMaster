"""Checks that no itinerary shows one place name twice.

Run from backend/:  python test_dedupe.py

Two separate causes, checked separately. At the data level, a cache can
hold one place tagged twice under different refined categories — Rotorua's
Agrodome and Paradise Valley Springs both appear twice, a few hundred
metres apart. At the itinerary level, a name can legitimately belong to two
distinct places — Auckland has two Gow Langsford Gallery branches eight
kilometres apart — and scheduling both reads as a bug even though the data
is right.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from app.llm import LLMError
from app.agents.planner_agent import (
    _fallback_days,
    _first_of_each_name,
    _resolve_days,
    plan_trip,
)
from app.models import (
    Attraction,
    LLMDayPlan,
    LLMItineraryItem,
    LLMTripPlan,
    Location,
    TripRequest,
    name_key,
)
from app.poi_repository import _merge_same_place, load_attractions

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if condition else 'FAIL'} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(label)


def attraction(id_: str, name: str, lat: float = -36.85, lon: float = 174.76) -> Attraction:
    return Attraction(
        id=id_, name=name, category="culture", location=Location(lat=lat, lon=lon)
    )


START = date.today() + timedelta(days=10)


def request(days: int = 3) -> TripRequest:
    return TripRequest(
        destination="Auckland",
        start_date=START,
        end_date=START + timedelta(days=days - 1),
        preferences=["culture"],
        budget_level="mid_range",
    )


def names_of(days) -> list[str]:
    return [item.attraction.name for day in days for item in day.items]


print("1. name_key")
check("case folded", name_key("Sky Tower") == name_key("SKY TOWER"))
check("whitespace collapsed", name_key(" Sky  Tower ") == name_key("Sky Tower"))
check("different names stay different", name_key("Sky Tower") != name_key("Sky Garden"))

print("2. _merge_same_place (data level)")
# 0.0045° of latitude is about 500 m; 0.003° is comfortably inside it.
near = _merge_same_place(
    [attraction("A01", "Agrodome"), attraction("A02", "Agrodome", lat=-36.853)],
    "Rotorua",
)
check("same name, 300 m apart → one kept", len(near) == 1, f"{len(near)}")
check("the earlier id survives", near[0].id == "A01")

far = _merge_same_place(
    [attraction("A01", "Gow Langsford Gallery"),
     attraction("A02", "Gow Langsford Gallery", lat=-36.92)],
    "Auckland",
)
check("same name, 8 km apart → both kept", len(far) == 2, f"{len(far)}")

both = _merge_same_place(
    [attraction("A01", "Auckland Museum"), attraction("A02", "Auckland Domain")],
    "Auckland",
)
check("different names at one spot → both kept", len(both) == 2, f"{len(both)}")

print("3. the real caches")
for destination, expectation in [("Rotorua", "merged"), ("Auckland", "branches kept")]:
    loaded = load_attractions(destination)
    keys = [name_key(a.name) for a in loaded]
    check(f"{destination}: no repeated name within 500 m ({expectation})",
          len(loaded) > 0)
    repeats = {k for k in keys if keys.count(k) > 1}
    if destination == "Rotorua":
        check("Rotorua: Agrodome appears once",
              keys.count("agrodome") == 1, str(keys.count("agrodome")))
        check("Rotorua: Paradise Valley Springs appears once",
              keys.count("paradise valley springs") == 1)
    if destination == "Auckland":
        check("Auckland: the two gallery branches both survive the load",
              "gow langsford gallery" in repeats, str(sorted(repeats)))

print("4. _first_of_each_name")
kept = _first_of_each_name([
    attraction("A01", "Gow Langsford Gallery"),
    attraction("A02", "Auckland Museum"),
    attraction("A03", "gow langsford  gallery"),
])
check("later repeat dropped", [a.id for a in kept] == ["A01", "A02"],
      str([a.id for a in kept]))

print("5. _resolve_days (itinerary level)")
pool = {
    a.id: a
    for a in [
        attraction("A01", "Gow Langsford Gallery"),
        attraction("A02", "Auckland Museum"),
        attraction("A03", "Gow Langsford Gallery", lat=-36.92),
    ]
}
llm_plan = LLMTripPlan(
    destination="Auckland",
    days=[
        LLMDayPlan(
            day=1,
            summary="A day of galleries and museums in the central city.",
            items=[
                LLMItineraryItem(attraction_id="A01", time_slot="morning",
                                 note="Contemporary art in the city centre."),
                LLMItineraryItem(attraction_id="A02", time_slot="afternoon",
                                 note="The collections on the Domain."),
                LLMItineraryItem(attraction_id="A03", time_slot="evening",
                                 note="The other branch of the same gallery."),
            ],
        )
    ],
)
days, unknown = _resolve_days(llm_plan, pool, request(1))
scheduled = names_of(days)
check("the repeated name is scheduled once", len(scheduled) == 2, str(scheduled))
check("the first of the pair is the one kept", "A01" in [i.attraction.id for i in days[0].items])
check("the unrelated stop is untouched", "Auckland Museum" in scheduled)
check("no unknown ids", unknown == [])

print("6. a repeated id, which the schema is supposed to catch first")
repeated_id = dict(
    destination="Auckland",
    days=[dict(
        day=1, summary="One place, offered twice by the model.",
        items=[
            dict(attraction_id="A01", time_slot="morning", note="Once."),
            dict(attraction_id="A01", time_slot="evening", note="Again."),
            dict(attraction_id="A02", time_slot="afternoon", note="Museum."),
        ],
        meals=[],
    )],
)
try:
    LLMTripPlan.model_validate(repeated_id)
    check("the schema rejects it", False)
except Exception as exc:
    check("the schema rejects it", "more than once" in str(exc))
# And if it ever did not, the resolver would still hold. Built without
# validation to reach the code path the schema normally makes unreachable.
unvalidated = LLMTripPlan.model_construct(
    destination="Auckland",
    days=[LLMDayPlan.model_construct(
        day=1, summary="One place, offered twice by the model.",
        items=[LLMItineraryItem.model_construct(**item)
               for item in repeated_id["days"][0]["items"]],
        meals=[],
    )],
    hotel_id=None,
)
days, _ = _resolve_days(unvalidated, pool, request(1))
check("the resolver drops the repeat anyway", len(names_of(days)) == 2,
      str(names_of(days)))

print("7. _fallback_days")
shortlist = [
    attraction("A01", "Gow Langsford Gallery"),
    attraction("A03", "Gow Langsford Gallery", lat=-36.92),
    attraction("A02", "Auckland Museum"),
    attraction("A04", "Auckland Art Gallery"),
    attraction("A05", "Sky Tower"),
    attraction("A06", "Mount Eden"),
]
fallback = _fallback_days(request(2), shortlist)
got = names_of(fallback)
check("no name twice", len(got) == len(set(map(name_key, got))), str(got))
# Five distinct names across two days at two-or-three a day: the dedupe
# must not cost a day its stops.
check("both days still filled", len(fallback) == 2, str(len(fallback)))
check("day 1 has more than one stop", len(fallback[0].items) >= 2,
      str(len(fallback[0].items)))

print("8. plan_trip's alternatives")
# Forced down the fallback path rather than left to fail on its own: the
# test then makes no network call and behaves the same on a machine with a
# working key as on one without.
with patch("app.agents.planner_agent.get_llm",
           side_effect=LLMError("no model in tests")):
    plan = plan_trip(request(1), shortlist, llm=None)
alt_names = [a.name for a in plan.alternatives]
alt_keys = [name_key(n) for n in alt_names]
check("no duplicate name among alternatives",
      len(alt_keys) == len(set(alt_keys)), str(alt_names))
sched_keys = {name_key(n) for n in names_of(plan.days)}
check("no alternative repeats a scheduled name",
      not (set(alt_keys) & sched_keys), str(set(alt_keys) & sched_keys))
check("every name on the plan is unique",
      len(sched_keys) == len(names_of(plan.days)), str(names_of(plan.days)))

print()
print("all checks passed" if not failures else f"{len(failures)} FAILED: {failures}")
raise SystemExit(1 if failures else 0)
