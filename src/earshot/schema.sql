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
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- retry backoff: not before this time
    locked_at    TIMESTAMPTZ,
    locked_by    TEXT,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, kind)                -- enqueueing the same work twice is a no-op
);

-- "Available now" lookup for workers: only queued jobs, ordered by when they may run.
CREATE INDEX IF NOT EXISTS jobs_queued_idx ON jobs (run_after) WHERE status = 'queued';
