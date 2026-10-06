"""Reading stored transcripts. Deliberately dependency-light: the API imports this, and must
not pull in the speech stack (faster-whisper, ctranslate2, PyAV) just to read words from
the database. A lean serving path means less memory on a 512 MB host."""

import psycopg

from earshot.transcribe import Word


def get_transcript(conn: psycopg.Connection, episode_id: int) -> list[Word]:
    """The stitched transcript: every word with absolute timestamps, in time order."""
    words: list[Word] = []
    for (chunk_words,) in conn.execute(
        "SELECT words FROM chunks WHERE episode_id = %s ORDER BY idx", (episode_id,)
    ):
        words.extend(Word(w["text"], w["start"], w["end"]) for w in chunk_words)
    return words
