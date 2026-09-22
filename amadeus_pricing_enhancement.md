# Live Hotel Pricing Enhancement (Amadeus) — Draft Text

Two ready-to-paste versions of the same decision. Use **Option A** if implementing
(add to Requirements as a Could Have + supporting rows), or **Option B** if deferring
(add to the final report's Future Work). Do not use both.

---

## Option A — Could Have requirement (for the proposal / requirements section)

**CH-x: Live hotel rate enhancement.** The system *could* augment the accommodation
component of the budget estimate with live nightly rates retrieved from the Amadeus
Self-Service Hotel Search API for the demonstration cities. Live rates, where
available, take precedence over the heuristic table for the accommodation line only;
all other budget categories (meals, admissions, transport) remain heuristic. The
integration is confined to the Budget Tool's data backend behind its existing
interface: no agent, workflow, or data-model changes are required. Where a live rate
is unavailable — due to coverage gaps in the test environment, API failure, or
exhausted quota — the system falls back automatically and silently to the heuristic
estimate. The UI distinguishes the two provenances explicitly, labelling values as
"live rate (Amadeus)" or "estimate (heuristic)".

**Rationale.** Unlike Booking.com (partner-gated) and Google Places (display and
caching terms incompatible with the Leaflet rendering layer and the pre-fetch
architecture; returns categorical price levels rather than prices), Amadeus
Self-Service grants instant keyed access with a free test-environment quota
sufficient for demonstration use. The integration also serves as a live validation
of the layered architecture: exchanging the pricing data backend without touching
the agent layer is precisely the extensibility claim made in the Conclusion.

**Constraints and acceptance.**
- Implemented only after the MVP is fully working locally (same rule as deployment).
- Effort budget: 2–3 days, scheduled opportunistically within Phase 3 or later.
- OAuth 2.0 client-credentials flow with automatic token refresh (tokens expire
  every 30 minutes); the API key is held server-side in `.env` and never exposed
  to the frontend.
- Amadeus responses for the demonstration cities may be cached for the session
  within the test environment's terms; the heuristic table remains the invariant
  offline path, so the system's zero-runtime-dependency guarantee is preserved
  whenever the enhancement is disabled or degraded.
- Acceptance: with the enhancement enabled and the API reachable, at least one
  demonstration city displays a live-labelled accommodation rate; with the API
  unreachable, itinerary generation succeeds unchanged with heuristic labels;
  the budget breakdown total equals the sum of its lines in both modes.

**Risk table row (add to the risk register).**

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Amadeus test environment lacks rate data for a demonstration city or date | Medium | Low | Coverage verified per city before demo (as with Overpass); automatic fallback to heuristic estimate; enhancement is Could Have and detachable without trace |

---

## Option B — Future Work paragraph (for the final report)

**Live pricing integration.** All monetary figures in the current system are
transparent heuristic estimates, a deliberate scoping decision: commercial rate
sources are either partner-gated (Booking.com) or carry display and caching terms
incompatible with this system's Leaflet rendering layer and pre-fetched-cache
architecture (Google Places, whose `priceLevel` field is in any case a categorical
0–4 band rather than a price). A practical path to real rates nevertheless exists.
The Amadeus Self-Service Hotel Search API provides instantly keyed access to live
nightly rates with a free test-environment quota adequate for demonstration use,
with the caveats that its coverage favours branded chain hotels over the motels and
hostels characteristic of New Zealand's mid- and budget tiers, and that it addresses
only the accommodation category. Because pricing is encapsulated in the Budget
Tool behind a stable interface, adopting it would be a data-backend substitution —
live rates preferred for accommodation, automatic fallback to the heuristic table
on any failure, and explicit provenance labels in the UI — with no changes to the
agents, the LangGraph workflow, or the data model. That such an extension is
confined to a single component is itself evidence for the architecture's central
claim: grounded data sources and deterministic computation can be exchanged or
upgraded independently of the multi-agent orchestration they support.
