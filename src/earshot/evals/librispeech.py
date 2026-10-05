"""LibriSpeech eval: WER and timestamp accuracy through the real pipeline.

73 LibriSpeech utterances (Hugging Face's official test sample) are joined into one
recording with silent gaps, then run through VAD → chunking → FLAC → Groq exactly
like a podcast. Scoring is "align by text, then measure time":
  - WER: the full reference and hypothesis texts are aligned by edit distance. No
    timestamps are involved, so timing errors can't leak into WER.
  - timestamp accuracy: for each word the alignment marks correct, how far its
    timestamp lies from the utterance it truly belongs to (what citations depend on).
Raw transcripts are cached (evals/cache, gitignored), so re-scoring costs no quota.
"""

import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import soundfile as sf
from huggingface_hub import hf_hub_download

from earshot.audio import SAMPLE_RATE, Span, encode_flac, find_speech, plan_chunks
import jiwer

from earshot.evals.wer import WerReport, normalize
from earshot.transcribe import Word, transcribe_chunk

DATASET = "hf-internal-testing/librispeech_asr_dummy"
DATASET_FILE = "clean/validation-00000-of-00001.parquet"
GAP_SECONDS = 1.0
RESULTS_DIR = Path("evals/results")
CACHE_DIR = Path("evals/cache")


@dataclass(frozen=True)
class Sample:
    id: str
    text: str
    audio: np.ndarray


def load_samples() -> list[Sample]:
    """Download (cached after the first time) and decode the 73 utterances."""
    path = hf_hub_download(DATASET, DATASET_FILE, repo_type="dataset")
    samples = []
    for row in pq.read_table(path, columns=["id", "text", "audio"]).to_pylist():
        audio, rate = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
        if rate != SAMPLE_RATE:
            raise ValueError(f"{row['id']}: expected {SAMPLE_RATE} Hz, got {rate}")
        samples.append(Sample(row["id"], row["text"], audio))
    return samples


def join_samples(samples: list[Sample], gap_seconds: float = GAP_SECONDS) -> tuple[np.ndarray, list[Span]]:
    """Concatenate utterances with silence between them; return the audio and where each one sits."""
    gap = np.zeros(int(gap_seconds * SAMPLE_RATE), dtype=np.float32)
    pieces, spans, cursor = [gap], [], len(gap)
    for s in samples:
        spans.append(Span(cursor / SAMPLE_RATE, (cursor + len(s.audio)) / SAMPLE_RATE))
        pieces += [s.audio, gap]
        cursor += len(s.audio) + len(gap)
    return np.concatenate(pieces), spans


def tokens_with_owner(samples: list[Sample], words: list[Word]):
    """Normalized tokens: reference tokens tagged with their utterance index, hypothesis
    tokens tagged with the Word they came from (one Word can yield several tokens)."""
    ref = [(tok, i) for i, s in enumerate(samples) for tok in normalize(s.text).split()]
    hyp = [(tok, w) for w in words for tok in normalize(w.text).split()]
    return ref, hyp


def score(samples: list[Sample], spans: list[Span], words: list[Word]) -> dict:
    """Align texts, then measure time. Returns WER, per-utterance errors, and timestamp offsets."""
    ref, hyp = tokens_with_owner(samples, words)
    out = jiwer.process_words(" ".join(t for t, _ in ref), " ".join(t for t, _ in hyp))

    errors = [0] * len(samples)          # S + D + I per utterance
    hyp_by_utt: list[list[str]] = [[] for _ in samples]
    offsets: list[float] = []            # seconds outside the true utterance, for correct words
    last_utt = 0
    for chunk in out.alignments[0]:
        r_idx = range(chunk.ref_start_idx, chunk.ref_end_idx)
        h_idx = range(chunk.hyp_start_idx, chunk.hyp_end_idx)
        if chunk.type == "insert":
            errors[last_utt] += len(h_idx)
            hyp_by_utt[last_utt] += [hyp[h][0] for h in h_idx]
            continue
        for k, r in enumerate(r_idx):
            utt = ref[r][1]
            last_utt = utt
            if chunk.type == "delete":
                errors[utt] += 1
                continue
            tok, word = hyp[h_idx[k]]
            hyp_by_utt[utt].append(tok)
            if chunk.type == "substitute":
                errors[utt] += 1
            else:  # equal: the word was heard correctly, so check where it was placed in time
                mid = (word.start + word.end) / 2
                span = spans[utt]
                offsets.append(max(span.start - mid, 0.0, mid - span.end))

    ref_words = out.hits + out.substitutions + out.deletions
    report = WerReport(round(out.wer, 4), ref_words, out.substitutions, out.deletions, out.insertions)
    ref_counts = [0] * len(samples)
    for _, i in ref:
        ref_counts[i] += 1
    per_utt = sorted(
        ({"id": s.id, "wer": round(errors[i] / ref_counts[i], 4) if ref_counts[i] else 0.0,
          "ref": normalize(s.text), "hyp": " ".join(hyp_by_utt[i])}
         for i, s in enumerate(samples)),
        key=lambda u: u["wer"], reverse=True,
    )
    offsets.sort()
    return {
        "report": report,
        "worst_utterances": per_utt[:5],
        "timestamps": {
            "correct_words_checked": len(offsets),
            "inside_true_utterance_rate": round(sum(o == 0 for o in offsets) / len(offsets), 4) if offsets else 0.0,
            "p95_seconds_outside": round(offsets[int(0.95 * (len(offsets) - 1))], 3) if offsets else 0.0,
            "max_seconds_outside": round(offsets[-1], 3) if offsets else 0.0,
        },
    }


def transcribe_audio(audio: np.ndarray, transcriber) -> tuple[list[Word], int]:
    """VAD → chunks → transcriber, with words offset to absolute time. Returns (words, chunk count)."""
    chunks = plan_chunks(find_speech(audio))
    words: list[Word] = []
    for span in chunks:
        result = transcriber(encode_flac(audio, span))
        words += [Word(w.text, span.start + w.start, span.start + w.end) for w in result.words]
    return words, len(chunks)


def run(model: str, transcriber=None, samples: list[Sample] | None = None,
        cache_dir: Path | None = CACHE_DIR) -> dict:
    samples = samples if samples is not None else load_samples()
    audio, spans = join_samples(samples)
    cache = cache_dir / f"librispeech-{model}.words.json" if cache_dir else None

    started = time.time()
    if cache and cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
        words, n_chunks = [Word(**w) for w in raw["words"]], raw["chunks"]
    else:
        transcriber = transcriber or (lambda flac: transcribe_chunk(flac, model=model, language="en"))
        words, n_chunks = transcribe_audio(audio, transcriber)
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"chunks": n_chunks, "words": [asdict(w) for w in words]}), encoding="utf-8")
    elapsed = time.time() - started

    scored = score(samples, spans, words)
    return {
        "dataset": f"{DATASET} ({len(samples)} utterances)",
        "model": model,
        "date": datetime.now(UTC).date().isoformat(),
        "audio_seconds": round(len(audio) / SAMPLE_RATE, 1),
        "chunks": n_chunks,
        "seconds_elapsed": round(elapsed, 1),
        **asdict(scored["report"]),
        "words": len(words),
        "timestamps": scored["timestamps"],
        "worst_utterances": scored["worst_utterances"],
    }


def save(result: dict, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"librispeech-{result['model']}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path
