"""Groq Whisper client: one FLAC chunk in, words with timestamps out.

Errors are classified by whose fault they are, because the worker reacts differently:
  429           -> RateLimited (defer without spending an attempt; not the job's fault)
  5xx / network -> TranscriptionError(retryable=True)  (normal retry with backoff)
  other 4xx     -> TranscriptionError(retryable=False) (bad request/key; retrying won't help)
Error messages never include request headers, which carry the API key.
"""

import os
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Chosen by eval (2026-10-06): turbo tied large-v3 on LibriSpeech (2.65% WER each) and on
# podcast content, has tighter timestamps (max 0.55 s vs 1.35 s off), is 2.8x cheaper on
# the paid tier, and transcribes everything. large-v3 silently skipped a 68-word ad
# passage on NPR, which could just as well have been real content.
DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_RETRY_AFTER_SECONDS = 60.0
TIMEOUT = httpx.Timeout(120.0, connect=10.0)  # 10 MB upload + transcription takes a while


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # seconds from the start of the chunk
    end: float


@dataclass(frozen=True)
class Transcription:
    text: str
    words: list[Word]


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"Groq rate limit hit; retry after {retry_after:.0f}s")
        self.retry_after = retry_after


class TranscriptionError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def get_api_key() -> str:
    load_dotenv()
    key = os.environ.get("GROQ_API_KEY", "")
    if not key or key == "your-groq-key":
        raise RuntimeError("GROQ_API_KEY is not set. Add your key to .env.")
    return key


def transcribe_chunk(
    flac: bytes,
    *,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> Transcription:
    """Transcribe one FLAC chunk. Word times are relative to the chunk's start."""
    key = api_key or get_api_key()
    data = {
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
        "temperature": "0",
    }
    if language:
        data["language"] = language

    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        response = http.post(
            GROQ_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {key}"},
            data=data,
            files={"file": ("chunk.flac", flac, "audio/flac")},
        )
    except httpx.TransportError as e:  # timeouts, DNS, connection resets
        raise TranscriptionError(f"network error talking to Groq: {type(e).__name__}", retryable=True) from None
    finally:
        if client is None:
            http.close()

    if response.status_code == 429:
        raise RateLimited(_retry_after_seconds(response))
    if response.status_code >= 400:
        raise TranscriptionError(
            f"Groq returned {response.status_code}: {_error_message(response)}",
            retryable=response.status_code >= 500,
        )

    body = response.json()
    words = [
        Word(text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]))
        for w in body.get("words", [])
    ]
    return Transcription(text=body.get("text", "").strip(), words=words)


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return DEFAULT_RETRY_AFTER_SECONDS


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]
