"""CLI wiring (no real DB): commands dispatch correctly and logs never include request URLs."""

import logging
from contextlib import contextmanager

from earshot import cli


@contextmanager
def fake_connect(**kwargs):
    yield object()


def test_worker_command_runs_the_worker_and_quiets_httpx(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(cli, "run_worker", lambda conn, worker_id, once, until_empty: calls.update(id=worker_id, once=once))

    cli.main(["worker", "--once", "--id", "w-test"])

    assert calls == {"id": "w-test", "once": True}
    # httpx logs full URLs at INFO (tracking IDs, tokens in private feeds): must be muted.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_ctrl_c_stops_the_worker_cleanly(monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "apply_schema", lambda conn: None)
    monkeypatch.setattr(cli, "run_worker", interrupted)

    cli.main(["worker"])
    assert "worker stopped" in capsys.readouterr().out


def test_eval_command_does_not_need_the_database(monkeypatch, capsys):
    from earshot.evals import librispeech

    def no_db(**kwargs):
        raise AssertionError("eval must not connect to the database")

    monkeypatch.setattr(cli, "connect", no_db)
    monkeypatch.setattr(librispeech, "run", lambda model: {
        "model": model, "wer": 0.05, "ref_words": 100, "substitutions": 3, "deletions": 1,
        "insertions": 1, "audio_seconds": 60.0, "seconds_elapsed": 5.0,
        "timestamps": {"inside_true_utterance_rate": 0.99, "p95_seconds_outside": 0.0, "max_seconds_outside": 0.3}})
    monkeypatch.setattr(librispeech, "save", lambda result: "evals/results/x.json")

    cli.main(["eval", "librispeech", "--model", "whisper-large-v3-turbo"])
    assert "whisper-large-v3-turbo: WER 5.00%" in capsys.readouterr().out
