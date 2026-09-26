import { useState, type FormEvent } from "react";
import { ApiError, api, setAccessCode } from "../api/client";
import ContourField from "./ContourField";

const CONTACT_URL = "https://www.linkedin.com/in/jinchun-shi-624311389/";

interface AccessGateProps {
  onUnlocked: () => void;
  /** Why the gate is showing again, when a stored code stopped working. */
  notice?: string | null;
}

/**
 * The first thing a visitor sees on a deployment that requires a code.
 *
 * Asked for up front rather than after a refused plan: filling in a trip
 * and waiting through a failed submission to learn that a code was needed
 * all along is a poor first impression, and it charged the visitor an
 * attempt for a code they had never been asked for.
 *
 * This gate is the courtesy, not the protection. The page's JavaScript is
 * public, so the gate can be stepped around; the server checks the code on
 * every generation regardless, and that check is what keeps the model
 * costs bounded.
 */
export default function AccessGate({ onUnlocked, notice }: AccessGateProps) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(notice ?? null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const candidate = code.trim();
    if (!candidate || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.verifyAccess(candidate);
      setAccessCode(candidate);
      onUnlocked();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("That code isn't valid. Check it and try again.");
      } else if (err instanceof ApiError) {
        // 429 carries the server's own wording about the lockout.
        setError(err.message);
      } else {
        setError("Could not reach the planner. Please try again shortly.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative mx-auto mt-6 max-w-md overflow-hidden rounded-sm border border-vellum bg-white shadow-sheet">
      {/* A band of contours along the top edge, clear of the text: the same
          terrain motif as the header, without drawing lines through the
          words a visitor is being asked to read. */}
      <div className="relative h-14 border-b border-vellum bg-parchment/40">
        <ContourField
          seed={6712}
          lines={5}
          height={56}
          className="pointer-events-none absolute inset-0 h-full w-full text-contour"
        />
      </div>
      <form onSubmit={submit} className="relative px-6 pb-6 pt-6">
        <p className="eyebrow">Demo access</p>
        <h2 className="mt-2 font-display text-2xl font-semibold leading-tight text-ink">
          Plan a New Zealand trip with a team of AI agents
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-graphite">
          This is a live demonstration of TravelGuideMaster. Generating an
          itinerary calls a language model, so access is by code.
        </p>

        <label className="field-label mt-6" htmlFor="access-code">
          Access code
        </label>
        <input
          id="access-code"
          className="field font-mono"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          autoFocus
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "access-error" : undefined}
        />

        {error && (
          <p
            id="access-error"
            role="alert"
            className="mt-3 rounded-sm border border-thermal/50 bg-thermal/10 px-3 py-2 text-sm text-ink"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !code.trim()}
          className="mt-5 w-full rounded-sm bg-water px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-40"
        >
          {busy ? "Checking…" : "Continue"}
        </button>

        <p className="mt-6 border-t border-vellum pt-4 text-sm text-graphite">
          Don&rsquo;t have a code?{" "}
          <a
            href={CONTACT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-water underline decoration-water/40 underline-offset-2 hover:decoration-water"
          >
            Contact me on LinkedIn
          </a>{" "}
          for trial access.
        </p>
      </form>
    </div>
  );
}
