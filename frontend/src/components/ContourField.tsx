import { useMemo } from "react";

/**
 * Contour lines, seeded from a destination's own coordinates.
 *
 * Every destination gets a stable, distinct pattern — Rotorua's is not
 * Wellington's — because the seed is the latitude and longitude the app
 * already holds. It reads as terrain rather than as ornament, which is the
 * point: this is a planner for moving across ground.
 */

interface ContourFieldProps {
  seed: number;
  lines?: number;
  className?: string;
  /** Height of the drawing box; width is always 100%. */
  height?: number;
}

/** Deterministic PRNG, so a destination's terrain never shifts between renders. */
function makeRandom(seed: number): () => number {
  let state = Math.floor(Math.abs(seed) * 1000) % 2147483647;
  if (state <= 0) state += 2147483646;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

function contourPath(random: () => number, index: number, width: number, height: number): string {
  const steps = 9;
  const baseY = (height / (steps - 2)) * index * 0.85;
  const amplitude = 10 + random() * 26;
  const points: string[] = [];

  for (let i = 0; i <= steps; i++) {
    const x = (width / steps) * i;
    const drift = Math.sin(i * 0.9 + index * 0.6) * amplitude;
    const jitter = (random() - 0.5) * 12;
    points.push(`${x.toFixed(1)},${(baseY + drift + jitter).toFixed(1)}`);
  }

  // Smooth the polyline into a curve so it reads as terrain, not a chart.
  return points.reduce((path, point, i) => {
    if (i === 0) return `M ${point}`;
    const [px, py] = points[i - 1].split(",").map(Number);
    const [cx, cy] = point.split(",").map(Number);
    const midX = (px + cx) / 2;
    return `${path} Q ${px.toFixed(1)},${py.toFixed(1)} ${midX.toFixed(1)},${((py + cy) / 2).toFixed(1)}`;
  }, "");
}

export default function ContourField({
  seed,
  lines = 7,
  className = "",
  height = 120,
}: ContourFieldProps) {
  const width = 320;

  const paths = useMemo(() => {
    const random = makeRandom(seed);
    return Array.from({ length: lines }, (_, i) => contourPath(random, i, width, height));
  }, [seed, lines, height]);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {paths.map((d, i) => (
        <path
          key={i}
          d={d}
          fill="none"
          stroke="currentColor"
          strokeWidth={i % 3 === 0 ? 1.1 : 0.6}
          strokeOpacity={i % 3 === 0 ? 0.55 : 0.28}
        />
      ))}
    </svg>
  );
}
