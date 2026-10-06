// The one place the site talks to the Earshot API (FastAPI on Render).
// The browser calls Render directly; pages are static and never wait on the API at build time.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "https://earshot-api.onrender.com";

export type Citation = {
  n: number; // source number used in the answer text: [n]. Several quotes can share one n.
  quote: string;
  episode_id: number;
  title: string;
  audio_url: string;
  time: number; // seconds into the episode
};

export type Answer = { question: string; answer: string; found: boolean; citations: Citation[] };

export type Episode = {
  id: number;
  title: string;
  published_at: string | null;
  duration_seconds: number | null;
  audio_url: string;
};

/** A failure already phrased for the visitor. status 0 = the server couldn't be reached. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfter?: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 120_000): Promise<T> {
  let res: Response;
  try {
    // Long timeout on purpose: a sleeping free-tier server takes about a minute to wake.
    res = await fetch(`${API_URL}${path}`, { ...init, signal: AbortSignal.timeout(timeoutMs) });
  } catch {
    throw new ApiError("Can't reach the Earshot server right now. Please try again in a minute.", 0);
  }
  if (res.ok) return (await res.json()) as T;

  const retry = Number(res.headers.get("Retry-After")) || undefined;
  let detail: unknown;
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  if (res.status === 422) throw new ApiError("Questions need between 3 and 300 characters.", 422);
  // The API writes its 429/502/503 messages for people, so show them as they are.
  const message = typeof detail === "string" ? detail : "Something went wrong. Please try again.";
  throw new ApiError(message, res.status, retry);
}

let wake: Promise<boolean> | undefined;

/** Ping /health once per page load so a sleeping server starts waking while the visitor reads. */
export function wakeServer(): Promise<boolean> {
  wake ??= request("/health", {}, 150_000).then(() => true, () => { wake = undefined; return false; });
  return wake;
}

export const ask = (question: string) =>
  request<Answer>("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

export const listEpisodes = () => request<Episode[]>("/episodes");
