"""Job queue behaviour: idempotent enqueue, exclusive claiming, retries with backoff,
stale-job recovery, and fencing against out-of-date workers."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from earshot.db import connect
from earshot.queue import (
    BACKOFF_BASE_SECONDS,
    LeaseLost,
    claim,
    complete,
    enqueue,
    fail,
    reclaim_stale,
)

KIND = "transcribe"


def job_row(conn, job_id):
    return conn.execute(
        """
        SELECT status, attempts, locked_by, locked_at, last_error,
               run_after - updated_at AS delay
        FROM jobs WHERE id = %s
        """,
        (job_id,),
    ).fetchone()


def make_ready(conn, job_id):
    """Skip the backoff wait so the job can be claimed again right away."""
    conn.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (job_id,))


# --- enqueue ---------------------------------------------------------------

def test_enqueue_is_idempotent(db, make_episode):
    episode_id = make_episode()
    first = enqueue(db, episode_id, KIND)
    second = enqueue(db, episode_id, KIND)
    assert isinstance(first, int)
    assert second is None
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


# --- claim -----------------------------------------------------------------

def test_claim_marks_job_running(db, make_episode):
    job_id = enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-1", KIND)
    assert job.id == job_id
    assert job.attempts == 1
    status, attempts, locked_by, locked_at, _, _ = job_row(db, job_id)
    assert (status, attempts, locked_by) == ("running", 1, "worker-1")
    assert locked_at is not None


def test_claim_returns_none_when_queue_is_empty(db):
    assert claim(db, "worker-1", KIND) is None


def test_claim_ignores_other_kinds(db, make_episode):
    enqueue(db, make_episode(), "enrich")
    assert claim(db, "worker-1", KIND) is None


def test_claim_waits_for_run_after(db, make_episode):
    job_id = enqueue(db, make_episode(), KIND)
    db.execute("UPDATE jobs SET run_after = now() + interval '1 hour' WHERE id = %s", (job_id,))
    assert claim(db, "worker-1", KIND) is None


def test_claim_takes_the_oldest_ready_job_first(db, make_episode):
    enqueue(db, make_episode(), KIND)
    older = enqueue(db, make_episode(), KIND)
    db.execute("UPDATE jobs SET run_after = now() - interval '1 hour' WHERE id = %s", (older,))
    assert claim(db, "worker-1", KIND).id == older


# --- concurrency: SKIP LOCKED ----------------------------------------------

def test_second_worker_skips_a_locked_job_instead_of_waiting(db, make_episode, test_db_url):
    first = enqueue(db, make_episode(), KIND)
    second = enqueue(db, make_episode(), KIND)
    # worker_a is NOT autocommit, so its claim stays uncommitted and keeps the row locked.
    with connect(test_db_url) as worker_a, connect(test_db_url, autocommit=True) as worker_b:
        worker_b.execute("SET statement_timeout = '2s'")  # if it blocks, fail instead of hang
        job_a = claim(worker_a, "worker-a", KIND)
        job_b = claim(worker_b, "worker-b", KIND)
        worker_a.rollback()
    assert job_a.id == first
    assert job_b.id == second


def test_only_job_locked_means_second_worker_gets_nothing(db, make_episode, test_db_url):
    enqueue(db, make_episode(), KIND)
    with connect(test_db_url) as worker_a, connect(test_db_url, autocommit=True) as worker_b:
        worker_b.execute("SET statement_timeout = '2s'")
        assert claim(worker_a, "worker-a", KIND) is not None
        assert claim(worker_b, "worker-b", KIND) is None
        worker_a.rollback()


def test_concurrent_workers_claim_every_job_exactly_once(db, make_episode, test_db_url):
    job_ids = {enqueue(db, make_episode(), KIND) for _ in range(40)}

    def drain(worker_id):
        claimed = []
        with connect(test_db_url, autocommit=True) as conn:
            while (job := claim(conn, worker_id, KIND)) is not None:
                claimed.append(job.id)
        return claimed

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(drain, [f"worker-{i}" for i in range(4)]))

    all_claimed = [job_id for claimed in results for job_id in claimed]
    assert len(all_claimed) == len(set(all_claimed)), "a job was claimed twice"
    assert set(all_claimed) == job_ids, "a job was never claimed"




def make_stale(conn, job_id):
    """Pretend the worker took this job an hour ago and then went silent."""
    conn.execute("UPDATE jobs SET locked_at = now() - interval '1 hour' WHERE id = %s", (job_id,))


# --- complete --------------------------------------------------------------

def test_complete_marks_job_done_and_unlocks_it(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-1", KIND)
    complete(db, job)
    status, _, locked_by, locked_at, _, _ = job_row(db, job.id)
    assert (status, locked_by, locked_at) == ("done", None, None)


def test_completing_twice_raises_lease_lost(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-1", KIND)
    complete(db, job)
    with pytest.raises(LeaseLost):
        complete(db, job)


# --- fail: retries and backoff ---------------------------------------------

def test_fail_requeues_with_backoff(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-1", KIND)
    assert fail(db, job, "Groq 429: rate limited") == "queued"
    status, _, locked_by, _, last_error, delay = job_row(db, job.id)
    assert (status, locked_by, last_error) == ("queued", None, "Groq 429: rate limited")
    assert delay == timedelta(seconds=BACKOFF_BASE_SECONDS)
    assert claim(db, "worker-1", KIND) is None  # still backing off


def test_backoff_doubles_on_each_retry(db, make_episode):
    job_id = enqueue(db, make_episode(), KIND)
    delays = []
    for _ in range(2):
        job = claim(db, "worker-1", KIND)
        fail(db, job, "boom")
        delays.append(job_row(db, job_id)[5])
        make_ready(db, job_id)
    assert delays == [timedelta(seconds=BACKOFF_BASE_SECONDS),
                      timedelta(seconds=BACKOFF_BASE_SECONDS * 2)]


def test_job_is_marked_failed_after_max_attempts(db, make_episode):
    job_id = enqueue(db, make_episode(), KIND)
    statuses = []
    for _ in range(3):  # max_attempts defaults to 3
        job = claim(db, "worker-1", KIND)
        statuses.append(fail(db, job, "boom"))
        make_ready(db, job_id)
    assert statuses == ["queued", "queued", "failed"]
    assert claim(db, "worker-1", KIND) is None  # failed jobs are never handed out again


def test_failing_a_finished_job_raises_lease_lost(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-1", KIND)
    complete(db, job)
    with pytest.raises(LeaseLost):
        fail(db, job, "boom")


# --- reclaim_stale: recovering from crashed workers ------------------------

def test_stale_job_is_requeued_and_can_be_claimed_again(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-crashed", KIND)
    make_stale(db, job.id)

    assert reclaim_stale(db) == {"queued": 1}
    status, _, locked_by, _, last_error, _ = job_row(db, job.id)
    assert (status, locked_by) == ("queued", None)
    assert "worker-crashed" in last_error

    retry = claim(db, "worker-2", KIND)
    assert (retry.id, retry.attempts) == (job.id, 2)  # the crashed attempt still counts


def test_fresh_running_job_is_left_alone(db, make_episode):
    enqueue(db, make_episode(), KIND)
    job = claim(db, "worker-busy", KIND)
    assert reclaim_stale(db) == {}
    assert job_row(db, job.id)[0] == "running"


def test_queued_and_done_jobs_are_never_reclaimed(db, make_episode):
    enqueue(db, make_episode(), KIND)          # stays queued
    enqueue(db, make_episode(), KIND)
    done = claim(db, "worker-1", KIND)
    complete(db, done)
    db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour'")  # old timestamps everywhere
    assert reclaim_stale(db) == {}


def test_poison_pill_is_failed_after_max_attempts(db, make_episode):
    """A job that crashes every worker must eventually stop being retried."""
    job_id = enqueue(db, make_episode(), KIND)
    results = []
    for _ in range(3):
        claim(db, "worker-doomed", KIND)
        make_stale(db, job_id)
        results.append(reclaim_stale(db))
    assert results == [{"queued": 1}, {"queued": 1}, {"failed": 1}]
    assert claim(db, "worker-1", KIND) is None


# --- fencing: a slow worker must not overwrite the new owner ---------------

def test_slow_worker_cannot_complete_a_reclaimed_job(db, make_episode):
    enqueue(db, make_episode(), KIND)
    slow = claim(db, "worker-slow", KIND)      # attempt 1
    make_stale(db, slow.id)
    reclaim_stale(db)
    fresh = claim(db, "worker-fresh", KIND)    # attempt 2, same job

    with pytest.raises(LeaseLost):
        complete(db, slow)                     # the slow worker finally finishes: rejected
    assert job_row(db, fresh.id)[:3] == ("running", 2, "worker-fresh")

    complete(db, fresh)                        # the current owner still succeeds
    assert job_row(db, fresh.id)[0] == "done"


def test_slow_worker_cannot_fail_a_reclaimed_job(db, make_episode):
    enqueue(db, make_episode(), KIND)
    slow = claim(db, "worker-slow", KIND)
    make_stale(db, slow.id)
    reclaim_stale(db)
    claim(db, "worker-fresh", KIND)
    with pytest.raises(LeaseLost):
        fail(db, slow, "timed out")