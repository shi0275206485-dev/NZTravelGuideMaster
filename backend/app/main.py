"""TravelGuideMaster FastAPI application.

Route surface is deliberately small:

    GET  /api/health          liveness + cache stats
    GET  /api/destinations    supported destinations for the input form
    POST /api/plan            generate an itinerary (LLM-backed, protected)
    POST /api/recompute       recompute routes + budget after an edit (no LLM)

Only /api/plan touches the LLM, so it is the only route carrying the
access-code dependency and the strict rate limit — the recompute route is
deterministic and cheap, and gating it would make editing feel punitive.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .cache import Cache
from .config import Settings, get_settings
from .models import DESTINATION_CONFIG, TripPlan, TripRequest
from .pipeline import run_pipeline
from .recompute import RecomputeError, recompute
from .poi_repository import PoiUnavailableError

# --------------------------------------------------------------------------
# Shared resources
#
# Initialised lazily rather than in a lifespan hook, so importing the app
# from a script or a test that doesn't run startup events still works.
# --------------------------------------------------------------------------

@lru_cache
def get_cache() -> Cache:
    return Cache(get_settings().cache_db_path)


app = FastAPI(
    title="TravelGuideMaster API",
    description="Multi-agent AI travel planner for New Zealand destinations",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Access control (FR10)
#
# Checked before any LLM work begins, so rejected requests cost nothing.
# Disabled automatically when no code is configured, which keeps local
# development frictionless while the deployment sets one.
# --------------------------------------------------------------------------

def require_access_code(
    x_access_code: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.access_control_enabled:
        return
    import secrets

    if not secrets.compare_digest(x_access_code, settings.demo_access_code):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing access code. Please enter the demo code provided.",
        )


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/api/health")
def health(cache: Cache = Depends(get_cache)) -> dict:
    return {"status": "ok", "cache": cache.stats()}


@app.get("/api/destinations")
def destinations() -> list[dict]:
    """Supported destinations, for populating the input form (FR1)."""
    return [
        {
            "name": name,
            "lat": cfg["lat"],
            "lon": cfg["lon"],
            "tier": cfg["tier"],
        }
        for name, cfg in DESTINATION_CONFIG.items()
    ]


@app.post("/api/plan", response_model=TripPlan,
          dependencies=[Depends(require_access_code)])
def generate_plan(request: TripRequest) -> TripPlan:
    """Generate an itinerary.

    Runs the four-node pipeline. Individual agents degrade internally —
    a missing forecast or an unreachable model reduces what the itinerary
    knows rather than failing the request — so a 5xx here means there was
    genuinely nothing to plan with.
    """
    state = run_pipeline(request)
    plan = state.get("plan")

    if plan is None:
        errors = state.get("errors") or ["Could not generate an itinerary."]
        # Missing pre-fetched data is a deployment problem, not a bad
        # request: the destination is advertised as supported.
        raise HTTPException(status_code=503, detail=errors[0])

    return plan


@app.post("/api/recompute", response_model=TripPlan)
def recompute_plan(plan: TripPlan) -> TripPlan:
    """Bring routes and costs back into line after an edit (FR8).

    Deterministic by design — no model call — so an edit settles in about
    the time it took to make. Not gated by the access code: it costs a
    routing lookup and some arithmetic, and gating it would make editing
    feel punitive next to generating.
    """
    try:
        return recompute(plan, cache=get_cache())
    except RecomputeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
