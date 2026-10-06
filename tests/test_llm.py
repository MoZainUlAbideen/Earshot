"""Groq chat client: JSON parsing, retries on 429/5xx, error classification. Fake HTTP only."""

import json

import httpx
import pytest

from earshot.llm import LLMError, chat_json
from earshot.transcribe import RateLimited

KEY = "gsk_test_FAKE"
MSG = [{"role": "user", "content": "hi"}]


def ok(data, prompt=10, completion=5):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)}}],
                                     "usage": {"prompt_tokens": prompt, "completion_tokens": completion}})


def scripted(*responses):
    """A fake transport that replays responses in order and records requests."""
    seen = []
    queue = list(responses)

    def handler(request):
        seen.append(json.loads(request.read()))
        return queue.pop(0)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def call(client, **kw):
    sleeps = []
    result = chat_json(MSG, api_key=KEY, client=client, sleep=sleeps.append, **kw)
    return result, sleeps


def test_returns_parsed_json_and_token_usage():
    client, seen = scripted(ok({"question": "why?"}, prompt=120, completion=30))
    result, _ = call(client)
    assert result.data == {"question": "why?"}
    assert (result.prompt_tokens, result.completion_tokens) == (120, 30)
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert seen[0]["reasoning_effort"] == "low"  # gpt-oss reasoning tokens count against quota


def test_reasoning_effort_only_sent_to_gpt_oss():
    client, seen = scripted(ok({}))
    call(client, model="qwen/qwen3.8-27b")
    assert "reasoning_effort" not in seen[0]


def test_429_waits_for_retry_after_then_succeeds():
    client, _ = scripted(httpx.Response(429, headers={"retry-after": "7"}), ok({"a": 1}))
    result, sleeps = call(client)
    assert result.data == {"a": 1}
    assert sleeps == [7.0]


def test_long_retry_after_is_handed_to_the_caller():
    # e.g. the daily token budget is exhausted: waiting an hour in-process is the caller's call
    client, _ = scripted(httpx.Response(429, headers={"retry-after": "3600"}))
    with pytest.raises(RateLimited) as exc:
        call(client)
    assert exc.value.retry_after == 3600


def test_server_errors_are_retried_with_backoff():
    client, _ = scripted(httpx.Response(503), httpx.Response(502), ok({"a": 1}))
    result, sleeps = call(client)
    assert result.data == {"a": 1}
    assert sleeps == [1, 2]


def test_client_error_is_not_retried():
    client, seen = scripted(httpx.Response(400, json={"error": {"message": "bad model"}}))
    with pytest.raises(LLMError) as exc:
        call(client)
    assert exc.value.retryable is False and "bad model" in str(exc.value)
    assert len(seen) == 1


def test_invalid_json_from_the_model_is_a_retryable_error():
    client, _ = scripted(httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}))
    with pytest.raises(LLMError) as exc:
        call(client)
    assert exc.value.retryable is True


def test_errors_never_contain_the_key():
    client, _ = scripted(httpx.Response(401, text="unauthorized"))
    with pytest.raises(LLMError) as exc:
        call(client)
    assert KEY not in str(exc.value)


def test_key_whitespace_is_stripped_from_the_header():
    headers = []

    def handler(request):
        headers.append(request.headers["Authorization"])
        return ok({"a": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    chat_json(MSG, api_key=KEY + "\n", client=client)  # as pasted into a dashboard
    assert headers == [f"Bearer {KEY}"]


def test_malformed_request_fails_fast_without_retries():
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.LocalProtocolError("Illegal header value")

    sleeps = []
    with pytest.raises(LLMError) as exc:
        chat_json(MSG, api_key=KEY, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert exc.value.retryable is False and "GROQ_API_KEY" in str(exc.value)
    assert len(attempts) == 1 and sleeps == []
