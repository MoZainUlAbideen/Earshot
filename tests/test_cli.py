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
    monkeypatch.setattr(cli, "run_worker", lambda conn, worker_id, once: calls.update(id=worker_id, once=once))

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
