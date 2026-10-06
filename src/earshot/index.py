"""Indexing: finished transcripts → passages (the rows search runs over).

Only episodes whose transcribe job is 'done' are indexed. A half-transcribed episode
has some chunks saved (checkpoints), and indexing it would leave holes that nothing
fills later. Rebuilding an episode's passages replaces them in one transaction, so
search never sees a half-written episode.
"""

import psycopg

from earshot.ingest import TRANSCRIBE
from earshot.passages import make_passages
from earshot.pipeline import get_transcript


def episodes_to_index(conn: psycopg.Connection) -> list[int]:
    """Fully transcribed episodes that have no passages yet."""
    rows = conn.execute(
        """
        SELECT j.episode_id FROM jobs j
        WHERE j.kind = %s AND j.status = 'done'
          AND NOT EXISTS (SELECT 1 FROM passages p WHERE p.episode_id = j.episode_id)
        ORDER BY j.episode_id
        """,
        (TRANSCRIBE,),
    ).fetchall()
    return [r[0] for r in rows]


def build_passages(conn: psycopg.Connection, episode_id: int) -> int:
    """(Re)build one episode's passages atomically. Returns how many were written."""
    passages = make_passages(get_transcript(conn, episode_id))
    with conn.transaction():
        conn.execute("DELETE FROM passages WHERE episode_id = %s", (episode_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO passages (episode_id, idx, start_s, end_s, text) VALUES (%s, %s, %s, %s, %s)",
                [(episode_id, p.idx, p.start, p.end, p.text) for p in passages],
            )
    return len(passages)


def index_new_episodes(conn: psycopg.Connection) -> dict[int, int]:
    """Build passages for every newly finished episode. Idempotent."""
    return {episode_id: build_passages(conn, episode_id) for episode_id in episodes_to_index(conn)}
