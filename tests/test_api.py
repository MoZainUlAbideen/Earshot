"""API: endpoints, validation, per-visitor rate limit, daily budget guard, error mapping.
Real test DB; fake embedder/reranker/LLM injected through the app factory."""

import pytest
from fastapi.testclient import TestClient

from earshot import api
from earshot.api import TokenBucket, create_app
from earshot.embed import embed_pending
from earshot.index import index_new_episodes
from earshot.llm import ChatResult, LLMError
from earshot.transcribe import RateLimited
from search_helpers import TALK, HashEmbedder, OverlapReranker, add_transcript

GOOD = {"found": True, "answer": "Agents need memory [1].",
        "citations": [{"n": 1, "quote": "agents need long term memory to plan"}]}


def fake_llm(data=GOOD, error=None):
    def llm(messages, model, max_tokens):
        if error:
            raise error
        return ChatResult(data, 900, 100)
    return llm


@pytest.fixture
def corpus(db, make_episode):
    add_transcript(db, make_episode, TALK)
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())


def client_for(test_db_url, llm=None, limiter=None):
    app = create_app(embedder=HashEmbedder(), reranker=OverlapReranker(), llm=llm or fake_llm(),
                     database_url=test_db_url, rate_limiter=limiter, warm=False)
    return TestClient(app)


def test_health_and_episodes(corpus, test_db_url):
    with client_for(test_db_url) as c:
        assert c.get("/health").json()["status"] == "ok"
        episodes = c.get("/episodes").json()
        assert len(episodes) == 1 and episodes[0]["audio_url"]


def test_search_returns_short_snippets_only(corpus, test_db_url):
    with client_for(test_db_url) as c:
        hits = c.get("/search", params={"q": "vector databases"}).json()
        assert hits and all(len(h["snippet"]) <= api.SNIPPET_CHARS for h in hits)
        assert "text" not in hits[0]  # never the full passage


def test_ask_returns_verified_citations_and_records_usage(db, corpus, test_db_url):
    with client_for(test_db_url) as c:
        body = c.post("/ask", json={"question": "what do agents need"}).json()
    assert body["found"] is True
    assert body["citations"][0]["time"] == pytest.approx(61.6, abs=0.01)
    tokens, answers = db.execute("SELECT tokens, answers FROM usage_daily").fetchone()
    assert (tokens, answers) == (1000, 1)


@pytest.mark.parametrize("question", ["", "hi", "x" * 301])
def test_question_length_is_validated(corpus, test_db_url, question):
    with client_for(test_db_url) as c:
        assert c.post("/ask", json={"question": question}).status_code == 422


