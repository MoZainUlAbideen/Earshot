import Link from "next/link";
import { AnswerView } from "@/components/answer-view";
import { SoundRibbon } from "@/components/sound-ribbon";
import type { Answer } from "@/lib/api";
import example from "@/data/example-answer.json";

// Recorded from the real pipeline over the live data on 2026-10-06, not written by hand.
const exampleAnswer = example as Answer;

const EXAMPLE_QUESTIONS = [
  // One per indexed episode topic, so each is answerable.
  "What is an AGENTS.md file used for?",
  "How do you get discovered in AI search?",
  "What are computer-use agents?",
  "What happened to the Fox News crew in Ukraine?",
];

const FEATURES = [
  { tag: "VERIFIED", title: "Every quote checked", body: "Plain code confirms each quote appears word for word in the transcript before you see it." },
  { tag: "0:00:01", title: "Jumps to the exact second", body: "Click a citation and the original audio plays from the moment the words were said." },
  { tag: "HYBRID", title: "Meaning and keywords", body: "Finds the right moment even when your question uses different words than the speaker." },
  { tag: "NOT FOUND", title: "Says when it can't find it", body: "If no claim survives the check, Earshot tells you instead of guessing." },
];

const STEPS = [
  { title: "Ask in plain words", body: "Type a question about anything discussed in the indexed episodes." },
  { title: "Search 875 moments", body: "Keyword and meaning search run together over transcripts of 9 episodes, and the best passages go to the model." },
  { title: "Hear the proof", body: "The answer cites exact quotes. Each one plays the publisher's audio from that second." },
];

const NUMBERS = [
  { value: "2.65%", label: "word error rate on a standard speech test set" },
  { value: "0.55 s", label: "worst word-timestamp error measured" },
  { value: "100%", label: "of shown quotes matched word for word in the transcript" },
];

export default function Home() {
  return (
    <main>
      {/* Full-bleed hero: the ribbon runs to the screen edge, the text stays in the column. */}
      <section className="relative overflow-hidden border-b border-edge">
        <SoundRibbon />
        <div className="relative mx-auto max-w-6xl px-4 sm:px-6">
          <div className="grid max-w-3xl gap-5 pt-20 pb-24 sm:pt-28 sm:pb-32">
            <p className="flex items-center gap-2 font-mono text-xs uppercase tracking-wider text-muted">
              <span className="size-1.5 rounded-full bg-brand" aria-hidden="true" />
              9 episodes · 875 searchable moments · every quote checked
            </p>
            <h1 className="font-display text-4xl leading-[1.04] font-bold tracking-tight sm:text-6xl">
              Ask any podcast a question. <span className="text-brand">Hear the exact moment</span> it was answered.
            </h1>
            <p className="max-w-[58ch] text-lg text-ink-2">
              Earshot searches podcast transcripts, writes a short answer, and links every claim to the second it
              was said in the original audio.
            </p>
            <div className="flex flex-wrap gap-3 pt-1">
              <Link href="/ask" className="rounded bg-brand px-5 py-3 font-semibold text-white hover:bg-brand-hi">
                Ask a question
              </Link>
              <Link
                href="/accuracy"
                className="rounded border border-white/35 bg-black/40 px-5 py-3 font-semibold backdrop-blur hover:border-white"
              >
                How we measure accuracy
              </Link>
            </div>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-6xl px-4 pt-12 sm:px-6">
        <section className="grid gap-px overflow-hidden rounded-md border border-edge bg-edge sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <div key={f.title} className="grid content-start gap-1.5 bg-panel p-5 transition-colors hover:bg-raised">
              <span className="font-mono text-xs text-muted">{f.tag}</span>
              <h3 className="font-display text-lg font-semibold">{f.title}</h3>
              <p className="text-sm text-muted">{f.body}</p>
            </div>
          ))}
        </section>

        <section id="how" className="scroll-mt-20 py-16">
          <h2 className="font-display text-3xl font-bold">How it works</h2>
          <ol className="mt-6 grid gap-6 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.title} className="grid content-start gap-2 border-t border-edge pt-4">
                <span className="font-mono text-xs text-muted">Step {i + 1}</span>
                <h3 className="font-display text-xl font-semibold">{s.title}</h3>
                <p className="text-ink-2">{s.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="grid gap-8 pb-16 lg:grid-cols-[1fr_1.4fr]">
          <div className="grid content-start gap-4">
            <h2 className="font-display text-3xl font-bold">A real answer</h2>
            <p className="text-ink-2">
              Asked of the live index: <span className="text-ink">“{exampleAnswer.question}”</span> Click a quote to
              hear it in the original episode.
            </p>
            <p className="text-sm text-muted">Try another question:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLE_QUESTIONS.map((q) => (
                <Link
                  key={q}
                  href={{ pathname: "/ask", query: { q } }}
                  className="rounded border border-edge bg-panel px-3 py-2 text-sm text-ink-2 transition hover:-translate-y-px hover:border-white/60 hover:text-ink"
                >
                  {q}
                </Link>
              ))}
            </div>
          </div>
          <div className="relative min-w-0">
            {/* A soft red light spilling onto the black behind the card. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute -inset-10 rounded-full bg-[radial-gradient(closest-side,rgba(228,32,43,0.18),transparent)] blur-2xl"
            />
            <div className="relative">
              <AnswerView answer={exampleAnswer} />
            </div>
          </div>
        </section>

        <section className="grid gap-px overflow-hidden rounded-md border border-edge bg-edge sm:grid-cols-3">
          {NUMBERS.map((n) => (
            <div key={n.label} className="bg-panel p-5">
              <p className="font-display text-3xl font-bold tabular-nums">{n.value}</p>
              <p className="mt-1 text-sm text-muted">{n.label}</p>
            </div>
          ))}
        </section>
        <p className="pt-3 pb-16 text-sm text-muted">
          Measured on 2026-10-05 and 2026-10-06.{" "}
          <Link href="/accuracy" className="text-ink underline decoration-white/30 underline-offset-4 hover:decoration-white">
            See every test and its limits →
          </Link>
        </p>
      </div>
    </main>
  );
}
