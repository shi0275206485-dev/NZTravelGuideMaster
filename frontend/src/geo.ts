import type { Location } from "./types/api";

/**
 * Distance in the browser.
 *
 * Straight-line, computed locally, instantly. The itinerary's real travel
 * times come from OSRM on the server and follow roads — but for the
 * question "which of these would fit into this day", a crow-flies figure
 * answered the moment the list renders is worth more than a road distance
 * that needs a round trip per candidate.
 */

const EARTH_RADIUS_KM = 6371;

export function haversineKm(a: Location, b: Location): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(h));
}

/**
 * How far a candidate is from the nearest place already in the day.
 *
 * The nearest rather than the average: a stop that sits beside one of the
 * day's existing stops fits the day, even if the day's other end is across
 * town. An average would penalise it for the distance already there.
 */
export function distanceToNearest(
  point: Location,
  others: Location[]
): number | null {
  if (others.length === 0) return null;
  return Math.min(...others.map((other) => haversineKm(point, other)));
}

export function formatKm(km: number): string {
  if (km < 1) return `${Math.round(km * 1000)} m`;
  return `${km.toFixed(1)} km`;
}
