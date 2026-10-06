"""Groq chat client returning JSON (eval question generation now; answering in M5).

Free tier for the gpt-oss/qwen models (checked 2026-10-06): 30 RPM, 8K tokens/min,
200K tokens/day. gpt-oss is a reasoning model, and its hidden reasoning tokens count too,
so reasoning_effort defaults to "low". The daily token cap is a product constraint
(~40-60 answers/day at 3-5K tokens each).

Same principles as the Whisper client: timeout, errors classified, 429 waits for
`retry-after` (capped) and retries a few times, and the key never appears in errors.
"""

import json
import time
from dataclasses import dataclass

import httpx

from earshot.transcribe import RateLimited, get_api_key

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_CHAT_MODEL = "openai/gpt-oss-120b"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
MAX_RETRIES = 4
MAX_WAIT_SECONDS = 65.0  # the per-minute token budget refills within a minute


class LLMError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ChatResult:
    data: dict
    prompt_tokens: int
    completion_tokens: int


def chat_json(
    messages: list[dict],
    *,
    model: str = DEFAULT_CHAT_MODEL,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    reasoning_effort: str | None = "low",
    api_key: str | None = None,
    client: httpx.Client | None = None,
    sleep=time.sleep,
) -> ChatResult:
    """Send a chat request that must answer with a JSON object; return it parsed."""
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if reasoning_effort and model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = reasoning_effort
    headers = {"Authorization": f"Bearer {api_key or get_api_key()}"}

    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = http.post(GROQ_CHAT_URL, headers=headers, json=body)
            except httpx.TransportError as e:
                if attempt == MAX_RETRIES:
                    raise LLMError(f"network error talking to Groq: {type(e).__name__}", retryable=True) from None
                sleep(2 ** attempt)
                continue
            if response.status_code == 429:
                wait = _retry_after(response)
                if attempt == MAX_RETRIES or wait > MAX_WAIT_SECONDS:
                    raise RateLimited(wait)  # e.g. the daily token budget is spent: let the caller decide
                sleep(wait)
                continue
            if response.status_code >= 500 and attempt < MAX_RETRIES:
                sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                raise LLMError(f"Groq chat returned {response.status_code}: {_message(response)}",
                               retryable=response.status_code >= 500)
            payload = response.json()
            content = payload["choices"][0]["message"]["content"] or ""
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                raise LLMError("model did not return valid JSON", retryable=True) from None
            usage = payload.get("usage", {})
            return ChatResult(data, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        raise LLMError("retries exhausted", retryable=True)  # pragma: no cover (loop always returns/raises)
    finally:
        if client is None:
            http.close()


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return 10.0


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return response.text[:300]
