import { useMemo, useState } from "react";
import type { Attraction, Location, TimeSlot } from "../types/api";
import { TIME_SLOT_LABEL, formatCost } from "../format";
import { distanceToNearest, formatKm } from "../geo";

/**
 * Choosing a stop to add, from what the planner shortlisted but did not use.
 *
 * Offered rather than searched: these are the candidates the planner
 * already judged to suit this traveller, so a swap stays within the shape
 * of the trip they asked for. A free search box would invite requests the
 * system cannot honour — somewhere in another city, or somewhere that is
 * not in the data at all.
 *
 * Ordered by how close each one is to the places already in the day,
 * because that is the question being asked. Sorting alphabetically, or by
 * the significance ranking that produced the shortlist, would bury the one
 * useful fact: whether adding this means a detour.
 */

interface AddStopProps {
  alternatives: Attraction[];
  /** Decided by which slot's add button was pressed; not asked again. */
  slot: TimeSlot;
  /** Where the day already goes, for measuring against. */
  dayStops: Location[];
  onAdd: (attraction: Attraction, slot: TimeSlot) => void;
  onCancel: () => void;
}

/** Beyond this, adding a stop means driving out and back. */
const DETOUR_KM = 8;

export default function AddStop({
  alternatives,
  slot,
  dayStops,
  onAdd,
  onCancel,
}: AddStopProps) {
  const [query, setQuery] = useState("");

  const ranked = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return alternatives
      .filter(
        (a) =>
          !needle ||
          a.name.toLowerCase().includes(needle) ||
          a.category.toLowerCase().includes(needle)
      )
      .map((attraction) => ({
        attraction,
        km: distanceToNearest(attraction.location, dayStops),
      }))
      .sort((a, b) => {
        if (a.km === null || b.km === null) return 0;
        return a.km - b.km;
      });
  }, [alternatives, dayStops, query]);

  return (
    <div className="rounded-sm border border-water bg-white p-3 shadow-sheet">
      <div className="flex items-baseline justify-between gap-3">
        <p className="eyebrow">
          Add to {TIME_SLOT_LABEL[slot].toLowerCase()}
        </p>
        <button
          type="button"
          onClick={onCancel}
          className="font-mono text-[0.62rem] uppercase tracking-survey text-graphite hover:text-ink"
        >
          Cancel
        </button>
      </div>

      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter by name or kind"
        className="field mt-2.5 text-[0.82rem]"
        autoFocus
      />

      {dayStops.length > 0 && ranked.length > 0 && (
        <p className="mt-2 font-mono text-[0.62rem] text-graphite">
          Nearest to this day&rsquo;s stops first
        </p>
      )}

      {ranked.length === 0 ? (
        <p className="mt-3 text-[0.8rem] text-graphite">
          {alternatives.length === 0
            ? "Every shortlisted place is already in the itinerary."
            : "Nothing matches that."}
        </p>
      ) : (
        <ul className="mt-1.5 max-h-64 overflow-y-auto">
          {ranked.map(({ attraction, km }) => {
            const detour = km !== null && km > DETOUR_KM;
            return (
              <li key={attraction.id}>
                <button
                  type="button"
                  onClick={() => onAdd(attraction, slot)}
                  className="w-full rounded-sm px-2 py-2 text-left transition-colors hover:bg-parchment"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="text-[0.85rem] text-ink">
                      {attraction.name}
                    </span>
                    <span className="ml-auto shrink-0 font-mono text-[0.66rem] text-graphite">
                      {formatCost(attraction.estimated_cost)}
                    </span>
                  </span>
                  <span className="mt-0.5 flex items-baseline gap-2 font-mono text-[0.62rem]">
                    <span className="text-graphite">
                      {attraction.category}
                      {attraction.has_wikidata && " · well-known"}
                    </span>
                    {km !== null && (
                      <span
                        className={`ml-auto shrink-0 ${
                          detour ? "text-thermal" : "text-water"
                        }`}
                      >
                        {formatKm(km)} away
                        {detour && " — a detour"}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
