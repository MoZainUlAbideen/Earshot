"""Eval plumbing: normalization, corpus WER, joining utterances, assigning words by time.
No API calls: a fake transcriber stands in for Groq."""

from pathlib import Path

import numpy as np
import pytest

from earshot.audio import SAMPLE_RATE, Span, load_audio
from earshot import pipeline
from earshot.evals import librispeech
from earshot.evals.librispeech import Sample, join_samples, score
from earshot.evals.wer import corpus_wer, normalize
from earshot.transcribe import Transcription, Word

SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")


# --- WER ----------------------------------------------------------------------------

def test_normalizer_removes_formatting_differences():
    assert normalize("MISTER QUILTER IS THE APOSTLE") == normalize("Mr. Quilter is the apostle.")
    assert normalize("HE HAD TWENTY ONE DOLLARS") == normalize("He had $21.")


def test_normalizer_merges_counting_words_into_one_number():
    """Documented quirk: changes the word count, so WER denominators shift with numbers."""
    assert normalize("one two three") == "123"


def test_perfect_transcript_scores_zero():
    assert corpus_wer(["HELLO WORLD"], ["Hello, world!"]).wer == 0.0


def test_wer_counts_each_error_type():
    report = corpus_wer(["alpha bravo charlie delta"], ["alpha bravo zulu delta echo"])
    # 'charlie'->'zulu' is a substitution, 'echo' an insertion: 2 errors / 4 reference words
    assert (report.substitutions, report.deletions, report.insertions, report.ref_words) == (1, 0, 1, 4)
    assert report.wer == pytest.approx(0.5)


def test_wer_total_is_right_even_when_alignment_is_ambiguous():
    # Two equally minimal alignments exist (sub+del+ins, or 3 subs); only the total is defined.
    report = corpus_wer(["the cat sat on the mat"], ["the bat sat on mat today"])
    assert report.substitutions + report.deletions + report.insertions == 3
    assert report.wer == pytest.approx(3 / 6)


def test_corpus_wer_weights_by_words_not_by_sentence():
    # One wrong word in a 1-word sentence + a perfect 9-word sentence = 1/10, not mean(100%, 0%) = 50%.
    # (Avoid spelled-out numbers here: the normalizer merges "one two three" into "123".)
    nine = "alpha bravo charlie delta echo foxtrot golf hotel india"
    report = corpus_wer(["yes", nine], ["no", nine])
    assert report.wer == pytest.approx(0.1)


# --- joining and assigning -----------------------------------------------------------

def sample(seconds: float, text: str = "x") -> Sample:
    return Sample(id=text, text=text, audio=np.ones(int(seconds * SAMPLE_RATE), dtype=np.float32))


def test_join_places_each_utterance_between_gaps():
    audio, spans = join_samples([sample(2), sample(3)], gap_seconds=1)
    assert spans == [Span(1.0, 3.0), Span(4.0, 7.0)]
    assert len(audio) == 8 * SAMPLE_RATE  # gap 1 + 2 + gap 1 + 3 + gap 1


SPANS = [Span(1, 3), Span(4, 7)]
TWO = [sample(2, "hello there"), sample(3, "good morning world")]


def test_perfect_words_score_zero_and_sit_inside_their_utterances():
    words = [Word("Hello", 1.2, 1.5), Word("there.", 1.6, 2.0),
             Word("Good", 4.2, 4.5), Word("morning,", 4.6, 5.0), Word("world.", 5.1, 5.5)]
    result = score(TWO, SPANS, words)
    assert result["report"].wer == 0.0
    assert result["timestamps"]["inside_true_utterance_rate"] == 1.0


def test_early_timestamp_does_not_count_as_a_wer_error():
    """Regression: Whisper often starts the first word after a pause early (in the
    silence). Correctly heard words must not become fake deletions/insertions."""
    words = [Word("Hello", 1.2, 1.5), Word("there.", 1.6, 2.0),
             Word("Good", 3.4, 3.8),  # mid 3.6: 0.4 s before its utterance, nearer the previous one
             Word("morning,", 4.6, 5.0), Word("world.", 5.1, 5.5)]
    result = score(TWO, SPANS, words)
    assert result["report"].wer == 0.0
    assert result["timestamps"]["max_seconds_outside"] == pytest.approx(0.4)
    assert result["timestamps"]["inside_true_utterance_rate"] == pytest.approx(4 / 5)


def test_errors_are_attributed_to_the_right_utterance():
    words = [Word("Hello", 1.2, 1.5), Word("there.", 1.6, 2.0),
             Word("Good", 4.2, 4.5), Word("evening", 4.6, 5.0)]  # 'morning'->'evening', 'world' missing
    result = score(TWO, SPANS, words)
    report = result["report"]
    assert (report.substitutions, report.deletions, report.insertions) == (1, 1, 0)
    worst = result["worst_utterances"][0]
    assert (worst["id"], worst["hyp"]) == ("good morning world", "good evening")
    assert worst["wer"] == pytest.approx(2 / 3, abs=1e-4)  # stored rounded to 4 decimals


# --- end to end with a fake transcriber -------------------------------------------------

def test_run_end_to_end_with_perfect_fake_transcriber(monkeypatch):
    """Real audio + real VAD/chunking; the fake 'transcribes' each chunk perfectly by
    returning each utterance's text at its true position relative to the chunk."""
    fixture = load_audio(SPEECH_SAMPLE)
    texts = ["Welcome to the Earshot test recording.",
             "Scaling laws describe how models improve with more data.",
             "Every answer cites the exact moment it was said."]
    cuts = [(0.9, 3.4), (6.9, 10.4), (13.8, 16.9)]
    samples = [Sample(f"s{i}", t, fixture[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)])
               for i, (t, (a, b)) in enumerate(zip(texts, cuts))]
    _, spans = join_samples(samples)

    chunk_starts = []
    real_encode = pipeline.encode_flac

    def spy_encode(audio, span):
        chunk_starts.append(span.start)
        return real_encode(audio, span)

    def perfect(flac):
        offset = chunk_starts[-1]
        words = []
        for text, span in zip(texts, spans):
            n = len(text.split())
            step = span.duration / (n + 1)
            words += [Word(tok, span.start - offset + step * (k + 0.5), span.start - offset + step * (k + 1))
                      for k, tok in enumerate(text.split())]
        return Transcription(" ".join(texts), words)

    monkeypatch.setattr(pipeline, "encode_flac", spy_encode)
    result = librispeech.run("fake-model", transcriber=perfect, samples=samples, cache_dir=None)

    assert result["wer"] == 0.0
    assert result["timestamps"]["inside_true_utterance_rate"] == 1.0
    assert result["chunks"] == 1
    assert result["ref_words"] == 24


def test_cached_transcript_is_reused_without_calling_the_api(tmp_path):
    samples = [sample(2, "hello")]  # silence-free ones: VAD may find speech or not; cache decides
    calls = []

    def fake(flac):
        calls.append(1)
        return Transcription("hello", [Word("hello", 0.0, 0.5)])

    librispeech.run("m", transcriber=fake, samples=samples, cache_dir=tmp_path)
    first_calls = len(calls)
    librispeech.run("m", transcriber=fake, samples=samples, cache_dir=tmp_path)
    assert len(calls) == first_calls  # second run read the cache
    assert (tmp_path / "librispeech-m.words.json").exists()
