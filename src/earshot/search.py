"""Hybrid search: keyword (Postgres full-text) + semantic (pgvector) → RRF → optional rerank.

  keyword  finds exact words (names, jargon like "AGENTS.md"); misses paraphrases
  vector   finds meaning ("models getting worse" ≈ "performance degradation"); can miss rare terms
  RRF      merges the two ranked lists by rank position, 1/(k + rank), so their scores never
           need to be on the same scale (they aren't)
  rerank   a cross-encoder reads (question, passage) pairs together: more accurate, but
           too slow for everything, so it only reorders the fused shortlist
"""

from dataclasses import dataclass

import psycopg

from earshot.embed import Embedder, to_pgvector

RRF_K = 60
CANDIDATES = 50      # per leg, before fusion
RERANK_TOP = 20      # how many fused results the cross-encoder re-reads
# Chosen by eval (evals/results/retrieval.json, 45 questions): R@1 0.80 / MRR 0.85 at 1.35 s
# p50, vs MiniLM-L-12 0.82 / 0.86 at 2.9 s (one question apart: noise) and MiniLM-L-6
# 0.78 / 0.83 at 1.8 s. Near-best quality at the lowest latency; 130 MB.
RERANK_MODEL = "jinaai/jina-reranker-v1-tiny-en"
RERANK_BATCH = 4
MODES = ("keyword", "vector", "hybrid", "hybrid+rerank")


@dataclass(frozen=True)
class Hit:
    passage_id: int
    episode_id: int
    title: str
    start: float
    end: float
    text: str
    score: float


def keyword_ids(conn: psycopg.Connection, query: str, limit: int = CANDIDATES) -> list[int]:
    """Full-text search with OR semantics. websearch_to_tsquery would AND every word,
    and a natural-language question rarely has all its words in one passage."""
    rows = conn.execute(
        """
        WITH q AS (
            SELECT NULLIF(replace(plainto_tsquery('english', %s)::text, '&', '|'), '')::tsquery AS query
        )
        SELECT p.id FROM passages p, q
        WHERE q.query IS NOT NULL AND p.tsv @@ q.query
        ORDER BY ts_rank_cd(p.tsv, q.query) DESC, p.id
        LIMIT %s
        """,
        (query, limit),
    ).fetchall()
    return [r[0] for r in rows]


def vector_ids(conn: psycopg.Connection, query_vector: list[float], limit: int = CANDIDATES) -> list[int]:
    """Nearest passages by cosine distance (pgvector's <=> operator, HNSW index)."""
    rows = conn.execute(
        """
        SELECT id FROM passages WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector, id
        LIMIT %s
        """,
        (to_pgvector(query_vector), limit),
    ).fetchall()
    return [r[0] for r in rows]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: score(d) = sum over lists of 1 / (k + rank). Best first."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, pid in enumerate(ranking, start=1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


class Reranker:
    def __init__(self, model_name: str = RERANK_MODEL, model=None):
        self.model_name = model_name
        self._model = model

    @property
    def model(self):
        if self._model is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._model = TextCrossEncoder(self.model_name)
        return self._model

    def scores(self, query: str, texts: list[str]) -> list[float]:
        # Small batches: ONNX Runtime's memory arena grows to the largest batch and never
        # shrinks. Measured: batch 64 → RSS 516 MB after 7 reranks; batch 4 → 396 MB.
        return [float(s) for s in self.model.rerank(query, texts, batch_size=RERANK_BATCH)]


def _fetch(conn: psycopg.Connection, ranked: list[tuple[int, float]]) -> list[Hit]:
    if not ranked:
        return []
    ids = [pid for pid, _ in ranked]
    rows = {
        r[0]: r for r in conn.execute(
            """
            SELECT p.id, p.episode_id, e.title, p.start_s, p.end_s, p.text
            FROM passages p JOIN episodes e ON e.id = p.episode_id WHERE p.id = ANY(%s)
            """,
            (ids,),
        )
    }
    return [Hit(*rows[pid], score=score) for pid, score in ranked if pid in rows]


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    embedder: Embedder,
    mode: str = "hybrid+rerank",
    k: int = 10,
    reranker: Reranker | None = None,
) -> list[Hit]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if mode == "keyword":
        ranked = [(pid, 1.0 / (RRF_K + r)) for r, pid in enumerate(keyword_ids(conn, query), 1)]
    elif mode == "vector":
        ranked = [(pid, 1.0 / (RRF_K + r)) for r, pid in enumerate(vector_ids(conn, embedder.query(query)), 1)]
    else:
        ranked = rrf([keyword_ids(conn, query), vector_ids(conn, embedder.query(query))])

    if mode == "hybrid+rerank":
        shortlist = _fetch(conn, ranked[:RERANK_TOP])
        scores = (reranker or Reranker()).scores(query, [h.text for h in shortlist])
        return sorted(
            (Hit(**{**h.__dict__, "score": s}) for h, s in zip(shortlist, scores)),
            key=lambda h: -h.score,
        )[:k]
    return _fetch(conn, ranked[:k])
