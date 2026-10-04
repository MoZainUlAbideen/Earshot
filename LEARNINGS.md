# Earshot — Learning Log

One entry per milestone: what we built, why, what broke and how we fixed it,
plus a 60-second interview script.

---

## M0 — Setup and scaffold (2026-10-04)

### What we built
- uv project with a **src layout** (`src/earshot/`), Python pinned to 3.12 via `.python-version`
- `pytest` as a **dev** dependency; exact versions locked in `uv.lock`
- Smoke test (`tests/test_smoke.py`): the package imports and its entry point exists
- `.gitignore` for Python caches, `.venv/`, and `.env` secrets (with `!.env.example` as the exception)
- `.env.example` listing `PODCAST_INDEX_API_KEY`, `PODCAST_INDEX_API_SECRET`, `GROQ_API_KEY` (placeholders only)
- README stub, this log, branch renamed to `main`, first push to GitHub

### Why
- **src layout:** tests import the *installed* package, not loose files, so packaging bugs show up early.
- **Lockfile:** deterministic builds. CI and Render install exactly what we tested.
- **Smoke test:** the cheapest "does it even turn on" check; it becomes the first CI gate.
- **.env / .env.example:** 12-factor config. Code stays the same everywhere; secrets come from the environment and never enter git history.

### What broke and how we fixed it
1. **Typo in git email.** `uv init` copied the author email from git config, which had `gmai.com`.
   Spotted it in `pyproject.toml`, checked `git config user.email` to confirm the source,
   fixed both before the first commit.
   *Lesson:* tools inherit config silently. Check generated files rather than trusting them.
2. **Pasted git commands did nothing.** `git remote add origin <https://...>` kept the
   `< >` from a placeholder. In PowerShell `<` is reserved, so it raised a parse error.
   Because PowerShell parses a whole pasted block *before* running it, **none** of the 7 lines
   ran, not just the bad one. Confirmed by checking state (`git log`, `git status`, `git remote -v`),
   then reran the commands one line at a time without the brackets.
   *Lesson:* fail-fast validation. Check the actual state before assuming what ran.

### 60-second interview script
> "I set up Earshot as a uv-managed Python package using a src layout, so tests run against
> the installed package rather than local files, and I pinned the interpreter and locked
> every dependency version for reproducible builds. Secrets follow 12-factor config: they
> live in a gitignored `.env`, and only a placeholder `.env.example` is committed, because
> git history is effectively permanent and leaked keys have to be rotated, not just deleted.
> I added a smoke test as the cheapest possible CI gate. One thing I learned: PowerShell
> parses an entire pasted script before executing, so a single syntax error meant none of my
> git commands ran. Rather than assuming, I checked the repo state first. That habit of
> verifying instead of guessing is how I approach debugging generally."

---

## M1 — Ingestion and job queue (2026-10-04)

### What we built
- **Postgres 17 in Docker** (`docker-compose.yml`): pinned version, named volume, localhost-only
  port, health check, and credentials from `.env`
- **Schema** (`schema.sql`):
  - `episodes`: UNIQUE `dedup_key = sha256(feed_url + guid)`
  - `jobs`: status CHECK, `attempts`/`max_attempts`, `run_after`, `locked_at`/`locked_by`, and
    UNIQUE `(episode_id, kind)`, plus a partial index on queued jobs
- **Job queue** (`queue.py`):
  - `enqueue`: idempotent
  - `claim`: `FOR UPDATE SKIP LOCKED`
  - `complete` / `fail`: exponential backoff 30s → 60s → `failed`
  - `reclaim_stale`: recovers jobs from crashed workers
  - `attempts` used as a **fencing token**
- **RSS ingestion** (`ingest.py`): fetch with a timeout → parse (skip non-audio, normalize
  durations, fall back to the audio URL when guid is missing) → save episode + enqueue its job in
  **one transaction**
- **CLI**: `earshot ingest <feed> --limit N`, `earshot status`
- **46 tests**, run against a separate `earshot_test` DB, including:
  - a two-worker lock test
  - a 4-thread × 40-job "exactly once" stress test
  - a poison-pill test
  - fencing tests
