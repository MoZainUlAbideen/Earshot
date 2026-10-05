"""Contract test against the real Groq API. Skipped by default; run with `uv run pytest -m live`.

Uses the TTS fixture, where we know every word and (from VAD) where each sentence is.
"""

import re
from pathlib import Path

import pytest

from earshot.audio import encode_flac, find_speech, load_audio, plan_chunks
from earshot.transcribe import transcribe_chunk

pytestmark = pytest.mark.live

SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")

SENTENCES = [
    "welcome to the earshot test recording",
    "scaling laws describe how models improve with more data",
    "every answer cites the exact moment it was said",
]


def normalize(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.lower()).strip()


def test_groq_transcribes_fixture_with_correct_absolute_timestamps():
    audio = load_audio(SPEECH_SAMPLE)
    speech = find_speech(audio)
    [chunk] = plan_chunks(speech)

    result = transcribe_chunk(encode_flac(audio, chunk), language="en")

    assert normalize(result.text) == " ".join(SENTENCES)

    # Each sentence's words, shifted by the chunk offset, must sit inside the VAD segment.
    words = iter(result.words)
    for sentence, segment in zip(SENTENCES, speech):
        sentence_words = [next(words) for _ in sentence.split()]
        start = chunk.start + sentence_words[0].start
        end = chunk.start + sentence_words[-1].end
        assert segment.start - 0.3 <= start <= end <= segment.end + 0.3
