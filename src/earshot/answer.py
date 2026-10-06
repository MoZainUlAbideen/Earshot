"""Answering with verified citations.

question → search (top passages) → LLM answer with [n] markers + a verbatim quote per [n]
         → critic (plain code, not an LLM): each quote must appear verbatim in passage n,
           and is located in the word timings, so the citation time is the quote's first word
         → sentences without a verified citation are removed; if nothing is left, refuse.

A deterministic verifier around a probabilistic generator: the model can invent a quote,
but a string check against the transcript can't be talked into accepting one.
"""

import json
import re
import time
from dataclasses import dataclass, field

import psycopg

from earshot.citations import locate_quote
from earshot.llm import DEFAULT_CHAT_MODEL, chat_json
from earshot.pipeline import get_transcript
from earshot.search import Hit, search

TOP_K = 5
REFUSAL = "I couldn't find a supported answer to that in these episodes."

SYSTEM = """You answer questions about podcast episodes using ONLY the numbered passages given.
Return JSON: {"found": true, "answer": "...", "citations": [{"n": 1, "quote": "..."}]}
Rules:
- Every sentence of the answer must end with one or more passage markers like [1] or [2][3].
- For every marker you use, add a citation whose "quote" is copied VERBATIM from that passage:
  6 to 25 consecutive words, exactly as they appear (it will be checked word for word).
- The quote must be the words that SUPPORT the sentence (what a speaker stated), never a
  question someone asked or an unrelated line.
- Attribute opinions to the speakers ("the guest argues ...") instead of stating them as facts.
- If the passages do not answer the question, return {"found": false, "answer": "", "citations": []}.
- Be concise: 2 to 5 sentences."""

_MARKER = re.compile(r"\[(\d+)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Citation:
    n: int
    quote: str
    episode_id: int
    title: str
    audio_url: str
    time: float  # seconds: where the quoted words start, so the player jumps here


@dataclass
class Answer:
    question: str
    text: str
    found: bool
    citations: list[Citation] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)  # citations the critic refused, and why
    removed_sentences: int = 0
    tokens: int = 0
    seconds: dict = field(default_factory=dict)
    repaired: bool = False  # True if the repair pass produced the final answer


def format_passages(hits: list[Hit]) -> str:
    return "\n\n".join(
        f'[{i}] Episode: "{h.title}" (from {int(h.start // 60)}:{int(h.start % 60):02d})\n{h.text}'
        for i, h in enumerate(hits, 1)
    )


def verify(conn: psycopg.Connection, hits: list[Hit], raw_citations: list) -> tuple[list[Citation], list[dict]]:
    """The critic: keep a citation only if its quote is verbatim in the passage it points to."""
    audio = {i: u for i, u in conn.execute(
        "SELECT id, audio_url FROM episodes WHERE id = ANY(%s)", ([h.episode_id for h in hits],))}
    transcripts: dict[int, list] = {}
    verified, rejected = [], []
    for c in raw_citations if isinstance(raw_citations, list) else []:
        n, quote = c.get("n") if isinstance(c, dict) else None, (c.get("quote") or "") if isinstance(c, dict) else ""
        if not isinstance(n, int) or not 1 <= n <= len(hits):
            rejected.append({"n": n, "quote": quote, "reason": "no such passage"})
            continue
        hit = hits[n - 1]
        if "?" in quote:
            # A question (often the host's) is verbatim but isn't evidence for a claim.
            # Existence alone can't prove support; this catches the commonest failure cheaply.
            rejected.append({"n": n, "quote": quote, "reason": "quote is a question, not a statement"})
            continue
        if hit.episode_id not in transcripts:
            transcripts[hit.episode_id] = get_transcript(conn, hit.episode_id)
        t = locate_quote(quote, transcripts[hit.episode_id], hit.start, hit.end)
        if t is None:
            rejected.append({"n": n, "quote": quote, "reason": "quote not found verbatim in passage"})
            continue
        verified.append(Citation(n, quote, hit.episode_id, hit.title, audio.get(hit.episode_id, ""), round(t, 2)))
    return verified, rejected


def keep_supported_sentences(text: str, verified_ns: set[int]) -> tuple[str, int]:
    """Drop markers that failed verification, then drop sentences left with no marker."""
    kept, removed = [], 0
    for sentence in _SENTENCE.split(text.strip()):
        markers = {int(m) for m in _MARKER.findall(sentence)}
        if markers & verified_ns:
            kept.append(_MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in verified_ns else "", sentence).replace("  ", " "))
        elif sentence:
            removed += 1
    return " ".join(kept).strip(), removed


def answer(
    conn: psycopg.Connection,
    question: str,
    *,
    embedder,
    reranker=None,
    llm=chat_json,
    model: str = DEFAULT_CHAT_MODEL,
    k: int = TOP_K,
) -> Answer:
    t0 = time.perf_counter()
    hits = search(conn, question, embedder=embedder, mode="hybrid+rerank", k=k, reranker=reranker)
    seconds = {"search": round(time.perf_counter() - t0, 2)}
    if not hits:
        return Answer(question, REFUSAL, found=False, seconds=seconds)

    t0 = time.perf_counter()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nPassages:\n\n{format_passages(hits)}"}]
    result = llm(messages, model=model, max_tokens=1500)
    tokens = result.prompt_tokens + result.completion_tokens
    data = result.data

    if not data.get("found") or not data.get("answer"):
        seconds["llm"] = round(time.perf_counter() - t0, 2)
        return Answer(question, REFUSAL, found=False, tokens=tokens, seconds=seconds)

    verified, rejected = verify(conn, hits, data.get("citations", []))
    text, removed = keep_supported_sentences(data["answer"], {c.n for c in verified})

    repaired = False
    if rejected:
        # One repair pass: tell the model which quotes failed and why. Its new answer goes
        # through the SAME deterministic critic, so safety is unchanged; it only gets a
        # second chance at quoting. Exactly one retry, so it can't loop.
        failures = "\n".join(f'- [{r["n"]}] "{r["quote"]}": {r["reason"]}' for r in rejected)
        retry = llm(messages + [
            {"role": "assistant", "content": json.dumps(data)},
            {"role": "user", "content": "These citations failed verification:\n" + failures +
             "\nReturn the full corrected JSON. Replace each failed quote with words copied EXACTLY, "
             "character for character, from the same passage, and make them a statement that supports "
             "the sentence, not a question. If no such words exist, remove that claim."},
        ], model=model, max_tokens=1500)
        tokens += retry.prompt_tokens + retry.completion_tokens
        if retry.data.get("found") and retry.data.get("answer"):
            v2, r2 = verify(conn, hits, retry.data.get("citations", []))
            text2, removed2 = keep_supported_sentences(retry.data["answer"], {c.n for c in v2})
            if len(text2) > len(text):  # keep whichever version has more verified content
                verified, text, removed, repaired = v2, text2, removed2, True
                rejected = rejected + r2  # keep the full audit trail
    seconds["llm"] = round(time.perf_counter() - t0, 2)

    if not text:
        return Answer(question, REFUSAL, found=False, rejected=rejected, removed_sentences=removed,
                      tokens=tokens, seconds=seconds, repaired=repaired)
    used = {int(m) for m in _MARKER.findall(text)}
    return Answer(question, text, found=True, citations=[c for c in verified if c.n in used],
                  rejected=rejected, removed_sentences=removed, tokens=tokens, seconds=seconds,
                  repaired=repaired)
