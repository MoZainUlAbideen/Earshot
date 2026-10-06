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
---

## M2 — Speech pipeline (2026-10-04 → 2026-10-06)

### What we built
- **Audio prep** (`audio.py`): decode to 16 kHz mono → Silero VAD → chunks of ≤10 min
  cut only in silences → FLAC. A 600 s chunk is 19.2 MB even uncompressed, so it fits
  Groq's 25 MB cap *by construction*. Silence between chunks isn't sent, which saves
  quota.
- **Groq client** (`transcribe.py`): word timestamps, errors classified by whose fault
  they are (429 → `RateLimited`; 5xx/network → retryable; other 4xx → permanent). The
  key never appears in errors. Mocked tests, plus a live contract test (`pytest -m live`).
- **Checkpointed transcription** (`pipeline.py`, `chunks` table): each finished chunk is
  saved, so retries resume and duplicate runs make zero API calls. Checkpoints are tied
  to the audio's sha256 *and* the chunk plan.
- **Worker** (`worker.py`, `download.py`): streamed temp download (timeout, 400 MB cap,
  always deleted). A 429 *defers* (attempt refunded) and pauses the whole worker
  (quota is per account). Ctrl+C hands the job back. Fencing moved to a monotonic
  `lease` column.
- **Evals** (`evals/`): LibriSpeech WER + timestamp accuracy through the full pipeline;
  hallucination signals; a podcast eval with a fixed clip and a neutral draft; a local
  faster-whisper fallback.
- **Model decision:** `whisper-large-v3-turbo` became the default, based on data (below).

### Why (key decisions)
- **Chunk size is a property of the backend,** not a global: Groq is upload-bound (600 s),
  local is RAM-bound (120 s).
- **"Align by text, then measure time":** WER and timestamp accuracy are measured
  separately, so one metric's error can't leak into the other.
- **Separate inference from scoring:** raw transcripts are cached, so scoring changes
  cost no quota.
- **Every model and the human hear one fixed clip:** dynamic ad insertion means a fresh
  stream can differ.

### What broke and how we fixed it
1. **Dependency drift:** faster-whisper declares `av>=11` (no ceiling). uv installed av 19,
   which removed an argument faster-whisper uses → `TypeError`. Proved av 18.1.0 works in
   a throwaway overlay (`uv run --with`), then pinned `av<19` with a comment saying why.
2. **Password in test output:** pytest's default tracebacks print function arguments,
   including the full `DATABASE_URL`. Reproduced with a fake password (shown once by
   default, zero times with `--tb=short`), then made `--tb=short` the default. CI logs on
   public repos are public.
3. **My own fencing flaw:** `defer` refunds `attempts`, but `attempts` was also the fencing
   token, so a stale claim could carry the same token as the live one. Fix: a separate
   `lease` counter that only increases. Mutation-tested (the old fence makes exactly the
   refund-scenario test fail).
4. **Dynamic ad insertion, measured:** the feed declared 2,115 s, and we measured 2,318 s
   (+203 s, matching +3.2 MB in `x-total-bytes`). Downloads differ per listener.
5. **Whisper glitches found on real data:** a duplicated phrase with 0.02 s word durations
   at 14:57. Stored raw; this led to the hallucination-signal detectors (they caught it
   twice, and raised 0 flags on clean audio).
6. **Logs leaked URLs:** httpx logs full request URLs, and private feeds can carry tokens.
   Set the httpx logger to WARNING (tested).
7. **My eval methodology bug:** I first assigned words to utterances by *timestamp*.
   Whisper timestamps the first word after a pause 0.2–0.55 s early, so correct words
   became fake deletions, and "nearest utterance" then made fake insertions. That read
   3.59% vs 3.17% and suggested turbo was better. Fair scoring: **2.65% vs 2.65%**.
8. **A pipe hid a crash:** `cmd | grep` reported success while Python had crashed (a
   pipeline's exit code is the last command's).
