import { useEffect, useMemo, useState } from "react";
import type {
  BudgetLevel,
  Destination,
  TravelPreference,
  TripRequest,
} from "../types/api";

const PREFERENCES: { value: TravelPreference; label: string; phrase: string }[] = [
  { value: "nature", label: "Nature", phrase: "the outdoors" },
  { value: "culture", label: "Culture", phrase: "museums and local history" },
  { value: "food", label: "Food", phrase: "eating well" },
  { value: "family", label: "Family", phrase: "things that work with kids" },
  { value: "adventure", label: "Adventure", phrase: "a bit of adrenaline" },
  { value: "relaxation", label: "Relaxation", phrase: "a slower pace" },
];

const BUDGET_LEVELS: { value: BudgetLevel; label: string; phrase: string }[] = [
  { value: "budget", label: "Budget", phrase: "keeping costs down" },
  { value: "mid_range", label: "Mid-range", phrase: "a mid-range budget" },
  { value: "premium", label: "Premium", phrase: "happy to spend on the good stuff" },
];

const MAX_NOTE = 500;

interface TripFormProps {
  /** Chosen in the hero above; the form only reads it. */
  selected: Destination | null;
  onSubmit: (request: TripRequest) => void;
  /** Prefills the form from an earlier request, for "reuse". */
  initial?: TripRequest | null;
}

function isoDate(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-NZ", {
    day: "numeric",
    month: "long",
  });
}

function joinPhrases(phrases: string[]): string {
  if (phrases.length === 0) return "";
  if (phrases.length === 1) return phrases[0];
  return `${phrases.slice(0, -1).join(", ")} and ${phrases[phrases.length - 1]}`;
}

