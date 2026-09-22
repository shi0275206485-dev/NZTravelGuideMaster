import { useEffect, useState } from "react";

/**
 * The pipeline's stages, shown while the plan is being built.
 *
 * Progress is simulated against measured durations rather than reported by
 * the backend. `run_pipeline` is synchronous — it returns the finished plan
 * in one response — so surfacing genuine per-node progress would mean an
 * async task queue and either polling or SSE. At five to eight seconds end
 * to end, the machinery would cost more than the accuracy is worth. The
 * completed plan's real timings are shown afterwards, where they can be
 * honest.
 *
 * The weights below are proportions of a run, taken from measured node
 * timings: the two model calls dominate, the rule-based agents barely
 * register.
 */
interface Stage {
  id: string;
  label: string;
  detail: string;
  weight: number;
}

const STAGES: Stage[] = [
  {
    id: "attractions",
    label: "AttractionSearchAgent",
    detail: "Reading the destination's places, then choosing what suits you",
    weight: 0.42,
  },
  {
    id: "weather",
    label: "WeatherQueryAgent",
    detail: "Checking the forecast for each day of the trip",
    weight: 0.08,
  },
  {
    id: "hotel",
    label: "HotelAgent",
    detail: "Ranking accommodation by distance, type and price",
    weight: 0.05,
  },
  {
    id: "planner",
    label: "PlannerAgent",
    detail: "Arranging the days, grouping places that belong together",
    weight: 0.37,
  },
  {
    id: "routing",
    label: "Routing",
    detail: "Working out the roads between each day's stops",
    weight: 0.08,
  },
];

/** Typical end-to-end run, measured across the three destinations. */
const EXPECTED_MS = 7000;

interface PlanningProgressProps {
  destination: string;
  days: number;
}

export default function PlanningProgress({
  destination,
  days,
}: PlanningProgressProps) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = performance.now();
    const timer = window.setInterval(() => {
      setElapsed(performance.now() - started);
    }, 100);
    return () => window.clearInterval(timer);
  }, []);

  // Which stage the run is notionally in, from the elapsed fraction.
  const fraction = elapsed / EXPECTED_MS;
  let cumulative = 0;
  const boundaries = STAGES.map((stage) => {
    cumulative += stage.weight;
    return cumulative;
  });
  // Never marks the last stage complete: the run is finished when the
  // response arrives, not when the estimate runs out. Claiming otherwise
  // would leave a finished-looking screen sitting there.
  const currentIndex = Math.min(
    boundaries.findIndex((edge) => fraction < edge),
    STAGES.length - 1
  );
  const activeIndex = currentIndex === -1 ? STAGES.length - 1 : currentIndex;
  const overrunning = elapsed > EXPECTED_MS * 1.6;

  return (
    <div className="mx-auto max-w-xl py-10">
      <p className="eyebrow">Planning</p>
      <h2 className="mt-2 font-display text-2xl font-semibold text-ink">
        {days} day{days === 1 ? "" : "s"} in {destination}
      </h2>
      <p className="mt-2 text-sm text-graphite">
        Four agents are working on this, one after another.
      </p>

      <ol className="mt-8 space-y-1">
        {STAGES.map((stage, index) => {
          const done = index < activeIndex;
          const active = index === activeIndex;
          return (
            <li
              key={stage.id}
              className={`flex gap-3 rounded-sm px-3 py-3 transition-colors ${
                active ? "bg-parchment" : ""
              }`}
            >
              <span
                aria-hidden="true"
                className={`mt-1 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[0.6rem] ${
                  done
                    ? "border-bush bg-bush text-paper"
                    : active
                      ? "border-thermal text-thermal"
                      : "border-graphite/35 text-transparent"
                }`}
              >
                {done ? "✓" : active ? "·" : ""}
              </span>
              <div className="min-w-0">
                <p
                  className={`font-mono text-[0.72rem] uppercase tracking-survey ${
                    done ? "text-bush" : active ? "text-ink" : "text-graphite/50"
                  }`}
                >
                  {stage.label}
                </p>
                <p
                  className={`mt-0.5 text-[0.82rem] leading-snug ${
                    active ? "text-graphite" : "text-graphite/50"
                  }`}
                >
                  {stage.detail}
                </p>
              </div>
            </li>
          );
        })}
      </ol>

      <div className="mt-8 h-px w-full bg-vellum">
        <div
          className="h-px bg-thermal transition-[width] duration-100 ease-linear"
          // Caps just short of full while waiting, so the bar never sits
          // completed against a screen that is still working.
          style={{ width: `${Math.min(fraction * 100, 96)}%` }}
        />
      </div>

      <p className="mt-3 font-mono text-[0.7rem] text-graphite">
        {(elapsed / 1000).toFixed(1)}s
        {overrunning && " — taking longer than usual, still working"}
      </p>
    </div>
  );
}
