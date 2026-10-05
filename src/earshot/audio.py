"""Audio preparation: decode → find speech (Silero VAD) → plan chunks cut in silences.

Chunks never split a word: every boundary falls in a pause between speech segments.
A chunk is at most MAX_CHUNK_SECONDS long, which also bounds its upload size:
600 s × 16 kHz × 2 bytes = 19.2 MB raw, under Groq's 25 MB limit before FLAC
compression even starts.
"""

import io
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from faster_whisper.audio import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

SAMPLE_RATE = 16_000
MAX_CHUNK_SECONDS = 600

# Podcast speakers rarely pause for 2 s (the library default), so count shorter
# pauses as gaps to get more safe places to cut.
VAD_OPTIONS = VadOptions(
    min_silence_duration_ms=500,
    speech_pad_ms=200,
    max_speech_duration_s=MAX_CHUNK_SECONDS,  # VAD itself splits long monologues at the best pause
)


@dataclass(frozen=True)
class Span:
    """A stretch of audio, in seconds from the start of the episode."""
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def load_audio(path: str) -> np.ndarray:
    """Decode any common format to 16 kHz mono float32 (PyAV bundles FFmpeg)."""
    return decode_audio(path, sampling_rate=SAMPLE_RATE)


def find_speech(audio: np.ndarray) -> list[Span]:
    """Run Silero VAD and return the speech segments in seconds."""
    segments = get_speech_timestamps(audio, VAD_OPTIONS, sampling_rate=SAMPLE_RATE)
    return [Span(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE) for s in segments]


def plan_chunks(speech: list[Span], max_seconds: float = MAX_CHUNK_SECONDS) -> list[Span]:
    """Pack consecutive speech segments into chunks of at most `max_seconds`.

    Each chunk runs from its first segment's start to its last segment's end, so
    boundaries always fall in silence and the silence between chunks isn't sent
    (Groq bills per audio-second). A single segment longer than the limit (rare,
    since VAD already splits at max_speech_duration_s) is hard-split as a last resort.
    """
    chunks: list[Span] = []
    current: Span | None = None
    for seg in sorted(speech, key=lambda s: s.start):
        for piece in _split_long(seg, max_seconds):
            if current is None:
                current = piece
            elif piece.end - current.start <= max_seconds:
                current = Span(current.start, piece.end)
            else:
                chunks.append(current)
                current = piece
    if current is not None:
        chunks.append(current)
    return chunks


def _split_long(seg: Span, max_seconds: float) -> list[Span]:
    pieces = []
    start = seg.start
    while seg.end - start > max_seconds:
        pieces.append(Span(start, start + max_seconds))
        start += max_seconds
    pieces.append(Span(start, seg.end))
    return pieces


def encode_flac(audio: np.ndarray, span: Span) -> bytes:
    """Cut `span` out of the episode audio and encode it as 16-bit mono FLAC."""
    samples = audio[int(span.start * SAMPLE_RATE): int(span.end * SAMPLE_RATE)]
    buffer = io.BytesIO()
    sf.write(buffer, samples, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()
