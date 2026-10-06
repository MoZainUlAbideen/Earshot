"""HTTP API (FastAPI): the web layer over search and answering.

  GET  /health      liveness + whether the DB and models are ready
  GET  /episodes    what's searchable
  GET  /search?q=   timestamped hits (short snippets only, never full transcripts)
  POST /ask         an answer with verified, timestamped citations

Cost protection has two layers:
  - per-visitor token bucket (in memory, best effort): fairness, so one visitor can't use
    up the day; resets on restart, which is fine because...
  - the daily budget guard (in Postgres, durable): refuses new questions before Groq's daily
    token cap is reached, instead of failing mid-answer.
Models load once at startup and are warmed, so the first visitor doesn't pay for loading.
"""

import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from earshot.answer import answer
from earshot.db import apply_schema, get_database_url
from earshot.llm import LLMError
from earshot.search import search
from earshot.transcribe import RateLimited

SNIPPET_CHARS = 300          # copyright: show short snippets, never whole transcripts
DAILY_TOKEN_BUDGET = int(os.environ.get("DAILY_TOKEN_BUDGET", "180000"))  # under Groq's 200K/day
EST_TOKENS_PER_ANSWER = 2500  # reserve before asking (measured mean 1,842; repair can add more)
ASKS_PER_HOUR = int(os.environ.get("ASKS_PER_HOUR", "10"))


# --- per-visitor rate limit ---------------------------------------------------------------

class TokenBucket:
    """Each key gets `capacity` tokens, refilled continuously at `per_hour` per hour."""

    def __init__(self, capacity: int, per_hour: float, clock=time.monotonic):
        self.capacity, self.rate, self.clock = capacity, per_hour / 3600.0, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """Take one token. Returns 0 if allowed, else seconds until a token is available."""
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / self.rate


def client_key(request: Request) -> str:
    """Visitor identity for rate limiting.

    Behind proxies (Render), the TCP peer is a proxy, so with TRUST_PROXY=1 we read
    X-Forwarded-For counting from the RIGHT: each of our TRUSTED_PROXY_HOPS proxies appends
    one entry, so the client is the entry just before them. Entries further left were
    sent by the client and can be forged, so they're never trusted. The hop count is
    measured with /whoami, not assumed."""
    if os.environ.get("TRUST_PROXY") == "1":
        entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
        if entries:
            return entries[max(0, len(entries) - hops)]
    return request.client.host if request.client else "unknown"


# --- daily budget guard -------------------------------------------------------------------

def tokens_used_today(conn) -> int:
    row = conn.execute("SELECT tokens FROM usage_daily WHERE day = %s", (datetime.now(UTC).date(),)).fetchone()
    return row[0] if row else 0


def record_usage(conn, tokens: int) -> None:
    conn.execute(
        """INSERT INTO usage_daily (day, tokens, answers) VALUES (%s, %s, 1)
           ON CONFLICT (day) DO UPDATE SET tokens = usage_daily.tokens + EXCLUDED.tokens,
                                           answers = usage_daily.answers + 1""",
        (datetime.now(UTC).date(), tokens),
    )


# --- request/response shapes ------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=300)


class CitationOut(BaseModel):
    n: int
    quote: str
    episode_id: int
    title: str
    audio_url: str
    time: float


class AskResponse(BaseModel):
    question: str
    answer: str
    found: bool
    citations: list[CitationOut]


# --- app --------------------------------------------------------------------------------------

