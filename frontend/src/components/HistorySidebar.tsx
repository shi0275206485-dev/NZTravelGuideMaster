import type { HistoryEntry } from "../history";
import { describeEntry, formatTime } from "../history";

interface HistorySidebarProps {
  entries: HistoryEntry[];
  activeId: string | null;
  /** True while the form is showing, so no entry is the current view. */
  onForm: boolean;
  onSelect: (entry: HistoryEntry) => void;
  onReuse: (entry: HistoryEntry) => void;
  onRemove: (id: string) => void;
  onNew: () => void;
}

export default function HistorySidebar({
  entries,
  activeId,
  onForm,
  onSelect,
  onReuse,
  onRemove,
  onNew,
}: HistorySidebarProps) {
  return (
    <nav
      aria-label="Your plans"
      className="lg:sticky lg:top-6 lg:self-start"
    >
      <button
        type="button"
        onClick={onNew}
        aria-current={onForm ? "page" : undefined}
        className={`w-full rounded-sm border px-3 py-2.5 text-left font-mono text-[0.7rem] uppercase tracking-survey transition-colors ${
          onForm
            ? "border-thermal bg-thermal/10 text-thermal"
            : "border-vellum text-graphite hover:border-ink hover:text-ink"
        }`}
      >
        + New trip
      </button>

      {entries.length > 0 && (
        <>
          <p className="eyebrow mt-6 mb-2">This session</p>
          <ul className="space-y-1">
            {entries.map((entry) => {
              const active = entry.id === activeId && !onForm;
              return (
                <li key={entry.id}>
                  <div
                    className={`group rounded-sm border transition-colors ${
                      active
                        ? "border-water bg-parchment"
                        : "border-transparent hover:border-vellum hover:bg-white"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => onSelect(entry)}
                      aria-current={active ? "page" : undefined}
                      className="w-full px-3 pt-2.5 pb-1 text-left"
                    >
                      <span
                        className={`block text-[0.82rem] leading-snug ${
                          active ? "text-ink" : "text-graphite"
                        }`}
                      >
                        {describeEntry(entry)}
                      </span>
                      <span className="mt-0.5 block font-mono text-[0.62rem] text-graphite/60">
                        {formatTime(entry.createdAt)}
                        {entry.request.preferences.length > 0 && (
                          <> · {entry.request.preferences.join(", ")}</>
                        )}
                      </span>
                    </button>

                    {/* Actions stay hidden until the entry is hovered or
                        focused: a list of twelve plans with six buttons
                        each reads as a control panel, not a history. */}
                    <div className="flex gap-3 px-3 pb-2 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={() => onReuse(entry)}
                        className="font-mono text-[0.62rem] uppercase tracking-survey text-graphite hover:text-water"
                      >
                        Reuse
                      </button>
                      <button
                        type="button"
                        onClick={() => onRemove(entry.id)}
                        className="font-mono text-[0.62rem] uppercase tracking-survey text-graphite hover:text-thermal"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>

          <p className="mt-4 text-[0.7rem] leading-snug text-graphite/60">
            Kept until you close this tab. &ldquo;Reuse&rdquo; opens the form
            with the same choices, ready to adjust.
          </p>
        </>
      )}
    </nav>
  );
}
