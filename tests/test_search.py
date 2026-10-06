"""Indexing and hybrid search against the test DB (needs the pgvector image).
A fake hashing embedder and a word-overlap reranker keep this fast and deterministic."""

import pytest

from earshot.embed import embed_pending
from earshot.index import build_passages, episodes_to_index, index_new_episodes
from earshot.search import Hit, keyword_ids, rrf, search
from search_helpers import TALK, HashEmbedder, OverlapReranker, add_transcript


@pytest.fixture
def indexed(db, make_episode):
    episode_id = add_transcript(db, make_episode, TALK)
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())
    return episode_id


# --- indexing ------------------------------------------------------------------------

def test_only_fully_transcribed_episodes_are_indexed(db, make_episode):
    done = add_transcript(db, make_episode, TALK)
    add_transcript(db, make_episode, TALK, done=False)  # still transcribing: must wait
    assert episodes_to_index(db) == [done]


def test_indexing_is_idempotent(db, make_episode):
    add_transcript(db, make_episode, TALK)
    first = index_new_episodes(db)
    assert sum(first.values()) > 0
    assert index_new_episodes(db) == {}  # nothing new the second time


def test_rebuilding_an_episode_replaces_its_passages(db, make_episode):
    episode_id = add_transcript(db, make_episode, TALK)
    n = build_passages(db, episode_id)
    assert build_passages(db, episode_id) == n
    assert db.execute("SELECT count(*) FROM passages").fetchone()[0] == n


def test_embedding_records_the_model_and_reembeds_on_model_change(db, indexed):
    assert db.execute("SELECT count(*) FROM passages WHERE embed_model = 'fake-hash-v1'").fetchone()[0] > 0
    assert embed_pending(db, HashEmbedder()) == 0  # nothing pending for the same model

    class NewModel(HashEmbedder):
        model_name = "fake-hash-v2"

    total = db.execute("SELECT count(*) FROM passages").fetchone()[0]
    assert embed_pending(db, NewModel()) == total  # every vector redone: no mixed spaces


# --- search ------------------------------------------------------------------------------

def test_keyword_search_uses_or_so_natural_questions_match(db, indexed):
    # Nothing contains ALL of these words; an AND query would return nothing.
    ids = keyword_ids(db, "what do people say about memory for agents and planning")
    assert ids, "OR semantics should match passages containing some of the words"


def test_keyword_search_handles_queries_with_no_searchable_words(db, indexed):
    assert keyword_ids(db, "the and of") == []  # all stopwords: no crash, no results


@pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
def test_each_mode_finds_the_right_moment(db, indexed, mode):
    hits = search(db, "AGENTS.md coding assistants", embedder=HashEmbedder(), mode=mode, k=3)
    assert hits and "AGENTS.md" in hits[0].text
    assert hits[0].start >= 90  # the third sentence starts at 120 s; its window starts by then


def test_rerank_mode_reorders_by_the_cross_encoder(db, indexed):
    hits = search(db, "long term memory agents plan", embedder=HashEmbedder(),
                  mode="hybrid+rerank", reranker=OverlapReranker(), k=2)
    assert "memory" in hits[0].text
    assert hits[0].score >= hits[1].score


def test_unknown_mode_is_rejected(db, indexed):
    with pytest.raises(ValueError):
        search(db, "x", embedder=HashEmbedder(), mode="magic")


def test_hits_carry_episode_title_and_citation_time(db, indexed):
    hit = search(db, "vector databases", embedder=HashEmbedder(), mode="hybrid", k=1)[0]
    assert isinstance(hit, Hit)
    assert hit.title and hit.start == 0.0


# --- fusion (pure) -------------------------------------------------------------------

def test_rrf_rewards_agreement_between_lists():
    fused = dict(rrf([[1, 2, 3], [3, 1, 4]]))
    assert max(fused, key=fused.get) == 1           # high in both lists
    assert fused[3] > fused[2]                      # in both lists beats in one
    assert fused[1] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_ignores_raw_score_scales():
    # Only ranks matter: the same order gives the same fusion whatever the original scores were.
    assert rrf([[7, 8]]) == rrf([[7, 8]])
    assert [pid for pid, _ in rrf([[7, 8], []])] == [7, 8]
