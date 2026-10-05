"""Audio prep: chunk planning rules (pure) and VAD/encoding on a real speech clip.

The fixture is Windows TTS saying three sentences with ~3 s pauses between them:
  0-1 s silence | sentence 1 | 3 s | sentence 2 | 3 s | sentence 3 | 1 s
"""

import io
import random
from pathlib import Path

import pytest
import soundfile as sf

from earshot.audio import (
    MAX_CHUNK_SECONDS,
    SAMPLE_RATE,
    Span,
    encode_flac,
    find_speech,
    load_audio,
    plan_chunks,
)

GROQ_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")


# --- plan_chunks: pure rules ------------------------------------------------

def test_no_speech_means_no_chunks():
    assert plan_chunks([]) == []


def test_segments_that_fit_become_one_chunk():
    speech = [Span(1, 3), Span(5, 8), Span(10, 12)]
    assert plan_chunks(speech, max_seconds=60) == [Span(1, 12)]


def test_chunks_split_between_segments_never_inside_one():
    speech = [Span(0, 4), Span(5, 9), Span(10, 14)]
    chunks = plan_chunks(speech, max_seconds=10)
    assert chunks == [Span(0, 9), Span(10, 14)]  # cut in the 9→10 silence


def test_input_order_does_not_matter():
    speech = [Span(10, 14), Span(0, 4), Span(5, 9)]
    assert plan_chunks(speech, max_seconds=10) == [Span(0, 9), Span(10, 14)]


def test_overlong_single_segment_is_hard_split_as_last_resort():
    chunks = plan_chunks([Span(0, 25)], max_seconds=10)
    assert chunks == [Span(0, 10), Span(10, 20), Span(20, 25)]


def test_chunk_rules_hold_for_random_speech_patterns():
    """Invariants over 200 random episodes: chunks are within the limit, ordered,
    non-overlapping, and every second of speech lands inside some chunk."""
    rng = random.Random(42)
    for _ in range(200):
        t, speech = 0.0, []
        for _ in range(rng.randint(1, 40)):
            t += rng.uniform(0.1, 5)                   # silence
            length = rng.uniform(0.5, 40)              # speech, sometimes longer than the limit
            speech.append(Span(t, t + length))
            t += length
        max_s = rng.choice([10, 30, 60])
        chunks = plan_chunks(speech, max_seconds=max_s)

        assert all(c.duration <= max_s + 1e-9 for c in chunks)
        assert all(a.end <= b.start for a, b in zip(chunks, chunks[1:]))
        for seg in speech:
            covered = sum(
                max(0.0, min(seg.end, c.end) - max(seg.start, c.start)) for c in chunks
            )
            assert covered == pytest.approx(seg.duration)


def test_max_chunk_fits_groq_upload_limit_even_uncompressed():
    raw_bytes = MAX_CHUNK_SECONDS * SAMPLE_RATE * 2  # 16-bit mono PCM, before FLAC
    assert raw_bytes < GROQ_MAX_UPLOAD_BYTES


# --- real audio --------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_audio():
    return load_audio(SPEECH_SAMPLE)


def test_load_audio_gives_16khz_mono_float(sample_audio):
    assert sample_audio.ndim == 1
    assert sample_audio.dtype.name == "float32"
    assert len(sample_audio) / SAMPLE_RATE == pytest.approx(18.44, abs=0.05)


def test_vad_finds_the_three_sentences(sample_audio):
    speech = find_speech(sample_audio)
    assert len(speech) == 3
    # Observed: 0.92-3.34, 6.97-10.34, 13.82-16.87. Allow slack for model updates.
    expected = [(0.9, 3.3), (7.0, 10.3), (13.8, 16.9)]
    for span, (start, end) in zip(speech, expected):
        assert span.start == pytest.approx(start, abs=0.5)
        assert span.end == pytest.approx(end, abs=0.5)


def test_small_limit_cuts_in_the_pauses(sample_audio):
    speech = find_speech(sample_audio)
    chunks = plan_chunks(speech, max_seconds=6)
    assert chunks == speech  # each sentence alone; every cut falls in a 3 s pause


def test_encode_flac_round_trips_the_chunk(sample_audio):
    span = Span(6.97, 10.34)
    data = encode_flac(sample_audio, span)
    decoded, rate = sf.read(io.BytesIO(data))
    assert rate == SAMPLE_RATE
    assert len(decoded) / SAMPLE_RATE == pytest.approx(span.duration, abs=0.01)
    assert len(data) < GROQ_MAX_UPLOAD_BYTES
