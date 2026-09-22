/**
 * Session-scoped history of generated plans.
 *
 * Kept in sessionStorage rather than in React state alone so a stray
 * refresh does not discard several minutes of planning, and rather than
 * localStorage because a shared or public machine should not still be
 * showing someone's trip tomorrow. Closing the tab is the intended way to
 * clear it.
 */

import type { TripPlan, TripRequest } from "./types/api";

const STORAGE_KEY = "travelguidemaster:history";

/**
 * Plans carry a full-day route polyline each, a few kilobytes apiece, so
 * an unbounded history would eventually hit the storage quota. Twelve is
 * far more than anyone compares in one sitting.
 */
const MAX_ENTRIES = 12;

export interface HistoryEntry {
  id: string;
  request: TripRequest;
  plan: TripPlan;
  createdAt: number;
}

function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    // Corrupt or unavailable storage is not worth failing the app over;
    // the user simply starts with an empty history.
    return [];
  }
}

function persist(entries: HistoryEntry[]): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // Over quota, or storage disabled. The in-memory list still works for
    // this session, which is the part the user can see.
  }
}

export function addToHistory(
  entries: HistoryEntry[],
  request: TripRequest,
  plan: TripPlan
): { entries: HistoryEntry[]; entry: HistoryEntry } {
  const entry: HistoryEntry = {
    id: newId(),
    request,
    plan,
    createdAt: Date.now(),
  };
  const next = [entry, ...entries].slice(0, MAX_ENTRIES);
  persist(next);
  return { entries: next, entry };
}

export function removeFromHistory(
  entries: HistoryEntry[],
  id: string
): HistoryEntry[] {
  const next = entries.filter((e) => e.id !== id);
  persist(next);
  return next;
}

export function clearHistory(): HistoryEntry[] {
  persist([]);
  return [];
}

/** "2 days in Rotorua" — the label a history entry is recognised by. */
export function describeEntry(entry: HistoryEntry): string {
  const days = entry.plan.days.length;
  return `${days} day${days === 1 ? "" : "s"} in ${entry.plan.destination}`;
}

export function formatTime(timestamp: number): string {
  return new Date(timestamp).toLocaleTimeString("en-NZ", {
    hour: "numeric",
    minute: "2-digit",
  });
}