- **Mutation-tested**: removing `SKIP LOCKED` or the fencing check makes the right tests fail

### Why
- **Postgres as the queue instead of Redis:** one fewer service. Jobs are durable, queryable
  and claimed atomically, which is plenty for hundreds of episodes.
- **Idempotency in the database** (UNIQUE + `ON CONFLICT DO NOTHING`): re-running ingestion or
  enqueueing twice is always safe, even under races.
- **Claim fast, work outside the transaction:** transcription takes minutes, so holding a
  transaction open that long would tie up connections and locks. The trade-off is that a crashed
  worker leaves its job `running`, which is why `reclaim_stale` exists.
- **Fencing token:** once a slow worker's job has been reclaimed and handed to a new worker, the
  old worker's late `complete()` is rejected (`LeaseLost`) instead of overwriting the new owner.
- **Delivery is at-least-once**, so every future job handler must be idempotent.
- **RSS first, Podcast Index later:** RSS is the original source and needs no key. Podcast
  Index becomes the discovery layer.

### What broke and how we fixed it
1. **`docker` not found right after installing.**
   - *Diagnosis:* `docker.exe` existed and was on the system PATH, but the terminal had been
     opened before the install, and processes read PATH only at startup. WSL2 also needed a reboot.
   - *Fix:* reboot.
   - *Lesson:* processes snapshot their environment at start.
2. **`psql` via PowerShell printed nothing.**
   - *Diagnosis:* Windows PowerShell 5.1 strips embedded double quotes when passing arguments to
     native programs. The identical command worked from Git Bash.
   - *Fix:* use Git Bash for one-off `psql` commands.
   - *Lesson:* sometimes it's the shell that's broken, not the tool.
3. **Test suite hung forever.**
   - *Diagnosis:*
     - `pg_stat_activity` showed **zero** connections, so it wasn't a lock.
     - One connect with a timeout took exactly the full timeout, then succeeded on `127.0.0.1`.
     - Timing each address separately: IPv4 connected in 0.04s, IPv6 `::1` hung.
     - `localhost` resolves to `::1` first, and Docker Desktop accepts IPv6 connections but
       never answers them, because the port is published on 127.0.0.1 only.
   - *Fix:* `127.0.0.1` in `DATABASE_URL`, plus `connect_timeout` on every connection, with a
     test that locks the timeout in.
   - *Lesson:* every network call needs a timeout.
4. **`.env` written with a BOM.**
   - *Diagnosis:* PowerShell 5.1's `-Encoding utf8` prepends `EF BB BF`.
   - *Fix:* rewrote it without the BOM.
5. **Missing durations on live data.**
   - *Diagnosis:* 3 of 5 Lex Fridman episodes had NULL duration. The raw feed showed the
     publisher simply omits `<itunes:duration>` for those episodes. Not our bug.
   - *Lesson:* treat "missing" as a normal state for real-world data.

### 60-second interview script
> "Earshot's ingestion reads podcast RSS feeds and stores each episode with a fingerprint,
> a hash of the feed URL and the episode's guid, behind a UNIQUE constraint, so re-ingesting
> is always a no-op. Each new episode gets a job in a Postgres-backed queue, written in the
> same transaction so they can't drift apart. Workers claim jobs with `SELECT FOR UPDATE SKIP
> LOCKED`, which means concurrent workers never block on or double-claim a job; I proved it
> with a 4-thread stress test and by mutation testing. Workers commit the claim immediately and
> work outside the transaction, so a claim is effectively a lease. If a worker dies, a reclaim
> step requeues its job after a timeout, and the attempt counter doubles as a fencing token,
> so a slow worker can't overwrite whoever owns the job now. Failures retry with exponential
> backoff, and attempts are counted at claim time so a poison-pill job stops after three tries.
> Because delivery is at-least-once, every stage downstream has to be idempotent. The hardest
> bug was a test suite that hung: I found zero connections on the database side and traced it
> to localhost resolving to IPv6, which Docker accepted but never answered. The fix was an
> explicit IPv4 host plus a timeout on every network call."