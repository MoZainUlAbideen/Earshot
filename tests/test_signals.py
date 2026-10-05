"""Hallucination signals on hand-built word lists."""

from earshot.evals.signals import (
    backward_jumps,
    compressed_runs,
    find_suspects,
    repeated_phrases,
    summarize,
)
from earshot.transcribe import Word


def speech(text: str, start: float = 0.0, step: float = 0.3) -> list[Word]:
    """Normal-looking words: 0.25 s each, 0.3 s apart."""
    return [Word(t, start + i * step, start + i * step + 0.25) for i, t in enumerate(text.split())]


def test_clean_speech_raises_nothing():
    words = speech("this is a perfectly ordinary sentence with nothing odd about it")
    assert find_suspects(words) == []


def test_single_short_word_is_normal_but_a_run_is_flagged():
    words = speech("so I said", 0) + [Word("a", 1.0, 1.02)] + speech("thing", 1.1)
    assert compressed_runs(words) == []
    words = speech("so I said", 0) + [Word(t, 1.0 + i * 0.02, 1.0 + i * 0.02 + 0.02)
                                       for i, t in enumerate(["Andrey", "Kuvshinov,", "Father"])]
    assert compressed_runs(words) == [(3, 6)]


def test_small_jitter_is_ignored_but_a_big_backward_jump_is_flagged():
    jitter = [Word("a", 1.00, 1.2), Word("b", 0.95, 1.3)]  # 0.05 s back: normal
    assert backward_jumps(jitter) == []
    jump = [Word("shape.", 897.6, 897.7), Word("taking", 893.5, 893.9)]  # 4 s back
    assert backward_jumps(jump) == [1]


def test_repeated_phrase_is_flagged_but_short_repeats_are_not():
    assert repeated_phrases(speech("I think I think we should go")) == []  # 2 words: normal
    hits = repeated_phrases(speech("we will tell you we will tell you the truth"))
    assert hits == [(0, 4)]


def test_repeat_detection_ignores_case_and_punctuation():
    assert repeated_phrases(speech("No gray areas. no gray areas, at all")) == [(0, 3)]


def test_summary_counts_and_rates():
    words = speech("we will tell you we will tell you", 0) + speech("fine", 3600)  # spans ~1 hour
    result = summarize(words)
    assert (result["compressed_run"], result["backward_jump"], result["repeated_phrase"]) == (0, 0, 1)
    assert result["suspects_per_hour"] == 1.0
    assert result["suspects"][0]["at"] == "0:00"
