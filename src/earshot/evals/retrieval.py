"""Retrieval eval: a golden question set with known answer times, scored per search mode.

Golden set: an LLM writes one *paraphrased* question per sampled passage, plus a verbatim
evidence quote. The quote is located in the episode's word timings, inside that passage
only, which gives the exact answer time. Unlocatable quotes are dropped, not guessed.
Paraphrasing matters: questions that copy the passage's words would give keyword search
an unfair edge (the same kind of bias as an anchored reference).

The committed golden file holds questions and (feed_url, guid, answer_time), no
transcript text.

A hit is correct when it's the right episode AND its passage contains the answer time.
"""

import gc
import json
import random
import statistics
import time
from pathlib import Path

import psycopg

from earshot.evals.wer import normalize
from earshot.llm import DEFAULT_CHAT_MODEL, chat_json
from earshot.transcripts import get_transcript
from earshot.search import RERANK_TOP, _fetch, keyword_ids, rrf, vector_ids

GOLDEN_PATH = Path("evals/golden/retrieval.jsonl")
RESULTS_PATH = Path("evals/results/retrieval.json")
CITE_TOLERANCE_SECONDS = 15

SYSTEM = """You write evaluation questions for a podcast search engine.
Given one passage from a podcast transcript, return JSON:
{"skip": false, "question": "...", "evidence": "..."}
Rules:
- The question must be something a curious listener would naturally type, answered by a
  specific claim, fact, opinion or explanation in THIS passage (not general knowledge).
- PARAPHRASE: do not copy phrases from the passage; use different words. At most one
  proper noun (person, company or product name) and only if needed.
- "evidence": copy 6 to 15 consecutive words VERBATIM from the passage that answer the question.
- If the passage is an ad, an intro/outro, small talk, or too vague to support a specific
  question, return {"skip": true}."""


def _tokens(words) -> list[tuple[str, float]]:
    return [(tok, w.start) for w in words for tok in normalize(w.text).split()]


def locate(evidence: str, words, start: float, end: float) -> float | None:
    """Time of the evidence quote's first word, searching only inside [start, end]."""
    needle = normalize(evidence).split()
    if len(needle) < 4:
        return None
    hay = [(t, s) for t, s in _tokens(words) if start - 1 <= s <= end + 1]
    toks = [t for t, _ in hay]
    for i in range(len(toks) - len(needle) + 1):
        if toks[i:i + len(needle)] == needle:
            return hay[i][1]
    return None


def generate_golden(conn: psycopg.Connection, n: int = 60, *, seed: int = 42, llm=chat_json,
                    model: str = DEFAULT_CHAT_MODEL) -> tuple[list[dict], dict]:
    """Sample passages evenly across episodes; ask the LLM for a question per passage."""
    rows = conn.execute(
        """SELECT p.episode_id, p.start_s, p.end_s, p.text, e.feed_url, e.guid
           FROM passages p JOIN episodes e ON e.id = p.episode_id ORDER BY p.episode_id, p.idx"""
    ).fetchall()
    by_episode: dict[int, list] = {}
    for r in rows:
        by_episode.setdefault(r[0], []).append(r)
    rng = random.Random(seed)
    per_episode = max(1, n // len(by_episode))
    sampled = [p for eps in by_episode.values() for p in rng.sample(eps, min(per_episode, len(eps)))]

    golden, stats, transcripts = [], {"sampled": len(sampled), "skipped": 0, "unlocated": 0, "tokens": 0}, {}
    for episode_id, start, end, text, feed_url, guid in sampled:
        result = llm([{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}], model=model)
        stats["tokens"] += result.prompt_tokens + result.completion_tokens
        data = result.data
        if data.get("skip") or not data.get("question") or not data.get("evidence"):
            stats["skipped"] += 1
            continue
        if episode_id not in transcripts:
            transcripts[episode_id] = get_transcript(conn, episode_id)
        answer_time = locate(data["evidence"], transcripts[episode_id], start, end)
        if answer_time is None:
            stats["unlocated"] += 1
            continue
        golden.append({"question": data["question"].strip(), "feed_url": feed_url, "guid": guid,
                       "answer_time": round(answer_time, 2), "generator": model})
    return golden, stats


def save_golden(golden: list[dict], path: Path = GOLDEN_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(g) + "\n" for g in golden), encoding="utf-8")


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_correct(hit, episode_id: int, t: float) -> bool:
    return hit.episode_id == episode_id and hit.start - 0.5 <= t <= hit.end + 0.5


