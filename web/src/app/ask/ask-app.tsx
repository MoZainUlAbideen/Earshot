"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AnswerView } from "@/components/answer-view";
import { ApiError, ask, listEpisodes, wakeServer, type Answer, type Episode } from "@/lib/api";
import { formatTime } from "@/lib/answer-text";

type Server = "checking" | "waking" | "ready" | "down";
const SLOW_MS = 2500; // a warm server answers /health well under this; slower means it was asleep
const MAX_CHARS = 300; // same limit the API enforces

export function AskApp() {
  const initial = useSearchParams().get("q") ?? "";
  const [question, setQuestion] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [server, setServer] = useState<Server>("checking");
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const autoAsked = useRef(false);

  useEffect(() => {
    let done = false;
    const slow = setTimeout(() => !done && setServer("waking"), SLOW_MS);
    wakeServer().then((ok) => {
      done = true;
      clearTimeout(slow);
      setServer(ok ? "ready" : "down");
      if (ok) listEpisodes().then(setEpisodes, () => {});
    });
    return () => clearTimeout(slow);
  }, []);

  async function submit(q: string) {
    const text = q.trim();
    if (text.length < 3 || loading) return;
    setLoading(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await ask(text));
      setServer("ready");
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError("Something went wrong. Please try again.", 0);
      const wait = err.status === 429 && err.retryAfter ? ` You can ask again in about ${Math.ceil(err.retryAfter / 60)} min.` : "";
      setError(err.message + wait);
    } finally {
      setLoading(false);
    }
  }

  // Arriving from an example link (/ask?q=...): ask it straight away, once.
  useEffect(() => {
    if (initial && !autoAsked.current) {
      autoAsked.current = true;
      void submit(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once for the initial ?q=
  }, []);

  return (
    <div className="grid gap-6">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(question);
        }}
        className="grid gap-2"
      >
        <label htmlFor="question" className="sr-only">Your question</label>
        <div className="flex gap-2 rounded-md border border-edge bg-panel p-2 focus-within:border-accent">
          <input
            id="question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={MAX_CHARS}
            placeholder="e.g. What is an AGENTS.md file used for?"
            autoComplete="off"
            className="min-w-0 flex-1 bg-transparent px-2 text-ink placeholder:text-muted focus:outline-none"
          />
          <button
            type="submit"
            disabled={loading || question.trim().length < 3}
            className="shrink-0 rounded bg-brand px-4 py-2 font-semibold text-white hover:bg-brand-hi disabled:opacity-40"
          >
            {loading ? "Asking…" : "Ask"}
          </button>
        </div>
        <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
          <ServerStatus server={server} />
          <span className="tabular-nums">{question.length}/{MAX_CHARS}</span>
        </div>
      </form>

      <div aria-live="polite" className="grid gap-4">
        {loading && (
          <div className="rounded-md border border-edge bg-panel p-5 text-ink-2">
            {server === "ready"
              ? "Searching 875 moments and checking every quote…"
              : "Waking up the server. It sleeps when nobody is using it, so the first question can take about a minute."}
          </div>
        )}
        {error && <div role="alert" className="rounded-md border border-brand/50 bg-brand/10 p-5 text-ink">{error}</div>}
        {answer && <AnswerView answer={answer} />}
      </div>

      {episodes.length > 0 && (
        <section className="grid gap-3 border-t border-edge pt-6">
          <h2 className="font-display text-xl font-semibold">Indexed episodes</h2>
          <ul className="grid gap-px overflow-hidden rounded-md border border-edge bg-edge">
            {episodes.map((ep) => (
              <li key={ep.id} className="flex flex-wrap justify-between gap-x-4 gap-y-1 bg-panel px-4 py-3 text-sm">
                <span className="min-w-0 text-ink-2">{ep.title}</span>
                <span className="font-mono text-xs text-muted tabular-nums">
                  {ep.published_at?.slice(0, 10)}{ep.duration_seconds ? ` · ${formatTime(ep.duration_seconds)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ServerStatus({ server }: { server: Server }) {
  const text = {
    checking: "Connecting to the server…",
    waking: "Server is waking up (about a minute on the free tier)…",
    ready: "Server ready",
    down: "Server unreachable right now. Try again in a minute.",
  }[server];
  const dot = { checking: "bg-muted", waking: "bg-amber-400 animate-pulse", ready: "bg-emerald-400", down: "bg-brand" }[server];
  return (
    <span className="flex items-center gap-2">
      <span className={`size-2 rounded-full ${dot}`} aria-hidden="true" />
      {text}
    </span>
  );
}
