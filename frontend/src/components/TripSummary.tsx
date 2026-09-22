import type { Budget, CostEstimate, Hotel } from "../types/api";
import { formatCost } from "../format";

interface TripSummaryProps {
  hotel?: Hotel | null;
  budget?: Budget | null;
  notes: string[];
  nights: number;
}

const BUDGET_ROWS: { key: keyof Budget; label: string }[] = [
  { key: "accommodation", label: "Accommodation" },
  { key: "meals", label: "Food" },
  { key: "transport", label: "Getting around" },
  { key: "attractions", label: "Entry" },
];

function total(budget: Budget): CostEstimate {
  const parts = BUDGET_ROWS.map((row) => budget[row.key]);
  return {
    low_nzd: parts.reduce((sum, part) => sum + part.low_nzd, 0),
    high_nzd: parts.reduce((sum, part) => sum + part.high_nzd, 0),
    basis: "sum of the rows above",
  };
}

export default function TripSummary({
  hotel,
  budget,
  notes,
  nights,
}: TripSummaryProps) {
  return (
    <div className="space-y-5">
      {hotel && (
        <section className="sheet p-4">
          <p className="eyebrow mb-2">Staying at</p>
          <p className="font-display text-sm font-semibold text-ink">
            {hotel.name}
          </p>
          <p className="mt-0.5 font-mono text-[0.7rem] text-graphite">
            {hotel.accommodation_type.replace("_", " ")}
            {hotel.stars ? ` · ${hotel.stars}-star` : ""}
          </p>
          {hotel.estimated_cost_per_night && (
            <p className="mt-2 font-mono text-[0.75rem] text-ink">
              {formatCost(hotel.estimated_cost_per_night)}
              <span className="text-graphite"> / night</span>
              {nights > 0 && (
                <span className="text-graphite">
                  {" "}
                  × {nights} night{nights === 1 ? "" : "s"}
                </span>
              )}
            </p>
          )}
        </section>
      )}

      {budget && (
        <section className="sheet p-4">
          <p className="eyebrow mb-3">Estimated cost</p>
          <table className="w-full text-[0.82rem]">
            <tbody>
              {BUDGET_ROWS.map(({ key, label }) => (
                <tr key={key} className="align-baseline">
                  <td className="py-1 pr-2 text-graphite">{label}</td>
                  <td className="py-1 text-right font-mono text-ink">
                    {formatCost(budget[key])}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-vellum align-baseline">
                <td className="pt-2 pr-2 font-display font-semibold text-ink">
                  Total
                </td>
                <td className="pt-2 text-right font-mono font-semibold text-ink">
                  {formatCost(total(budget))}
                </td>
              </tr>
            </tbody>
          </table>

          {/* Each row carries how it was derived. Shown rather than hidden
              behind a tooltip: an estimate whose basis is inspectable reads
              as a considered figure, one without reads as a guess. */}
          <details className="mt-3 group">
            <summary className="cursor-pointer font-mono text-[0.66rem] uppercase tracking-survey text-graphite hover:text-ink">
              How these were worked out
            </summary>
            <dl className="mt-2 space-y-1.5">
              {BUDGET_ROWS.map(({ key, label }) => (
                <div key={key}>
                  <dt className="font-mono text-[0.66rem] text-graphite">{label}</dt>
                  <dd className="text-[0.75rem] leading-snug text-graphite/80">
                    {budget[key].basis}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        </section>
      )}

      {notes.length > 0 && (
        <section className="rounded-sm border border-vellum bg-parchment p-4">
          <p className="eyebrow mb-2">Worth knowing</p>
          <ul className="space-y-1.5">
            {notes.map((note) => (
              <li key={note} className="text-[0.8rem] leading-relaxed text-graphite">
                {note}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
