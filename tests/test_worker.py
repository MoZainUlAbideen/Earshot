"""Worker: the happy path, and every failure routed by whose fault it is."""

import os
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from earshot import worker
from earshot.download import DownloadError
from earshot.ingest import TRANSCRIBE
from earshot.queue import claim, enqueue, reclaim_stale
from earshot.transcribe import RateLimited, Transcription, TranscriptionError, Word
from earshot.worker import process_one, run_worker

SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")
OK = Transcription(text="hello", words=[Word("hello", 0.1, 0.5)])


class FakeDownloader:
    """Copies the speech fixture into the worker's temp dir; remembers what it did."""

    def __init__(self, error: Exception | None = None, side_effect=None):
        self.error = error
        self.side_effect = side_effect
        self.urls, self.paths = [], []

    def __call__(self, url: str, dest_dir: str) -> str:
        self.urls.append(url)
        if self.side_effect:
            self.side_effect()
        if self.error:
            raise self.error
        path = os.path.join(dest_dir, "episode.audio")
        shutil.copy(SPEECH_SAMPLE, path)
        self.paths.append(path)
        return path


class FakeTranscriber:
    def __init__(self, error: BaseException | None = None):
        self.error = error
        self.calls = 0

    def __call__(self, flac: bytes) -> Transcription:
        self.calls += 1
        if self.error:
            raise self.error
        return OK


@pytest.fixture
def queued_job(db, make_episode):
    episode_id = make_episode()
    job_id = enqueue(db, episode_id, TRANSCRIBE)
    return job_id


def job_state(db, job_id):
    return db.execute(
        "SELECT status, attempts, last_error, run_after - updated_at FROM jobs WHERE id = %s", (job_id,)
    ).fetchone()


def process(db, downloader=None, transcriber=None, worker_id="worker-1"):
    return process_one(db, worker_id, downloader=downloader or FakeDownloader(),
                       transcriber=transcriber or FakeTranscriber())


# --- happy path ---------------------------------------------------------------------

def test_empty_queue_returns_none(db):
    assert process(db) is None


def test_job_is_downloaded_transcribed_and_completed(db, queued_job):
    downloader = FakeDownloader()
    outcome = process(db, downloader=downloader)

    assert (outcome.status, outcome.job_id) == ("done", queued_job)
    assert job_state(db, queued_job)[0] == "done"
    assert downloader.urls == ["https://example.com/ep.mp3"]
    assert db.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1


def test_temp_audio_is_deleted_after_the_job(db, queued_job):
    downloader = FakeDownloader()
    process(db, downloader=downloader)
    assert not os.path.exists(downloader.paths[0])
    assert not os.path.exists(os.path.dirname(downloader.paths[0]))


def test_temp_audio_is_deleted_even_when_the_job_fails(db, queued_job):
    downloader = FakeDownloader()
    process(db, downloader=downloader, transcriber=FakeTranscriber(TranscriptionError("boom", retryable=True)))
    assert not os.path.exists(downloader.paths[0])


# --- failure routing ----------------------------------------------------------------

def test_rate_limit_defers_without_spending_an_attempt(db, queued_job):
    outcome = process(db, transcriber=FakeTranscriber(RateLimited(retry_after=42)))
    assert (outcome.status, outcome.wait) == ("deferred", 42)
    status, attempts, last_error, delay = job_state(db, queued_job)
    assert (status, attempts) == ("queued", 0)
    assert delay == timedelta(seconds=42)
    assert "rate limited" in last_error


def test_retryable_transcription_error_retries_with_backoff(db, queued_job):
    outcome = process(db, transcriber=FakeTranscriber(TranscriptionError("Groq 503", retryable=True)))
    assert outcome.status == "retrying"
    assert job_state(db, queued_job)[:3] == ("queued", 1, "Groq 503")


def test_permanent_transcription_error_fails_immediately(db, queued_job):
    outcome = process(db, transcriber=FakeTranscriber(TranscriptionError("bad audio", retryable=False)))
    assert outcome.status == "failed"
    assert job_state(db, queued_job)[0] == "failed"


def test_dead_audio_url_fails_immediately_without_transcribing(db, queued_job):
    transcriber = FakeTranscriber()
    outcome = process(db, downloader=FakeDownloader(DownloadError("audio download returned 404", retryable=False)),
                      transcriber=transcriber)
    assert outcome.status == "failed"
    assert transcriber.calls == 0


def test_flaky_download_is_retried(db, queued_job):
    outcome = process(db, downloader=FakeDownloader(DownloadError("network error", retryable=True)))
    assert outcome.status == "retrying"
    assert job_state(db, queued_job)[:2] == ("queued", 1)


def test_unexpected_bug_is_recorded_and_retried_and_worker_survives(db, queued_job):
    outcome = process(db, transcriber=FakeTranscriber(ValueError("oops")))
    assert outcome.status == "retrying"
    assert "ValueError" in job_state(db, queued_job)[2]


def test_ctrl_c_hands_the_job_back_immediately(db, queued_job):
    with pytest.raises(KeyboardInterrupt):
        process(db, transcriber=FakeTranscriber(KeyboardInterrupt()))
    status, attempts, last_error, delay = job_state(db, queued_job)
    assert (status, attempts, delay) == ("queued", 0, timedelta(0))
    assert "shut down" in last_error


def test_lease_lost_mid_job_drops_the_result(db, queued_job):
    def job_gets_taken_over():
        db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour'")
        reclaim_stale(db)
        claim(db, "worker-2", TRANSCRIBE)

    outcome = process(db, downloader=FakeDownloader(side_effect=job_gets_taken_over))
    assert outcome.status == "lease_lost"
    status, = db.execute("SELECT status FROM jobs WHERE id = %s", (queued_job,)).fetchone()
    owner, = db.execute("SELECT locked_by FROM jobs WHERE id = %s", (queued_job,)).fetchone()
    assert (status, owner) == ("running", "worker-2")  # the new owner is untouched


def test_stale_job_from_a_dead_worker_is_picked_up(db, queued_job):
    claim(db, "worker-dead", TRANSCRIBE)
    db.execute("UPDATE jobs SET locked_at = now() - interval '1 hour'")
    outcome = process(db, worker_id="worker-alive")
    assert outcome.status == "done"


# --- run loop -----------------------------------------------------------------------

def test_once_processes_a_single_job(db, make_episode):
    first = enqueue(db, make_episode(), TRANSCRIBE)
    second = enqueue(db, make_episode(), TRANSCRIBE)
    run_worker(db, "worker-1", once=True, downloader=FakeDownloader(), transcriber=FakeTranscriber())
    statuses = {job_id: job_state(db, job_id)[0] for job_id in (first, second)}
    assert sorted(statuses.values()) == ["done", "queued"]


def test_worker_pauses_for_rate_limit_and_idles_when_empty(db, queued_job, monkeypatch):
    sleeps = []
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    run_worker(db, "worker-1", max_iterations=2, idle_sleep=7,
               downloader=FakeDownloader(), transcriber=FakeTranscriber(RateLimited(retry_after=42)))
    # iteration 1: 429 -> pause 42s; iteration 2: job not ready yet -> idle 7s
    assert sleeps == [42, 7]
