/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // A LINZ Topo50 sheet: warm map paper, sepia contours, harbour
        // blue, bush green, and the sulphur ochre of Rotorua's thermal
        // fields. Light rather than dark because planning a trip is a
        // cheerful act and a dark instrument panel does not read that way —
        // but drawn from survey cartography rather than the blue-sky
        // gradient every travel site already uses.
        paper: "#F5F2EA", // page
        parchment: "#EBE6DA", // panels
        vellum: "#E0DACB", // borders, dividers
        ink: "#1B2A2F", // primary text        13.2:1 on paper
        graphite: "#54666C", // secondary text  5.4:1 on paper
        contour: "#9C7248", // contour lines, graphics only
        water: "#2F6E96", // interactive, links  5.0:1
        bush: "#2C6249", // positive, selected   6.4:1
        thermal: "#B4661E", // warnings, accents — large text and graphics
      },
      fontFamily: {
        display: ["Archivo", "system-ui", "sans-serif"],
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono: ["'IBM Plex Mono'", "ui-monospace", "monospace"],
      },
      letterSpacing: {
        survey: "0.18em",
      },
      boxShadow: {
        // Paper lying on paper: a shallow, warm-tinted lift rather than a
        // grey drop shadow, which reads as plastic on a map sheet.
        sheet: "0 1px 2px rgba(27, 42, 47, 0.06), 0 2px 8px rgba(27, 42, 47, 0.04)",
      },
    },
  },
  plugins: [],
};
