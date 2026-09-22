import RouteSchematic from "./RouteSchematic";
import type { Budget, TripPlan } from "../types/api";
import { TIME_SLOT_LABEL, dayColour, formatCost, formatDate } from "../format";

/**
 * The itinerary as a printed sheet.
 *
 * A separate layout rather than a screenshot of the screen. The live view
 * carries edit buttons, hover states, a sticky map and a scroll position,
 * none of which belong on paper — and capturing it would freeze whatever
 * happened to be open at the time. Rendering a purpose-built sheet means
 * the export is the same every time and readable at A4 width.
 *
 * Rendered off-screen and captured, so it never appears in the interface.
 */

const BUDGET_ROWS: { key: keyof Budget; label: string }[] = [
  { key: "accommodation", label: "Accommodation" },
  { key: "meals", label: "Food" },
  { key: "transport", label: "Getting around" },
  { key: "attractions", label: "Entry" },
];

function budgetTotal(budget: Budget) {
  const parts = BUDGET_ROWS.map((r) => budget[r.key]);
  return {
    low_nzd: parts.reduce((sum, p) => sum + p.low_nzd, 0),
    high_nzd: parts.reduce((sum, p) => sum + p.high_nzd, 0),
    basis: "",
  };
}

/** A4 at 96 dpi, less a 15 mm margin either side. */
export const SHEET_WIDTH = 680;

interface PrintableItineraryProps {
  plan: TripPlan;
  /** A capture of the live map, when one could be taken. */
  mapImage?: string | null;
}

export default function PrintableItinerary({
  plan,
  mapImage,
}: PrintableItineraryProps) {
  return (
    <div
      style={{ width: SHEET_WIDTH }}
      className="bg-paper px-8 py-8 font-sans text-ink"
    >
      <header className="border-b-2 border-ink pb-3">
        <p className="font-mono text-[0.6rem] uppercase tracking-survey text-graphite">
          Aotearoa New Zealand · trip plan
        </p>
        <h1 className="mt-1.5 font-display text-2xl font-bold leading-none">
          {plan.days.length} day{plan.days.length === 1 ? "" : "s"} in{" "}
          {plan.destination}
        </h1>
        <p className="mt-1.5 font-mono text-[0.7rem] text-graphite">
          {formatDate(plan.start_date)} — {formatDate(plan.end_date)}
          {plan.hotel && <> · staying at {plan.hotel.name}</>}
        </p>
      </header>

      {/* The real map where it could be captured, the schematic otherwise.
          The schematic carries names because, without tiles beneath it, a
          numbered dot is not identifiable by anything else. */}
      <div className="mt-5 flex justify-center">
        {mapImage ? (
          <img
            src={mapImage}
            alt="Map of the trip's routes and stops"
            style={{ width: 600 }}
            className="rounded-sm border border-vellum"
          />
        ) : (
          <RouteSchematic
            days={plan.days}
            hotel={plan.hotel}
            width={600}
            height={300}
            labelled
          />
        )}
      </div>

      {plan.days.map((day) => (
        <section
          key={day.day}
          className="mt-6 border-t border-vellum pt-4"
          // Days are the natural place to break: a page that splits mid-day
          // makes someone hold two sheets to read one morning.
          style={{ breakInside: "avoid" }}
        >
          <div className="flex items-baseline gap-2">
            <span
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: dayColour(day.day) }}
            />
            <h2 className="font-display text-base font-semibold">
              Day {day.day}
            </h2>
            {day.date && (
              <span className="font-mono text-[0.68rem] text-graphite">
                {formatDate(day.date)}
              </span>
            )}
            {day.weather?.available && (
              <span className="font-mono text-[0.68rem] text-graphite">
                {day.weather.temp_min_c != null &&
                  `${Math.round(day.weather.temp_min_c)}–${Math.round(
                    day.weather.temp_max_c ?? 0
                  )}°C`}
                {day.weather.precipitation_probability != null &&
                  ` · ${day.weather.precipitation_probability}% rain`}
              </span>
            )}
            {day.travel_summary && (
              <span className="ml-auto font-mono text-[0.68rem] text-graphite">
                {day.travel_summary}
              </span>
            )}
          </div>

          <p className="mt-1.5 text-[0.85rem] leading-relaxed text-graphite">
            {day.summary}
          </p>

          {day.items.length === 0 ? (
            <p className="mt-3 text-[0.82rem] italic text-graphite">
              Nothing planned.
            </p>
          ) : (
            <ol className="mt-3 space-y-2.5">
              {day.items.map((item, index) => (
                <li key={item.attraction.id} className="flex gap-3">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[0.62rem] text-paper"
                        style={{ backgroundColor: dayColour(day.day) }}>
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="font-mono text-[0.6rem] uppercase tracking-survey text-graphite">
                        {TIME_SLOT_LABEL[item.time_slot]}
                      </span>
                      <span className="font-display text-[0.92rem] font-semibold">
                        {item.attraction.name}
                      </span>
                      <span className="ml-auto font-mono text-[0.7rem] text-graphite">
                        {formatCost(item.attraction.estimated_cost)}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[0.82rem] leading-snug text-graphite">
                      {item.note}
                    </p>
                    {item.attraction.opening_hours && (
                      <p className="mt-0.5 font-mono text-[0.62rem] text-graphite">
                        {item.attraction.opening_hours}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}

          {day.meals.length > 0 && (
            <ul className="mt-3 border-t border-vellum pt-2">
              {day.meals.map((meal) => (
                <li
                  key={meal.meal_type}
                  className="flex items-baseline gap-2 text-[0.8rem]"
                >
                  <span className="font-mono text-[0.6rem] uppercase tracking-survey text-graphite">
                    {meal.meal_type}
                  </span>
                  <span className="text-graphite">{meal.suggestion}</span>
                  <span className="ml-auto font-mono text-[0.68rem] text-graphite">
                    {formatCost(meal.estimated_cost)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      {plan.budget && (
        <section
          className="mt-6 border-t-2 border-ink pt-4"
          style={{ breakInside: "avoid" }}
        >
          <h2 className="font-display text-base font-semibold">
            Estimated cost
          </h2>
          <table className="mt-2 w-full text-[0.85rem]">
            <tbody>
              {BUDGET_ROWS.map(({ key, label }) => (
                <tr key={key}>
                  <td className="py-1 text-graphite">{label}</td>
                  <td className="py-1 text-right font-mono text-[0.78rem] text-graphite">
                    {plan.budget![key].basis}
                  </td>
                  <td className="py-1 pl-4 text-right font-mono">
                    {formatCost(plan.budget![key])}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-ink">
                <td className="pt-2 font-display font-semibold">Total</td>
                <td />
                <td className="pt-2 pl-4 text-right font-mono font-semibold">
                  {formatCost(budgetTotal(plan.budget))}
                </td>
              </tr>
            </tbody>
          </table>
        </section>
      )}

      {plan.notes.length > 0 && (
        <section className="mt-5" style={{ breakInside: "avoid" }}>
          <ul className="space-y-1">
            {plan.notes.map((note) => (
              <li key={note} className="text-[0.75rem] leading-snug text-graphite">
                {note}
              </li>
            ))}
          </ul>
        </section>
      )}

      <footer className="mt-6 border-t border-vellum pt-2">
        <p className="font-mono text-[0.6rem] text-graphite">
          Planned with TravelGuideMaster · Map data © OpenStreetMap contributors ·
          Routing by OSRM · Costs are estimates, not live prices
        </p>
      </footer>
    </div>
  );
}
