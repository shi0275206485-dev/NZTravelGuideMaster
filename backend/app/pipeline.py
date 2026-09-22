"""
The planning pipeline, as a LangGraph workflow.

Four nodes, run in sequence:
    attractions -> weather -> hotel -> plan

The order is a dependency order, not a preference. Hotels are ranked against where the
traveller is actually going, so attractions must be chosen first; the planner needs all
three before it can arrange anything. Attractions and weather are genuinely independent
and could run in parallel, which is the obvious next optimisation - but with the pipeline
at a few seconds end to end, sequential execution is not what is costing anything, and it
keeps failures easy to attribute.

Each node degrades rather than raising: a missing forecast or an unreachable model
reduces what the itinerary knows, it does not stop the itinerary existing. Only an
empty attraction pool is fatal, because there is then nothing to plan.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .agents.attraction_agent import RankedAttraction, search_attractions
from .agents.hotel_agent import RankedHotel, rank_hotels
from .agents.planner_agent import PlanningFailed, plan_trip
from .agents.weather_agent import get_weather
from .models import TripPlan, TripRequest, WeatherInfo
from .routing import add_routes
from .poi_repository import PoiUnavailableError

logger = logging.getLogger(__name__)

def merge_timings(current:dict ,update: dict) -> dict:
    """Accumulate per-node timings instead of overwriting them."""
    return {**(current or {}),**(update or {})}

def merge_errors(current: list, update: list) -> list:
    """Keep every failure, in the order the nodes hit them.

    Without a reducer the last node to report replaces the first, and the
    first is usually the root cause: no POI data makes the planner fail
    too, and "could not build any days" is the symptom of the message it
    would have overwritten.
    """
    return [*(current or []), *(update or [])]

class PipelineState(TypedDict, total= False):
    """What flows between nodes.
    
    Carries the agents' intermediate results as well as the final plan, so a caller
    that wants to show its working - which places were shortlisted and why, which hotels
    were close - does not have to re-run anything.
    """

    request: TripRequest
    attractions: list[RankedAttraction]
    weather: dict[int, WeatherInfo]
    hotels: list[RankedHotel]
    plan: Optional[TripPlan]
    # Fatal only: something that stops the itinerary existing. A node that
    # merely loses a capability records it in `degradations`, so the first
    # entry here stays the reason a request failed rather than a warning
    # that happened to be logged along the way.
    errors: Annotated[list[str], merge_errors]
    degradations: Annotated[list[str], merge_errors]
    timings: Annotated[dict[str, float], merge_timings]

def _timed(name: str, fn):
    """Run a node's work, recording how long it took."""
    started = time.perf_counter()
    result = fn()
    return result, {name: round(time.perf_counter() - started, 3)}

def attractions_node(state: PipelineState) -> dict:
    request = state["request"]
    try:
        result, timing = _timed("attractions", lambda: search_attractions(request))
        return {"attractions": result, "timings": timing}
    except PoiUnavailableError as exc:
        logger.error("no POI data for %s: %s", request.destination, exc)
        return {"attractions":[], "errors": [str(exc)]}

def weather_node(state: PipelineState) -> dict:
    request = state["request"]
    result, timing = _timed("weather", lambda: get_weather(
        request.destination, request.start_date, request.end_date
    ))
    return {"weather": result, "timings": timing}

def hotel_node(state: PipelineState)->dict:
    request = state["request"]
    chosen = [r.attraction for r in state.get("attractions",[])]
    try: 
        result, timing = _timed("hotel", lambda: rank_hotels(
            request, attractions= chosen, limit=5
        ))
        return {"hotels": result, "timings": timing}
    except PoiUnavailableError as exc:
        # A trip without a recommended hotel is still a trip.
        logger.warning("no accommodation data for %s: %s", request.destination, exc)
        return {"hotels": [], "degradations": [f"No accommodation data: {exc}"]}
def routing_node(state: PipelineState)->dict:
    """Draw the roads between each day's stops
    Runs after planning because it needs the finished order of the day.
    Enrichment only: a plan without routes is still a plan, so failures
    here leave the itinerary intact and the map without a line.
    """
    plan = state.get("plan")
    if plan is None:
        return {}
    result, timing = _timed("routing", lambda: add_routes(plan))
    return {"plan": result, "timings": timing}

def planner_node(state: PipelineState)->dict:
    request = state["request"]
    attractions = [r.attraction for r in state.get("attractions",[])]
    hotels = state.get("hotels",[])

    try:
        result, timing = _timed("planner", lambda: plan_trip(
            request,
            attractions = attractions,
            hotel= hotels[0].hotel if hotels else None,
            hotel_nightly= hotels[0].nightly_cost if hotels else None,
            weather= state.get("weather",{})
        ))
        return {"plan": result, "timings": timing}
    except PlanningFailed as exc:
        logger.error("planning failed for %s: %s", request.destination, exc)
        # "errors", not "error": LangGraph drops an update key that is not
        # in the state schema without raising or warning, so the misspelt
        # version left the caller with an empty list and the traveller with
        # a generic failure message while the real reason went only to the
        # log.
        return {"plan": None, "errors": [str(exc)]}

def build_graph():
    graph = StateGraph(PipelineState)
    graph.add_node("attractions", attractions_node)
    graph.add_node("weather", weather_node)
    graph.add_node("hotel", hotel_node)
    graph.add_node("planner", planner_node)
    graph.add_node("routing", routing_node)

    graph.add_edge(START,"attractions")
    graph.add_edge("attractions", "weather")
    graph.add_edge("weather", "hotel")
    graph.add_edge("hotel", "planner")
    graph.add_edge("planner", "routing")
    graph.add_edge("routing", END)
    return graph.compile()

_pipeline = None

def get_pipeline():
    """Compiled once; the graph is stateless between runs"""
    global _pipeline
    if _pipeline is None:
        _pipeline = build_graph()
    return _pipeline
def run_pipeline(request: TripRequest)-> PipelineState:
    """Run the full pipeline and return the final state.
    
    Return the state rather than just the plan so callers can inspect the
    intermediate results, the per-node timings, and any degradation that occurred
    along the way.
    """

    started = time.perf_counter()
    final: PipelineState =get_pipeline().invoke({
        "request": request, "errors":[], "degradations": [], "timings": {}
    })
    total = round(time.perf_counter()-started, 3)
    final["timings"] = {**final.get("timings", {}), "total": total}

    logger.info(
        "pipeline for %s completed in %.2fs (%s)",
        request.destination, total,
        ", ".join(f"{k}={v}s" for k,v in final["timings"].items() if k!="total"),
    )
    return final