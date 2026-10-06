"""Retrieval eval: evidence location, golden generation (fake LLM), and metrics."""

import pytest

from earshot.embed import embed_pending
from earshot.evals.retrieval import evaluate, generate_golden, locate
from earshot.index import index_new_episodes
from earshot.llm import ChatResult
from earshot.transcribe import Word
from search_helpers import TALK, HashEmbedder, OverlapReranker, add_transcript


def words_of(text: str, start: float = 0.0) -> list[Word]:
    return [Word(w, start + i * 0.5, start + i * 0.5 + 0.4) for i, w in enumerate(text.split())]


def test_locate_finds_the_quote_time_inside_the_passage():
    words = words_of("we talk about agents. Later we talk about agents needing memory today", 100)
    # "talk about agents needing memory" starts at the 2nd occurrence of "talk"
    assert locate("talk about agents needing memory", words, 100, 200) == pytest.approx(103.0)


def test_locate_ignores_matches_outside_the_passage_window():
    words = words_of("talk about agents needing memory", 0) + words_of("talk about agents needing memory", 500)
    assert locate("Talk about agents, needing memory!", words, 490, 600) == pytest.approx(500.0)


def test_locate_rejects_unfindable_or_too_short_quotes():
    words = words_of("we talk about agents needing memory")
    assert locate("a paraphrase that is not in the text", words, 0, 100) is None
    assert locate("agents", words, 0, 100) is None  # too short to be a reliable anchor


class FakeLLM:
    """Asks about whatever the passage is about; skips nothing."""

    def __call__(self, messages, model):
        text = messages[1]["content"]
        evidence = " ".join(text.split()[2:9])
        return ChatResult({"skip": False, "question": f"what is said about {text.split()[3]}?",
                           "evidence": evidence}, 100, 20)


@pytest.fixture
def corpus(db, make_episode):
    add_transcript(db, make_episode, TALK)
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())


def test_golden_items_have_located_answer_times_and_no_transcript_text(db, corpus):
    golden, stats = generate_golden(db, n=3, llm=FakeLLM())
    assert golden and stats["sampled"] >= len(golden)
    for g in golden:
        assert set(g) == {"question", "feed_url", "guid", "answer_time", "generator"}  # no transcript text


def test_skips_and_unlocated_quotes_are_counted_not_kept(db, corpus):
    def skipper(messages, model):
        return ChatResult({"skip": True}, 50, 5)

    def misquoter(messages, model):
        return ChatResult({"question": "q?", "evidence": "words that never appear anywhere at all"}, 50, 5)

    assert generate_golden(db, n=3, llm=skipper)[0] == []
    golden, stats = generate_golden(db, n=3, llm=misquoter)
    assert golden == [] and stats["unlocated"] == stats["sampled"]


def test_evaluate_reports_recall_mrr_and_citation_offsets(db, corpus):
    guid, feed = db.execute("SELECT guid, feed_url FROM episodes").fetchone()
    # helper passages: 0-3.9 s, 60-64.7 s, 120-123.5 s; answer times must fall inside them
    golden = [
        {"question": "AGENTS.md files for configuring coding assistants", "feed_url": feed, "guid": guid, "answer_time": 121.0},
        {"question": "long term memory agents plan tasks", "feed_url": feed, "guid": guid, "answer_time": 61.0},
        {"question": "unknown show", "feed_url": "x", "guid": "nope", "answer_time": 1.0},
    ]
    report = evaluate(db, golden, HashEmbedder(), {"overlap": OverlapReranker()})
    assert report["questions"] == 2 and report["missing_episodes"] == 1
    hybrid = report["modes"]["hybrid"]
    assert hybrid["recall@10"] == 1.0
    assert 0 < hybrid["mrr@10"] <= 1.0
    assert set(report["modes"]) == {"keyword", "vector", "hybrid", "hybrid+rerank:overlap"}
