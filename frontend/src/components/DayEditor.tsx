import { useEffect, useMemo, useRef, useState } from "react";
import AddStop from "./AddStop";
import type {
  Attraction,
  DayPlan,
  ItineraryItem,
  TimeSlot,
} from "../types/api";
import {
  TIME_SLOT_LABEL,
  dayColour,
  formatCost,
  formatDate,
  formatDuration,
  weatherLine,
} from "../format";
import { distanceToNearest, formatKm } from "../geo";

/**
 * Editing one day, in a dialog, with an explicit save.
 *
 * Staged rather than live. Editing in place meant every click went
 * straight to the server, so there was no way to try an arrangement and
 * change your mind — and the controls had to live permanently beside each
 * stop, which turned the itinerary into a form. A dialog scoped to a
 * single day gives the traveller somewhere to experiment, and gives the
 * card back its job of being read.
 *
 * Stops are grouped by time slot because that is how a day is actually
 * shaped, and because the slot is what the planner assigns — so the slot
 * is the natural unit to move something into or out of.
 */

const SLOTS: TimeSlot[] = ["morning", "afternoon", "evening"];

const SLOT_RANK: Record<TimeSlot, number> = {
  morning: 0,
  afternoon: 1,
  evening: 2,
};

/**
 * The day's stops in clock order.
 *
 * The list is kept in this order at all times rather than sorted only for
 * display, because the order of the array is not just a rendering
 * question: it is the order the stop numbers count in, the order the
 * "from the previous stop" distances are measured along, and the order the
 * server hands to OSRM when it draws the route. Appending a new morning
 * stop to a day that already had an evening one used to leave the array
 * interleaved, and the route was then drawn morning → evening → afternoon
 * while the screen showed something sensible.
 *
 * Sort is stable (guaranteed since ES2019), so stops within a slot keep
 * the order the traveller put them in.
 */
function inSlotOrder(list: ItineraryItem[]): ItineraryItem[] {
  return [...list].sort(
    (a, b) => SLOT_RANK[a.time_slot] - SLOT_RANK[b.time_slot]
  );
}

interface DayEditorProps {
  day: DayPlan;
  alternatives: Attraction[];
  onSave: (items: ItineraryItem[]) => void;
  onClose: () => void;
}

