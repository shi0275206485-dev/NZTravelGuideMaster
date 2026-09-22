import { useMemo } from "react";
import polyline from "@mapbox/polyline";
import type { DayPlan, Hotel } from "../types/api";
import { dayColour } from "../format";

/**
 * A schematic of the trip's routes, drawn rather than screenshotted.
 *
 * The obvious approach — capturing the Leaflet map — fails on two counts.
 * Tile images taint the canvas unless every tile server cooperates with
 * CORS, so the capture throws rather than degrading; and a street map
 * rendered at print size is mostly labels for streets nobody is looking
 * for. What a printed itinerary actually needs from a map is the shape of
 * each day and where its stops sit relative to one another.
 *
 * Drawing it from the polylines already in the plan gives exactly that,
 * with no network, no tiles, and no way for it to fail.
 */

interface RouteSchematicProps {
  days: DayPlan[];
  hotel?: Hotel | null;
  width?: number;
  height?: number;
  /** Names beside each stop, for when this stands in for a real map. */
  labelled?: boolean;
}

interface Point {
  lat: number;
  lon: number;
}

export default function RouteSchematic({
  days,
  hotel,
  width = 620,
  height = 380,
  labelled = false,
}: RouteSchematicProps) {
  const drawing = useMemo(() => {
    const routes = days.map((day) => ({
      day: day.day,
      path: (day.route_legs.find((leg) => leg.geometry)?.geometry
        ? (polyline.decode(
            day.route_legs.find((leg) => leg.geometry)!.geometry!
          ) as [number, number][])
        : []
      ).map(([lat, lon]) => ({ lat, lon })),
    }));

    const stops = days.flatMap((day) =>
      day.items.map((item, index) => ({
        day: day.day,
        order: index + 1,
        name: item.attraction.name,
        lat: item.attraction.location.lat,
        lon: item.attraction.location.lon,
      }))
    );

    const all: Point[] = [
      ...routes.flatMap((r) => r.path),
      ...stops,
      ...(hotel ? [{ lat: hotel.location.lat, lon: hotel.location.lon }] : []),
    ];
    if (all.length === 0) return null;

    const lats = all.map((p) => p.lat);
    const lons = all.map((p) => p.lon);
    const minLat = Math.min(...lats);
    const maxLat = Math.max(...lats);
    const minLon = Math.min(...lons);
    const maxLon = Math.max(...lons);

    // Latitude degrees are a fixed distance; longitude degrees shrink
    // toward the poles. Without this correction a New Zealand map comes
    // out noticeably stretched east to west.
    const lonScale = Math.cos(((minLat + maxLat) / 2) * (Math.PI / 180));
    const spanLat = Math.max(maxLat - minLat, 1e-4);
    const spanLon = Math.max((maxLon - minLon) * lonScale, 1e-4);

    const pad = 24;
    const scale = Math.min(
      (width - pad * 2) / spanLon,
      (height - pad * 2) / spanLat
    );
    const offsetX = (width - spanLon * scale) / 2;
    const offsetY = (height - spanLat * scale) / 2;

    const project = (p: Point) => ({
      x: offsetX + (p.lon - minLon) * lonScale * scale,
      // SVG y grows downward; latitude grows north.
      y: offsetY + (maxLat - p.lat) * scale,
    });

    return {
      routes: routes.map((r) => ({
        day: r.day,
        d: r.path
          .map((p, i) => {
            const { x, y } = project(p);
            return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
          })
          .join(" "),
      })),
      stops: stops.map((s) => ({ ...s, ...project(s) })),
      hotel: hotel
        ? project({ lat: hotel.location.lat, lon: hotel.location.lon })
        : null,
    };
  }, [days, hotel, width, height]);

  if (!drawing) return null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label="Schematic of each day's route and stops"
      style={{ background: "#F5F2EA" }}
    >
      <rect
        x="0.5"
        y="0.5"
        width={width - 1}
        height={height - 1}
        fill="none"
        stroke="#E0DACB"
      />

      {drawing.routes.map(
        ({ day, d }) =>
          d && (
            <path
              key={day}
              d={d}
              fill="none"
              stroke={dayColour(day)}
              strokeWidth={2.5}
              strokeLinejoin="round"
              strokeLinecap="round"
              opacity={0.9}
            />
          )
      )}

      {drawing.hotel && (
        <g transform={`translate(${drawing.hotel.x}, ${drawing.hotel.y})`}>
          <path
            d="M0 6 L-6 0 L0 -6 L6 0 Z"
            fill="#1B2A2F"
            stroke="#F5F2EA"
            strokeWidth={1.5}
          />
        </g>
      )}

      {drawing.stops.map((stop) => (
        <g key={`${stop.day}-${stop.order}`} transform={`translate(${stop.x}, ${stop.y})`}>
          <circle r={9} fill={dayColour(stop.day)} stroke="#1B2A2F" strokeWidth={1.5} />
          <text
            textAnchor="middle"
            dy="3.5"
            fontSize="10"
            fontFamily="'IBM Plex Mono', monospace"
            fill="#F5F2EA"
          >
            {stop.order}
          </text>
          {labelled && (
            <text
              x={12}
              dy="3.5"
              fontSize="9"
              fontFamily="'IBM Plex Sans', sans-serif"
              fill="#1B2A2F"
              // Drawn twice: a thick paper-coloured stroke beneath the
              // fill, so a label crossing a route line stays readable
              // without a box around it.
              stroke="#F5F2EA"
              strokeWidth={3}
              paintOrder="stroke"
            >
              {stop.name}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}
