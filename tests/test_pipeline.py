"""Episode transcription: checkpoints, resume after failure, idempotent re-runs,
and invalidation when the audio or the chunk plan changes."""

import shutil
from pathlib import Path

import pytest
import soundfile as sf

from earshot.audio import Span
from earshot.pipeline import get_transcript, transcribe_episode
from earshot.transcribe import RateLimited, Transcription, Word

SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")
SMALL_CHUNKS = 6  # seconds: splits the fixture's three sentences into three chunks
# Observed VAD segments for the fixture (see test_audio.py).
FIXTURE_CHUNKS = [Span(0.92, 3.336), Span(6.968, 10.344), Span(13.816, 16.872)]


class FakeTranscriber:
    """Returns one word per chunk ('chunk-N' at 0.1-0.5 s). Can fail on a chosen call."""

    def __init__(self, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def __call__(self, flac: bytes) -> Transcription:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RateLimited(retry_after=30)
        n = self.calls - 1
        return Transcription(text=f"chunk-{n}", words=[Word(f"chunk-{n}", 0.1, 0.5)])


def run(db, episode_id, fake, path=SPEECH_SAMPLE):
    return transcribe_episode(db, episode_id, path, transcriber=fake, max_chunk_seconds=SMALL_CHUNKS)


def chunk_count(db, episode_id):
    return db.execute("SELECT count(*) FROM chunks WHERE episode_id = %s", (episode_id,)).fetchone()[0]


def test_transcribes_every_chunk_with_absolute_timestamps(db, make_episode):
    episode_id = make_episode()
    fake = FakeTranscriber()
    result = run(db, episode_id, fake)

    assert (result.chunks_total, result.transcribed, result.skipped) == (3, 3, 0)
    words = get_transcript(db, episode_id)
    assert [w.text for w in words] == ["chunk-0", "chunk-1", "chunk-2"]
    for word, chunk in zip(words, FIXTURE_CHUNKS):
        assert word.start == pytest.approx(chunk.start + 0.1, abs=0.01)  # offset applied
        assert word.end == pytest.approx(chunk.start + 0.5, abs=0.01)


def test_rerun_makes_no_api_calls(db, make_episode):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())
    second = FakeTranscriber()
    result = run(db, episode_id, second)
    assert second.calls == 0
    assert (result.transcribed, result.skipped) == (0, 3)
    assert chunk_count(db, episode_id) == 3


def test_resumes_after_rate_limit_without_redoing_finished_chunks(db, make_episode):
    episode_id = make_episode()
    with pytest.raises(RateLimited):
        run(db, episode_id, FakeTranscriber(fail_on_call=2))   # chunk 0 done, chunk 1 hits 429
    assert chunk_count(db, episode_id) == 1                    # progress kept

    retry = FakeTranscriber()
    result = run(db, episode_id, retry)
    assert retry.calls == 2                                    # only chunks 1 and 2
    assert (result.transcribed, result.skipped) == (2, 1)
    assert [w.text for w in get_transcript(db, episode_id)] == ["chunk-0", "chunk-0", "chunk-1"]


def test_changed_audio_discards_old_checkpoints(db, make_episode, tmp_path):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())

    # Same speech, different file bytes: like a re-download with different metadata/ads.
    data, rate = sf.read(SPEECH_SAMPLE)
    altered = tmp_path / "redownload.flac"
    sf.write(altered, data, rate, format="FLAC", subtype="PCM_16", compression_level=0.0)

    fake = FakeTranscriber()
    result = run(db, episode_id, fake, path=str(altered))
    assert fake.calls == 3                      # everything redone
    assert (result.transcribed, result.skipped) == (3, 0)
    assert chunk_count(db, episode_id) == 3


def test_changed_chunk_plan_discards_old_checkpoints(db, make_episode):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())              # 3 small chunks
    fake = FakeTranscriber()
    result = transcribe_episode(db, episode_id, SPEECH_SAMPLE, transcriber=fake)  # default: 1 big chunk
    assert (result.chunks_total, fake.calls) == (1, 1)
    assert chunk_count(db, episode_id) == 1


def test_identical_copy_of_the_audio_reuses_checkpoints(db, make_episode, tmp_path):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())
    copy = tmp_path / "same.flac"
    shutil.copy(SPEECH_SAMPLE, copy)              # new path, identical bytes
    fake = FakeTranscriber()
    run(db, episode_id, fake, path=str(copy))
    assert fake.calls == 0


def test_measured_duration_is_stored_on_the_episode(db, make_episode):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())
    duration = db.execute("SELECT duration_seconds FROM episodes WHERE id = %s", (episode_id,)).fetchone()[0]
    assert duration == 18  # fixture is 18.44 s


def test_chunks_are_deleted_with_their_episode(db, make_episode):
    episode_id = make_episode()
    run(db, episode_id, FakeTranscriber())
    db.execute("DELETE FROM episodes WHERE id = %s", (episode_id,))
    assert chunk_count(db, episode_id) == 0
