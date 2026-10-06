// Pure helpers for showing an answer: no React, no network, so they're easy to test.

export type Segment = { kind: "text"; text: string } | { kind: "cite"; n: number };

/** Split "Open models matter[2][3]." into text and citation markers.
 *  Same marker pattern the backend's critic checks: [digits]. */
export function splitCitations(answer: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of answer.matchAll(/\[(\d+)\]/g)) {
    if (m.index > last) out.push({ kind: "text", text: answer.slice(last, m.index) });
    out.push({ kind: "cite", n: Number(m[1]) });
    last = m.index + m[0].length;
  }
  if (last < answer.length) out.push({ kind: "text", text: answer.slice(last) });
  return out;
}

/** 328 -> "5:28", 3725 -> "1:02:05". */
export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
