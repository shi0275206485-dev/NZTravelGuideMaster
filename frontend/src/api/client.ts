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

/**
 * The code the traveller entered, or — in development only — one from
 * `VITE_ACCESS_CODE`.
 *
 * The development guard is the whole point. Vite inlines every `VITE_*`
 * variable into the built JavaScript as a string literal, so a code read
 * from the environment unconditionally would ship inside a bundle anyone
 * can download, and the gate would check a value it had already handed
 * out. `import.meta.env.DEV` is replaced with `false` in a production
 * build, and the branch — literal included — is then removed as dead code.
 */
export function getAccessCode(): string {
  const stored = sessionStorage.getItem(ACCESS_CODE_KEY);
  if (stored !== null) return stored;
  return import.meta.env.DEV ? (import.meta.env.VITE_ACCESS_CODE ?? "") : "";
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
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length) {
        // A schema rejection (422) arrives as a list of field errors
        // rather than a sentence. Reading the first one out is the
        // difference between "Trips cannot start in the past" and
        // "Request failed (422)".
        const first = body.detail[0];
        if (typeof first?.msg === "string") {
          detail = first.msg.replace(/^Value error,\s*/, "");
        }
      }
    } catch {
      // Non-JSON error body; the status-based message stands.
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; cache: Record<string, number> }>("/health"),
  /** Whether this deployment asks for a code at all. */
  access: () => request<{ required: boolean }>("/access"),
  /**
   * Check a candidate code before storing it. Sent explicitly rather than
   * from storage: until it is accepted it is not the traveller's code.
   * Wrong codes count against the same attempt limit as planning does.
   */
  verifyAccess: (code: string) =>
    request<{ ok: boolean }>("/access/verify", {
      method: "POST",
      headers: { "X-Access-Code": code },
    }),
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
