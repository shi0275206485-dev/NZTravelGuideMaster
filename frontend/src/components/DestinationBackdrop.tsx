import type { ReactNode } from "react";
import auckland from "../assets/auckland.jpg";
import rotorua from "../assets/rotorua.jpg";
import wellington from "../assets/wellington.jpg";
import type { Destination } from "../types/api";

/**
 * The chosen destination, washed across everything beneath it.
 *
 * The photograph is a backdrop rather than a picture: it sits at low
 * opacity under a paper-toned scrim, so it colours the page without
 * competing with the form on top of it. Choosing a destination changes
 * the light in the room, which makes the choice feel consequential in a
 * way a highlighted button does not.
 *
 * Kept deliberately faint. Text over photography is the reliable way to
 * ruin legibility, and this page is mostly text — so the image is pushed
 * back far enough that the ink on it still reads as ink on paper.
 */

const IMAGES: Record<string, { src: string; place: string }> = {
  Auckland: { src: auckland, place: "Waitematā Harbour" },
  Rotorua: { src: rotorua, place: "Wai-O-Tapu" },
  Wellington: { src: wellington, place: "Kelburn" },
};

interface DestinationBackdropProps {
  destinations: Destination[];
  selected: Destination | null;
  onSelect: (destination: Destination) => void;
  children: ReactNode;
}

export default function DestinationBackdrop({
  destinations,
  selected,
  onSelect,
  children,
}: DestinationBackdropProps) {
  const active = selected ? IMAGES[selected.name] : null;

  return (
    <section className="relative -mx-6 overflow-hidden rounded-sm">
      {/* All three stay mounted and cross-fade. Swapping one src would
          show a blank frame while the next decodes, turning a choice into
          what looks like a page load. Decorative, so hidden from the
          accessibility tree — the destination is named in text below. */}
      <div className="absolute inset-0" aria-hidden="true">
        {destinations.map((destination) => {
          const image = IMAGES[destination.name];
          if (!image) return null;
          return (
            <img
              key={destination.name}
              src={image.src}
              alt=""
              className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-700 ${
                selected?.name === destination.name ? "opacity-100" : "opacity-0"
              }`}
            />
          );
        })}
        {/* Two layers: a flat wash that holds the photograph back, and a
            gradient that thickens toward the foot, where the form's text
            is densest. */}
        <div className="absolute inset-0 bg-paper/[0.3]" />
        <div className="absolute inset-0 bg-gradient-to-b from-paper/30 via-paper/60 to-paper" />
      </div>

      <div className="relative px-6 py-10 sm:px-10">
        <div className="mx-auto max-w-2xl">
          <div
            role="radiogroup"
            aria-label="Destination"
            className="flex flex-wrap gap-2"
          >
            {destinations.map((destination) => {
              const isActive = selected?.name === destination.name;
              return (
                <button
                  key={destination.name}
                  type="button"
                  role="radio"
                  aria-checked={isActive}
                  onClick={() => onSelect(destination)}
                  className={`rounded-sm border px-4 py-2.5 font-display text-sm font-semibold shadow-sheet transition-colors ${
                    isActive
                      ? "border-ink bg-ink text-paper"
                      : "border-vellum bg-white/80 text-graphite backdrop-blur-sm hover:border-graphite/40 hover:text-ink"
                  }`}
                >
                  {destination.name}
                </button>
              );
            })}
          </div>

          {selected && (
            <p className="mt-3 font-mono text-[0.7rem] text-graphite">
              {Math.abs(selected.lat).toFixed(4)}°S {selected.lon.toFixed(4)}°E
              {active && <> · {active.place}</>}
            </p>
          )}

          <div className="mt-8">{children}</div>
        </div>
      </div>
    </section>
  );
}
