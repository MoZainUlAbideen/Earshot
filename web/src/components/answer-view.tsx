"use client";

import { useRef, useState } from "react";
import type { Answer, Citation } from "@/lib/api";
import { formatTime, splitCitations } from "@/lib/answer-text";

const LEAD_IN_SECONDS = 1; // start just before the quote so the first word isn't clipped

/** An answer with clickable citations, and one audio player that jumps to the cited second.
 *  Audio streams straight from the publisher's server; Earshot never hosts it. */
export function AnswerView({ answer }: { answer: Answer }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [current, setCurrent] = useState<Citation | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [audioError, setAudioError] = useState(false);

  function play(c: Citation) {
    const el = audio.current;
    if (!el) return;
    setAudioError(false);
    setCurrent(c);
    const start = Math.max(0, c.time - LEAD_IN_SECONDS);
    const seekAndPlay = () => {
      el.currentTime = start;
      el.play().catch(() => setAudioError(true));
    };
    if (el.dataset.src !== c.audio_url) {
      el.dataset.src = c.audio_url;
      el.src = c.audio_url;
      el.addEventListener("loadedmetadata", seekAndPlay, { once: true }); // can't seek before this
      el.load();
    } else {
      seekAndPlay();
    }
  }

  const firstFor = (n: number) => answer.citations.find((c) => c.n === n);

  if (!answer.found) {
    return (
      <div className="rounded-md border border-edge bg-raised p-6">
        <p className="font-display text-lg font-semibold">No answer found in these episodes.</p>
        <p className="mt-1 text-muted">{answer.answer}</p>
      </div>
    );
  }

  return (
    <div className="grid gap-5 rounded-md border border-edge bg-raised p-5 sm:p-6">
      <p className="text-[15.5px] leading-7 text-ink-2">
        {splitCitations(answer.answer).map((seg, i) => {
          if (seg.kind === "text") return <span key={i}>{seg.text}</span>;
          const c = firstFor(seg.n);
          if (!c) return null; // the critic removed this source; drop its marker
          return (
            <button
              key={i}
              type="button"
              onClick={() => play(c)}
              title={`Play from ${formatTime(c.time)}`}
              className="mx-0.5 rounded-sm bg-accent/15 px-1.5 align-[2px] font-mono text-[11px] text-accent hover:bg-accent/30"
            >
              {seg.n}
            </button>
          );
        })}
      </p>

      <ol className="grid gap-2">
        {answer.citations.map((c, i) => {
          const active = current === c;
          return (
            <li key={i}>
              <button
                type="button"
                onClick={() => play(c)}
                className={`grid w-full gap-1 rounded border bg-panel p-3 text-left transition-colors hover:border-white/60 ${active ? "border-brand" : "border-edge"}`}
              >
                <span className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs">
                  <span className="text-accent">[{c.n}]</span>
                  <span className="text-ink">▶ {formatTime(c.time)}</span>
                  <span className="min-w-0 truncate font-sans text-muted">{c.title}</span>
                </span>
                <span className="text-sm text-ink-2">“{c.quote}”</span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="grid gap-2 border-t border-edge pt-4">
        <div className="flex items-center gap-3">
          <button
            type="button"
            aria-label={playing ? "Pause" : "Play"}
            disabled={!current}
            onClick={() => {
              const el = audio.current;
              if (!el || !current) return;
              if (el.paused) el.play().catch(() => setAudioError(true));
              else el.pause();
            }}
            className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-black disabled:opacity-30"
          >
            {playing ? (
              <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden="true"><rect width="4" height="14" fill="currentColor" /><rect x="8" width="4" height="14" fill="currentColor" /></svg>
            ) : (
              <svg width="12" height="14" viewBox="0 0 12 14" aria-hidden="true"><path d="M0 0 12 7 0 14z" fill="currentColor" /></svg>
            )}
          </button>
          <input
            type="range"
            aria-label="Position in episode"
            min={0}
            max={duration || 1}
            step={1}
            value={position}
            disabled={!current}
            onChange={(e) => {
              if (audio.current) audio.current.currentTime = Number(e.target.value);
            }}
            className="h-1 min-w-0 flex-1 cursor-pointer accent-accent disabled:opacity-30"
          />
          <span className="shrink-0 font-mono text-xs tabular-nums text-muted">
            {formatTime(position)}{duration ? ` / ${formatTime(duration)}` : ""}
          </span>
        </div>
        <p className="text-xs text-muted">
          {audioError
            ? "The publisher's audio couldn't be loaded. Try again, or open the episode in your podcast app."
            : current
              ? `Playing [${current.n}] from ${formatTime(current.time)} · ${current.title}`
              : "Click a citation to hear the original audio at that moment."}
        </p>
        <audio
          ref={audio}
          preload="none"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onTimeUpdate={(e) => setPosition(e.currentTarget.currentTime)}
          onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
          onError={() => current && setAudioError(true)}
        />
      </div>
    </div>
  );
}
