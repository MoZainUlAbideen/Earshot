"""Episode transcription with checkpoints: audio file → chunks → words in the DB.

Each finished chunk is saved immediately, so a retry after a crash or a 429 resumes
where it stopped and never re-sends audio already transcribed. Re-running a fully
transcribed episode makes no API calls (idempotent under at-least-once delivery).
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from earshot.audio import (
    MAX_CHUNK_SECONDS,
    SAMPLE_RATE,
    encode_flac,
    find_speech,
    load_audio,
    plan_chunks,
)
from earshot.transcribe import DEFAULT_MODEL, Transcription, Word, transcribe_chunk

Transcriber = Callable[[bytes], Transcription]


@dataclass(frozen=True)
class EpisodeResult:
    chunks_total: int
    transcribed: int  # chunks sent to the API this run
    skipped: int      # chunks already done (checkpoints)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def transcribe_episode(
    conn: psycopg.Connection,
    episode_id: int,
    audio_path: str,
    *,
    transcriber: Transcriber | None = None,
    model: str = DEFAULT_MODEL,
    max_chunk_seconds: float = MAX_CHUNK_SECONDS,
) -> EpisodeResult:
    """Transcribe every chunk of an episode that isn't already saved.

    RateLimited / TranscriptionError propagate to the caller (the worker); chunks
    finished before the error stay saved.
    """
    transcribe = transcriber or (lambda flac: transcribe_chunk(flac, model=model))
    audio = load_audio(audio_path)
    audio_hash = file_sha256(audio_path)

    conn.execute(
        "UPDATE episodes SET duration_seconds = %s WHERE id = %s",
        (round(len(audio) / SAMPLE_RATE), episode_id),
    )

    chunks = plan_chunks(find_speech(audio), max_seconds=max_chunk_seconds)

    # Checkpoints are only valid for the exact same audio AND the same chunk plan.
    # A re-download with different content (e.g. dynamically inserted ads) or a
    # changed plan would shift timestamps, so stale checkpoints are discarded.
    planned = {(idx, span.start, span.end) for idx, span in enumerate(chunks)}
    saved = conn.execute(
        "SELECT idx, start_s, end_s, audio_sha256 FROM chunks WHERE episode_id = %s",
        (episode_id,),
    ).fetchall()
    if any((idx, start, end) not in planned or sha != audio_hash for idx, start, end, sha in saved):
        conn.execute("DELETE FROM chunks WHERE episode_id = %s", (episode_id,))
        saved = []
    done = {idx for idx, *_ in saved}

    transcribed = 0
    for idx, span in enumerate(chunks):
        if idx in done:
            continue
        result = transcribe(encode_flac(audio, span))
        words = [
            {"text": w.text, "start": round(span.start + w.start, 3), "end": round(span.start + w.end, 3)}
            for w in result.words
        ]
        conn.execute(
            """
            INSERT INTO chunks (episode_id, idx, start_s, end_s, text, words, model, audio_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id, idx) DO NOTHING
            """,
            (episode_id, idx, span.start, span.end, result.text, Jsonb(words), model, audio_hash),
        )
        transcribed += 1

    return EpisodeResult(chunks_total=len(chunks), transcribed=transcribed, skipped=len(chunks) - transcribed)


def get_transcript(conn: psycopg.Connection, episode_id: int) -> list[Word]:
    """The stitched transcript: every word with absolute timestamps, in time order."""
    words: list[Word] = []
    for (chunk_words,) in conn.execute(
        "SELECT words FROM chunks WHERE episode_id = %s ORDER BY idx", (episode_id,)
    ):
        words.extend(Word(w["text"], w["start"], w["end"]) for w in chunk_words)
    return words
