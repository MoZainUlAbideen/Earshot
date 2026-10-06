"""Quote verification and location: tolerant of formatting, strict about wording."""

import pytest

from earshot.citations import locate_quote, normalize, quote_in_text
from earshot.transcribe import Word

PASSAGE = "So the big change last year was agentic systems. You don’t just chat; the model takes actions."


def words_of(text: str, start: float = 0.0) -> list[Word]:
    return [Word(w, start + i * 0.5, start + i * 0.5 + 0.4) for i, w in enumerate(text.split())]


def test_normalize_handles_case_punctuation_and_curly_apostrophes():
    assert normalize("You DON’T just chat!") == ["you", "don't", "just", "chat"]


def test_verbatim_quote_with_different_formatting_is_accepted():
    assert quote_in_text("the big change last year was agentic systems", PASSAGE)
    assert quote_in_text("you don't just chat, the model takes actions", PASSAGE)


def test_paraphrase_or_invented_quote_is_rejected():
    assert not quote_in_text("the major shift last year was agentic systems", PASSAGE)
    assert not quote_in_text("the model refuses to take any actions", PASSAGE)


def test_too_short_quotes_are_rejected():
    assert not quote_in_text("agentic systems", PASSAGE)


def test_locate_returns_the_exact_time_of_the_first_quoted_word():
    words = words_of(PASSAGE, 100)
    # "agentic" is word index 7 -> 100 + 7 * 0.5
    assert locate_quote("agentic systems. You don't just", words, 100, 120) == pytest.approx(103.5)


def test_locate_only_searches_inside_the_cited_passage():
    words = words_of(PASSAGE, 0) + words_of(PASSAGE, 600)
    assert locate_quote("the model takes actions", words, 590, 640) >= 600


def test_locate_returns_none_for_quotes_not_in_the_transcript():
    assert locate_quote("this sentence was never said on air", words_of(PASSAGE), 0, 60) is None
