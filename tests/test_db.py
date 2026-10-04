"""The schema's guarantees: dedup, idempotent enqueue, valid statuses, sane defaults."""

import psycopg
import pytest
from psycopg import errors

from earshot import db as db_module
from earshot.db import apply_schema, get_database_url


def insert_episode(conn: psycopg.Connection, dedup_key: str = "key-1") -> int:
    return conn.execute(
        """
        INSERT INTO episodes (feed_url, guid, title, audio_url, dedup_key)
        VALUES ('https://example.com/feed.xml', 'guid-1', 'Ep 1',
                'https://example.com/ep1.mp3', %s)
        RETURNING id
        """,
        (dedup_key,),
    ).fetchone()[0]


def test_missing_database_url_fails_fast(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db_module, "load_dotenv", lambda: None)  # don't re-read .env
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_database_url()


def test_connect_always_sets_a_timeout(monkeypatch):
    captured = {}
    monkeypatch.setattr(db_module.psycopg, "connect", lambda url, **kw: captured.update(kw))
    db_module.connect("postgresql://x@127.0.0.1/x")
    assert captured["connect_timeout"] == db_module.CONNECT_TIMEOUT_SECONDS


def test_apply_schema_is_idempotent(db):
    apply_schema(db)  # already applied by the fixture; a second run must not fail
    tables = {
        row[0]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {"episodes", "jobs"} <= tables


def test_duplicate_episode_is_rejected(db):
    insert_episode(db, dedup_key="same")
    with pytest.raises(errors.UniqueViolation):
        insert_episode(db, dedup_key="same")


def test_duplicate_job_is_rejected(db):
    episode_id = insert_episode(db)
    db.execute("INSERT INTO jobs (episode_id, kind) VALUES (%s, 'transcribe')", (episode_id,))
    with pytest.raises(errors.UniqueViolation):
        db.execute("INSERT INTO jobs (episode_id, kind) VALUES (%s, 'transcribe')", (episode_id,))


def test_invalid_job_status_is_rejected(db):
    episode_id = insert_episode(db)
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO jobs (episode_id, kind, status) VALUES (%s, 'transcribe', 'banana')",
            (episode_id,),
        )


def test_new_job_defaults(db):
    episode_id = insert_episode(db)
    status, attempts, max_attempts = db.execute(
        """
        INSERT INTO jobs (episode_id, kind) VALUES (%s, 'transcribe')
        RETURNING status, attempts, max_attempts
        """,
        (episode_id,),
    ).fetchone()
    assert (status, attempts, max_attempts) == ("queued", 0, 3)
