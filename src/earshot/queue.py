"""Postgres-backed job queue: enqueue, claim, complete, fail.

Workers claim a job in one short statement and do the slow work outside any
transaction; the 'running' status (not a held lock) keeps other workers away.
"""

from dataclasses import dataclass

import psycopg

BACKOFF_BASE_SECONDS = 30  # retry delays: 30s, 60s, 120s, ...


@dataclass(frozen=True)
class Job:
    id: int
    episode_id: int
    kind: str
    attempts: int
    max_attempts: int


def enqueue(conn: psycopg.Connection, episode_id: int, kind: str) -> int | None:
    """Queue work for an episode. Returns the new job id, or None if it was already queued."""
    row = conn.execute(
        """
        INSERT INTO jobs (episode_id, kind) VALUES (%s, %s)
        ON CONFLICT (episode_id, kind) DO NOTHING
        RETURNING id
        """,
        (episode_id, kind),
    ).fetchone()
    return row[0] if row else None


def claim(conn: psycopg.Connection, worker_id: str, kind: str) -> Job | None:
    """Atomically take the oldest ready job of this kind, or None if there is none.

    FOR UPDATE SKIP LOCKED: concurrent workers skip rows another worker is
    claiming instead of waiting for them, so no job is ever handed out twice.
    """
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'running', attempts = attempts + 1,
            locked_at = now(), locked_by = %(worker)s, updated_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'queued' AND kind = %(kind)s AND run_after <= now()
            ORDER BY run_after, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, episode_id, kind, attempts, max_attempts
        """,
        {"worker": worker_id, "kind": kind},
    ).fetchone()
    return Job(*row) if row else None


def complete(conn: psycopg.Connection, job_id: int) -> None:
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'done', locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = %s AND status = 'running'
        RETURNING id
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"job {job_id} is not running")


def fail(conn: psycopg.Connection, job_id: int, error: str) -> str:
    """Record a failure. Requeues with exponential backoff until max_attempts,
    then marks the job 'failed'. Returns the job's new status."""
    row = conn.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
            run_after = CASE WHEN attempts >= max_attempts THEN run_after
                             ELSE now() + make_interval(secs => %(base)s * power(2, attempts - 1))
                        END,
            last_error = %(error)s, locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = %(id)s AND status = 'running'
        RETURNING status
        """,
        {"base": BACKOFF_BASE_SECONDS, "error": error, "id": job_id},
    ).fetchone()
    if row is None:
        raise ValueError(f"job {job_id} is not running")
    return row[0]
