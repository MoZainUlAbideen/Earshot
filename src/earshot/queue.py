"""Postgres-backed job queue: enqueue, claim, complete, fail, reclaim_stale.

Workers claim a job in one short statement and do the slow work outside any
transaction; the 'running' status (not a held lock) keeps other workers away.

A claim is a lease: if a worker dies, reclaim_stale() requeues its job after a
timeout. Every claim bumps `lease`, a counter that never decreases, which is the
fencing token: complete()/fail()/defer() only succeed for the claim that is still
current, so a slow worker whose job was reclaimed can't overwrite the new owner's
work. (`attempts` is the retry budget and can be refunded by defer(), so it can't
be the token.) Delivery is at-least-once, so job handlers must be idempotent.
"""

from dataclasses import dataclass
from datetime import timedelta

import psycopg

BACKOFF_BASE_SECONDS = 30  # retry delays: 30s, 60s, 120s, ...
STALE_AFTER = timedelta(minutes=30)  # a 'running' job older than this is presumed abandoned


@dataclass(frozen=True)
class Job:
    id: int
    episode_id: int
    kind: str
    attempts: int
    max_attempts: int
    lease: int  # fencing token for this claim


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
        SET status = 'running', attempts = attempts + 1, lease = lease + 1,
            locked_at = now(), locked_by = %(worker)s, updated_at = now()
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'queued' AND kind = %(kind)s AND run_after <= now()
            ORDER BY run_after, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, episode_id, kind, attempts, max_attempts, lease
        """,
        {"worker": worker_id, "kind": kind},
    ).fetchone()
    return Job(*row) if row else None


class LeaseLost(Exception):
    """This claim is no longer current: the job was reclaimed or isn't running.
    The worker should drop its result; another claim owns the job now."""


def complete(conn: psycopg.Connection, job: Job) -> None:
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'done', locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = %s AND lease = %s AND status = 'running'
        RETURNING id
        """,
        (job.id, job.lease),
    ).fetchone()
    if row is None:
        raise LeaseLost(f"job {job.id} lease {job.lease} is no longer current")


def fail(conn: psycopg.Connection, job: Job, error: str, *, permanent: bool = False) -> str:
    """Record a failure. Requeues with exponential backoff until max_attempts,
    then marks the job 'failed'. `permanent=True` (e.g. a 404 audio URL, where
    retrying can't help) fails it immediately. Returns the job's new status."""
    row = conn.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN %(permanent)s OR attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
            run_after = CASE WHEN %(permanent)s OR attempts >= max_attempts THEN run_after
                             ELSE now() + make_interval(secs => %(base)s * power(2, attempts - 1))
                        END,
            last_error = %(error)s, locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = %(id)s AND lease = %(lease)s AND status = 'running'
        RETURNING status
        """,
        {"base": BACKOFF_BASE_SECONDS, "error": error, "id": job.id,
         "lease": job.lease, "permanent": permanent},
    ).fetchone()
    if row is None:
        raise LeaseLost(f"job {job.id} lease {job.lease} is no longer current")
    return row[0]


def defer(conn: psycopg.Connection, job: Job, seconds: float, reason: str) -> None:
    """Put a job back without counting this attempt: for throttling (429) or a
    graceful shutdown, which aren't the job's fault. Runs again after `seconds`."""
    row = conn.execute(
        """
        UPDATE jobs
        SET status = 'queued', attempts = attempts - 1,
            run_after = now() + make_interval(secs => %(seconds)s),
            last_error = %(reason)s, locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE id = %(id)s AND lease = %(lease)s AND status = 'running'
        RETURNING id
        """,
        {"seconds": max(0.0, seconds), "reason": reason, "id": job.id, "lease": job.lease},
    ).fetchone()
    if row is None:
        raise LeaseLost(f"job {job.id} lease {job.lease} is no longer current")


def reclaim_stale(conn: psycopg.Connection, older_than: timedelta = STALE_AFTER) -> dict[str, int]:
    """Requeue jobs whose worker presumably died (running longer than `older_than`).

    Jobs that have used all their attempts are marked 'failed' instead, so a job
    that crashes every worker (a "poison pill") can't loop forever.
    Returns counts by new status, e.g. {'queued': 2, 'failed': 1}.
    """
    rows = conn.execute(
        """
        UPDATE jobs
        SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
            last_error = 'reclaimed: worker ' || coalesce(locked_by, '?')
                         || ' held the job since ' || locked_at::text,
            run_after = now(), locked_at = NULL, locked_by = NULL, updated_at = now()
        WHERE status = 'running' AND locked_at < now() - %s
        RETURNING status
        """,
        (older_than,),
    ).fetchall()
    counts: dict[str, int] = {}
    for (status,) in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts
