"""Copy all data from one Earshot database to another (local → Neon), verified.

Uses our own schema (apply_schema) on the target, not pg_dump: Neon runs PostgreSQL 18 while
local runs 17, and dump tools should match the newer server. Rows stream with COPY (bulk)
in foreign-key order, keeping original ids so the links stay intact; generated columns
(passages.tsv) are recomputed by the target. Afterwards identity counters are reset and
every table is checked by row count AND a content checksum on both sides.

Refuses a non-empty target: copying twice would duplicate or mix data.
"""

import psycopg

from earshot.db import apply_schema

# (table, columns to copy, checksum expression). Generated columns are left out.
TABLES = [
    ("episodes", "id, feed_url, guid, title, audio_url, published_at, duration_seconds, dedup_key, created_at",
     "id::text || dedup_key || title"),
    ("jobs", "id, episode_id, kind, status, attempts, lease, max_attempts, run_after, locked_at, locked_by, "
             "last_error, created_at, updated_at",
     "id::text || episode_id::text || kind || status"),
    ("chunks", "id, episode_id, idx, start_s, end_s, text, words, model, audio_sha256, created_at",
     "id::text || md5(words::text) || audio_sha256"),
    ("passages", "id, episode_id, idx, start_s, end_s, text, embedding, embed_model",
     "id::text || md5(text) || coalesce(md5(embedding::text), '')"),
]


class TargetNotEmpty(Exception):
    pass


def fingerprint(conn: psycopg.Connection, table: str, expr: str) -> tuple[int, str]:
    """(row count, md5 over every row in id order)."""
    return conn.execute(f"SELECT count(*), coalesce(md5(string_agg({expr}, '|' ORDER BY id)), '') FROM {table}").fetchone()


def copy_all(source: psycopg.Connection, target: psycopg.Connection) -> dict[str, tuple[int, str]]:
    """Copy every table and verify. Returns {table: (rows, checksum)}; raises on any mismatch."""
    apply_schema(target)
    non_empty = [t for t, _, _ in TABLES if target.execute(f"SELECT EXISTS (SELECT 1 FROM {t})").fetchone()[0]]
    if non_empty:
        raise TargetNotEmpty(f"target already has data in {non_empty}; refusing to copy over it")

    with target.transaction():
        for table, cols, _ in TABLES:
            with source.cursor().copy(f"COPY {table} ({cols}) TO STDOUT") as out, \
                 target.cursor().copy(f"COPY {table} ({cols}) FROM STDIN") as inp:
                for block in out:
                    inp.write(block)
            # Identity counters: the next new row must not reuse a copied id.
            target.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), coalesce(max(id), 0) + 1, false) FROM {table}")

    report = {}
    for table, _, expr in TABLES:
        src, dst = fingerprint(source, table, expr), fingerprint(target, table, expr)
        if src != dst:
            raise RuntimeError(f"{table}: verification failed (source {src[0]} rows, target {dst[0]} rows, "
                               f"checksums {'match' if src[1] == dst[1] else 'differ'})")
        report[table] = dst
    return report
