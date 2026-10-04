"""Ingestion: parsing RSS edge cases, dedup keys, and idempotent saving + enqueueing."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from earshot import ingest
from earshot.ingest import (
    EpisodeMeta,
    ingest_feed,
    make_dedup_key,
    parse_duration,
    parse_feed,
    save_episodes,
)

FEED_URL = "https://example.com/feed.xml"
SAMPLE_FEED = (Path(__file__).parent / "fixtures" / "sample_feed.xml").read_bytes()


# --- parsing ---------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3600", 3600),
        ("45:30", 2730),
        ("1:02:03", 3723),
        ("  90 ", 90),
        ("", None),
        (None, None),
        ("about an hour", None),
    ],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


def test_parse_feed_skips_items_without_audio():
    titles = [e.title for e in parse_feed(FEED_URL, SAMPLE_FEED)]
    assert "Show notes only" not in titles
    assert "Video bonus" not in titles
    assert len(titles) == 3


def test_parse_feed_sorts_newest_first():
    titles = [e.title for e in parse_feed(FEED_URL, SAMPLE_FEED)]
    assert titles == [
        "Episode 3: Scaling Laws",
        "Episode 2: No Guid Here",
        "Episode 1: The Beginning",
    ]


def test_parse_feed_extracts_fields():
    newest = parse_feed(FEED_URL, SAMPLE_FEED)[0]
    assert newest == EpisodeMeta(
        feed_url=FEED_URL,
        guid="sample-ep-3",
        title="Episode 3: Scaling Laws",
        audio_url="https://example.com/audio/ep3.mp3",
        published_at=datetime(2025, 10, 1, 10, 0, tzinfo=UTC),
        duration_seconds=3723,
    )


def test_missing_guid_falls_back_to_audio_url():
    no_guid = parse_feed(FEED_URL, SAMPLE_FEED)[1]
    assert no_guid.guid == "https://example.com/audio/ep2.mp3"


# --- dedup key -------------------------------------------------------------

def test_dedup_key_is_stable_and_feed_specific():
    assert make_dedup_key(FEED_URL, "g1") == make_dedup_key(FEED_URL, "g1")
    assert make_dedup_key(FEED_URL, "g1") != make_dedup_key(FEED_URL, "g2")
    assert make_dedup_key(FEED_URL, "g1") != make_dedup_key("https://other.com/feed", "g1")


# --- saving ----------------------------------------------------------------

def test_save_episodes_inserts_and_queues_a_job_each(db):
    result = save_episodes(db, parse_feed(FEED_URL, SAMPLE_FEED))
    assert (result.new, result.skipped) == (3, 0)
    jobs = db.execute("SELECT kind, status FROM jobs").fetchall()
    assert jobs == [("transcribe", "queued")] * 3


def test_ingesting_the_same_feed_twice_is_a_no_op(db):
    episodes = parse_feed(FEED_URL, SAMPLE_FEED)
    save_episodes(db, episodes)
    again = save_episodes(db, episodes)
    assert (again.new, again.skipped) == (0, 3)
    assert db.execute("SELECT count(*) FROM episodes").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 3


def test_ingest_feed_respects_limit(db, monkeypatch):
    monkeypatch.setattr(ingest, "fetch_feed", lambda url: SAMPLE_FEED)  # no real network
    result = ingest_feed(db, FEED_URL, limit=2)
    assert result.new == 2
    titles = {row[0] for row in db.execute("SELECT title FROM episodes")}
    assert titles == {"Episode 3: Scaling Laws", "Episode 2: No Guid Here"}


# --- fetching --------------------------------------------------------------

def test_fetch_feed_always_sets_a_timeout(monkeypatch):
    captured = {}

    class FakeResponse:
        content = b"<rss/>"

        def raise_for_status(self):
            pass

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(ingest.httpx, "get", fake_get)
    assert ingest.fetch_feed(FEED_URL) == b"<rss/>"
    assert captured["timeout"] == ingest.FETCH_TIMEOUT_SECONDS
