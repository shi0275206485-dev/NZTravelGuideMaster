import type { DayPlan } from "../types/api";
import {
  HEAVY_TRAVEL_MINUTES,
  TIME_SLOT_LABEL,
  dayColour,
  formatCost,
  formatDate,
  weatherLine,
} from "../format";

interface DayCardProps {
  day: DayPlan;
  /** Highlights this day and its route on the map. */
  active?: boolean;
  onFocus?: (day: number) => void;
  /** Opens the day's editor. */
  onEdit?: (day: number) => void;
}

export default function DayCard({
  day,
  active = false,
  onFocus,
  onEdit,
}: DayCardProps) {
  const weather = weatherLine(day.weather);
  const travelMinutes = Math.round(day.travel_duration_s / 60);

  // A day can be full of good places and still be a bad day if most of it
  // is spent driving, which the list of stops does not reveal. Flagged only
  // past the threshold — an unremarkable number shown in warning colours
  // teaches people to ignore the colour.
  const heavyTravel = travelMinutes > HEAVY_TRAVEL_MINUTES;

  return (
    <article
      onClick={() => onFocus?.(day.day)}
      className={`rounded-sm border bg-white p-4 shadow-sheet transition-colors ${
        active ? "border-water" : "border-vellum hover:border-graphite/40"
      } ${onFocus ? "cursor-pointer" : ""}`}
    >
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: dayColour(day.day) }}
          aria-hidden="true"
        />
        <h3 className="font-display text-base font-semibold text-ink">
          Day {day.day}
        </h3>
        {day.date && (
          <span className="font-mono text-[0.7rem] text-graphite">
            {formatDate(day.date)}
          </span>
        )}
        {weather && (
          <span className="font-mono text-[0.7rem] text-graphite">{weather}</span>
        )}
        {day.weather?.is_wet && (
          <span className="rounded-sm bg-water/10 px-1.5 py-0.5 font-mono text-[0.62rem] uppercase tracking-survey text-water">
            wet — indoors favoured
          </span>
        )}
        {onEdit && (
          <button
            type="button"
            onClick={(event) => {
              // The card itself focuses the day on the map; editing is a
              // separate intent and should not also do that.
              event.stopPropagation();
              onEdit(day.day);
            }}
            className="ml-auto rounded-sm border border-vellum px-2.5 py-1 font-mono text-[0.62rem] uppercase tracking-survey text-graphite transition-colors hover:border-ink hover:text-ink"
          >
            Edit day
          </button>
        )}
      </header>

      <p className="mt-2 text-sm leading-relaxed text-ink/90">{day.summary}</p>

      {day.travel_summary && (
        <p
          className={`mt-2 font-mono text-[0.7rem] ${
            heavyTravel ? "text-thermal" : "text-graphite"
          }`}
        >
          {day.travel_summary}
          {heavyTravel && " — a fair amount of the day"}
        </p>
      )}

      <ol className="mt-4 space-y-3">
        {day.items.map((item) => (
          <li
            key={item.attraction.id}
            className="border-l border-vellum pl-3"
          >
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="eyebrow">{TIME_SLOT_LABEL[item.time_slot]}</span>
              <span className="font-mono text-[0.66rem] text-graphite">
                {item.attraction.category}
              </span>
              <span className="ml-auto font-mono text-[0.7rem] text-graphite">
                {formatCost(item.attraction.estimated_cost)}
              </span>
            </div>
            <p className="mt-0.5 font-display text-sm font-semibold text-ink">
              {item.attraction.name}
            </p>
            <p className="mt-0.5 text-[0.82rem] leading-relaxed text-graphite">
              {item.note}
            </p>
            {item.attraction.opening_hours && (
              <p className="mt-1 font-mono text-[0.66rem] text-graphite/70">
                {item.attraction.opening_hours}
              </p>
            )}
          </li>
        ))}
      </ol>

      {day.items.length === 0 && (
        <p className="mt-4 rounded-sm border border-dashed border-vellum px-3 py-4 text-center text-[0.82rem] text-graphite">
          Nothing planned for this day.
        </p>
      )}

      {day.meals.length > 0 && (
        <div className="mt-4 border-t border-vellum pt-3">
          <p className="eyebrow mb-2">Eating</p>
          <ul className="space-y-1.5">
            {day.meals.map((meal) => (
              <li
                key={meal.meal_type}
                className="flex flex-wrap items-baseline gap-x-2 text-[0.82rem]"
              >
                <span className="font-mono text-[0.66rem] uppercase tracking-survey text-graphite">
                  {meal.meal_type}
                </span>
                <span className="text-graphite">{meal.suggestion}</span>
                <span className="ml-auto font-mono text-[0.7rem] text-graphite">
                  {formatCost(meal.estimated_cost)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
