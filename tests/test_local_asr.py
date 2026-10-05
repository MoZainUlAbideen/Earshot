"""Local transcriber: converts faster-whisper output to our Transcription (fake model, no download)."""

from types import SimpleNamespace as NS

from earshot.local_asr import LocalTranscriber
from earshot.transcribe import Word


class FakeModel:
    def __init__(self):
        self.kwargs = None

    def transcribe(self, audio, **kwargs):
        self.kwargs = kwargs
        segments = [
            NS(text=" Hello there.", words=[NS(word=" Hello", start=0.1, end=0.4), NS(word=" there.", start=0.5, end=0.9)]),
            NS(text=" Bye.", words=[NS(word=" Bye.", start=1.5, end=1.8)]),
        ]
        return iter(segments), NS(language="en")


def test_converts_segments_to_words_and_text():
    result = LocalTranscriber(model=FakeModel())(b"flac")
    assert result.text == "Hello there. Bye."
    assert result.words == [Word("Hello", 0.1, 0.4), Word("there.", 0.5, 0.9), Word("Bye.", 1.5, 1.8)]


def test_asks_for_word_timestamps_and_skips_its_own_vad():
    model = FakeModel()
    LocalTranscriber(model=model)(b"flac")
    assert model.kwargs["word_timestamps"] is True
    assert model.kwargs["vad_filter"] is False


def test_pipeline_uses_the_backends_own_chunk_size():
    """Local models are RAM-limited, so they declare smaller chunks than Groq's 600 s."""
    from pathlib import Path

    from earshot.audio import load_audio
    from earshot.local_asr import LOCAL_MAX_CHUNK_SECONDS
    from earshot.pipeline import transcribe_audio

    sample = load_audio(str(Path(__file__).parent / "fixtures" / "speech_sample.flac"))

    class SixSecondChunks(LocalTranscriber):
        max_chunk_seconds = 6  # the fixture's three sentences become three chunks

    _, n_chunks = transcribe_audio(sample, SixSecondChunks(model=FakeModel()))
    assert n_chunks == 3
    assert LOCAL_MAX_CHUNK_SECONDS < 600
