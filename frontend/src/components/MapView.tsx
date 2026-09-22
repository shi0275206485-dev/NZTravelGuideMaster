import { useEffect, useMemo } from "react";
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import polyline from "@mapbox/polyline";
import type { DayPlan, Hotel } from "../types/api";
import { dayColour } from "../format";

/**
 * The accommodation marker, drawn rather than loaded.
 *
 * Leaflet's default pin is a bundled PNG whose path it recomputes
 * internally via `_getIconUrl`, which overrides anything passed to
 * `mergeOptions` — the usual result being an invisible marker with a
 * popup pointing at nothing. A `divIcon` sidesteps the bundler entirely
 * and, since it is inline SVG, can carry the palette the rest of the map
 * uses instead of a generic blue teardrop.
 */
const HOTEL_ICON = L.divIcon({
  className: "",
  iconSize: [30, 38],
  iconAnchor: [15, 38],
  popupAnchor: [0, -34],
  html: `
    <svg width="30" height="38" viewBox="0 0 30 38" xmlns="http://www.w3.org/2000/svg">
      <path d="M15 37C15 37 27 23.5 27 14.5A12 12 0 1 0 3 14.5C3 23.5 15 37 15 37Z"
            fill="#1B2A2F" stroke="#F5F2EA" stroke-width="2" stroke-linejoin="round"/>
      <path d="M9 17.5v-5l6-4 6 4v5" fill="none" stroke="#F5F2EA"
            stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M12 17.5v-3.5h6v3.5" fill="none" stroke="#F5F2EA"
            stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`,
});

interface MapViewProps {
  centre: [number, number];
  days?: DayPlan[];
  hotel?: Hotel | null;
  /** Dims every day but this one; null shows them all equally. */
  focusedDay?: number | null;
  zoom?: number;
  className?: string;
}

/** Fits the viewport to whatever is currently plotted. */
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 14, { animate: true });
      return;
    }
    map.fitBounds(L.latLngBounds(points), { padding: [40, 40] });
  }, [points, map]);
  return null;
}

export default function MapView({
  centre,
  days = [],
  hotel,
  focusedDay = null,
  zoom = 12,
  className = "",
}: MapViewProps) {
  // Decoding is memoised: a day's polyline is a few thousand points, and
  // re-decoding on every hover would make focus changes feel sticky.
  const routes = useMemo(
    () =>
      days.map((day) => {
        const encoded = day.route_legs.find((leg) => leg.geometry)?.geometry;
        return {
          day: day.day,
          path: encoded
            ? (polyline.decode(encoded) as [number, number][])
            : [],
        };
      }),
    [days]
  );

  const stops = useMemo(
    () =>
      days.flatMap((day) =>
        day.items.map((item, index) => ({
          day: day.day,
          order: index + 1,
          id: item.attraction.id,
          name: item.attraction.name,
          note: item.note,
          position: [
            item.attraction.location.lat,
            item.attraction.location.lon,
          ] as [number, number],
        }))
      ),
    [days]
  );

  const bounds = useMemo(() => {
    const points = stops.map((s) => s.position);
    if (hotel) points.push([hotel.location.lat, hotel.location.lon]);
    return points;
  }, [stops, hotel]);

  const dimmed = (day: number) => focusedDay !== null && focusedDay !== day;

  return (
    <MapContainer
      center={centre}
      zoom={zoom}
      scrollWheelZoom={false}
      className={className}
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        // Required for exporting: tiles fetched without CORS taint the
        // canvas, and reading it back then throws rather than degrading.
        crossOrigin="anonymous"
      />
      {bounds.length > 0 ? (
        <FitBounds points={bounds} />
      ) : (
        <FitBounds points={[centre]} />
      )}

      {routes.map(
        ({ day, path }) =>
          path.length > 1 && (
            <Polyline
              key={`route-${day}`}
              positions={path}
              pathOptions={{
                color: dayColour(day),
                weight: dimmed(day) ? 2 : 4,
                opacity: dimmed(day) ? 0.25 : 0.85,
              }}
            />
          )
      )}

      {hotel && (
        <Marker
          position={[hotel.location.lat, hotel.location.lon]}
          icon={HOTEL_ICON}
        >
          <Popup>
            <span className="font-sans text-sm font-semibold">{hotel.name}</span>
            <span className="block font-sans text-xs text-graphite">
              Your accommodation
            </span>
          </Popup>
        </Marker>
      )}

      {/* Numbered circles rather than pins: the number carries the visiting
          order, and the colour carries the day — both of which a default
          pin cannot express. */}
      {stops.map((stop) => (
        <CircleMarker
          key={`${stop.day}-${stop.id}`}
          center={stop.position}
          radius={dimmed(stop.day) ? 7 : 11}
          pathOptions={{
            color: "#1B2A2F",
            weight: 2,
            fillColor: dayColour(stop.day),
            fillOpacity: dimmed(stop.day) ? 0.35 : 1,
          }}
        >
          <Popup>
            <span className="font-sans text-[0.66rem] uppercase tracking-wide text-graphite">
              Day {stop.day} · stop {stop.order}
            </span>
            <span className="block font-sans text-sm font-semibold">
              {stop.name}
            </span>
            <span className="block font-sans text-xs text-graphite">
              {stop.note}
            </span>
          </Popup>
        </CircleMarker>
      ))}
    </MapContainer>
  );
}