def create_app(*, embedder=None, reranker=None, llm=None, database_url: str | None = None,
               rate_limiter: TokenBucket | None = None, warm: bool = True,
               rerank: bool | None = None) -> FastAPI:
    """App factory: tests inject fakes; production loads the real models.

    RERANK=0 turns the cross-encoder off (Render free has 0.1 CPU: reranking 20 passages
    would take tens of seconds). Search then uses hybrid retrieval: measured top-5 recall
    0.76 vs 0.93 with rerank. A config flag, not deleted code."""
    if rerank is None:
        rerank = os.environ.get("RERANK", "1") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        url = database_url or get_database_url()
        app.state.pool = ConnectionPool(url, min_size=1, max_size=4, kwargs={"autocommit": True},
                                        timeout=10, open=True)
        with app.state.pool.connection() as conn:
            apply_schema(conn)
        if embedder is None:
            from earshot.embed import Embedder
        app.state.embedder = embedder or Embedder()
        app.state.reranker = None
        if rerank:
            if reranker is None:
                from earshot.search import Reranker
            app.state.reranker = reranker or Reranker()
        if warm:  # pay model-loading cost at startup, not on the first visitor's request
            app.state.embedder.query("warm up")
            if app.state.reranker:
                app.state.reranker.scores("warm up", ["warm up"])
        yield
        app.state.pool.close()

    app = FastAPI(title="Earshot API", version="0.1.0", lifespan=lifespan)
    origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])
    limiter = rate_limiter or TokenBucket(capacity=ASKS_PER_HOUR, per_hour=ASKS_PER_HOUR)
    llm_kwargs = {"llm": llm} if llm is not None else {}

    @app.get("/health")
    def health(request: Request):
        with request.app.state.pool.connection() as conn:
            conn.execute("SELECT 1")
            passages = conn.execute("SELECT count(*) FROM passages").fetchone()[0]
        return {"status": "ok", "passages": passages,
                "search_mode": "hybrid+rerank" if request.app.state.reranker else "hybrid"}

    @app.get("/")
    def root():
        return {"service": "Earshot API", "docs": "/docs", "health": "/health"}

    @app.get("/whoami")
    def whoami(request: Request):
        """Shows the CALLER their own forwarding chain and the rate-limit key derived from it
        (like a "what's my IP" page). Used to measure the proxy hop count on the host."""
        return {"x_forwarded_for": request.headers.get("x-forwarded-for"),
                "peer": request.client.host if request.client else None,
                "rate_limit_key": client_key(request)}

    @app.get("/episodes")
    def episodes(request: Request):
        with request.app.state.pool.connection() as conn:
            rows = conn.execute(
                """SELECT e.id, e.title, e.published_at, e.duration_seconds, e.audio_url
                   FROM episodes e WHERE EXISTS (SELECT 1 FROM passages p WHERE p.episode_id = e.id)
                   ORDER BY e.published_at DESC NULLS LAST"""
            ).fetchall()
        return [{"id": r[0], "title": r[1], "published_at": r[2], "duration_seconds": r[3], "audio_url": r[4]}
                for r in rows]

    @app.get("/search")
    def search_endpoint(request: Request, q: str = Query(min_length=3, max_length=300),
                        k: int = Query(5, ge=1, le=10)):
        with request.app.state.pool.connection() as conn:
            reranker = request.app.state.reranker
            hits = search(conn, q, embedder=request.app.state.embedder, reranker=reranker, k=k,
                          mode="hybrid+rerank" if reranker else "hybrid")
        return [{"episode_id": h.episode_id, "title": h.title, "start": h.start,
                 "snippet": h.text[:SNIPPET_CHARS], "score": h.score} for h in hits]

    @app.post("/ask", response_model=AskResponse)
    def ask(body: AskRequest, request: Request):
        wait = limiter.take(client_key(request))
        if wait:
            return JSONResponse(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                                content={"detail": "Too many questions from you; please try again later."})
        with request.app.state.pool.connection() as conn:
            if tokens_used_today(conn) + EST_TOKENS_PER_ANSWER > DAILY_TOKEN_BUDGET:
                raise HTTPException(503, "Earshot has used today's free AI budget. Please come back tomorrow.")
            try:
                result = answer(conn, body.question, embedder=request.app.state.embedder,
                                reranker=request.app.state.reranker,
                                rerank=request.app.state.reranker is not None, **llm_kwargs)
            except RateLimited as e:
                return JSONResponse(status_code=503, headers={"Retry-After": str(int(e.retry_after) + 1)},
                                    content={"detail": "The AI service is busy; please try again shortly."})
            except LLMError:
                raise HTTPException(502, "The AI service returned an error; please try again.") from None
            record_usage(conn, result.tokens)
        return AskResponse(
            question=result.question, answer=result.text, found=result.found,
            citations=[CitationOut(**c.__dict__) for c in result.citations],
        )

    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn earshot.api:app_factory --factory`."""
    return create_app()
