import type { Metadata } from "next";

export const metadata: Metadata = { title: "Accuracy" };

// Numbers copied from the README's measured results (evals/results/ in the repo).
const ASR = {
  head: ["Model", "Word error rate", "Words timestamped inside their true utterance", "Worst offset"],
  rows: [
    ["whisper-large-v3", "2.65%", "99.1%", "1.35 s"],
    ["whisper-large-v3-turbo (used)", "2.65%", "99.5%", "0.55 s"],
  ],
};

const RETRIEVAL = {
  head: ["Search mode", "Recall@1", "Recall@5", "MRR", "p50 latency"],
  rows: [
    ["Keyword (Postgres full-text)", "0.29", "0.53", "0.38", "31 ms"],
    ["Vector (bge-small + pgvector)", "0.58", "0.80", "0.67", "41 ms"],
    ["Hybrid, keyword + vector (live site)", "0.51", "0.76", "0.60", "69 ms"],
    ["Hybrid + reranker", "0.80", "0.93", "0.85", "1.35 s"],
  ],
};

const ANSWERS = {
  head: ["Full pipeline, with reranker", "No repair pass", "With repair pass"],
  rows: [
    ["Answered (all 45 are answerable)", "77.8%", "93.3%"],
    ["Verified citation within ±15 s of the true answer", "62.2%", "80.0%"],
    ["Tokens per answer", "1,310", "1,842"],
  ],
};

const LIMITS = [
  {
    title: "The live site runs without the reranker",
    body: "The free server has a tenth of a CPU, where a reranker would take tens of seconds per question. Search therefore runs in hybrid mode: recall@5 of 0.76 instead of 0.93. The answer scores above were measured with the reranker, so the live site will miss some answers that the full system finds.",
  },
  {
    title: "A verified quote exists, but may not prove the claim",
    body: "The checker proves each quote appears word for word in the transcript at the cited time. It does not prove the quote supports the sentence it is attached to. That judgement still comes from the language model.",
  },
  {
    title: "Timestamps can drift when publishers change ads",
    body: "Times match the audio as it was downloaded. Some podcasts insert different ads for each listener, which can shift the rest of the episode by a few seconds.",
  },
  {
    title: "Small test sets",
    body: "45 questions over 9 episodes, written by a model and deliberately paraphrased. Recall is a lower bound because only one passage counts as correct per question.",
  },
];

function Table({ head, rows, highlight }: { head: string[]; rows: string[][]; highlight?: number }) {
  return (
    <div className="overflow-x-auto rounded-md border border-edge">
      <table className="w-full min-w-[34rem] text-left text-sm">
        <thead className="bg-raised text-xs uppercase tracking-wide text-muted">
          <tr>{head.map((h) => <th key={h} className="px-4 py-3 font-semibold">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-edge bg-panel">
          {rows.map((r, i) => (
            <tr key={r[0]} className={i === highlight ? "text-ink" : "text-ink-2"}>
              {r.map((c, j) => (
                <td key={j} className={`px-4 py-3 ${j ? "font-mono tabular-nums" : ""}`}>
                  {c}{j === 0 && i === highlight && <span className="ml-2 rounded-sm bg-accent/15 px-1.5 py-0.5 font-mono text-[11px] text-accent">LIVE</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({ title, intro, children }: { title: string; intro: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-4">
      <h2 className="font-display text-2xl font-bold">{title}</h2>
      <p className="max-w-[70ch] text-ink-2">{intro}</p>
      {children}
    </section>
  );
}

export default function AccuracyPage() {
  return (
    <main className="mx-auto grid max-w-4xl gap-14 px-4 pt-12 pb-20 sm:px-6">
      <div className="grid gap-3">
        <h1 className="font-display text-4xl font-bold">How accurate is Earshot?</h1>
        <p className="max-w-[70ch] text-lg text-ink-2">
          Each stage is measured on its own: transcription, search, and the final answers. These are the results,
          including where the live site falls short of them.
        </p>
      </div>

      <Section
        title="Transcription"
        intro="73 utterances from LibriSpeech, a standard read-speech test set, run through the full pipeline. Words are aligned by text first, then their timestamps are measured, so timing errors can't hide inside the word error rate. Measured 2026-10-05."
      >
        <Table {...ASR} highlight={1} />
      </Section>

      <Section
        title="Search"
        intro="45 paraphrased questions over 9 episodes (875 passages), each with a known answer time. Recall@5 is how often the right passage is in the top 5 results. Measured 2026-10-06 on a laptop CPU."
      >
        <Table {...RETRIEVAL} highlight={2} />
      </Section>

      <Section
        title="Answers"
        intro="The same 45 questions through search, the language model, the quote checker and one repair pass, where the model is told which quotes failed and tries again. Measured 2026-10-06."
      >
        <Table {...ANSWERS} />
      </Section>

      <section className="grid gap-4">
        <h2 className="font-display text-2xl font-bold">Known limits</h2>
        <div className="grid gap-px overflow-hidden rounded-md border border-edge bg-edge sm:grid-cols-2">
          {LIMITS.map((l) => (
            <div key={l.title} className="grid content-start gap-2 bg-panel p-5">
              <h3 className="font-display text-lg font-semibold">{l.title}</h3>
              <p className="text-sm text-muted">{l.body}</p>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