def test_visitor_rate_limit_returns_429_with_retry_after(corpus, test_db_url):
    with client_for(test_db_url, limiter=TokenBucket(capacity=2, per_hour=2)) as c:
        codes = [c.post("/ask", json={"question": "what do agents need"}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        retry = c.post("/ask", json={"question": "what do agents need"}).headers["Retry-After"]
        assert int(retry) > 0


def test_daily_budget_guard_refuses_before_the_cap(db, corpus, test_db_url):
    db.execute("INSERT INTO usage_daily (day, tokens) VALUES ((now() AT TIME ZONE 'utc')::date, %s)",
               (api.DAILY_TOKEN_BUDGET,))
    calls = []
    with client_for(test_db_url, llm=lambda m, model, max_tokens: calls.append(1)) as c:
        r = c.post("/ask", json={"question": "what do agents need"})
    assert r.status_code == 503 and "budget" in r.json()["detail"]
    assert calls == []  # the LLM was never called


def test_groq_rate_limit_maps_to_503_with_retry_after(corpus, test_db_url):
    with client_for(test_db_url, llm=fake_llm(error=RateLimited(30))) as c:
        r = c.post("/ask", json={"question": "what do agents need"})
    assert r.status_code == 503 and r.headers["Retry-After"] == "31"


def test_groq_error_maps_to_502_and_logs_the_cause(corpus, test_db_url, caplog):
    error = LLMError("Groq chat returned 503: over capacity", retryable=True)
    with client_for(test_db_url, llm=fake_llm(error=error)) as c, caplog.at_level("ERROR", logger="earshot.api"):
        r = c.post("/ask", json={"question": "what do agents need"})
    assert r.status_code == 502
    assert "over capacity" not in r.text                    # the visitor sees the generic message
    assert "Groq chat returned 503: over capacity" in caplog.text  # the operator sees the cause


def test_cors_allows_only_configured_origins(corpus, test_db_url, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://earshot.vercel.app")
    with client_for(test_db_url) as c:
        ok = c.options("/ask", headers={"Origin": "https://earshot.vercel.app", "Access-Control-Request-Method": "POST"})
        bad = c.options("/ask", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://earshot.vercel.app"
    assert "access-control-allow-origin" not in bad.headers


def test_cors_setting_tolerates_spaces_and_trailing_slashes(corpus, test_db_url, monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://earshot-ten-pi.vercel.app/ , http://localhost:3000")
    with client_for(test_db_url) as c:
        r = c.options("/ask", headers={"Origin": "https://earshot-ten-pi.vercel.app",
                                       "Access-Control-Request-Method": "POST"})
    assert r.headers.get("access-control-allow-origin") == "https://earshot-ten-pi.vercel.app"


def test_rerank_off_never_loads_or_calls_a_reranker(corpus, test_db_url):
    class ExplodingReranker:
        def scores(self, query, texts):
            raise AssertionError("reranker must not be used when RERANK is off")

    app = create_app(embedder=HashEmbedder(), reranker=ExplodingReranker(), llm=fake_llm(),
                     database_url=test_db_url, warm=True, rerank=False)
    with TestClient(app) as c:
        assert c.get("/health").json()["search_mode"] == "hybrid"
        assert c.get("/search", params={"q": "vector databases"}).status_code == 200
        assert c.post("/ask", json={"question": "what do agents need"}).json()["found"] is True


def test_rerank_flag_comes_from_the_environment(corpus, test_db_url, monkeypatch):
    monkeypatch.setenv("RERANK", "0")
    app = create_app(embedder=HashEmbedder(), reranker=OverlapReranker(), llm=fake_llm(),
                     database_url=test_db_url, warm=False)
    with TestClient(app) as c:
        assert c.get("/health").json()["search_mode"] == "hybrid"


def test_token_bucket_refills_over_time():
    now = [0.0]
    bucket = TokenBucket(capacity=1, per_hour=3600, clock=lambda: now[0])  # 1 token per second
    assert bucket.take("ip") == 0
    assert bucket.take("ip") > 0
    now[0] += 1.0
    assert bucket.take("ip") == 0


class FakeRequest:
    def __init__(self, forwarded):
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = type("C", (), {"host": "10.0.0.1"})()


def test_proxy_header_is_only_trusted_when_configured(monkeypatch):
    req = FakeRequest("6.6.6.6, 203.0.113.9")  # first entry is client-supplied
    assert api.client_key(req) == "10.0.0.1"
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert api.client_key(req) == "203.0.113.9"  # one trusted hop: the entry our proxy appended


@pytest.mark.parametrize("chain, hops, expected", [
    ("198.51.100.7, 10.1.2.3", 2, "198.51.100.7"),            # proxy appended client, then itself
    ("6.6.6.6, 198.51.100.7, 10.1.2.3", 2, "198.51.100.7"),   # a forged leading entry is ignored
    ("198.51.100.7", 2, "198.51.100.7"),                      # fewer entries than hops: leftmost
    # The real Render shape (measured via /whoami): client, Cloudflare, Render load balancer
    ("198.51.100.7, 172.71.151.203, 10.30.107.46", 3, "198.51.100.7"),
    ("6.6.6.6,198.51.100.7, 172.71.146.199, 10.26.159.23", 3, "198.51.100.7"),
])
def test_client_is_counted_from_the_right_past_trusted_hops(monkeypatch, chain, hops, expected):
    monkeypatch.setenv("TRUST_PROXY", "1")
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", str(hops))
    assert api.client_key(FakeRequest(chain)) == expected


def test_whoami_shows_the_caller_their_own_key(corpus, test_db_url):
    with client_for(test_db_url) as c:
        body = c.get("/whoami", headers={"X-Forwarded-For": "6.6.6.6"}).json()
        assert body["x_forwarded_for"] == "6.6.6.6"
        assert body["rate_limit_key"] == "testclient"  # TRUST_PROXY off: the forged header is ignored
        assert c.get("/").json()["service"] == "Earshot API"
