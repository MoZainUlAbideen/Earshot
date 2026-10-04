"""Postgres connection and schema setup."""

import os
from importlib.resources import files

import psycopg
from dotenv import load_dotenv


def get_database_url() -> str:
    """Read DATABASE_URL from the environment (or .env), failing fast if it's missing."""
    load_dotenv()  # real environment variables win over .env (override=False)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return url


CONNECT_TIMEOUT_SECONDS = 5


def connect(url: str | None = None, **kwargs) -> psycopg.Connection:
    """Connect with a timeout, so an unreachable DB raises instead of hanging forever."""
    kwargs.setdefault("connect_timeout", CONNECT_TIMEOUT_SECONDS)
    return psycopg.connect(url or get_database_url(), **kwargs)


def apply_schema(conn: psycopg.Connection) -> None:
    """Create tables and indexes. Idempotent: safe to run on every startup."""
    sql = files("earshot").joinpath("schema.sql").read_text(encoding="utf-8")
    with conn.transaction():
        conn.execute(sql)
