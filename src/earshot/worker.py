"""Transcription worker: reclaim stale → claim → download (temp) → transcribe → complete.

Every failure is routed by whose fault it is:
  RateLimited                   -> defer (no attempt spent) and pause the whole worker,
                                   because Groq's quota is per account, not per job
  retryable Transcription/Download error -> fail with backoff
  permanent Transcription/Download error -> fail now (retrying can't help)
  LeaseLost                     -> our claim was taken over; drop the result
  KeyboardInterrupt             -> hand the job back immediately (graceful shutdown)
  any other exception (a bug)   -> fail with backoff, record the error, keep running
Audio lives only in a temporary directory that is deleted when the job ends.
"""

import logging
import os
import socket
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from earshot.download import DownloadError, download_audio
from earshot.ingest import TRANSCRIBE
from earshot.pipeline import Transcriber, transcribe_episode
from earshot.queue import Job, LeaseLost, claim, complete, defer, fail, reclaim_stale
from earshot.transcribe import RateLimited, TranscriptionError

log = logging.getLogger("earshot.worker")

IDLE_SLEEP_SECONDS = 10
Downloader = Callable[[str, str], str]


@dataclass(frozen=True)
class Outcome:
    status: str          # done | deferred | retrying | failed | lease_lost
    job_id: int
    wait: float = 0.0    # how long the worker should pause before the next job


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def process_one(
    conn: psycopg.Connection,
    worker_id: str,
    *,
    transcriber: Transcriber | None = None,
    downloader: Downloader = download_audio,
) -> Outcome | None:
    """Process the next ready transcribe job. Returns None if there is nothing to do."""
    reclaimed = reclaim_stale(conn)
    if reclaimed:
        log.warning("reclaimed stale jobs: %s", reclaimed)

    job = claim(conn, worker_id, TRANSCRIBE)
    if job is None:
        return None
    try:
        return _run(conn, job, transcriber, downloader)
    except LeaseLost:
        log.warning("job %s: lease lost (reclaimed by another worker); result dropped", job.id)
        return Outcome("lease_lost", job.id)


def _run(conn, job: Job, transcriber, downloader) -> Outcome:
    audio_url = conn.execute(
        "SELECT audio_url FROM episodes WHERE id = %s", (job.episode_id,)
    ).fetchone()[0]
    log.info("job %s: episode %s, attempt %s/%s", job.id, job.episode_id, job.attempts, job.max_attempts)
    try:
        with tempfile.TemporaryDirectory(prefix="earshot-") as tmp:
            path = downloader(audio_url, tmp)
            result = transcribe_episode(conn, job.episode_id, path, transcriber=transcriber)
        complete(conn, job)
        log.info("job %s: done (%s chunks sent, %s from checkpoints)", job.id, result.transcribed, result.skipped)
        return Outcome("done", job.id)
    except LeaseLost:
        raise
    except RateLimited as e:
        defer(conn, job, e.retry_after, f"rate limited; retry after {e.retry_after:.0f}s")
        log.warning("job %s: rate limited, deferred %.0fs (no attempt spent)", job.id, e.retry_after)
        return Outcome("deferred", job.id, wait=e.retry_after)
    except (TranscriptionError, DownloadError) as e:
        status = fail(conn, job, str(e), permanent=not e.retryable)
        log.warning("job %s: %s -> %s", job.id, e, status)
        return Outcome("failed" if status == "failed" else "retrying", job.id)
    except KeyboardInterrupt:
        defer(conn, job, 0, "worker shut down mid-job")
        log.warning("job %s: handed back on shutdown", job.id)
        raise
    except Exception as e:  # a bug: record it, retry with backoff, keep the worker alive
        log.exception("job %s: unexpected error", job.id)
        status = fail(conn, job, f"unexpected {type(e).__name__}: {e}"[:500])
        return Outcome("failed" if status == "failed" else "retrying", job.id)


def run_worker(
    conn: psycopg.Connection,
    worker_id: str,
    *,
    once: bool = False,
    idle_sleep: float = IDLE_SLEEP_SECONDS,
    max_iterations: int | None = None,
    **kwargs,
) -> None:
    """Loop over jobs until interrupted. `once`: process at most one job, then return."""
    log.info("worker %s started", worker_id)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        outcome = process_one(conn, worker_id, **kwargs)
        if once:
            return
        if outcome is None:
            time.sleep(idle_sleep)
        elif outcome.wait:
            log.info("pausing %.0fs for the rate limit", outcome.wait)
            time.sleep(outcome.wait)
