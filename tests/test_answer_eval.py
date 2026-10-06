"""Answer eval metrics with a fake LLM that cites a chosen quote."""

import pytest

from earshot.embed import embed_pending
from earshot.evals.answers import evaluate_answers
from earshot.index import index_new_episodes
from earshot.llm import ChatResult
from search_helpers import TALK, HashEmbedder, OverlapReranker, add_transcript


@pytest.fixture
def corpus(db, make_episode):
    add_transcript(db, make_episode, TALK)
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())
    return db.execute("SELECT feed_url, guid FROM episodes").fetchone()


def citing(quote):
    def fake(messages, model, max_tokens):
        return ChatResult({"found": True, "answer": "Claim [1].", "citations": [{"n": 1, "quote": quote}]}, 800, 100)
    return fake


def test_citation_near_the_answer_counts_as_accurate(db, corpus):
    feed, guid = corpus
    golden = [{"question": "agents memory plan tasks", "feed_url": feed, "guid": guid, "answer_time": 62.0}]
    # quote starts at "agents" = 61.6 s -> 0.4 s from the answer
    report = evaluate_answers(db, golden, embedder=HashEmbedder(), reranker=OverlapReranker(),
                              llm=citing("agents need long term memory to plan"))["summary"]
    assert report["answered_rate"] == 1.0
    assert report["citation_within_15s"] == 1.0


def test_verbatim_but_wrong_moment_is_not_accurate(db, corpus):
    feed, guid = corpus
    golden = [{"question": "agents memory plan tasks", "feed_url": feed, "guid": guid, "answer_time": 125.0}]
    report = evaluate_answers(db, golden, embedder=HashEmbedder(), reranker=OverlapReranker(),
                              llm=citing("agents need long term memory to plan"))["summary"]
    assert report["answered_rate"] == 1.0          # it answered...
    assert report["citation_within_15s"] == 0.0    # ...but cited ~63 s away from the real answer
    assert report["citation_within_60s"] == 0.0


def test_daily_budget_exhaustion_keeps_partial_results(db, corpus):
    from earshot.transcribe import RateLimited

    feed, guid = corpus
    golden = [{"question": f"agents memory {i}", "feed_url": feed, "guid": guid, "answer_time": 62.0} for i in range(3)]
    calls = []

    def runs_out(messages, model, max_tokens):
        calls.append(1)
        if len(calls) > 1:
            raise RateLimited(3600)
        return ChatResult({"found": True, "answer": "Claim [1].",
                           "citations": [{"n": 1, "quote": "agents need long term memory to plan"}]}, 800, 100)

    report = evaluate_answers(db, golden, embedder=HashEmbedder(), reranker=OverlapReranker(), llm=runs_out)["summary"]
    assert report["questions"] == 1
    assert "rate limited after 1 questions" in report["stopped_early"]


def test_rejections_and_refusals_are_counted(db, corpus):
    feed, guid = corpus
    golden = [{"question": "agents memory plan", "feed_url": feed, "guid": guid, "answer_time": 62.0}]
    report = evaluate_answers(db, golden, embedder=HashEmbedder(), reranker=OverlapReranker(),
                              llm=citing("a quote nobody ever said on this show"))["summary"]
    assert report["answered_rate"] == 0.0
    assert report["citations_rejected_rate"] == 1.0
    assert report["rejected_reasons"] == {"quote not found verbatim in passage": 1}
