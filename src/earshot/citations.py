"""Citation checking: does a quote really appear in a passage, and exactly when was it said?

This is production code, so it uses a small normalizer (case, punctuation, curly quotes)
rather than the dev-only Whisper normalizer used for WER. A quote is supposed to be
verbatim, so only formatting differences need forgiving, not paraphrases.
"""

import re
import unicodedata

from earshot.transcribe import Word

MIN_QUOTE_TOKENS = 4  # shorter quotes are too ambiguous to anchor a citation

_PUNCT = re.compile(r"[^\w\s']")


def normalize(text: str) -> list[str]:
    """Lowercase tokens with punctuation removed; curly apostrophes made straight."""
    text = unicodedata.normalize("NFKC", text).replace("’", "'").replace("‘", "'").lower()
    return [t.strip("'") for t in _PUNCT.sub(" ", text).split() if t.strip("'")]


def quote_in_text(quote: str, text: str) -> bool:
    needle, hay = normalize(quote), normalize(text)
    if len(needle) < MIN_QUOTE_TOKENS:
        return False
    return any(hay[i:i + len(needle)] == needle for i in range(len(hay) - len(needle) + 1))


def locate_quote(quote: str, words: list[Word], start: float, end: float) -> float | None:
    """Start time of the quote's first word, searching only words inside [start, end].
    Returns None if the quote isn't there verbatim (after normalization)."""
    needle = normalize(quote)
    if len(needle) < MIN_QUOTE_TOKENS:
        return None
    tokens = [(tok, w.start) for w in words if start - 1 <= w.start <= end + 1 for tok in normalize(w.text)]
    hay = [t for t, _ in tokens]
    for i in range(len(hay) - len(needle) + 1):
        if hay[i:i + len(needle)] == needle:
            return tokens[i][1]
    return None
