"""Groq client: request shape, response parsing, and error classification.
All tests use a fake HTTP transport: no network, no quota spent."""

import httpx
import pytest

from earshot import transcribe
from earshot.transcribe import (
    DEFAULT_RETRY_AFTER_SECONDS,
    GROQ_TRANSCRIBE_URL,
    RateLimited,
    TranscriptionError,
    Word,
    get_api_key,
    transcribe_chunk,
)

FAKE_KEY = "gsk_test_FAKEKEY123"
FLAC = b"fLaC-fake-audio-bytes"

SUCCESS_BODY = {
    "text": " Hello world.",
    "words": [
        {"word": " Hello", "start": 0.12, "end": 0.48},
        {"word": " world.", "start": 0.52, "end": 0.95},
    ],
}


def fake_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def respond(status, json=None, headers=None, text=None):
    def handler(request):
        if json is not None:
            return httpx.Response(status, json=json, headers=headers)
        return httpx.Response(status, text=text or "", headers=headers)
    return fake_client(handler)


def run(client):
    return transcribe_chunk(FLAC, api_key=FAKE_KEY, client=client)


# --- request -------------------------------------------------------------------

def test_request_has_the_right_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.read()
        return httpx.Response(200, json=SUCCESS_BODY)

    run(fake_client(handler))
    assert seen["url"] == GROQ_TRANSCRIBE_URL
    assert seen["auth"] == f"Bearer {FAKE_KEY}"
    for expected in [b"whisper-large-v3", b"verbose_json", b"timestamp_granularities[]",
                     b"word", FLAC, b'filename="chunk.flac"']:
        assert expected in seen["body"]


def test_default_client_has_a_timeout(monkeypatch):
    captured = {}
    real_client = httpx.Client

    def spy(**kwargs):
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SUCCESS_BODY)))

    monkeypatch.setattr(transcribe.httpx, "Client", spy)
    transcribe_chunk(FLAC, api_key=FAKE_KEY)
    assert captured["timeout"] == transcribe.TIMEOUT


# --- success -------------------------------------------------------------------

def test_parses_text_and_word_timestamps():
    result = run(respond(200, json=SUCCESS_BODY))
    assert result.text == "Hello world."
    assert result.words == [Word("Hello", 0.12, 0.48), Word("world.", 0.52, 0.95)]


def test_response_without_words_gives_empty_list():
    assert run(respond(200, json={"text": ""})).words == []


# --- errors --------------------------------------------------------------------

def test_429_raises_rate_limited_with_retry_after():
    with pytest.raises(RateLimited) as exc:
        run(respond(429, json={"error": {"message": "slow down"}}, headers={"retry-after": "37"}))
    assert exc.value.retry_after == 37.0


def test_429_without_retry_after_uses_a_safe_default():
    with pytest.raises(RateLimited) as exc:
        run(respond(429, text="too many"))
    assert exc.value.retry_after == DEFAULT_RETRY_AFTER_SECONDS


def test_server_error_is_retryable():
    with pytest.raises(TranscriptionError) as exc:
        run(respond(503, json={"error": {"message": "overloaded"}}))
    assert exc.value.retryable is True
    assert "503" in str(exc.value) and "overloaded" in str(exc.value)


def test_client_error_is_not_retryable():
    with pytest.raises(TranscriptionError) as exc:
        run(respond(400, json={"error": {"message": "could not decode audio"}}))
    assert exc.value.retryable is False


def test_network_failure_is_retryable():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(TranscriptionError) as exc:
        run(fake_client(handler))
    assert exc.value.retryable is True


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_errors_never_contain_the_api_key(status):
    with pytest.raises((RateLimited, TranscriptionError)) as exc:
        run(respond(status, text="error"))
    assert FAKE_KEY not in str(exc.value)


# --- config --------------------------------------------------------------------

@pytest.mark.parametrize("value", ["", "your-groq-key"])
def test_missing_or_placeholder_key_fails_fast(monkeypatch, value):
    monkeypatch.setenv("GROQ_API_KEY", value)
    monkeypatch.setattr(transcribe, "load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_api_key()
