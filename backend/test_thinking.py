"""Does reasoning earn its cost in the PlannerAgent?

Reasoning is disabled system-wide because on selection tasks it cost 10x
the latency for the same answer. Planning is the one call where it might
pay for itself: arranging days under several simultaneous constraints —
day count, items per day, no repeats, wet-day handling, geographic
grouping — is closer to deduction than recall.

This runs the same request both ways, several times each, and reports what
changed. Judgement is not automatic: the script measures what can be
counted (latency, tokens, items per day, constraint violations, how far
apart a day's stops are) and prints the itineraries for reading.

Usage:
  python test_thinking.py
  python test_thinking.py --repeats 5 --dest Wellington
  python test_thinking.py --days 5
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import time
from datetime import date, timedelta
from pathlib import Path

from app.agents.attraction_agent import search_attractions
from app.agents.hotel_agent import rank_hotels
from app.agents.planner_agent import plan_trip
from app.agents.weather_agent import get_weather
from app.llm import get_llm
from app.models import DayPlan, TripPlan, TripRequest

EARTH_RADIUS_KM = 6371.0


def km_between(a, b) -> float:
    lat1, lon1 = math.radians(a.lat), math.radians(a.lon)
    lat2, lon2 = math.radians(b.lat), math.radians(b.lon)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def day_spread_km(day: DayPlan) -> float:
    """Greatest distance between any two stops in a day.

    A proxy for whether the day hangs together geographically. The planner
    is asked to group each day, but is given no coordinates — so this
    measures whether world knowledge alone is enough to do it.
    """
    points = [i.attraction.location for i in day.items]
    if len(points) < 2:
        return 0.0
    return max(km_between(a, b)
               for i, a in enumerate(points) for b in points[i + 1:])


def wet_day_violations(plan: TripPlan) -> int:
    """Outdoor stops scheduled on days forecast wet.

    Categories that offer no shelter. Not a hard error — sometimes the
    shortlist has nothing indoors — but a wet day full of viewpoints
    suggests the forecast was ignored.
    """
    exposed = {"park", "nature", "beach", "viewpoint"}
    return sum(
        1
        for day in plan.days
        if day.weather and day.weather.is_wet
        for item in day.items
        if item.attraction.category in exposed
    )


def repeated_attractions(plan: TripPlan) -> int:
    seen, repeats = set(), 0
    for day in plan.days:
        for item in day.items:
            if item.attraction.id in seen:
                repeats += 1
            seen.add(item.attraction.id)
    return repeats


def measure(plan: TripPlan, elapsed: float, usage: dict) -> dict:
    per_day = [len(d.items) for d in plan.days]
    spreads = [day_spread_km(d) for d in plan.days if len(d.items) > 1]
    return {
        "seconds": round(elapsed, 2),
        "completion_tokens": usage.get("completion_tokens", 0),
        "days": len(plan.days),
        "items_total": sum(per_day),
        "items_per_day": round(statistics.mean(per_day), 2) if per_day else 0,
        "meals_total": sum(len(d.meals) for d in plan.days),
        "mean_day_spread_km": round(statistics.mean(spreads), 2) if spreads else 0.0,
        "max_day_spread_km": round(max(spreads), 2) if spreads else 0.0,
        "wet_violations": wet_day_violations(plan),
        "repeats": repeated_attractions(plan),
    }


def show(plan: TripPlan) -> None:
    for day in plan.days:
        wet = " [WET]" if day.weather and day.weather.is_wet else ""
        print(f"    Day {day.day}{wet} — spread {day_spread_km(day):.1f} km")
        for item in day.items:
            print(f"      {item.time_slot:<10}{item.attraction.name} "
                  f"({item.attraction.category})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="Rotorua")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--note", default="travelling with a 5-year-old")
    ap.add_argument("--out", type=Path, default=Path("test_results/thinking.csv"))
    args = ap.parse_args()

    start = date.today() + timedelta(days=7)
    request = TripRequest(
        destination=args.dest,
        start_date=start,
        end_date=start + timedelta(days=args.days - 1),
        preferences=["nature", "culture"],
        budget_level="mid_range",
        free_text=args.note,
    )

    print(f"{'=' * 70}")
    print(f"{args.dest} · {args.days} days · {args.repeats} runs per mode")
    print(f"{'=' * 70}")

    # The upstream agents are run once and their results reused, so the only
    # thing differing between modes is the planner's own call.
    print("\npreparing shared inputs...")
    attractions = [r.attraction for r in search_attractions(request)]
    weather = get_weather(request.destination, request.start_date, request.end_date)
    hotels = rank_hotels(request, attractions=attractions, limit=1)
    hotel = hotels[0].hotel if hotels else None
    print(f"  {len(attractions)} attractions, "
          f"{sum(1 for w in weather.values() if w.available)}/{len(weather)} days forecast, "
          f"hotel: {hotel.name if hotel else 'none'}")

    rows: list[dict] = []
    # for thinking in (False):
    label = "reasoning OFF"
    print(f"\n{'-' * 70}\n{label}\n{'-' * 70}")
    
    for run in range(1, args.repeats + 1):
        client = get_llm()
        started = time.perf_counter()
        try:
            plan = plan_trip(
                request, attractions=attractions, hotel=hotel,
                weather=weather, thinking=False,
            )
            elapsed = time.perf_counter() - started
        except Exception as exc:
            import traceback; traceback.print_exc()
            print(f"  run {run}: FAILED — {exc}")
            rows.append({"mode": label, "run": run, "seconds": -1,
                          "completion_tokens": 0, "days": 0, "items_total": 0,
                          "items_per_day": 0, "meals_total": 0,
                          "mean_day_spread_km": 0, "max_day_spread_km": 0,
                          "wet_violations": 0, "repeats": 0})
            continue
    
        row = {"mode": label, "run": run,
                **measure(plan, elapsed, client.last_usage)}
        rows.append(row)
        print(f"  run {run}: {row['seconds']:>5.1f}s  "
              f"{row['completion_tokens']:>5} tok  "
              f"{row['items_total']} items ({row['items_per_day']}/day)  "
              f"spread {row['mean_day_spread_km']:.1f} km")
        if run == 1:
            show(plan)    

    # --- comparison ---
    print(f"\n{'=' * 70}\nCOMPARISON\n{'=' * 70}")
    fields = ["seconds", "completion_tokens", "items_per_day",
               "mean_day_spread_km", "max_day_spread_km", "wet_violations", "repeats"]
    off = [r for r in rows if r["mode"] == "reasoning OFF" and r["seconds"] > 0]
    on = [r for r in rows if r["mode"] == "reasoning ON" and r["seconds"] > 0]

    print(f"{'metric':<22}{'OFF':>10}{'ON':>10}{'change':>12}")
    for field in fields:
        if not off or not on:
            continue
        a = statistics.mean(r[field] for r in off)
        b = statistics.mean(r[field] for r in on)
        if a == 0:
            change = "—" if b == 0 else "+"
        else:
            change = f"{(b - a) / a * 100:+.0f}%"
        print(f"{field:<22}{a:>10.2f}{b:>10.2f}{change:>12}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    exists = args.out.exists()
    with open(args.out, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"\n→ {args.out}")
    print("\nLower spread means days that hang together geographically.")
    print("Reasoning is worth enabling only if it improves something that")
    print("matters more than the latency and tokens it costs.")


if __name__ == "__main__":
    main()
