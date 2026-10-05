"""Real-podcast WER against a hand-corrected reference.

prepare(): download the episode once and cut a fixed clip (~N minutes, ending in a
silence), so every model and the human corrector use the *same* audio. With dynamic
ad insertion, a fresh stream could contain different ads. Transcribe the clip with
each Groq model, plus a local model whose output becomes the draft reference.
The draft comes from a neutral third model so correcting it doesn't anchor the
reference toward either model being compared.

score(): WER of each model vs the corrected reference.

Copyright: the clip, transcripts and reference stay local in evals/podcast/
(gitignored). Only metrics are written to evals/results/.
"""

import json
import tempfile
import time
from collections import Counter
from pathlib import Path

import jiwer
import soundfile as sf

from earshot.audio import SAMPLE_RATE, find_speech, load_audio, plan_chunks
from earshot.download import download_audio
from earshot.evals.signals import summarize
from earshot.evals.wer import corpus_wer, normalize
from earshot.local_asr import DEFAULT_LOCAL_MODEL, LocalTranscriber
from earshot.pipeline import transcribe_audio
from earshot.transcribe import Word, transcribe_chunk

PODCAST_DIR = Path("evals/podcast")
RESULTS_DIR = Path("evals/results")
GROQ_MODELS = ["whisper-large-v3", "whisper-large-v3-turbo"]
MARKER_EVERY_SECONDS = 30

INSTRUCTIONS = """\
# Reference transcript: correct this draft so it matches EXACTLY what is said in clip.flac.
# - Fix every wrong, missing or extra word. Ignore capitalization and punctuation
#   (they are normalized away before scoring).
# - Write what is actually said, including filler words if clearly spoken ("um", "you know").
# - Lines starting with # are ignored. "# m:ss" markers help you find your place in the audio.
# - The draft comes from a small local model, not from the models being compared.
"""


def local_model_name(size: str) -> str:
    return f"local-{size}"


def format_draft(words: list[Word]) -> str:
    """Draft reference: a line per sentence, with a time marker every ~30 s."""
    lines, current, next_marker = [], [], 0.0
    for w in words:
        if w.start >= next_marker:
            if current:
                lines.append(" ".join(current))
                current = []
            lines.append(f"# {int(w.start // 60)}:{int(w.start % 60):02d}")
            next_marker = (w.start // MARKER_EVERY_SECONDS + 1) * MARKER_EVERY_SECONDS
        current.append(w.text)
        if w.text.endswith((".", "?", "!")):
            lines.append(" ".join(current))
            current = []
    if current:
        lines.append(" ".join(current))
    return INSTRUCTIONS + "\n" + "\n".join(lines) + "\n"


def load_reference(path: Path) -> str:
    """Reference text with comment/marker lines removed."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return " ".join(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))


def _save_words(path: Path, words: list[Word], seconds: float, chunks: int) -> None:
    path.write_text(json.dumps({"seconds_elapsed": round(seconds, 1), "chunks": chunks,
                                "words": [w.__dict__ for w in words]}), encoding="utf-8")


def _load_words(path: Path) -> tuple[list[Word], dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Word(**w) for w in raw["words"]], raw


def prepare(name: str, audio_url: str, minutes: float = 10, *, local_model: str = DEFAULT_LOCAL_MODEL,
            base_dir: Path = PODCAST_DIR, transcribers: dict | None = None, downloader=download_audio) -> dict:
    """Idempotent: reuses an existing clip and cached transcripts, and NEVER overwrites
    reference.txt (it may hold your corrections)."""
    folder = base_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    clip_path = folder / "clip.flac"

    if not clip_path.exists():
        with tempfile.TemporaryDirectory(prefix="earshot-eval-") as tmp:
            audio = load_audio(downloader(audio_url, tmp))
        first_chunk = plan_chunks(find_speech(audio), max_seconds=minutes * 60)[0]
        sf.write(clip_path, audio[: int(first_chunk.end * SAMPLE_RATE)], SAMPLE_RATE, format="FLAC", subtype="PCM_16")
        del audio  # the full episode (~150 MB for 40 min) isn't needed once the clip is saved
    clip = load_audio(str(clip_path))

    local_name = local_model_name(local_model)
    transcribers = transcribers or {
        **{m: (lambda flac, m=m: transcribe_chunk(flac, model=m, language="en")) for m in GROQ_MODELS},
        local_name: LocalTranscriber(local_model),
    }
    timings = {}
    for model, transcriber in transcribers.items():
        cache = folder / f"{model}.words.json"
        if not cache.exists():
            started = time.time()
            words, chunks = transcribe_audio(clip, transcriber)
            _save_words(cache, words, time.time() - started, chunks)
        timings[model] = _load_words(cache)[1]["seconds_elapsed"]

    draft_path, reference_path = folder / "reference_draft.txt", folder / "reference.txt"
    draft_source = local_name if local_name in transcribers else next(iter(transcribers))
    draft = format_draft(_load_words(folder / f"{draft_source}.words.json")[0])
    draft_path.write_text(draft, encoding="utf-8")
    created_reference = not reference_path.exists()
    if created_reference:
        reference_path.write_text(draft, encoding="utf-8")

    return {"folder": str(folder), "clip_seconds": round(len(clip) / SAMPLE_RATE, 1),
            "seconds_elapsed": timings, "reference_created": created_reference}


def _top_substitutions(reference: str, hypothesis: str, n: int = 8) -> list[str]:
    out = jiwer.process_words(normalize(reference), normalize(hypothesis))
    pairs = Counter()
    for chunk in out.alignments[0]:
        if chunk.type == "substitute":
            ref_words = out.references[0][chunk.ref_start_idx:chunk.ref_end_idx]
            hyp_words = out.hypotheses[0][chunk.hyp_start_idx:chunk.hyp_end_idx]
            pairs.update(f"{r} -> {h}" for r, h in zip(ref_words, hyp_words))
    return [f"{pair} (x{count})" for pair, count in pairs.most_common(n)]


def score(name: str, *, base_dir: Path = PODCAST_DIR, results_dir: Path | None = RESULTS_DIR) -> list[dict]:
    folder = base_dir / name
    reference_path = folder / "reference.txt"
    reference = load_reference(reference_path)
    draft_path = folder / "reference_draft.txt"
    uncorrected = draft_path.exists() and reference_path.read_text(encoding="utf-8") == draft_path.read_text(encoding="utf-8")

    results = []
    for cache in sorted(folder.glob("*.words.json")):
        model = cache.name.removesuffix(".words.json")
        words, raw = _load_words(cache)
        hypothesis = " ".join(w.text for w in words)
        report = corpus_wer([reference], [hypothesis])
        signals = summarize(words)
        clip_seconds = words[-1].end if words else 0.0
        result = {
            "eval": f"podcast:{name}",
            "model": model,
            "reference_is_uncorrected_draft": uncorrected,
            "wer": report.wer,
            "ref_words": report.ref_words,
            "substitutions": report.substitutions,
            "deletions": report.deletions,
            "insertions": report.insertions,
            "top_substitutions": _top_substitutions(reference, hypothesis),
            "hallucination_signals": {k: signals[k] for k in ("compressed_run", "backward_jump", "repeated_phrase")},
            "seconds_elapsed": raw["seconds_elapsed"],
            "speed_x_realtime": round(clip_seconds / raw["seconds_elapsed"], 1) if raw["seconds_elapsed"] else None,
        }
        results.append(result)
        if results_dir:
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / f"podcast-{name}-{model}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return results
