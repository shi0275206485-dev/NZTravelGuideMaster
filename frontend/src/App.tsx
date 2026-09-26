import { useEffect, useMemo, useRef, useState } from "react";
import AccessGate from "./components/AccessGate";
import ContourField from "./components/ContourField";
import DayCard from "./components/DayCard";
import DayEditor from "./components/DayEditor";
import PrintableItinerary from "./components/PrintableItinerary";
import DestinationBackdrop from "./components/DestinationBackdrop";
import HistorySidebar from "./components/HistorySidebar";
import MapView from "./components/MapView";
import PlanningProgress from "./components/PlanningProgress";
import TripForm from "./components/TripForm";
import TripSummary from "./components/TripSummary";
import { ApiError, api, getAccessCode } from "./api/client";
import { ExportError, captureMap, exportPdf, exportPng } from "./export";
import {
  addToHistory,
  loadHistory,
  removeFromHistory,
  type HistoryEntry,
} from "./history";
import type {
  Destination,
  ItineraryItem,
  TripPlan,
  TripRequest,
} from "./types/api";

type Status =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

/**
 * Three views, not one page.
 *
 * The form, the progress screen and the itinerary each want the whole
 * width and the reader's whole attention. Showing them together meant the
 * itinerary appeared beneath a form nobody was looking at any more, and
 * the several seconds of planning happened invisibly behind a disabled
 * button.
 */
type View =
  | { name: "form" }
  | { name: "planning"; request: TripRequest }
  | { name: "itinerary" };