export default function TripForm({
  selected,
  onSubmit,
  initial = null,
}: TripFormProps) {
  const [startDate, setStartDate] = useState(initial?.start_date ?? isoDate(14));
  const [endDate, setEndDate] = useState(initial?.end_date ?? isoDate(16));
  const [preferences, setPreferences] = useState<TravelPreference[]>(
    initial?.preferences ?? ["nature"]
  );
  const [budgetLevel, setBudgetLevel] = useState<BudgetLevel>(
    initial?.budget_level ?? "mid_range"
  );
  const [note, setNote] = useState(initial?.free_text ?? "");

  const nights = Math.max(
    0,
    Math.round(
      (new Date(endDate).getTime() - new Date(startDate).getTime()) / 86_400_000
    )
  );
  const days = nights + 1;
  const datesValid = nights >= 0 && days <= 7;

  /**
   * The selections above, rendered as a sentence.
   *
   * Read-only by design. The traveller's own words go in the field below
   * rather than into this text, so the structured selections can never be
   * knocked out of sync by typing — the backend keeps receiving clean enum
   * values, and the free text stays clearly separable from them.
   */
  const summarySentence = useMemo(() => {
    if (!selected) return "";
    const prefPhrases = PREFERENCES.filter((p) =>
      preferences.includes(p.value)
    ).map((p) => p.phrase);
    const budget = BUDGET_LEVELS.find((b) => b.value === budgetLevel)!;
    const interests = prefPhrases.length
      ? ` I'm here for ${joinPhrases(prefPhrases)},`
      : "";
    return (
      `I'm spending ${days} day${days === 1 ? "" : "s"} in ${selected.name}, ` +
      `arriving ${formatDate(startDate)}.${interests} ${budget.phrase}.`
    );
  }, [selected, days, startDate, preferences, budgetLevel]);

  // useState's initial value applies only on mount, so reusing a second
  // entry while the form is already open would otherwise leave the first
  // one's values in place.
  useEffect(() => {
    if (!initial) return;
    setStartDate(initial.start_date);
    setEndDate(initial.end_date);
    setPreferences(initial.preferences);
    setBudgetLevel(initial.budget_level);
    setNote(initial.free_text ?? "");
  }, [initial]);

  // Announce regeneration to screen readers without moving focus.
  const [announcement, setAnnouncement] = useState("");
  useEffect(() => {
    if (summarySentence) setAnnouncement(summarySentence);
  }, [summarySentence]);

  // No busy state: submitting replaces this whole view with the progress
  // screen, so a form that waits on itself never appears.
  const canSubmit = Boolean(selected) && preferences.length > 0 && datesValid;

  function togglePreference(value: TravelPreference) {
    setPreferences((current) =>
      current.includes(value)
        ? current.filter((p) => p !== value)
        : [...current, value]
    );
  }

  function submit() {
    if (!selected || !canSubmit) return;
    onSubmit({
      destination: selected.name,
      start_date: startDate,
      end_date: endDate,
      preferences,
      budget_level: budgetLevel,
      free_text: note.trim() || null,
    });
  }

  return (
    <div className="space-y-8">
      {/* The composed request, assembled from the controls below. */}
      <div className="sheet border-water/50 p-4">
        <p className="eyebrow mb-3">Your request</p>
        {selected ? (
          <p className="text-[0.95rem] leading-relaxed text-ink">
            {summarySentence}
          </p>
        ) : (
          <p className="text-sm text-graphite">Pick a destination to begin.</p>
        )}

        <label className="field-label mt-5" htmlFor="note">
          Anything else?{" "}
          <span className="normal-case tracking-normal">(optional)</span>
        </label>
        <textarea
          id="note"
          rows={3}
          maxLength={MAX_NOTE}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. travelling with a 5-year-old, nothing too strenuous — and we'd like one proper sit-down dinner"
          className="field resize-none leading-relaxed"
        />
        <div className="mt-1.5 flex items-baseline justify-between gap-3">
          <p className="text-[0.7rem] leading-snug text-graphite">
            Guides which places get picked. It can&rsquo;t change the
            destination or add somewhere that isn&rsquo;t in the list.
          </p>
          <span className="shrink-0 font-mono text-[0.66rem] text-graphite">
            {note.length}/{MAX_NOTE}
          </span>
        </div>
      </div>

      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="field-label" htmlFor="start-date">
            Arrive
          </label>
          <input
            id="start-date"
            type="date"
            className="field font-mono"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        <div>
          <label className="field-label" htmlFor="end-date">
            Depart
          </label>
          <input
            id="end-date"
            type="date"
            className="field font-mono"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>
      </div>

      {!datesValid && (
        <p className="font-mono text-xs text-thermal">
          Trips run 1&ndash;7 days. Check the departure date.
        </p>
      )}

      <fieldset>
        <legend className="field-label">What you are here for</legend>
        <div className="flex flex-wrap gap-2">
          {PREFERENCES.map((preference) => {
            const active = preferences.includes(preference.value);
            return (
              <button
                key={preference.value}
                type="button"
                onClick={() => togglePreference(preference.value)}
                aria-pressed={active}
                className={`rounded-sm border px-3 py-1.5 text-sm transition-colors ${
                  active
                    ? "border-water bg-water/10 text-ink"
                    : "border-vellum bg-white/70 text-graphite hover:border-graphite/40 hover:bg-white"
                }`}
              >
                {preference.label}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset>
        <legend className="field-label">Spending</legend>
        <div className="grid gap-2 sm:grid-cols-3">
          {BUDGET_LEVELS.map((level) => {
            const active = budgetLevel === level.value;
            return (
              <button
                key={level.value}
                type="button"
                onClick={() => setBudgetLevel(level.value)}
                aria-pressed={active}
                className={`rounded-sm border px-3 py-2.5 text-left transition-colors ${
                  active
                    ? "border-water bg-white shadow-sheet"
                    : "border-vellum bg-white/70 hover:border-graphite/40 hover:bg-white"
                }`}
              >
                <span className="block text-sm text-ink">{level.label}</span>
              </button>
            );
          })}
        </div>
      </fieldset>

      <button
        type="button"
        onClick={submit}
        disabled={!canSubmit}
        className="w-full rounded-sm bg-ink px-4 py-3 font-display text-sm font-semibold
                   uppercase tracking-survey text-paper transition-colors
                   hover:bg-water disabled:cursor-not-allowed disabled:bg-graphite/40"
      >
        Plan this trip
      </button>
    </div>
  );
}
