"""Indexing and hybrid search against the test DB (needs the pgvector image).
A fake hashing embedder and a word-overlap reranker keep this fast and deterministic."""

import hashlib
import math

import pytest
from psycopg.types.json import Jsonb

from earshot.embed import EMBED_DIM, embed_pending
from earshot.index import build_passages, episodes_to_index, index_new_episodes
from earshot.ingest import TRANSCRIBE
from earshot.queue import enqueue
from earshot.search import Hit, keyword_ids, rrf, search


class HashEmbedder:
    """Bag-of-words 'embedding': each word adds weight to a fixed dimension. Texts that
    share words end up close: a deterministic stand-in for semantic similarity."""

    model_name = "fake-hash-v1"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * EMBED_DIM
        for word in text.lower().split():
            word = word.strip(".,?!")
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def passages(self, texts):
        return [self._vec(t) for t in texts]

    def query(self, text):
        return self._vec(text)


class OverlapReranker:
    def scores(self, query, texts):
        q = set(query.lower().split())
        return [float(len(q & set(t.lower().split()))) for t in texts]


def add_transcript(db, make_episode, sentences: list[str], done: bool = True) -> int:
    """An episode whose transcript is `sentences`, one per 60 s, as one stored chunk."""
    episode_id = make_episode()
    words = []
    for i, sentence in enumerate(sentences):
        for j, w in enumerate(sentence.split()):
            words.append({"text": w, "start": i * 60 + j * 0.4, "end": i * 60 + j * 0.4 + 0.3})
    db.execute(
        """INSERT INTO chunks (episode_id, idx, start_s, end_s, text, words, model, audio_sha256)
           VALUES (%s, 0, 0, %s, %s, %s, 'test', 'x')""",
        (episode_id, len(sentences) * 60.0, " ".join(sentences), Jsonb(words)),
    )
    job_id = enqueue(db, episode_id, TRANSCRIBE)
    if done:
        db.execute("UPDATE jobs SET status = 'done' WHERE id = %s", (job_id,))
    return episode_id


TALK = [
    "today we discuss vector databases and approximate nearest neighbour search",
    "the guest explains why agents need long term memory to plan tasks",
    "finally we cover AGENTS.md files for configuring coding assistants",
]


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
