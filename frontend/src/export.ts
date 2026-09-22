import type { TripPlan } from "./types/api";

/**
 * Turning the printable sheet into a file.
 *
 * The sheet is rendered off-screen by the app and handed here as a DOM
 * node. Capturing a node the user cannot see keeps the export independent
 * of the interface's current state — nothing is mid-scroll, no dialog is
 * open, no row is hovered.
 *
 * There is no server involved: both formats are produced in the browser,
 * which is one fewer thing to deploy and means the itinerary never leaves
 * the machine to be printed.
 *
 * The two libraries are imported on demand. Together they are larger than
 * the rest of the application, and most visits never export anything —
 * loading them up front would make everyone wait for a feature few use.
 */

/** Retina-ish. Below 2 the mono type goes soft; above it the PDF bloats. */
const SCALE = 2;

const A4 = { width: 210, height: 297 }; // mm
const MARGIN_MM = 12;

export class ExportError extends Error {}

/**
 * Photographs the live map for the printed sheet.
 *
 * The alternative — drawing the routes as a schematic from their
 * coordinates — was tried and discarded: without a coastline, roads or
 * place names, dots and lines on a blank field tell a reader nothing
 * about where they are. A real map is worth the fragility.
 *
 * Fragile because tile images can taint the canvas. OpenStreetMap serves
 * tiles with CORS headers and the layer requests them accordingly, so
 * this normally succeeds; when it does not, null is returned and the
 * sheet prints without a map rather than failing entirely.
 */
export async function captureMap(
  node: HTMLElement | null
): Promise<string | null> {
  if (!node) return null;
  try {
    const { default: html2canvas } = await import("html2canvas");
    const canvas = await html2canvas(node, {
      scale: 2,
      useCORS: true,
      logging: false,
      backgroundColor: "#F5F2EA",
    });
    return canvas.toDataURL("image/jpeg", 0.9);
  } catch (error) {
    console.warn("map capture failed; exporting without it", error);
    return null;
  }
}

function filename(plan: TripPlan, extension: string): string {
  const place = plan.destination.toLowerCase().replace(/[^a-z]/g, "");
  return `travelguidemaster-${place}-${plan.start_date}.${extension}`;
}

async function capture(node: HTMLElement): Promise<HTMLCanvasElement> {
  const { default: html2canvas } = await import("html2canvas");
  try {
    return await html2canvas(node, {
      scale: SCALE,
      backgroundColor: "#F5F2EA",
      // The sheet is same-origin and image-free apart from inline SVG, so
      // nothing here can taint the canvas — the flags are belt and braces
      // in case a future version adds a photograph.
      useCORS: true,
      logging: false,
    });
  } catch (error) {
    throw new ExportError(
      `Could not render the itinerary for export: ${
        error instanceof Error ? error.message : String(error)
      }`
    );
  }
}

function download(href: string, name: string): void {
  const link = document.createElement("a");
  link.href = href;
  link.download = name;
  link.click();
}

export async function exportPng(
  node: HTMLElement,
  plan: TripPlan
): Promise<void> {
  const canvas = await capture(node);
  download(canvas.toDataURL("image/png"), filename(plan, "png"));
}

export async function exportPdf(
  node: HTMLElement,
  plan: TripPlan
): Promise<void> {
  const [canvas, { jsPDF }] = await Promise.all([
    capture(node),
    import("jspdf"),
  ]);
  const pdf = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });

  const usableWidth = A4.width - MARGIN_MM * 2;
  const usableHeight = A4.height - MARGIN_MM * 2;
  const imageHeight = (canvas.height / canvas.width) * usableWidth;

  const image = canvas.toDataURL("image/jpeg", 0.92);

  if (imageHeight <= usableHeight) {
    pdf.addImage(image, "JPEG", MARGIN_MM, MARGIN_MM, usableWidth, imageHeight);
  } else {
    // Taller than a page, so it is sliced across several. The whole image
    // is placed each time and clipped by the page — offsetting it upward
    // rather than re-rendering, which would mean capturing the sheet once
    // per page.
    let remaining = imageHeight;
    let offset = 0;
    while (remaining > 0) {
      pdf.addImage(
        image,
        "JPEG",
        MARGIN_MM,
        MARGIN_MM - offset,
        usableWidth,
        imageHeight
      );
      remaining -= usableHeight;
      offset += usableHeight;
      if (remaining > 0) pdf.addPage();
    }
  }

  pdf.save(filename(plan, "pdf"));
}
