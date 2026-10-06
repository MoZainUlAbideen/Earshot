"""Data transfer: copies everything with ids intact, verifies, recomputes generated columns,
resets identity counters, and refuses a non-empty target."""

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from earshot.db import connect
from earshot.embed import embed_pending
from earshot.index import index_new_episodes
from earshot.transfer import TargetNotEmpty, copy_all
from search_helpers import TALK, HashEmbedder, add_transcript

TARGET_DB = "earshot_test_target"


@pytest.fixture
def target(test_db_url):
    """A second, empty database to copy into."""
    with connect(test_db_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TARGET_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TARGET_DB)))
    with connect(make_conninfo(test_db_url, dbname=TARGET_DB), autocommit=True) as conn:
        yield conn


@pytest.fixture
def source(db, make_episode):
    add_transcript(db, make_episode, TALK)
    add_transcript(db, make_episode, TALK[:2])
    index_new_episodes(db)
    embed_pending(db, HashEmbedder())
    return db


def test_copies_every_table_with_ids_and_checksums_matching(source, target):
    report = copy_all(source, target)
    assert report["episodes"][0] == 2
    assert report["passages"][0] == source.execute("SELECT count(*) FROM passages").fetchone()[0]
    assert target.execute("SELECT count(embedding) FROM passages").fetchone()[0] == report["passages"][0]


def test_generated_search_column_is_recomputed_on_the_target(source, target):
    copy_all(source, target)
    hits = target.execute("SELECT count(*) FROM passages WHERE tsv @@ plainto_tsquery('english', 'vector databases')").fetchone()[0]
    assert hits > 0


def test_new_rows_on_the_target_do_not_reuse_copied_ids(source, target):
    copy_all(source, target)
    max_id = target.execute("SELECT max(id) FROM episodes").fetchone()[0]
    new_id = target.execute(
        "INSERT INTO episodes (feed_url, guid, title, audio_url, dedup_key) VALUES ('f','g','t','a','new') RETURNING id"
    ).fetchone()[0]
    assert new_id > max_id


def test_refuses_to_copy_into_a_non_empty_target(source, target):
    copy_all(source, target)
    with pytest.raises(TargetNotEmpty):
        copy_all(source, target)
    assert target.execute("SELECT count(*) FROM episodes").fetchone()[0] == 2  # nothing duplicated
