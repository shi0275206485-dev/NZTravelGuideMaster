"""TravelGuideMaster FastAPI application.

Route surface is deliberately small:

    GET  /api/health          liveness + cache stats
    GET  /api/access          whether an access code is required
    POST /api/access/verify   check a code (counted against the attempt limit)
    GET  /api/destinations    supported destinations for the input form
    POST /api/plan            generate an itinerary (LLM-backed, protected)
    POST /api/recompute       recompute routes + budget after an edit (no LLM)

Only /api/plan touches the LLM, so it is the only route carrying the
access-code dependency and the usage limits in app/quota.py — the recompute
route is deterministic and cheap, and gating it would make editing feel
punitive.

Behind the reverse proxy the client address comes from X-Forwarded-For,
which uvicorn applies when started with --proxy-headers. That is safe only
because the backend port is not published: the proxy is the one thing that
can reach it, and Caddy discards forwarding headers sent by clients.
"""

from __future__ import annotations

from functools import lru_cache

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .access_codes import AccessCodes
from .cache import Cache
from .config import Settings, get_settings
from .quota import (Limit, Quota, QuotaExceeded, local_day,
                    seconds_to_local_midnight)
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


@lru_cache
def get_quota() -> Quota:
    return Quota(get_settings().cache_db_path)


@lru_cache
def get_codes() -> AccessCodes:
    return AccessCodes(get_settings().cache_db_path)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many(exc: QuotaExceeded) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=str(exc),
        headers={"Retry-After": str(exc.retry_after_s)},
    )


logger = logging.getLogger(__name__)

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
    request: Request,
    x_access_code: str = Header(default=""),
    settings: Settings = Depends(get_settings),
    quota: Quota = Depends(get_quota),
    codes: AccessCodes = Depends(get_codes),
) -> None:
    """Accept the operator's code from .env, or any live trial code.

    The code in .env is what switches access control on, and it stays the
    way in when every trial code has expired — a deployment must not fall
    open because the last one ran out.
    """
    if not settings.access_control_enabled:
        return
    import secrets

    attempts = Limit.parse(settings.access_code_attempts)
    client = _client(request)

    # A locked-out address is refused before its code is even compared, so
    # that a correct guess made during the lockout teaches it nothing.
    if attempts and quota.peek("code-fail", client, attempts) >= attempts.count:
        raise _too_many(QuotaExceeded(
            "Too many incorrect access codes from this address. Try again later.",
            quota.retry_after("code-fail", client, attempts),
        ))

    # Compared as bytes: compare_digest raises TypeError on non-ASCII str,
    # which turned a header containing, say, "é" into a 500.
    supplied = x_access_code.encode("utf-8")
    expected = settings.demo_access_code.encode("utf-8")
    accepted = secrets.compare_digest(supplied, expected)
    if not accepted:
        issued = codes.check(x_access_code)
        if issued is not None:
            accepted = True
            logger.info("access code %s (%s) used from %s",
                        issued.id, issued.label, client)

    if not accepted:
        # An absent code is not a guess — it cannot match, so charging it
        # protects nothing, and it used to cost every new visitor one of
        # their attempts before they had been asked for a code at all.
        if attempts and supplied:
            try:
                quota.hit("code-fail", client, attempts,
                          "Too many incorrect access codes from this address. "
                          "Try again later.")
            except QuotaExceeded as exc:
                raise _too_many(exc) from None
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing access code. Please enter the demo code provided.",
        )


def enforce_generation_limits(
    request: Request,
    settings: Settings = Depends(get_settings),
    quota: Quota = Depends(get_quota),
) -> None:
    """Count this generation against the per-address and daily limits.

    Runs after the access check, so a request with a wrong code spends
    nothing from these budgets — it has its own. Per-address first, then
    daily: one address hammering the route is refused on its own count and
    does not use up everyone else's day.

    Counted before the pipeline runs rather than after it succeeds. Two
    requests arriving together must not both slip under the cap, and a
    generation that fails part-way has still made its model calls.
    """
    per_client = Limit.parse(settings.rate_limit_generate)
    if per_client:
        try:
            quota.hit("plan-ip", _client(request), per_client,
                      "You have generated several itineraries in a short time. "
                      "Please wait before generating another — editing an "
                      "existing plan is not limited.")
        except QuotaExceeded as exc:
            raise _too_many(exc) from None

    if settings.daily_generation_cap > 0:
        # Keyed by the local date, so the window never needs to roll over:
        # tomorrow is a different row. The window only has to outlast a day.
        daily = Limit(count=settings.daily_generation_cap, window_s=2 * 86400)
        try:
            quota.hit("plan-day", local_day(), daily,
                      "Today's generation budget for this demo has been used. "
                      "It resets at midnight NZ time.")
        except QuotaExceeded as exc:
            raise _too_many(QuotaExceeded(str(exc), seconds_to_local_midnight())) from None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.get("/api/health")
def health(cache: Cache = Depends(get_cache)) -> dict:
    return {"status": "ok", "cache": cache.stats()}


@app.get("/api/access")
def access_status(settings: Settings = Depends(get_settings)) -> dict:
    """Whether this deployment asks for an access code.

    Lets the front end ask on arrival rather than after the first refused
    plan — and not at all in local development, where no code is set.
    """
    return {"required": settings.access_control_enabled}


@app.post("/api/access/verify", dependencies=[Depends(require_access_code)])
def verify_access() -> dict:
    """Check a code without spending anything.

    The check is the dependency itself, so a wrong code here is charged to
    the same per-address attempt limit as on /api/plan: a separate
    verification route must not become a way to guess for free.
    """
    return {"ok": True}


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
          # Order matters: FastAPI resolves these in sequence, and the
          # access check must run first so that a wrong code is charged to
          # its own bucket and never to the generation budgets.
          dependencies=[Depends(require_access_code),
                        Depends(enforce_generation_limits)])
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
