"""AttractionSearchAgent.

Retrieves the candidate pool for a destination and uses the LLM to select
and rank the attractions that suit the traveller, returning a shortlist the
PlannerAgent can schedule.

The division of labour matters. The pre-fetch pipeline already ranked
candidates by *significance* — how notable a place is, inferred from OSM
tags. What it cannot judge is *suitability*: whether a geothermal park
suits someone travelling with a five-year-old, or whether a maritime museum
is what "culture" means to this traveller. That judgement is what the model
is here for, and it is the one thing in this system the model is genuinely
better at than a rule.

The model only ever emits ids. Names, coordinates, categories and opening
hours are resolved from the cache afterwards, so a hallucinated id fails
validation instead of surfacing as a plausible-looking place that does not
exist.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..llm import LLMClient, LLMError, get_llm
from ..models import Attraction, TripRequest
from ..poi_repository import load_attractions
from ..prompt_utils import fence_free_text

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the attraction specialist for a New Zealand travel planner.

You are given a list of candidate places and a traveller's preferences. You
choose which places suit them and put the best first. You never invent
places: you may only return ids from the list you are given, and you never
invent facts about them — you know each place's name and category, and
nothing else.

Output a single JSON object and nothing else — no markdown fences, no
commentary."""


class SelectedAttraction(BaseModel):
    """One choice, as the model expresses it."""

    id: str = Field(pattern=r"^A\d{2,3}$")
    name: str = Field(
        default="",
        max_length=200,
        description=(
            "The candidate's name, echoed back. Never read — the real name is "
            "resolved from the cache by id — but the field has to exist. "
            "Without it the model wedged the name in as a stray value "
            '(\'{"id": "A32", "Centennial Park", "reason": ...}\'), which '
            "shifted the object's keys and failed validation on three runs in "
            "five. Giving it somewhere to put the name is cheaper than "
            "instructing it not to want one."
        ),
    )
    reason: str = Field(
        min_length=5,
        max_length=130,
        description="Why this suits the traveller — a short phrase, not a sentence",
    )


class AttractionSelection(BaseModel):
    """The narrow schema the model must satisfy."""

    selected: list[SelectedAttraction] = Field(min_length=1, max_length=30)

    @field_validator("selected")
    @classmethod
    def ids_are_unique(cls, v: list[SelectedAttraction]) -> list[SelectedAttraction]:
        seen = set()
        for item in v:
            if item.id in seen:
                raise ValueError(f"attraction {item.id} selected more than once")
            seen.add(item.id)
        return v


class RankedAttraction(BaseModel):
    """An agent result: a real attraction plus why it was chosen."""

    attraction: Attraction
    reason: str
    rank: int


def _format_candidates(attractions: list[Attraction]) -> str:
    """One line per candidate.

    Coordinates are withheld deliberately. Given them, models try to reason
    about distance and do it badly; geography is handled downstream by the
    routing tool and by grouping days geographically at the planning stage.
    """
    lines = []
    for a in attractions:
        parts = [f"{a.id} | {a.name} | {a.category}"]
        if a.has_wikidata:
            parts.append("well-known")
        lines.append("  " + " | ".join(parts))
    return "\n".join(lines)


def build_prompt(request: TripRequest, candidates: list[Attraction], target: int) -> str:
    preferences = ", ".join(request.preferences)
    note = fence_free_text(request.free_text, "the candidate list")

    return f"""Choose attractions in {request.destination} for a {request.num_days}-day trip.

Traveller preferences: {preferences}
Budget level: {request.budget_level.replace('_', ' ')}
{note}
Candidates:
{_format_candidates(candidates)}

Rules:
- Select about {target} attractions, best first.
- Use ONLY ids from the list above. Do not invent ids.
- Do not select the same id twice.
- Favour places that match the stated preferences. Include some variety so
  the trip is not {request.num_days} days of the same kind of place.
- Give a SHORT phrase per choice (under 12 words) saying why it suits them.
  Not a full sentence — these render as one-line labels.
- Describe only what kind of place it is and what someone does there. Do NOT
  state facilities, access, prices, opening times, or policies — whether a
  place allows dogs, has step-free access, charges admission, or is reachable
  by cable car. You have not been given that information, and a traveller who
  acts on a wrong claim is turned away at the door.
- Output ONLY this JSON structure:
{{
  "selected": [
    {{"id": "A01", "name": "Te Puia", "reason": "Geothermal valley with active geysers"}},
    {{"id": "A07", "name": "Redwoods Treewalk", "reason": "Forest walking track through tall redwoods"}}
  ]
}}"""


def _fallback_selection(candidates: list[Attraction], target: int) -> list[RankedAttraction]:
    """Significance order, used when the model cannot be reached.

    Degraded but honest: the traveller still gets the destination's most
    notable places, just without preference matching. Better than failing
    the whole request over one unavailable service.
    """
    logger.warning("attraction agent falling back to significance ranking")
    return [
        RankedAttraction(
            attraction=a,
            reason="Selected by general significance (preference matching unavailable).",
            rank=i,
        )
        for i, a in enumerate(candidates[:target], start=1)
    ]


def search_attractions(
    request: TripRequest,
    llm: Optional[LLMClient] = None,
    candidates: Optional[list[Attraction]] = None,
) -> list[RankedAttraction]:
    """Select and rank attractions for this traveller.

    Returns roughly 4 per day, giving the planner enough to choose from
    without letting the itinerary prompt grow unnecessarily.
    """
    pool = candidates if candidates is not None else load_attractions(request.destination)
    if not pool:
        return []

    # Six per day. The planner schedules at most four, so this leaves it
    # roughly half the shortlist to discard — which is the point. At three
    # per day it had exactly as many places as slots to fill, could not
    # group days geographically without reusing somewhere, and failed
    # validation on every run. Selection needs something to reject.
    target = min(max(request.num_days * 6, 10), 30)
    by_id = {a.id: a for a in pool}

    try:
        client = llm or get_llm()
        selection = client.complete_structured(
            build_prompt(request, pool, target),
            AttractionSelection,
            system=SYSTEM_PROMPT,
            max_tokens=1200,
            # Near-deterministic on purpose. At the client default of 0.4,
            # repeating one request five times produced shortlists that
            # overlapped only 38-47% — a traveller pressing "plan" twice got
            # half a different trip. Variety is not a feature here: asked for
            # the nine places that best suit someone, the honest answer is
            # the same nine places every time.
            # Greedy decoding. Measured across four batches of repeated
            # identical requests: overlap between runs was 38% at 0.4, 44-47%
            # at 0.1, and 78% at 0. Choosing from a fixed list has a best
            # answer, and a traveller who regenerates expects the same trip
            # back, not a different one.
            temperature=0.0,
        )
    except LLMError:
        return _fallback_selection(pool, target)

    ranked: list[RankedAttraction] = []
    unknown: list[str] = []
    for choice in selection.selected:
        attraction = by_id.get(choice.id)
        if attraction is None:
            # Not fatal on its own: drop the invented id and keep the rest.
            unknown.append(choice.id)
            continue
        ranked.append(
            RankedAttraction(
                attraction=attraction, reason=choice.reason, rank=len(ranked) + 1
            )
        )

    if unknown:
        logger.warning("model returned %d unknown ids: %s", len(unknown), unknown)

    if not ranked:
        # Every id was invented — treat as a failed call rather than
        # returning an empty itinerary.
        return _fallback_selection(pool, target)

    return ranked
