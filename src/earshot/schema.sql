-- Earshot schema. Safe to apply repeatedly (IF NOT EXISTS everywhere).

-- The catalogue: one row per podcast episode we know about.
CREATE TABLE IF NOT EXISTS episodes (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_url         TEXT NOT NULL,
    guid             TEXT NOT NULL,          -- the RSS feed's own ID for the episode
    title            TEXT NOT NULL,
    audio_url        TEXT NOT NULL,          -- publisher's enclosure URL; we stream, never rehost
    published_at     TIMESTAMPTZ,
    duration_seconds INTEGER CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    dedup_key        TEXT NOT NULL UNIQUE,   -- sha256(feed_url + guid): same episode never stored twice
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The to-do board: units of work that workers claim and process.
CREATE TABLE IF NOT EXISTS jobs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id   BIGINT NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,              -- e.g. 'transcribe'
    status       TEXT NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts     INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    lease        BIGINT NOT NULL DEFAULT 0, -- +1 on every claim, never decreases: the fencing token
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- retry backoff: not before this time
    locked_at    TIMESTAMPTZ,
    locked_by    TEXT,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, kind)                -- enqueueing the same work twice is a no-op
);

-- Migration for databases created before `lease` existed.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease BIGINT NOT NULL DEFAULT 0;

-- "Available now" lookup for workers: only queued jobs, ordered by when they may run.
CREATE INDEX IF NOT EXISTS jobs_queued_idx ON jobs (run_after) WHERE status = 'queued';

-- Transcribed chunks: one row per finished chunk (row exists = done), so retries
-- skip work already paid for. Word times are absolute (seconds from episode start).
-- audio_sha256 ties each checkpoint to the exact audio file it came from: if a
-- re-download differs (e.g. dynamically inserted ads), old chunks are discarded.
CREATE TABLE IF NOT EXISTS chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    episode_id   BIGINT NOT NULL REFERENCES episodes (id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL CHECK (idx >= 0),
    start_s      DOUBLE PRECISION NOT NULL CHECK (start_s >= 0),
    end_s        DOUBLE PRECISION NOT NULL,
    text         TEXT NOT NULL,
    words        JSONB NOT NULL,            -- [{"text": ..., "start": ..., "end": ...}]
    model        TEXT NOT NULL,
    audio_sha256 TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, idx),
    CHECK (end_s > start_s)
);
