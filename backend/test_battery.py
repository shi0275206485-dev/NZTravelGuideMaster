"""Phase 2 test battery: boundaries, cross-destination comparison, stability.

Three suites, each answering a question the single-run pipeline test cannot:

  boundary    Does the system fail safely at the edges — one-day trips,
              dates past the forecast window, empty pools, oversized input?
  destination Do the three destinations behave comparably given identical
              input, or does one of them quietly under-perform?
  stability   Does the same request produce a consistent answer, or does
              the model's variance make the output unreliable?

Every run appends to CSV so results accumulate across sessions and can be
read into the report without re-running anything.

Usage:
  python test_battery.py                    # all three suites
  python test_battery.py --suite boundary
  python test_battery.py --suite stability --repeats 5
  python test_battery.py --out results/     # where the CSVs land
"""

from __future__ import annotations

import argparse
import csv
import statistics
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from app.agents.attraction_agent import search_attractions
from app.agents.hotel_agent import rank_hotels
from app.agents.weather_agent import get_weather, summarise_trip
from app.budget import compute_budget
from app.models import DESTINATION_CONFIG, DayPlan, ItineraryItem, TripRequest

TODAY = date.today()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def make_request(**overrides) -> TripRequest:
    defaults = dict(
        destination="Rotorua",
        start_date=TODAY + timedelta(days=14),
        end_date=TODAY + timedelta(days=16),
        preferences=["nature", "culture"],
        budget_level="mid_range",
        free_text=None,
    )
    defaults.update(overrides)
    return TripRequest(**defaults)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)
    print(f"\n  → {path} ({'appended' if exists else 'created'})")


