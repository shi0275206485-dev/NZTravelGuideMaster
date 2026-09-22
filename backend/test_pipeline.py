"""Run all three data agents against one request and print what the
PlannerAgent will receive.

This is the hand-off rehearsal: the three agents are finished, the planner
is not, so this shows exactly what the planner's inputs look like before
any prompt is written for it. Anything that looks wrong here would look
wrong in the itinerary too.

Usage:
  python test_pipeline.py
  python test_pipeline.py --dest Wellington --days 4
  python test_pipeline.py --note "travelling with a 5-year-old"
  python test_pipeline.py --far          # dates beyond the forecast window
"""

import argparse
import time
from datetime import date, timedelta

from app.agents.attraction_agent import search_attractions
from app.agents.hotel_agent import rank_hotels
from app.agents.weather_agent import get_weather, summarise_trip
from app.budget import compute_budget
from app.models import DayPlan, ItineraryItem, TripRequest


def rule(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="Rotorua")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--budget", default="mid_range",
                     choices=["budget", "mid_range", "premium"])
    ap.add_argument("--prefs", nargs="+", default=["nature", "culture"])
    ap.add_argument("--note", default=None)
    ap.add_argument("--far", action="store_true",
                     help="start 40 days out, past the forecast window")
    args = ap.parse_args()

    start = date.today() + timedelta(days=40 if args.far else 14)
    request = TripRequest(
        destination=args.dest,
        start_date=start,
        end_date=start + timedelta(days=args.days - 1),
        preferences=args.prefs,
        budget_level=args.budget,
        free_text=args.note,
    )

    print(f"\n{'=' * 70}")
    print(f"{request.destination} · {request.num_days} days · "
          f"{start} to {request.end_date}")
    print(f"preferences: {', '.join(request.preferences)} · "
          f"budget: {request.budget_level}")
    if request.free_text:
        print(f'note: "{request.free_text}"')
    print("=" * 70)

    timings: dict[str, float] = {}

    # 1. Attractions — the only agent that calls the model.
    t0 = time.perf_counter()
    attractions = search_attractions(request)
    timings["AttractionSearchAgent"] = time.perf_counter() - t0

    rule(f"AttractionSearchAgent — {len(attractions)} selected")
    for r in attractions:
        print(f"  {r.rank:>2}. [{r.attraction.id}] {r.attraction.name}")
        print(f"      {r.attraction.category:<12} {r.reason}")

    # 2. Weather — no model, and no network at all beyond the window.
    t0 = time.perf_counter()
    weather = get_weather(request.destination, request.start_date, request.end_date)
    timings["WeatherQueryAgent"] = time.perf_counter() - t0

    rule("WeatherQueryAgent")
    for day, info in sorted(weather.items()):
        if info.available:
            print(f"  Day {day}: {info.temp_min_c}–{info.temp_max_c}°C · "
                  f"{info.summary}")
        else:
            print(f"  Day {day}: {info.summary}")
    note = summarise_trip(weather)
    print(f"\n  trip note: {note or '(nothing to report)'}")

    # 3. Hotels — ranked against where the traveller is actually going,
    #    which is why this runs after the attractions are known.
    t0 = time.perf_counter()
    hotels = rank_hotels(
        request, attractions=[r.attraction for r in attractions], limit=5
    )
    timings["HotelAgent"] = time.perf_counter() - t0

    rule(f"HotelAgent — top {len(hotels)}")
    for r in hotels:
        print(f"  {r.rank}. [{r.hotel.id}] {r.hotel.name}")
        print(f"     {r.distance_km:>5.2f} km · NZD "
              f"{r.nightly_cost.low_nzd:.0f}–{r.nightly_cost.high_nzd:.0f}/night · "
              f"{r.reason}")

    # 4. Budget over a stand-in itinerary: every selected attraction spread
    #    evenly across the days. The planner will choose a subset, so this
    #    is an upper bound, not the figure the traveller will see.
    rule("Budget (indicative — assumes all selected attractions are visited)")
    if attractions:
        per_day = max(1, len(attractions) // request.num_days)
        days = []
        for d in range(request.num_days):
            chunk = attractions[d * per_day : (d + 1) * per_day][:5]
            if not chunk:
                break
            days.append(DayPlan(
                day=d + 1,
                summary=f"Day {d + 1} of the trip, placeholder for costing.",
                items=[
                    ItineraryItem(
                        attraction=r.attraction,
                        time_slot=["morning", "afternoon", "evening"][i % 3],
                        note="Placeholder note for budget calculation.",
                    )
                    for i, r in enumerate(chunk)
                ],
            ))
        budget = compute_budget(
            request.destination, days,
            hotel=hotels[0].hotel if hotels else None,
            budget_level=request.budget_level,
            start_date=request.start_date,
        )
        for label, cost in [("accommodation", budget.accommodation),
                             ("meals", budget.meals),
                             ("transport", budget.transport),
                             ("attractions", budget.attractions)]:
            print(f"  {label:<14} NZD {cost.low_nzd:>7.0f} – {cost.high_nzd:>7.0f}"
                  f"   ({cost.basis})")
        total = budget.total
        print(f"  {'TOTAL':<14} NZD {total.low_nzd:>7.0f} – {total.high_nzd:>7.0f}")

    rule("Timings")
    for name, seconds in timings.items():
        print(f"  {name:<24} {seconds:>6.2f}s")
    print(f"  {'sum':<24} {sum(timings.values()):>6.2f}s")
    print("\n  The planner adds one more model call on top of this; the target")
    print("  for the whole pipeline is under ~60s.")


if __name__ == "__main__":
    main()
