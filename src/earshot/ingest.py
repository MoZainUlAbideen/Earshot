"""Ingestion: RSS feed -> episodes table -> 'transcribe' jobs.

We store only the publisher's audio URL (stream, never rehost) plus metadata.
Re-running ingestion on the same feed is a no-op (dedup_key + ON CONFLICT).
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import httpx
import psycopg

from earshot.queue import enqueue

FETCH_TIMEOUT_SECONDS = 20
USER_AGENT = "Earshot/0.1 (+https://github.com/MoZainUlAbideen/Earshot)"
TRANSCRIBE = "transcribe"


@dataclass(frozen=True)
class EpisodeMeta:
    feed_url: str
    guid: str
    title: str
    audio_url: str
    published_at: datetime | None
    duration_seconds: int | None

    @property
    def dedup_key(self) -> str:
        return make_dedup_key(self.feed_url, self.guid)


@dataclass(frozen=True)
class IngestResult:
    new: int
    skipped: int


def make_dedup_key(feed_url: str, guid: str) -> str:
    """Fingerprint for an episode: same feed + same guid -> same key."""
    return hashlib.sha256(f"{feed_url}\n{guid}".encode()).hexdigest()


def parse_duration(value: str | None) -> int | None:
    """itunes:duration comes as '3600', '62:03' or '1:02:03'. Returns seconds, or None."""
    if not value:
        return None
    try:
        seconds = 0
        for part in value.strip().split(":"):
            seconds = seconds * 60 + int(float(part))
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _audio_url(entry) -> str | None:
    for enclosure in entry.get("enclosures", []):
        href = enclosure.get("href")
        mime = enclosure.get("type", "")
        if href and (not mime or mime.startswith("audio/")):
            return href
    return None


def _published_at(entry) -> datetime | None:
    parsed = entry.get("published_parsed")
    return datetime(*parsed[:6], tzinfo=UTC) if parsed else None


def parse_feed(feed_url: str, content: bytes) -> list[EpisodeMeta]:
    """Turn raw RSS bytes into episodes, newest first. Items without audio are skipped."""
    feed = feedparser.parse(content)
    episodes = []
    for entry in feed.entries:
        audio_url = _audio_url(entry)
        if not audio_url:
            continue
        episodes.append(
            EpisodeMeta(
                feed_url=feed_url,
                guid=entry.get("id") or audio_url,  # no guid? the audio URL is the next-best identity
                title=(entry.get("title") or "Untitled").strip(),
                audio_url=audio_url,
                published_at=_published_at(entry),
                duration_seconds=parse_duration(entry.get("itunes_duration")),
            )
        )
    oldest = datetime.min.replace(tzinfo=UTC)
    episodes.sort(key=lambda e: e.published_at or oldest, reverse=True)
    return episodes


def fetch_feed(feed_url: str) -> bytes:
    """Download a feed with a timeout (feedparser's own fetcher has none)."""
    response = httpx.get(
        feed_url,
        timeout=FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.content


def save_episodes(conn: psycopg.Connection, episodes: list[EpisodeMeta]) -> IngestResult:
    """Insert new episodes and queue a transcribe job for each.

    Episode + job are written in one transaction, so a crash can never leave an
    episode without its job.
    """
    new = 0
    for ep in episodes:
        with conn.transaction():
            row = conn.execute(
                """
                INSERT INTO episodes
                    (feed_url, guid, title, audio_url, published_at, duration_seconds, dedup_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (dedup_key) DO NOTHING
                RETURNING id
                """,
                (ep.feed_url, ep.guid, ep.title, ep.audio_url,
                 ep.published_at, ep.duration_seconds, ep.dedup_key),
            ).fetchone()
            if row:
                enqueue(conn, row[0], TRANSCRIBE)
                new += 1
    return IngestResult(new=new, skipped=len(episodes) - new)


def ingest_feed(conn: psycopg.Connection, feed_url: str, limit: int | None = 20) -> IngestResult:
    """Fetch a feed and save its newest `limit` episodes (None = all)."""
    episodes = parse_feed(feed_url, fetch_feed(feed_url))
    return save_episodes(conn, episodes[:limit] if limit else episodes)