def indicative_budget(request: TripRequest, attractions, hotels):
    """Cost the whole shortlist spread across the days.

    An upper bound, not the traveller's figure — the planner will pick a
    subset — but enough to check the budget tool behaves on real input.
    """
    if not attractions:
        return None
    per_day = max(1, len(attractions) // request.num_days)
    days = []
    for d in range(request.num_days):
        chunk = attractions[d * per_day : (d + 1) * per_day][:5]
        if not chunk:
            break
        days.append(DayPlan(
            day=d + 1,
            summary=f"Day {d + 1} placeholder for costing purposes.",
            items=[ItineraryItem(
                attraction=r.attraction,
                time_slot=["morning", "afternoon", "evening"][i % 3],
                note="Placeholder note for budget calculation.",
            ) for i, r in enumerate(chunk)],
        ))
    if not days:
        return None
    return compute_budget(
        request.destination, days,
        hotel=hotels[0].hotel if hotels else None,
        budget_level=request.budget_level,
        start_date=request.start_date,
    )


# ---------------------------------------------------------------------------
# suite 1 — boundaries
# ---------------------------------------------------------------------------

BOUNDARY_CASES = [
    {
        "case": "single-day trip",
        "expect": "one day, shortlist floor of 6 still applies",
        "build": lambda: make_request(end_date=TODAY + timedelta(days=14)),
    },
    {
        "case": "maximum 7-day trip",
        "expect": "7 days, shortlist capped at 15",
        "build": lambda: make_request(end_date=TODAY + timedelta(days=20)),
    },
    {
        "case": "8-day trip (over limit)",
        "expect": "rejected by TripRequest validation",
        "build": lambda: make_request(end_date=TODAY + timedelta(days=21)),
    },
    {
        "case": "reversed dates",
        "expect": "rejected by TripRequest validation",
        "build": lambda: make_request(
            start_date=TODAY + timedelta(days=16),
            end_date=TODAY + timedelta(days=14),
        ),
    },
    {
        "case": "trip starts today",
        "expect": "forecast available from day 1",
        "build": lambda: make_request(
            start_date=TODAY, end_date=TODAY + timedelta(days=2)
        ),
    },
    {
        "case": "starts inside forecast window, ends outside",
        "expect": "mixed availability across the days",
        "build": lambda: make_request(
            start_date=TODAY + timedelta(days=14),
            end_date=TODAY + timedelta(days=18),
        ),
    },
    {
        "case": "far future (90 days out)",
        "expect": "no weather, no HTTP call, itinerary still produced",
        "build": lambda: make_request(
            start_date=TODAY + timedelta(days=90),
            end_date=TODAY + timedelta(days=92),
        ),
    },
    {
        "case": "all six preferences at once",
        "expect": "a varied shortlist rather than a collapse to one category",
        "build": lambda: make_request(preferences=[
            "nature", "culture", "food", "family", "adventure", "relaxation"
        ]),
    },
    {
        "case": "free text at the 500-char limit",
        "expect": "accepted, prompt stays well-formed",
        "build": lambda: make_request(free_text="I want somewhere quiet. " * 20),
    },
    {
        "case": "free text over the limit",
        "expect": "rejected by TripRequest validation",
        "build": lambda: make_request(free_text="x" * 501),
    },
    {
        "case": "free text that is only whitespace",
        "expect": "normalised to None, treated as absent",
        "build": lambda: make_request(free_text="   \n\n   "),
    },
    {
        "case": "unsupported destination",
        "expect": "rejected by TripRequest validation",
        "build": lambda: make_request(destination="Queenstown"),
    },
]


def run_boundary(out_dir: Path) -> None:
    print(f"\n{'=' * 74}\nBOUNDARY SUITE\n{'=' * 74}")
    rows = []

    for spec in BOUNDARY_CASES:
        print(f"\n▸ {spec['case']}")
        print(f"  expect: {spec['expect']}")
        row = {
            "case": spec["case"], "expected": spec["expect"],
            "outcome": "", "days": "", "attractions": "", "hotels": "",
            "weather_days_available": "", "budget_low": "", "budget_high": "",
            "seconds": "", "detail": "",
        }

        try:
            request = spec["build"]()
        except ValidationError as exc:
            first = exc.errors()[0]
            row["outcome"] = "rejected at validation"
            row["detail"] = first["msg"][:120]
            print(f"  → rejected: {first['msg'][:90]}")
            rows.append(row)
            continue
        except Exception as exc:
            row["outcome"] = "unexpected error building request"
            row["detail"] = repr(exc)[:120]
            print(f"  → ERROR: {exc}")
            rows.append(row)
            continue

        started = time.perf_counter()
        try:
            attractions = search_attractions(request)
            weather = get_weather(
                request.destination, request.start_date, request.end_date
            )
            hotels = rank_hotels(
                request, attractions=[r.attraction for r in attractions], limit=5
            )
            budget = indicative_budget(request, attractions, hotels)
            elapsed = time.perf_counter() - started

            available = sum(1 for w in weather.values() if w.available)
            row.update({
                "outcome": "completed",
                "days": request.num_days,
                "attractions": len(attractions),
                "hotels": len(hotels),
                "weather_days_available": f"{available}/{len(weather)}",
                "budget_low": f"{budget.total.low_nzd:.0f}" if budget else "",
                "budget_high": f"{budget.total.high_nzd:.0f}" if budget else "",
                "seconds": f"{elapsed:.2f}",
                "detail": summarise_trip(weather)[:120],
            })
            print(f"  → {request.num_days}d · {len(attractions)} attractions · "
                  f"{len(hotels)} hotels · weather {available}/{len(weather)} · "
                  f"{elapsed:.1f}s")
            if budget:
                print(f"    budget NZD {budget.total.low_nzd:.0f}–"
                      f"{budget.total.high_nzd:.0f}")
        except Exception as exc:
            row["outcome"] = "FAILED"
            row["detail"] = repr(exc)[:200]
            row["seconds"] = f"{time.perf_counter() - started:.2f}"
            print(f"  → FAILED: {exc}")
            traceback.print_exc(limit=2)

        rows.append(row)

    write_csv(out_dir / "boundary.csv", rows, list(rows[0].keys()))


# ---------------------------------------------------------------------------
# suite 2 — cross-destination
# ---------------------------------------------------------------------------

def run_destinations(out_dir: Path) -> None:
    print(f"\n{'=' * 74}\nDESTINATION SUITE — identical request, all three\n{'=' * 74}")
    rows = []

    for destination in DESTINATION_CONFIG:
        print(f"\n▸ {destination}")
        request = make_request(destination=destination)
        started = time.perf_counter()

        attractions = search_attractions(request)
        hotels = rank_hotels(
            request, attractions=[r.attraction for r in attractions], limit=5
        )
        budget = indicative_budget(request, attractions, hotels)
        elapsed = time.perf_counter() - started

        categories: dict[str, int] = {}
        for r in attractions:
            key = r.attraction.category
            categories[key] = categories.get(key, 0) + 1

        landmarks = sum(1 for r in attractions if r.attraction.has_wikidata)
        distances = [h.distance_km for h in hotels]
        fallback = any("preference matching unavailable" in r.reason
                       for r in attractions)

        rows.append({
            "destination": destination,
            "attractions": len(attractions),
            "landmark_share": f"{landmarks}/{len(attractions)}" if attractions else "0/0",
            "categories": "; ".join(f"{k}={v}" for k, v in sorted(categories.items())),
            "hotels": len(hotels),
            "nearest_hotel_km": f"{min(distances):.2f}" if distances else "",
            "median_hotel_km": f"{statistics.median(distances):.2f}" if distances else "",
            "budget_low": f"{budget.total.low_nzd:.0f}" if budget else "",
            "budget_high": f"{budget.total.high_nzd:.0f}" if budget else "",
            "seconds": f"{elapsed:.2f}",
            "used_fallback": fallback,
            "top_three": " | ".join(r.attraction.name for r in attractions[:3]),
        })

        print(f"  {len(attractions)} attractions ({landmarks} well-known) · "
              f"{elapsed:.1f}s")
        print(f"  mix: {rows[-1]['categories']}")
        print(f"  hotels: nearest {rows[-1]['nearest_hotel_km']} km, "
              f"median {rows[-1]['median_hotel_km']} km")
        for r in attractions[:3]:
            print(f"    · {r.attraction.name} — {r.reason}")

    write_csv(out_dir / "destinations.csv", rows, list(rows[0].keys()))


# ---------------------------------------------------------------------------
# suite 3 — stability
# ---------------------------------------------------------------------------

def run_stability(out_dir: Path, repeats: int) -> None:
    print(f"\n{'=' * 74}\nSTABILITY SUITE — same request x{repeats}\n{'=' * 74}")
    request = make_request(free_text="travelling with a 5-year-old")
    rows = []
    all_ids: list[set[str]] = []
    latencies: list[float] = []

    for run in range(1, repeats + 1):
        started = time.perf_counter()
        attractions = search_attractions(request)
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)

        ids = {r.attraction.id for r in attractions}
        all_ids.append(ids)
        rows.append({
            "run": run,
            "count": len(attractions),
            "seconds": f"{elapsed:.2f}",
            "used_fallback": any(
                "preference matching unavailable" in r.reason for r in attractions
            ),
            "ids": ",".join(sorted(ids)),
            "top_three": " | ".join(r.attraction.name for r in attractions[:3]),
        })
        print(f"  run {run}: {len(attractions)} picks, {elapsed:.1f}s — "
              f"{rows[-1]['top_three']}")

    if all_ids:
        always = set.intersection(*all_ids)
        ever = set.union(*all_ids)
        # Jaccard against the first run: how much of the answer is the same
        # question asked twice, and how much is the model's own variance.
        overlaps = [
            len(all_ids[0] & other) / len(all_ids[0] | other)
            for other in all_ids[1:]
        ] or [1.0]

        print(f"\n  picked every time : {len(always)} — {sorted(always)}")
        print(f"  picked at least once: {len(ever)}")
        print(f"  mean overlap with run 1: {statistics.mean(overlaps):.0%}")
        print(f"  latency: mean {statistics.mean(latencies):.1f}s, "
              f"min {min(latencies):.1f}s, max {max(latencies):.1f}s")

        rows.append({
            "run": "SUMMARY",
            "count": f"stable={len(always)} of {len(ever)} ever picked",
            "seconds": f"mean {statistics.mean(latencies):.2f}",
            "used_fallback": "",
            "ids": f"mean_overlap={statistics.mean(overlaps):.2f}",
            "top_three": f"always: {', '.join(sorted(always))}",
        })

    write_csv(out_dir / "stability.csv", rows, list(rows[0].keys()))


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["boundary", "destination", "stability", "all"],
                     default="all")
    ap.add_argument("--repeats", type=int, default=3,
                     help="runs for the stability suite")
    ap.add_argument("--out", type=Path, default=Path("test_results"))
    args = ap.parse_args()

    if args.suite in ("boundary", "all"):
        run_boundary(args.out)
    if args.suite in ("destination", "all"):
        run_destinations(args.out)
    if args.suite in ("stability", "all"):
        run_stability(args.out, args.repeats)

    print(f"\n{'=' * 74}\nCSVs written to {args.out.resolve()}")


if __name__ == "__main__":
    main()
