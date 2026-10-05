"""Hallucination signals: cheap, reference-free checks for impossible transcripts.

Most episodes have no reference transcript, so WER can't be computed. But some
patterns can't happen in real speech. They don't prove an error; they say where
to listen:
  - compressed runs: >= 3 consecutive words each shorter than 0.05 s (a single
    short word is normal in fast speech; a run of them means made-up timings)
  - backward jumps: a word starting > 0.5 s before the previous one (smaller
    jitter is normal Whisper behaviour)
  - repeated phrases: the same >= 3-word phrase twice in a row (people do repeat
    short bits like "I think, I think")
Thresholds were chosen from data: clean LibriSpeech has 0-4 words under 0.03 s
and no runs; the NPR episode has 46 scattered ones plus a run at a known glitch.
"""

import re

from earshot.transcribe import Word

TINY_WORD_SECONDS = 0.05
MIN_RUN = 3
BACKWARD_JUMP_SECONDS = 0.5
MIN_REPEAT_WORDS = 3
MAX_REPEAT_WORDS = 8


def _norm(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def compressed_runs(words: list[Word]) -> list[tuple[int, int]]:
    """(start, end) index ranges of >= MIN_RUN consecutive tiny words."""
    runs, start = [], None
    for i, w in enumerate(words + [Word("", 0, 1)]):  # sentinel closes a trailing run
        if w.end - w.start < TINY_WORD_SECONDS:
            start = i if start is None else start
        else:
            if start is not None and i - start >= MIN_RUN:
                runs.append((start, i))
            start = None
    return runs


def backward_jumps(words: list[Word]) -> list[int]:
    """Indexes of words that start more than BACKWARD_JUMP_SECONDS before the previous word."""
    return [i for i in range(1, len(words))
            if words[i - 1].start - words[i].start > BACKWARD_JUMP_SECONDS]


def repeated_phrases(words: list[Word]) -> list[tuple[int, int]]:
    """(start, length) of phrases immediately repeated, e.g. 'a b c a b c' -> (0, 3).
    Longest repeat wins at each position; overlapping hits are skipped."""
    tokens = [_norm(w.text) for w in words]
    hits, i = [], 0
    while i < len(tokens):
        for n in range(MAX_REPEAT_WORDS, MIN_REPEAT_WORDS - 1, -1):
            a, b = tokens[i:i + n], tokens[i + n:i + 2 * n]
            if len(b) == n and a == b and all(a):
                hits.append((i, n))
                i += 2 * n
                break
        else:
            i += 1
    return hits


def find_suspects(words: list[Word], context: int = 4) -> list[dict]:
    """All signals as suspect regions, sorted by time, each with a snippet to listen for."""
    found = []
    for start, end in compressed_runs(words):
        found.append(("compressed_run", start, end))
    for i in backward_jumps(words):
        found.append(("backward_jump", i - 1, i + 1))
    for start, n in repeated_phrases(words):
        found.append(("repeated_phrase", start, start + 2 * n))
    suspects = []
    for kind, a, b in sorted(found, key=lambda f: f[1]):
        lo, hi = max(0, a - context), min(len(words), b + context)
        suspects.append({
            "kind": kind,
            "at_seconds": round(words[a].start, 2),
            "at": f"{int(words[a].start // 60)}:{int(words[a].start % 60):02d}",
            "snippet": " ".join(w.text for w in words[lo:hi]),
        })
    return suspects


def summarize(words: list[Word]) -> dict:
    suspects = find_suspects(words)
    hours = (words[-1].end - words[0].start) / 3600 if words else 0
    counts = {k: sum(s["kind"] == k for s in suspects)
              for k in ("compressed_run", "backward_jump", "repeated_phrase")}
    return {
        "words": len(words),
        **counts,
        "suspects_per_hour": round(len(suspects) / hours, 1) if hours else 0.0,
        "suspects": suspects,
    }
