"""Passage windowing: sizes, overlap, coverage, and robustness to Whisper's timestamp jitter."""

import random

import pytest

from earshot.passages import make_passages
from earshot.transcribe import Word


def steady(seconds: float, every: float = 0.4) -> list[Word]:
    """A word every `every` seconds, like steady speech."""
    n = int(seconds / every)
    return [Word(f"w{i}", i * every, i * every + 0.3) for i in range(n)]


def test_no_words_no_passages():
    assert make_passages([]) == []


def test_windows_are_45s_starting_every_30s():
    passages = make_passages(steady(120))
    assert [round(p.start) for p in passages] == [0, 30, 60, 90]
    # a word that starts inside the window is kept whole, so the end may pass 45 s by one word
    assert all(p.end - p.start <= 45 + 0.3 for p in passages[:-1])
    assert passages[-1].end == pytest.approx(119.9, abs=0.5)  # last one runs to the end


def test_consecutive_passages_overlap_by_about_15s():
    passages = make_passages(steady(120))
    for a, b in zip(passages, passages[1:]):
        assert a.end - b.start == pytest.approx(15, abs=1)


def test_short_transcript_is_one_passage():
    passages = make_passages(steady(20))
    assert len(passages) == 1
    assert passages[0].text.split()[0] == "w0"


def test_long_silence_does_not_create_empty_passages():
    words = steady(10) + [Word("after", 200.0, 200.4), Word("silence", 200.5, 200.9)]
    passages = make_passages(words)
    assert [p.text for p in passages][-1] == "after silence"
    assert all(p.text for p in passages)


def test_backward_timestamp_jitter_neither_drops_nor_loops():
    words = steady(100)
    words[50] = Word(words[50].text, words[50].start - 4.0, words[50].end - 4.0)  # like the NPR glitch
    passages = make_passages(words)
    covered = {t for p in passages for t in p.text.split()}
    assert covered == {w.text for w in words}


def test_every_word_is_covered_for_random_transcripts():
    rng = random.Random(7)
    for _ in range(100):
        t, words = 0.0, []
        for i in range(rng.randint(1, 400)):
            t += rng.choice([0.2, 0.3, 0.5, 2.0, 30.0, 90.0])  # mostly speech, some long pauses
            words.append(Word(f"w{i}", t, t + 0.25))
        passages = make_passages(words)
        covered = {tok for p in passages for tok in p.text.split()}
        assert covered == {w.text for w in words}
        assert [p.idx for p in passages] == list(range(len(passages)))
        assert all(a.start < b.start for a, b in zip(passages, passages[1:]))


def test_invalid_stride_is_rejected():
    with pytest.raises(ValueError):
        make_passages(steady(10), window=30, stride=45)