export default function App() {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [view, setView] = useState<View>({ name: "form" });
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [selected, setSelected] = useState<Destination | null>(null);
  const [plan, setPlan] = useState<TripPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  // "checking" until the server says whether a code is required at all.
  // A stored code is trusted until the server refuses it; a refusal
  // brings the gate back rather than failing the plan in place.
  const [access, setAccess] = useState<"checking" | "locked" | "open">("checking");
  const [gateNotice, setGateNotice] = useState<string | null>(null);
  const [focusedDay, setFocusedDay] = useState<number | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());
  const [activeId, setActiveId] = useState<string | null>(null);
  // What the form should open with — set when reusing a past request.
  const [formSeed, setFormSeed] = useState<TripRequest | null>(null);
  const [editingDay, setEditingDay] = useState<number | null>(null);
  const [exporting, setExporting] = useState<"png" | "pdf" | null>(null);
  const printRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<HTMLDivElement>(null);
  const [mapImage, setMapImage] = useState<string | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.access(), api.destinations()])
      .then(([accessInfo, data]) => {
        setAccess(accessInfo.required && !getAccessCode() ? "locked" : "open");
        setDestinations(data);
        setSelected(data[0] ?? null);
        setStatus({ kind: "ready" });
      })
      .catch((error: unknown) => {
        setStatus({
          kind: "error",
          message:
            error instanceof ApiError
              ? error.message
              : import.meta.env.DEV
                ? "Cannot reach the planner service. Is the backend running on port 8000?"
                : "The planner service is not responding. Please try again shortly.",
        });
      });
  }, []);

  async function handlePlan(request: TripRequest) {
    setPlanError(null);
    setFocusedDay(null);
    setView({ name: "planning", request });
    try {
      const result = await api.plan(request);
      const { entries, entry } = addToHistory(history, request, result);
      setHistory(entries);
      setActiveId(entry.id);
      setPlan(result);
      setView({ name: "itinerary" });
      window.scrollTo({ top: 0 });
    } catch (error) {
      setPlan(null);
      setView({ name: "form" });
      if (error instanceof ApiError && error.status === 401) {
        setGateNotice(
          "Your access code is no longer accepted. Please enter it again."
        );
        setAccess("locked");
      } else {
        setPlanError(
          error instanceof ApiError ? error.message : "Something went wrong."
        );
      }
    }
  }

  /**
   * Applies a local edit, then asks the server to bring routes and costs
   * back into line.
   *
   * The edited plan is shown immediately and corrected when the response
   * lands. Waiting for the round trip before moving a stop would make a
   * drag feel like a form submission; the figures being briefly stale is
   * the smaller cost, and the server's answer always wins.
   */
  async function applyEdit(next: TripPlan) {
    setPlan(next);
    setEditError(null);
    setRecomputing(true);
    try {
      const settled = await api.recompute(next);
      setPlan(settled);
      // The history entry should reflect what the traveller now has, not
      // what was first generated.
      setHistory((current) =>
        current.map((e) =>
          e.id === activeId ? { ...e, plan: settled } : e
        )
      );
    } catch (error) {
      setEditError(
        error instanceof ApiError
          ? error.message
          : "Could not update the itinerary."
      );
    } finally {
      setRecomputing(false);
    }
  }

  async function handleExport(format: "png" | "pdf") {
    const node = printRef.current;
    if (!node || !plan) return;
    setExporting(format);
    setEditError(null);
    try {
      // Undim any focused day first: the export should show the whole
      // trip, not whichever day the cursor happened to rest on.
      setFocusedDay(null);
      setMapImage(await captureMap(mapRef.current));
      // One frame for the sheet to re-render with the map in it.
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
      await (format === "png" ? exportPng : exportPdf)(node, plan);
    } catch (error) {
      setEditError(
        error instanceof ExportError
          ? error.message
          : "Could not export the itinerary."
      );
    } finally {
      setExporting(null);
    }
  }

  /** Commits one day's staged edits and asks the server to settle them. */
  function saveDay(dayNumber: number, items: ItineraryItem[]) {
    if (!plan) return;
    const scheduled = new Set(
      plan.days.flatMap((day) =>
        (day.day === dayNumber ? items : day.items).map((i) => i.attraction.id)
      )
    );
    // A stop taken off a day goes back on the offer list, so removing one
    // is undoable. The server cannot work this out for itself: it is sent
    // the edited plan, in which the removed stop appears nowhere, and the
    // shortlist it draws from is the one this list carries.
    const removed = plan.days
      .flatMap((day) => day.items)
      .filter((i) => !scheduled.has(i.attraction.id))
      .map((i) => i.attraction);

    setEditingDay(null);
    applyEdit({
      ...plan,
      days: plan.days.map((day) =>
        day.day === dayNumber ? { ...day, items } : day
      ),
      // Keeps the offer list honest while the server's answer is in flight.
      alternatives: [...plan.alternatives, ...removed].filter(
        (a, index, all) =>
          !scheduled.has(a.id) && all.findIndex((x) => x.id === a.id) === index
      ),
    });
  }

  function showEntry(entry: HistoryEntry) {
    setPlan(entry.plan);
    setActiveId(entry.id);
    setFocusedDay(null);
    setEditingDay(null);
    setEditError(null);
    setPlanError(null);
    setView({ name: "itinerary" });
    window.scrollTo({ top: 0 });
  }

  function reuseEntry(entry: HistoryEntry) {
    setFormSeed(entry.request);
    const destination = destinations.find(
      (d: Destination) => d.name === entry.request.destination
    );
    if (destination) setSelected(destination);
    setView({ name: "form" });
    window.scrollTo({ top: 0 });
  }

  function startNew() {
    setFormSeed(null);
    setPlanError(null);
    setView({ name: "form" });
    window.scrollTo({ top: 0 });
  }

  function forgetEntry(id: string) {
    const next = removeFromHistory(history, id);
    setHistory(next);
    // Removing what is on screen leaves nothing to look at.
    if (id === activeId) {
      setActiveId(null);
      setPlan(null);
      setView({ name: "form" });
    }
  }

  const nights = useMemo(() => {
    if (!plan) return 0;
    const ms =
      new Date(plan.end_date).getTime() - new Date(plan.start_date).getTime();
    return Math.max(0, Math.round(ms / 86_400_000));
  }, [plan]);

  const centre: [number, number] = plan
    ? [plan.days[0].items[0].attraction.location.lat,
       plan.days[0].items[0].attraction.location.lon]
    : selected
      ? [selected.lat, selected.lon]
      : [-38.1361, 176.2525];

  // Hidden until the first plan exists: an empty sidebar on a first visit
  // is a column of nothing beside the only thing to do.
  const showSidebar = history.length > 0 && access === "open";
  // Everything below the header waits for the gate.
  const unlocked = status.kind !== "ready" || access === "open";

  return (
    <div className="min-h-screen">
      <header className="relative overflow-hidden border-b border-vellum bg-parchment/50">
        <ContourField
          seed={selected ? Math.abs(selected.lat * selected.lon) : 6712}
          lines={6}
          height={80}
          className="pointer-events-none absolute inset-0 h-full w-full text-contour"
        />
        <div className="relative mx-auto flex max-w-6xl items-baseline gap-4 px-6 py-4">
          <h1 className="font-display text-xl font-bold leading-none text-ink">
            TravelGuideMaster
          </h1>
          <p className="eyebrow">Aotearoa New Zealand · trip planning</p>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-8">
        <div
          className={
            showSidebar
              ? "grid gap-8 lg:grid-cols-[minmax(0,210px)_minmax(0,1fr)]"
              : ""
          }
        >
          {showSidebar && (
            <HistorySidebar
              entries={history}
              activeId={activeId}
              onForm={view.name === "form"}
              onSelect={showEntry}
              onReuse={reuseEntry}
              onRemove={forgetEntry}
              onNew={startNew}
            />
          )}

          <div className="min-w-0">
        {status.kind === "ready" && access === "locked" && (
          <AccessGate
            notice={gateNotice}
            onUnlocked={() => {
              setGateNotice(null);
              setPlanError(null);
              setAccess("open");
            }}
          />
        )}
        {status.kind === "loading" && (
          <p className="font-mono text-sm text-graphite">Loading destinations…</p>
        )}

        {status.kind === "error" && (
          <div className="rounded-sm border border-thermal/50 bg-thermal/10 px-4 py-3">
            <p className="text-sm text-ink">{status.message}</p>
            {/* A hint for the developer, not the traveller: in a deployed
                build there is no backend directory to cd into. */}
            {import.meta.env.DEV && (
              <p className="mt-1 font-mono text-xs text-graphite">
                cd backend &amp;&amp; uvicorn app.main:app --reload
              </p>
            )}
          </div>
        )}

        {/* ---------- form ---------- */}
        {status.kind === "ready" && unlocked && view.name === "form" && (
          <DestinationBackdrop
            destinations={destinations}
            selected={selected}
            onSelect={setSelected}
          >
            <div>
              <TripForm
                selected={selected}
                onSubmit={handlePlan}
                initial={formSeed}
              />

              {planError && (
                <p className="mt-6 rounded-sm border border-thermal/50 bg-thermal/10 px-4 py-3 text-sm text-ink">
                  {planError}
                </p>
              )}
            </div>
          </DestinationBackdrop>
        )}

        {/* ---------- planning ---------- */}
        {unlocked && view.name === "planning" && (
          <PlanningProgress
            destination={view.request.destination}
            days={
              Math.round(
                (new Date(view.request.end_date).getTime() -
                  new Date(view.request.start_date).getTime()) /
                  86_400_000
              ) + 1
            }
          />
        )}

        {/* ---------- itinerary ---------- */}
        {unlocked && view.name === "itinerary" && plan && (
          <>
            <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
              <div>
                <h2 className="font-display text-2xl font-semibold text-ink">
                  {plan.days.length} day{plan.days.length === 1 ? "" : "s"} in{" "}
                  {plan.destination}
                </h2>
                <p className="mt-1 font-mono text-[0.72rem] text-graphite">
                  {plan.start_date} → {plan.end_date}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {recomputing && (
                  <span className="font-mono text-[0.66rem] uppercase tracking-survey text-water">
                    Updating…
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => handleExport("pdf")}
                  disabled={exporting !== null}
                  className="rounded-sm border border-graphite/35 px-4 py-2 font-mono text-[0.7rem] uppercase tracking-survey text-graphite transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
                >
                  {exporting === "pdf" ? "Preparing…" : "PDF"}
                </button>
                <button
                  type="button"
                  onClick={() => handleExport("png")}
                  disabled={exporting !== null}
                  className="rounded-sm border border-graphite/35 px-4 py-2 font-mono text-[0.7rem] uppercase tracking-survey text-graphite transition-colors hover:border-ink hover:text-ink disabled:opacity-50"
                >
                  {exporting === "png" ? "Preparing…" : "PNG"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const entry = history.find((e) => e.id === activeId);
                    if (entry) reuseEntry(entry);
                    else startNew();
                  }}
                  className="rounded-sm border border-graphite/35 px-4 py-2 font-mono text-[0.7rem] uppercase tracking-survey text-graphite transition-colors hover:border-ink hover:text-ink"
                >
                  Adjust and replan
                </button>
              </div>
            </div>

            {/* The map is context, not content: it stays put while the
                days scroll past it. Side by side, it was a narrow column
                showing a fraction of the region; pinned across the full
                width it can actually be read. */}
            <div className="sticky top-0 z-10 -mx-6 mb-8 bg-paper px-6 pt-2 pb-3">
              <div ref={mapRef}>
                <MapView
                  centre={centre}
                  days={plan.days}
                  hotel={plan.hotel}
                  focusedDay={focusedDay}
                  className="h-[40vh] min-h-[260px] w-full rounded-sm border border-vellum shadow-sheet"
                />
              </div>
            </div>

            <div className="grid gap-8 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,0.65fr)]">
              <section
                className="space-y-4"
                onMouseLeave={() => setFocusedDay(null)}
              >
                {editError && (
                  <p className="rounded-sm border border-thermal/50 bg-thermal/10 px-4 py-3 text-sm text-ink">
                    {editError}
                  </p>
                )}
                {plan.days.map((day) => (
                  <div
                    key={day.day}
                    onMouseEnter={() => setFocusedDay(day.day)}
                  >
                    <DayCard
                      day={day}
                      active={focusedDay === day.day}
                      onFocus={setFocusedDay}
                      onEdit={setEditingDay}
                    />
                  </div>
                ))}
              </section>

              <aside className="lg:sticky lg:top-[calc(40vh+2.5rem)] lg:self-start">
                <TripSummary
                  hotel={plan.hotel}
                  budget={plan.budget}
                  notes={plan.notes}
                  nights={nights}
                />
              </aside>
            </div>

            <div
              aria-hidden="true"
              className="pointer-events-none fixed left-0 top-0 -z-50 opacity-0"
            >
              <div ref={printRef}>
                <PrintableItinerary plan={plan} mapImage={mapImage} />
              </div>
            </div>

            {editingDay !== null && (
              <DayEditor
                day={plan.days.find((d) => d.day === editingDay)!}
                alternatives={plan.alternatives}
                onSave={(items) => saveDay(editingDay, items)}
                onClose={() => setEditingDay(null)}
              />
            )}
          </>
        )}
          </div>
        </div>
      </main>

      <footer className="border-t border-vellum px-6 py-6">
        <p className="mx-auto max-w-6xl font-mono text-[0.66rem] text-graphite">
          Map data © OpenStreetMap contributors · Routing by OSRM · Costs shown
          are estimates, not live prices
        </p>
      </footer>
    </div>
  );
}
