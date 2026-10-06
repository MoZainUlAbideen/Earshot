"""Passages: the unit of search. A transcript becomes overlapping ~45 s windows.

Why windows: whole episodes are too coarse to cite, single words too small to match a
question. ~45 s (~110 words) holds a complete thought and gives a precise timestamp.
Why overlap (stride 30 s → 15 s overlap): an answer that straddles a boundary still
lands whole inside some window.

Windows are contiguous runs of *words* (not raw time slices), so Whisper's small
backward timestamp jitter can't drop or duplicate words.
"""

from dataclasses import dataclass

from earshot.transcribe import Word

WINDOW_SECONDS = 45.0
STRIDE_SECONDS = 30.0


@dataclass(frozen=True)
class Passage:
    idx: int
    start: float   # seconds from episode start: where a citation will jump to
    end: float
    text: str


def make_passages(
    words: list[Word], window: float = WINDOW_SECONDS, stride: float = STRIDE_SECONDS
) -> list[Passage]:
    """Split time-ordered words into overlapping windows. Every word lands in at least
    one passage; the last passage always runs to the final word (no tiny tail)."""
    if stride <= 0 or window < stride:
        raise ValueError("need 0 < stride <= window, or words would be skipped")
    passages: list[Passage] = []
    i = 0
    while i < len(words):
        anchor = words[i].start
        j = i
        while j < len(words) and words[j].start < anchor + window:
            j += 1
        j = max(j, i + 1)  # always take at least one word
        span = words[i:j]
        passages.append(Passage(
            idx=len(passages),
            start=span[0].start,
            end=max(w.end for w in span),
            text=" ".join(w.text for w in span),
        ))
        if j >= len(words):
            break
        # next window starts at the first word at least `stride` later (and always moves forward)
        k = i + 1
        while k < j and words[k].start < anchor + stride:
            k += 1
        i = k
    return passages
