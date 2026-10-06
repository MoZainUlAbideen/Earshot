"""Podcast eval: draft formatting, reference loading, never overwriting corrections, scoring."""

import shutil
from pathlib import Path

from earshot.evals import podcast
from earshot.evals.podcast import format_draft, load_reference, prepare, score
from earshot.transcribe import Transcription, Word

SPEECH_SAMPLE = str(Path(__file__).parent / "fixtures" / "speech_sample.flac")
TRUTH = "Welcome to the Earshot test recording. Scaling laws describe how models improve with more data."


def fixture_downloader(url, dest_dir):
    path = Path(dest_dir) / "episode.audio"
    shutil.copy(SPEECH_SAMPLE, path)
    return str(path)


def fake(text):
    """A transcriber that 'hears' `text`, spreading words evenly across the chunk."""
    def transcribe(flac):
        tokens = text.split()
        return Transcription(text, [Word(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate(tokens)])
    return transcribe


TRANSCRIBERS = {
    "model-a": fake(TRUTH),
    "model-b": fake(TRUTH.replace("Scaling", "Sailing")),
    "local-draft": fake(TRUTH.replace("Earshot", "ear shot")),
}


def test_draft_has_markers_and_sentence_lines():
    words = [Word("Hello", 0.5, 0.8), Word("there.", 0.9, 1.2), Word("Later", 31.0, 31.3), Word("on.", 31.4, 31.6)]
    lines = format_draft(words).splitlines()
    assert lines[-4:] == ["# 0:00", "Hello there.", "# 0:31", "Later on."]


def test_reference_ignores_comment_and_marker_lines(tmp_path):
    path = tmp_path / "reference.txt"
    path.write_text("# instructions\n# 0:00\nHello there.\n\n# 0:31\nLater on.\n", encoding="utf-8")
    assert load_reference(path) == "Hello there. Later on."


def test_prepare_never_overwrites_corrections_and_reuses_caches(tmp_path, monkeypatch):
    transcribers = {"model-a": TRANSCRIBERS["model-a"], "local-draft": TRANSCRIBERS["local-draft"]}
    info = prepare("ep", "https://x/ep.mp3", local_model="draft", base_dir=tmp_path, transcribers=transcribers,
                   downloader=fixture_downloader)
    folder = tmp_path / "ep"
    assert info["reference_created"] is True
    assert (folder / "clip.flac").exists()
    assert "ear shot" in (folder / "reference.txt").read_text(encoding="utf-8")  # draft = local model

    (folder / "reference.txt").write_text(TRUTH + "\n", encoding="utf-8")  # the human's corrections
    calls = []
    counting = {k: (lambda flac, f=f: calls.append(1) or f(flac)) for k, f in transcribers.items()}
    info = prepare("ep", "https://x/ep.mp3", local_model="draft", base_dir=tmp_path, transcribers=counting,
                   downloader=lambda *a: (_ for _ in ()).throw(AssertionError("must reuse the clip")))
    assert info["reference_created"] is False
    assert (folder / "reference.txt").read_text(encoding="utf-8") == TRUTH + "\n"
    assert calls == []  # cached transcripts reused


def test_score_compares_each_model_to_the_corrected_reference(tmp_path, monkeypatch):
    prepare("ep", "https://x/ep.mp3", local_model="draft", base_dir=tmp_path, transcribers=TRANSCRIBERS, downloader=fixture_downloader)
    (tmp_path / "ep" / "reference.txt").write_text(TRUTH + "\n", encoding="utf-8")

    results = {r["model"]: r for r in score("ep", base_dir=tmp_path, results_dir=tmp_path / "results")}
    assert results["model-a"]["wer"] == 0.0
    assert results["model-b"]["substitutions"] == 1
    assert results["model-b"]["top_substitutions"] == ["scaling -> sailing (x1)"]
    assert results["model-a"]["reference_is_uncorrected_draft"] is False
    assert (tmp_path / "results" / "podcast-ep-model-a.json").exists()


def test_score_warns_when_reference_was_never_corrected(tmp_path, monkeypatch):
    prepare("ep", "https://x/ep.mp3", local_model="draft", base_dir=tmp_path, transcribers=TRANSCRIBERS, downloader=fixture_downloader)
    results = score("ep", base_dir=tmp_path, results_dir=None)
    assert all(r["reference_is_uncorrected_draft"] for r in results)


def test_barely_corrected_reference_is_flagged_as_anchored(tmp_path):
    """Regression: a reference that keeps 99%+ of the draft measures agreement with
    the draft model, not accuracy, even if the human did change a few words."""
    long_truth = " ".join(["word"] * 300)
    transcribers = {"local-draft": fake(long_truth), "model-a": fake(long_truth)}
    prepare("ep", "https://x/ep.mp3", local_model="draft", base_dir=tmp_path,
            transcribers=transcribers, downloader=fixture_downloader)
    ref = tmp_path / "ep" / "reference.txt"
    ref.write_text(ref.read_text(encoding="utf-8") + "fixed fixed\n", encoding="utf-8")  # 2 inserted words

    result = score("ep", base_dir=tmp_path, results_dir=None)[0]
    assert result["reference_edits_from_draft"] == 2
    assert result["reference_is_uncorrected_draft"] is True  # 2/300 < 1%
