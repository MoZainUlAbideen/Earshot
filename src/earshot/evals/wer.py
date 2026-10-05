"""Word error rate with Whisper's English text normalizer.

Normalization matters: LibriSpeech references are "MISTER QUILTER", Whisper writes
"Mr. Quilter". Raw comparison counts those as errors (100% WER on an identical
sentence); normalized, they match. We only want to count real mistakes.
"""

from dataclasses import dataclass

import jiwer
from whisper_normalizer.english import EnglishTextNormalizer

_normalizer = EnglishTextNormalizer()


def normalize(text: str) -> str:
    return " ".join(_normalizer(text).split())


@dataclass(frozen=True)
class WerReport:
    wer: float
    ref_words: int
    substitutions: int
    deletions: int
    insertions: int


def corpus_wer(references: list[str], hypotheses: list[str]) -> WerReport:
    """Corpus-level WER: total errors / total reference words (not a mean of per-line WERs,
    which would let short sentences dominate)."""
    refs = [normalize(r) for r in references]
    hyps = [normalize(h) for h in hypotheses]
    pairs = [(r, h) for r, h in zip(refs, hyps) if r]  # jiwer rejects empty references
    out = jiwer.process_words([r for r, _ in pairs], [h for _, h in pairs])
    ref_words = out.hits + out.substitutions + out.deletions
    return WerReport(
        wer=round(out.wer, 4),
        ref_words=ref_words,
        substitutions=out.substitutions,
        deletions=out.deletions,
        insertions=out.insertions,
    )
