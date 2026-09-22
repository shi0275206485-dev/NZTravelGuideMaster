import type { Destination, TripPlan, TripRequest } from "../types/api";

/**
 * Thin API client.
 *
 * The access code travels as a header rather than in the body so it can be
 * checked and rejected before any request handling begins — the backend
 * turns away an invalid code before the pipeline, and therefore the LLM,
 * is ever reached.
 */

const ACCESS_CODE_KEY = "travelguidemaster:access-code";

export function getAccessCode(): string {
  return (
    sessionStorage.getItem(ACCESS_CODE_KEY) ??
    import.meta.env.VITE_ACCESS_CODE ??
    ""
  );
}

export function setAccessCode(code: string): void {
  sessionStorage.setItem(ACCESS_CODE_KEY, code);
}

export function clearAccessCode(): void {
  sessionStorage.removeItem(ACCESS_CODE_KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Access-Code": getAccessCode(),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    if (response.status === 401) clearAccessCode();
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Non-JSON error body; the status-based message stands.
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; cache: Record<string, number> }>("/health"),
  destinations: () => request<Destination[]>("/destinations"),
  plan: (body: TripRequest) =>
    request<TripPlan>("/plan", { method: "POST", body: JSON.stringify(body) }),
  /** Deterministic: no model call, so edits settle in about a second. */
  recompute: (plan: TripPlan) =>
    request<TripPlan>("/recompute", {
      method: "POST",
      body: JSON.stringify(plan),
    }),
};
