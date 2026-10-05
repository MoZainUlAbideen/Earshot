"""Shared fixtures. DB tests run against a separate `earshot_test` database,
so they never touch dev data."""

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from earshot.db import apply_schema, connect, get_database_url

TEST_DB_NAME = "earshot_test"


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Create the test database if needed and give it a fresh schema, once per run."""
    dev_url = get_database_url()
    try:
        # CREATE DATABASE can't run inside a transaction, hence autocommit.
        with connect(dev_url, autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
            ).fetchone()
            if not exists:
                conn.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME))
                )
    except psycopg.OperationalError as e:
        pytest.fail(f"Can't reach Postgres. Is it running? `docker compose up -d --wait`\n{e}")

    url = make_conninfo(dev_url, dbname=TEST_DB_NAME)
    with connect(url, autocommit=True) as conn:
        # Wipe everything so schema changes (incl. new tables) are always picked up.
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        apply_schema(conn)
    return url


@pytest.fixture
def db(test_db_url):
    """A connection to the test DB with empty tables (each test starts clean)."""
    with connect(test_db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE chunks, jobs, episodes RESTART IDENTITY CASCADE")
        yield conn


@pytest.fixture
def make_episode(db):
    """Factory: insert an episode and return its id. Keys are unique unless given."""
    counter = 0

    def _make(dedup_key: str | None = None) -> int:
        nonlocal counter
        counter += 1
        return db.execute(
            """
            INSERT INTO episodes (feed_url, guid, title, audio_url, dedup_key)
            VALUES ('https://example.com/feed.xml', %(guid)s, %(guid)s,
                    'https://example.com/ep.mp3', %(key)s)
            RETURNING id
            """,
            {"guid": f"guid-{counter}", "key": dedup_key or f"key-{counter}"},
        ).fetchone()[0]

    return _make
