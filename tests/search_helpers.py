"""Shared test helpers for search/eval tests: deterministic stand-ins for the ML models,
and a way to create a transcribed episode without audio."""

import hashlib
import math

from psycopg.types.json import Jsonb

from earshot.embed import EMBED_DIM
from earshot.ingest import TRANSCRIBE
from earshot.queue import enqueue


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