9. **Out of memory on the local model:** the laptop had ~0.75 GB free, and small.en
   commits ~2.5 GB at load. Diagnosed by instrumenting the *real* command with psutil (a
   reconstruction didn't reproduce it, and my first ctypes probe silently returned 0 MB).
   The fix was design, not retries: per-backend chunk size and base.en (5.4× real time,
   1.6 GB peak).
10. **Anchoring bias, demonstrated:** the hand-corrected podcast reference differed from
    the base.en draft by only 5 of 1,629 words, so the "results" measured agreement with
    base.en (which "scored" 0.31%). Added a guard: if <1% of words changed, the score is
    flagged as agreement, not accuracy.
11. **What the podcast eval did reveal, without a good reference:** comparing large-v3
    with turbo directly, 68 of large-v3's 70 "missing" words were **one ad passage it
    silently skipped**. On content, the models agree. Accidental omission is the risk, so
    ad removal should be explicit (M3).
12. **Tooling lesson (mine, twice):** patching code with `sed` or ad-hoc scripts
    corrupted `\n`/`\r\n` escapes into real line breaks. Use exact-match edits.

### Results
| | WER (LibriSpeech, 1,169 words) | Timestamp: correct words inside true utterance | Speed |
|---|---|---|---|
| Groq large-v3 | 2.65% | 99.1% (max 1.35 s off) | ~59× real time |
| Groq large-v3-turbo (**default**) | 2.65% | 99.5% (max 0.55 s off) | ~56× real time |
| Local base.en (CPU fallback) | not measured (no unbiased podcast reference) | n/a | 5.4× real time |

### 60-second interview script
> "Earshot's speech pipeline downloads each episode to a temp file, uses voice activity
> detection to cut it into chunks of up to ten minutes, always in silence so no word is split,
> and sends each chunk to Whisper on Groq for word-level timestamps. Each finished chunk is
> checkpointed, so a rate limit or crash resumes where it stopped and never re-pays for audio,
> and checkpoints are tied to a hash of the exact audio, because podcast hosts insert different
> ads into every download. I measured that: 203 extra seconds on one episode. The worker sorts
> failures by whose fault they are: rate limits defer the job without spending an attempt,
> transient errors back off, permanent ones fail fast. I chose the model with an eval through the
> full pipeline, and my first version had a methodology bug: I grouped words by timestamp, so
> timing jitter showed up as recognition errors and pointed at the wrong model. Aligning by text
> first, then measuring time, showed a tie at 2.65% WER. The tiebreaker was that large-v3 silently
> skipped a 68-word passage that turbo kept, so I chose turbo: same accuracy, tighter timestamps,
> 2.8 times cheaper, and nothing gets dropped by accident."

---

## M4 — Retrieval (2026-10-06)

### What we built
- **Corpus:** 8 Practical AI episodes + NPR (9 episodes, 74,135 words) transcribed by
  `earshot worker --until-empty` in ~9 min, with 0 rate-limit hits.
- **Passages** (`passages.py`): overlapping ~45 s windows (stride 30 s) built from word
  *positions*, so Whisper's timestamp jitter can't drop words. 875 passages.
- **Postgres as the search engine:** full-text (`tsvector` generated column + GIN) and
  **pgvector** (HNSW, cosine) in the same database; no separate vector DB.
- **Embeddings** (`embed.py`): `bge-small-en-v1.5` via fastembed (ONNX, 67 MB, no torch);
  `embed_model` stored per row and re-embedded on a model change (embedding versioning).
- **Search** (`search.py`): keyword with OR semantics + vector → Reciprocal Rank Fusion →
  cross-encoder rerank of the top 20. CLI: `earshot index`, `earshot search`.
- **Retrieval eval** (`evals/retrieval.py`, `llm.py`): an LLM-generated paraphrased golden set
  with exact answer times; recall@k, MRR, citation offset and latency per mode and reranker.

### Why
- **One database** (FTS + vectors + queue + transcripts): transactional and simple. At
  ~1K–100K passages a dedicated vector DB adds operations work without adding capability.
- **RRF** fuses by rank, so incomparable scores (ts_rank vs cosine) never need calibrating.
- **Two-stage retrieve-then-rerank:** cheap recall over everything, expensive precision on 20.
- **The reranker was chosen on cost/latency,** not just accuracy: jina-tiny is one question
  behind MiniLM-L-12 at half the latency.

### What broke and how we fixed it
1. **Collation mismatch on the image swap:** `pgvector:pg17` is Debian 12 (glibc 2.36); the data
   was created on Debian 13 (glibc 2.41). A different text sort order can silently corrupt
   text indexes (e.g. the UNIQUE `dedup_key`). Pinned `pgvector/pgvector:0.8.7-pg17-trixie`,
   the warning went away, and `amcheck` verified all 7 B-tree indexes. Row counts matched
   before and after.
2. **Deployment ordering (mine):** I added `CREATE EXTENSION vector` to the schema *before*
   swapping the image, so every new CLI command failed. Infra before code. `apply_schema` is
   one transaction, so nothing was half-applied.
3. **AND vs OR:** `websearch_to_tsquery` ANDs every word, and natural-language questions rarely
   have all their words in one passage. Switched to OR (mutation-tested).
4. **Hybrid wasn't automatically better:** plain RRF scored *below* vector alone at top-1
   (0.51 vs 0.58) on paraphrased questions. Kept hybrid for exact-term queries and recall@10,
   and the reranker lifts it to 0.80.
5. **Coarse citations:** the passage start is ~15 s before the answer on median, so M5 will cite
   the exact quoted words (located in word timings).
6. **Memory again:** with ~0.7 GB free, the 1 GB rerankers couldn't load. The eval now runs one
   reranker at a time and unloads it.
7. **Tooling (mine, a third time):** a script turned `\n` into a real line break in `cli.py`.
   New rule: code with escape sequences only via exact-match edits; verify with `ast.parse`.
8. **Test helpers:** I imported a helper module that didn't exist. Moved the shared fakes into
   `tests/search_helpers.py` instead of importing between test files.

### Results
| Mode | R@1 | R@5 | MRR | p50 |
|---|---|---|---|---|
| keyword | 0.29 | 0.53 | 0.38 | 31 ms |
| vector | 0.58 | 0.80 | 0.67 | 41 ms |
| hybrid | 0.51 | 0.76 | 0.60 | 69 ms |
| hybrid + jina-tiny (default) | 0.80 | 0.93 | 0.85 | 1.35 s |

### 60-second interview script
> "Search runs entirely in Postgres: full-text search for exact terms and pgvector embeddings
> for meaning, fused with Reciprocal Rank Fusion, then a small cross-encoder reranks the top
> twenty. Transcripts are cut into overlapping 45-second passages so answers never straddle a
> boundary. To choose between designs, I built an eval: an LLM writes paraphrased questions for
> sampled passages, and I locate its verbatim evidence quote in the word timings to get the
> exact answer time. Recall@1 went from 0.29 keyword-only, to 0.58 vector, to 0.80 with
> reranking. A surprise was that plain hybrid fusion was *worse* than vector alone on
> paraphrased questions, because the keyword leg adds noise there, so 'hybrid always wins' is a
> claim to measure, not assume. I picked the reranker on the latency trade-off: one question
> behind the most accurate model at half the latency. And the eval showed passage-level
> citations land about 15 seconds early, so the answering stage cites the exact quoted words."