def evaluate(conn: psycopg.Connection, golden: list[dict], embedder, rerankers: dict, k: int = 10) -> dict:
    """Score every mode on every question. Rerankers: {name: Reranker}."""
    guid_to_episode = {(f, g): i for i, f, g in conn.execute("SELECT id, feed_url, guid FROM episodes")}
    modes = ["keyword", "vector", "hybrid"] + [f"hybrid+rerank:{name}" for name in rerankers]
    ranks = {m: [] for m in modes}           # rank of first correct hit (None if not in top k)
    offsets = {m: [] for m in modes}         # answer_time - cited start, for correct top-1 hits
    latency = {m: [] for m in modes}
    missing = 0
    prepared = []                            # per question: what the rerankers need

    def record(mode, hits, episode_id, t, seconds):
        latency[mode].append(seconds)
        rank = next((i for i, h in enumerate(hits, 1) if _is_correct(h, episode_id, t)), None)
        ranks[mode].append(rank)
        if rank == 1:
            offsets[mode].append(t - hits[0].start)

    for g in golden:
        episode_id = guid_to_episode.get((g["feed_url"], g["guid"]))
        if episode_id is None:
            missing += 1
            continue
        t = g["answer_time"]

        t0 = time.perf_counter(); kw = keyword_ids(conn, g["question"]); t_kw = time.perf_counter() - t0
        t0 = time.perf_counter(); qvec = embedder.query(g["question"]); vec = vector_ids(conn, qvec)
        t_vec = time.perf_counter() - t0
        fused = rrf([kw, vec])
        prepared.append((g["question"], episode_id, t, _fetch(conn, fused[:RERANK_TOP]), t_kw + t_vec))
        for mode, (hits, seconds) in {
            "keyword": (_fetch(conn, [(p, 0.0) for p in kw[:k]]), t_kw),
            "vector": (_fetch(conn, [(p, 0.0) for p in vec[:k]]), t_vec),
            "hybrid": (_fetch(conn, fused[:k]), t_kw + t_vec),
        }.items():
            record(mode, hits, episode_id, t, seconds)

    # One reranker at a time over all questions, then unloaded: with <1 GB free RAM,
    # holding several cross-encoders at once risks the out-of-memory failure seen in M2.
    for name, reranker in rerankers.items():
        for question, episode_id, t, shortlist, base_seconds in prepared:
            t0 = time.perf_counter()
            scores = reranker.scores(question, [h.text for h in shortlist])
            order = [h for h, _ in sorted(zip(shortlist, scores), key=lambda x: -x[1])][:k]
            record(f"hybrid+rerank:{name}", order, episode_id, t, base_seconds + time.perf_counter() - t0)
        if hasattr(reranker, "_model"):
            reranker._model = None
        gc.collect()

    report = {"questions": len(golden) - missing, "missing_episodes": missing, "k": k, "modes": {}}
    for mode in modes:
        r = ranks[mode]
        n = len(r) or 1
        off = offsets[mode]
        report["modes"][mode] = {
            "recall@1": round(sum(x == 1 for x in r if x) / n, 3),
            "recall@5": round(sum(1 for x in r if x and x <= 5) / n, 3),
            "recall@10": round(sum(1 for x in r if x) / n, 3),
            "mrr@10": round(sum(1 / x for x in r if x) / n, 3),
            "top1_cite_offset_median_s": round(statistics.median(off), 1) if off else None,
            "top1_cite_within_15s": round(sum(o <= CITE_TOLERANCE_SECONDS for o in off) / n, 3),
            "latency_p50_ms": round(statistics.median(latency[mode]) * 1000) if latency[mode] else None,
        }
    return report
