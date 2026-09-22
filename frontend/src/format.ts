/**
 * Presentation helpers shared across the itinerary views.
 *
 * Kept out of the components because several of them format the same
 * things — a cost range appears on an attraction card, a meal row, the
 * hotel and the budget table — and a range that reads differently in each
 * place looks like four different kinds of number.
 */

import type { CostEstimate, TimeSlot, WeatherInfo } from "./types/api";

/** Above this, a day is mostly travel and the figure is worth flagging. */
export const HEAVY_TRAVEL_MINUTES = 45;

export function formatCost(cost?: CostEstimate | null): string {
  if (!cost) return "—";
  if (cost.high_nzd === 0) return "Free";
  const low = Math.round(cost.low_nzd);
  const high = Math.round(cost.high_nzd);
  return low === high ? `$${low}` : `$${low}–${high}`;
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-NZ", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export const TIME_SLOT_LABEL: Record<TimeSlot, string> = {
  morning: "Morning",
  afternoon: "Afternoon",
  evening: "Evening",
};

/**
 * A short weather line, or null when there is nothing to say.
 *
 * Returns null rather than "unavailable" for days outside the forecast
 * window — the itinerary already carries one note explaining the gap, and
 * repeating it on every card turns an honest caveat into noise.
 */
export function weatherLine(weather?: WeatherInfo | null): string | null {
  if (!weather?.available) return null;
  const temps =
    weather.temp_min_c != null && weather.temp_max_c != null
      ? `${Math.round(weather.temp_min_c)}–${Math.round(weather.temp_max_c)}°C`
      : null;
  const rain =
    weather.precipitation_probability != null
      ? `${weather.precipitation_probability}% rain`
      : null;
  return [temps, rain].filter(Boolean).join(" · ") || null;
}

/**
 * A route colour per day, so overlapping lines stay tellable apart.
 *
 * Chosen for contrast against the light OSM base map — pale tints vanish
 * on it — and to stay distinguishable from one another for the common
 * forms of colour blindness, since the day number is the only other thing
 * carrying this information.
 */
export const DAY_COLOURS = [
  "#B4661E", // thermal
  "#2F6E96", // water
  "#2C6249", // bush
  "#8A3F52", // rata
  "#6B4E8C", // tussock shadow
  "#A8721E", // ochre
  "#3E6B7A", // slate blue
];

export function dayColour(day: number): string {
  return DAY_COLOURS[(day - 1) % DAY_COLOURS.length];
}
