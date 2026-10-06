"""Answer eval: does the full pipeline answer, and do its verified citations point at the
real answer?

Uses the retrieval golden set: every question was written from a passage, so it has an
answer and a known answer time. A refusal is therefore a miss. Citation accuracy catches
what the string-check critic can't: a quote that is verbatim but points to the wrong moment.
"""

import statistics

import psycopg

from earshot.answer import answer
from earshot.transcribe import RateLimited

WINDOWS = (15, 60)


def evaluate_answers(conn: psycopg.Connection, golden: list[dict], *, embedder, reranker, llm=None,
                     limit: int | None = None) -> dict:
    guid_to_episode = {(f, g): i for i, f, g in conn.execute("SELECT id, feed_url, guid FROM episodes")}
    kwargs = {"embedder": embedder, "reranker": reranker}
    if llm is not None:
        kwargs["llm"] = llm
    rows = []
    stopped_early = None
    for g in golden[:limit] if limit else golden:
        episode_id = guid_to_episode.get((g["feed_url"], g["guid"]))
        if episode_id is None:
            continue
        try:
            a = answer(conn, g["question"], **kwargs)
        except RateLimited as e:
            # e.g. the daily token budget is spent: keep what we have instead of losing the run
            stopped_early = f"rate limited after {len(rows)} questions (retry after {e.retry_after:.0f}s)"
            break
        offsets = [abs(c.time - g["answer_time"]) for c in a.citations if c.episode_id == episode_id]
        best = min(offsets) if offsets else None
        rows.append({
            "question": g["question"],
            "answered": a.found,
            "best_offset_s": round(best, 1) if best is not None else None,
            "right_episode": bool(offsets),
            "citations": len(a.citations),
            "rejected": len(a.rejected),
            "rejected_reasons": [r["reason"] for r in a.rejected],
            "removed_sentences": a.removed_sentences,
            "repaired": a.repaired,
            "tokens": a.tokens,
            "seconds": sum(a.seconds.values()),
        })

    n = len(rows) or 1
    proposed = sum(r["citations"] + r["rejected"] for r in rows)
    reasons: dict[str, int] = {}
    for r in rows:
        for reason in r["rejected_reasons"]:
            reasons[reason] = reasons.get(reason, 0) + 1
    report = {
        "questions": len(rows),
        "stopped_early": stopped_early,
        "repaired_rate": round(sum(r["repaired"] for r in rows) / n, 3),
        "answered_rate": round(sum(r["answered"] for r in rows) / n, 3),
        "right_episode_rate": round(sum(r["right_episode"] for r in rows) / n, 3),
        **{f"citation_within_{w}s": round(sum(1 for r in rows if r["best_offset_s"] is not None
                                                 and r["best_offset_s"] <= w) / n, 3) for w in WINDOWS},
        "citations_rejected_rate": round(sum(r["rejected"] for r in rows) / proposed, 3) if proposed else 0.0,
        "rejected_reasons": reasons,
        "answers_with_removed_sentences": sum(1 for r in rows if r["removed_sentences"]),
        "tokens_mean": round(statistics.mean(r["tokens"] for r in rows)) if rows else 0,
        "seconds_p50": round(statistics.median(r["seconds"] for r in rows), 2) if rows else 0,
    }
    return {"summary": report, "rows": rows}
