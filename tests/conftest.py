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
        conn.execute("DROP TABLE IF EXISTS jobs, episodes CASCADE")  # pick up schema changes
        apply_schema(conn)
    return url


@pytest.fixture
def db(test_db_url):
    """A connection to the test DB with empty tables (each test starts clean)."""
    with connect(test_db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE jobs, episodes RESTART IDENTITY CASCADE")
        yield conn
