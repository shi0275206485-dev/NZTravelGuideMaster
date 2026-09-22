import auckland from "../assets/auckland.jpg";
import rotorua from "../assets/rotorua.jpg";
import wellington from "../assets/wellington.jpg";
import type { Destination } from "../types/api";

/**
 * The destination picker, as a photograph.
 *
 * A row of text buttons asks someone to choose between three names; a
 * photograph shows them what they are choosing. Each image is one the
 * traveller would recognise the place by — a skyline at dusk, a cable car
 * over a harbour, a thermal pool — which is also why they are the
 * project's own photographs rather than stock: no licence to honour, and
 * no chance of showing a picture of somewhere else.
 */

const IMAGES: Record<string, { src: string; alt: string; credit: string }> = {
  Auckland: {
    src: auckland,
    alt: "Auckland's skyline and Sky Tower at dusk, seen across the Waitematā Harbour",
    credit: "Waitematā Harbour",
  },
  Rotorua: {
    src: rotorua,
    alt: "A steaming geothermal pool with turquoise water and bright orange mineral terraces",
    credit: "Wai-O-Tapu",
  },
  Wellington: {
    src: wellington,
    alt: "Wellington's red cable car climbing above the city, with the harbour and hills behind",
    credit: "Kelburn",
  },
};

interface DestinationHeroProps {
  destinations: Destination[];
  selected: Destination | null;
  onSelect: (destination: Destination) => void;
}

export default function DestinationHero({
  destinations,
  selected,
  onSelect,
}: DestinationHeroProps) {
  const active = selected ? IMAGES[selected.name] : null;

  return (
    <section className="relative -mx-6 overflow-hidden">
      <div className="relative h-[46vh] min-h-[300px] w-full">
        {/* All three stay mounted and cross-fade. Swapping the src instead
            would show a blank frame while the next one decodes, which
            makes choosing a destination feel like a page load. */}
        {destinations.map((destination) => {
          const image = IMAGES[destination.name];
          if (!image) return null;
          const isActive = selected?.name === destination.name;
          return (
            <img
              key={destination.name}
              src={image.src}
              alt={isActive ? image.alt : ""}
              aria-hidden={!isActive}
              className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-500 ${
                isActive ? "opacity-100" : "opacity-0"
              }`}
            />
          );
        })}

        {/* Ink wash from the foot, so the name and the buttons keep their
            contrast whatever the photograph is doing behind them. */}
        <div
          className="absolute inset-0 bg-gradient-to-t from-ink/85 via-ink/25 to-ink/5"
          aria-hidden="true"
        />

        <div className="absolute inset-x-0 bottom-0 px-6 pb-5 sm:px-10">
          <div className="mx-auto max-w-6xl">
            {selected && (
              <div className="mb-4">
                <h2 className="font-display text-3xl font-bold leading-none text-paper sm:text-4xl">
                  {selected.name}
                </h2>
                <p className="mt-1.5 font-mono text-[0.7rem] text-paper/70">
                  {Math.abs(selected.lat).toFixed(4)}°S{" "}
                  {selected.lon.toFixed(4)}°E
                  {active && <> · {active.credit}</>}
                </p>
              </div>
            )}

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
                    className={`rounded-sm border px-4 py-2 font-display text-sm font-semibold backdrop-blur-sm transition-colors ${
                      isActive
                        ? "border-paper bg-paper text-ink"
                        : "border-paper/40 bg-ink/30 text-paper hover:border-paper/80"
                    }`}
                  >
                    {destination.name}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