export default function DayEditor({
  day,
  alternatives,
  onSave,
  onClose,
}: DayEditorProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<ItineraryItem[]>(() =>
    inSlotOrder(day.items)
  );
  const [openStop, setOpenStop] = useState<string | null>(null);
  const [addingTo, setAddingTo] = useState<TimeSlot | null>(null);

  // The native element handles focus trapping, Esc and the backdrop, which
  // a hand-rolled overlay would have to reimplement and usually gets wrong.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [onClose]);

  const dirty = useMemo(() => {
    // Compared against the day in the same order the editor holds it,
    // so merely opening a day whose stops arrived interleaved does not
    // count as an edit.
    const original = inSlotOrder(day.items);
    if (items.length !== original.length) return true;
    return items.some(
      (item, index) =>
        item.attraction.id !== original[index].attraction.id ||
        item.time_slot !== original[index].time_slot
    );
  }, [items, day.items]);

  const bySlot = useMemo(() => {
    const grouped: Record<TimeSlot, ItineraryItem[]> = {
      morning: [],
      afternoon: [],
      evening: [],
    };
    for (const item of items) grouped[item.time_slot].push(item);
    return grouped;
  }, [items]);

  /** The leg arriving at a stop, for the detail panel. */
  function legTo(attractionId: string) {
    return day.route_legs.find((leg) => leg.to_id === attractionId);
  }

  /**
   * Straight-line distance from the stop before this one.
   *
   * Computed from the current order, so unlike the saved road legs it
   * stays true while the traveller is rearranging and the server has not
   * been asked anything yet. That is the moment the number is most useful:
   * it is what tells them whether the arrangement they are trying works.
   */
  function hopFromPrevious(index: number): number | null {
    if (index <= 0) return null;
    return distanceToNearest(items[index].attraction.location, [
      items[index - 1].attraction.location,
    ]);
  }

  /**
   * Moves a stop one place within its own time slot.
   *
   * Confined to the slot deliberately. The sections are the day's clock,
   * and a stop leaving its section is a different decision with its own
   * control ("→ afternoon"). The swap used to run over the whole day's
   * list, so on a day with one stop per slot "later" exchanged a stop with
   * its neighbour in the next section: both stayed exactly where they were
   * drawn, because each kept its slot, and only their numbers traded
   * places. Nothing moved, and the button still looked like it had worked.
   */
  function move(attractionId: string, direction: -1 | 1) {
    setItems((current) => {
      const from = current.findIndex((i) => i.attraction.id === attractionId);
      const to = from + direction;
      if (from < 0 || to < 0 || to >= current.length) return current;
      // The list is in slot order, so the adjacent stop shares the slot
      // exactly when the move stays inside the section.
      if (current[to].time_slot !== current[from].time_slot) return current;
      const next = [...current];
      [next[from], next[to]] = [next[to], next[from]];
      return next;
    });
  }

  function remove(attractionId: string) {
    setItems((current) =>
      current.filter((i) => i.attraction.id !== attractionId)
    );
  }

  function moveToSlot(attractionId: string, slot: TimeSlot) {
    setItems((current) =>
      inSlotOrder(
        current.map((i) =>
          i.attraction.id === attractionId ? { ...i, time_slot: slot } : i
        )
      )
    );
  }

  function add(attraction: Attraction, slot: TimeSlot) {
    setItems((current) =>
      inSlotOrder([
        ...current,
        { attraction, time_slot: slot, note: `Visit ${attraction.name}.` },
      ])
    );
    setAddingTo(null);
    setOpenStop(attraction.id);
  }

  const weather = weatherLine(day.weather);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="day-editor-title"
      className="w-[min(42rem,calc(100vw-2rem))] rounded-sm border border-vellum bg-paper p-0 text-ink shadow-sheet backdrop:bg-ink/40"
    >
      <header className="flex items-start gap-3 border-b border-vellum px-5 py-4">
        <span
          className="mt-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: dayColour(day.day) }}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <h2
            id="day-editor-title"
            className="font-display text-lg font-semibold leading-none"
          >
            Day {day.day}
            {day.date && (
              <span className="ml-2 font-mono text-[0.7rem] font-normal text-graphite">
                {formatDate(day.date)}
              </span>
            )}
          </h2>
          <p className="mt-1.5 text-[0.82rem] leading-snug text-graphite">
            {day.summary}
          </p>
          {weather && (
            <p className="mt-1 font-mono text-[0.66rem] text-graphite">
              {weather}
              {day.travel_summary && <> · {day.travel_summary}</>}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-1 -mt-1 shrink-0 px-2 py-1 font-mono text-sm text-graphite hover:text-ink"
        >
          ✕
        </button>
      </header>

      <div className="max-h-[min(60vh,32rem)] overflow-y-auto px-5 py-4">
        {SLOTS.map((slot) => {
          const slotItems = bySlot[slot];
          return (
            <section key={slot} className="border-b border-vellum last:border-0">
              <h3 className="flex items-baseline gap-2 py-3">
                <span className="eyebrow">{TIME_SLOT_LABEL[slot]}</span>
                <span className="font-mono text-[0.66rem] text-graphite">
                  {slotItems.length === 0
                    ? "nothing planned"
                    : `${slotItems.length} stop${slotItems.length === 1 ? "" : "s"}`}
                </span>
              </h3>

              <ul className="pb-2">
                {slotItems.map((item, positionInSlot) => {
                  const index = items.findIndex(
                    (i) => i.attraction.id === item.attraction.id
                  );
                  const leg = legTo(item.attraction.id);
                  const hop = hopFromPrevious(index);
                  const isOpen = openStop === item.attraction.id;
                  return (
                    <li
                      key={item.attraction.id}
                      className={`mb-2 rounded-sm border bg-white transition-colors ${
                        isOpen ? "border-water" : "border-vellum"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setOpenStop(isOpen ? null : item.attraction.id)
                        }
                        aria-expanded={isOpen}
                        className="w-full px-3 pt-2.5 text-left"
                      >
                        <span className="flex flex-wrap items-baseline gap-x-2">
                          <span
                            className="font-mono text-[0.62rem] text-graphite"
                            title={`Stop ${index + 1} of ${items.length} today`}
                          >
                            {index + 1}.
                          </span>
                          <span className="font-display text-sm font-semibold">
                            {item.attraction.name}
                          </span>
                          <span className="font-mono text-[0.62rem] text-graphite">
                            {item.attraction.category}
                          </span>
                          <span className="ml-auto font-mono text-[0.7rem] text-graphite">
                            {formatCost(item.attraction.estimated_cost)}
                          </span>
                        </span>
                        <span className="mt-1 block text-[0.8rem] leading-relaxed text-graphite">
                          {item.note}
                        </span>
                        {hop !== null && (
                          <span className="mt-1 block font-mono text-[0.62rem] text-graphite">
                            {formatKm(hop)} from the previous stop
                            {hop > 8 && (
                              <span className="text-thermal"> — a detour</span>
                            )}
                          </span>
                        )}
                      </button>

                      <div className="px-3 pb-2.5">
                      {isOpen && (
                        <div className="mt-2 border-t border-vellum pt-2">
                          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 font-mono text-[0.66rem]">
                            {item.attraction.opening_hours && (
                              <>
                                <dt className="text-graphite/70">Hours</dt>
                                <dd className="text-graphite">
                                  {item.attraction.opening_hours}
                                </dd>
                              </>
                            )}
                            <dt className="text-graphite/70">Where</dt>
                            <dd className="text-graphite">
                              {Math.abs(item.attraction.location.lat).toFixed(4)}°S{" "}
                              {item.attraction.location.lon.toFixed(4)}°E
                            </dd>
                            {/* Legs describe the saved order, so they stop
                                being true the moment something moves. */}
                            {leg && !dirty && (
                              <>
                                <dt className="text-graphite/70">Getting here</dt>
                                <dd className="text-graphite">
                                  {(leg.distance_m / 1000).toFixed(1)} km ·{" "}
                                  {formatDuration(leg.duration_s)} from{" "}
                                  {leg.from_id === "HOTEL"
                                    ? "your accommodation"
                                    : "the previous stop"}
                                </dd>
                              </>
                            )}
                            {item.attraction.estimated_cost && (
                              <>
                                <dt className="text-graphite/70">Entry</dt>
                                <dd className="text-graphite">
                                  {item.attraction.estimated_cost.basis}
                                </dd>
                              </>
                            )}
                          </dl>
                        </div>
                      )}

                      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
                        <button
                          type="button"
                          onClick={() => move(item.attraction.id, -1)}
                          // First and last in the section, not in the day:
                          // the arrows reorder within the slot, so they are
                          // spent once the stop reaches either end of it.
                          disabled={positionInSlot <= 0}
                          className="font-mono text-[0.66rem] text-graphite hover:text-ink disabled:opacity-30"
                        >
                          ↑ earlier
                        </button>
                        <button
                          type="button"
                          onClick={() => move(item.attraction.id, 1)}
                          disabled={positionInSlot >= slotItems.length - 1}
                          className="font-mono text-[0.66rem] text-graphite hover:text-ink disabled:opacity-30"
                        >
                          ↓ later
                        </button>
                        {SLOTS.filter((s) => s !== slot).map((target) => (
                          <button
                            key={target}
                            type="button"
                            onClick={() => moveToSlot(item.attraction.id, target)}
                            className="font-mono text-[0.66rem] text-graphite hover:text-water"
                          >
                            → {TIME_SLOT_LABEL[target].toLowerCase()}
                          </button>
                        ))}
                        <button
                          type="button"
                          onClick={() => remove(item.attraction.id)}
                          className="ml-auto font-mono text-[0.66rem] text-graphite hover:text-thermal"
                        >
                          Remove
                        </button>
                      </div>
                      </div>
                    </li>
                  );
                })}
              </ul>

              {addingTo === slot ? (
                <div className="pb-3">
                  <AddStop
                    alternatives={alternatives}
                    slot={slot}
                    dayStops={items.map((i) => i.attraction.location)}
                    onAdd={add}
                    onCancel={() => setAddingTo(null)}
                  />
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setAddingTo(slot)}
                  disabled={items.length >= 5}
                  className="mb-3 w-full rounded-sm border border-dashed border-vellum py-1.5 font-mono text-[0.62rem] uppercase tracking-survey text-graphite transition-colors hover:border-water hover:text-water disabled:opacity-40 disabled:hover:border-vellum disabled:hover:text-graphite"
                >
                  {items.length >= 5
                    ? "Day is full"
                    : `+ Add to ${TIME_SLOT_LABEL[slot].toLowerCase()}`}
                </button>
              )}
            </section>
          );
        })}
      </div>

      <footer className="flex items-center gap-3 border-t border-vellum px-5 py-3">
        <p className="font-mono text-[0.66rem] text-graphite">
          {dirty
            ? "Routes and costs update when you save."
            : "No changes yet."}
        </p>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm border border-graphite/35 px-4 py-2 font-mono text-[0.7rem] uppercase tracking-survey text-graphite hover:border-ink hover:text-ink"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave(items)}
            disabled={!dirty}
            className="rounded-sm bg-ink px-4 py-2 font-mono text-[0.7rem] uppercase tracking-survey text-paper transition-colors hover:bg-water disabled:bg-graphite/40"
          >
            Save day
          </button>
        </div>
      </footer>
    </dialog>
  );
}
