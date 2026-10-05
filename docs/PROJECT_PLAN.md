# Earshot — Project Plan & Handoff

> This file is the full plan agreed in planning chats on claude.ai.
> Claude Code: read this before starting any milestone. It tells you what
> we're building, the decisions already made, and where we are right now.

---

## 1. The product

**Earshot: ask any podcast a question and hear the exact moment it was answered.**

- **Problem:** Thousands of hours of expert talk live in podcasts, and none of it
  is searchable. Finding "that bit where someone explained X" means scrubbing
  through 3-hour episodes.
- **What it does:** The user asks a question, for example *"What do AI
  researchers say about scaling laws hitting a wall?"* Earshot answers across
  many episodes. Every claim carries a **timestamp citation**, and clicking it
  plays the original audio from that second.
- **Who would pay:** Journalists, researchers, students, and podcast networks
  that want their own back catalogue searchable (B2B).
- **Not region-specific.** Urdu podcasts are a possible stretch goal later,
  since Whisper is multilingual.

## 2. Why this project (learning goals)

- The owner (Zain) is learning AI engineering and preparing for **system design
  interviews**. Understanding every step matters as much as shipping it.
- It's the first **audio** project in the portfolio, after EdgarIQ (text RAG),
  Rehnuma (vision plus forecasting) and Parity (vision plus a crawler).
- It's deliberately simpler: one data type flowing through one straight
  pipeline (audio → text → search → answer), with cheap ground truth
  (a timestamp is either right or wrong).
- It maps to classic interview questions: "design YouTube/Spotify search,"
  "design a transcription service," "design a job queue," "design a rate
  limiter."

## 3. Hugging Face tasks used

| Task | Job in Earshot |
|---|---|
| Voice Activity Detection | Cut audio at silences so chunks never split a word (e.g. Silero VAD) |
| Automatic Speech Recognition | Whisper turns speech into text with word-level timestamps |
| Zero-Shot Classification | Detect sponsor/ad segments and topic chapters |
| Token Classification (NER) | Pull out people, companies and papers mentioned |
| Summarization | Chapter and episode summaries |
| Sentence Similarity | Embeddings for semantic search |
| Text Ranking | Reranker that reorders search results by real relevance |
| Text-to-Speech (stretch) | "Listen to the answer" audio briefing |

## 4. Architecture

```
Podcast Index API / RSS feed
        │
  [Ingest] ── content hash → skip if already processed (idempotency)
        │
  Postgres jobs table  ← simple job queue (SELECT ... FOR UPDATE SKIP LOCKED)
        │
  [Worker] VAD chunking → Whisper (Groq API, local faster-whisper fallback)
        │      → stitch word timestamps → ad detection → NER → chapters
        │
  Hybrid index: BM25 + embeddings (versioned) → reranker
        │
  [Agents] Router → Retriever → Answerer (timestamp citations)
        │          → Critic (deterministically checks every quote exists in transcript)
        │
  FastAPI (Render) ⇄ Next.js (Vercel) with an audio player that jumps to the citation
        │
  Langfuse tracing · CI eval gate · cost per audio-hour
```

## 5. Decisions already made

- **Python with `uv` only** (uv init / uv add / uv run). Never pip or venv.
- **Windows + PowerShell** dev machine. Project at `D:\VS-Code-Projects\Earshot`.
- **Job queue:** Postgres table with `SKIP LOCKED` rather than Redis, which is
  simpler and a strong interview talking point.
- **Local Postgres:** runs in Docker Desktop via `docker-compose.yml`, the same
  image CI and Render will use (dev/prod parity). Chosen over hosted Neon (needs
  internet, shared state in tests) and a native install (no CI parity).
- **Ingestion source:** RSS feeds directly (no API key, the original source).
  Podcast Index is used later for *discovery* (search for shows → feed URLs).
- **ASR:** Groq's Whisper API (free tier, rate-limited), with local
  faster-whisper as the fallback.
- **Backend:** FastAPI, deployed on Render free tier (Docker). It has no GPU and
  little RAM, so heavy processing runs in batches.
- **Frontend:** Next.js on Vercel, polished and professionally designed, in the
  same style of workflow as EdgarIQ, Rehnuma and Parity: a few explainer pages
  (what it does, how accuracy was measured), a GitHub link, and the main
  search/answer experience with an audio player.
- **Observability:** Langfuse tracing plus a CI eval gate (tests and evals must
  pass before deploy).
- **Initial scope:** a curated set of about 5 shows × 20 episodes, processed in
  batches. The live demo explores curated shows freely and rate-limits
  "add your own podcast."
- **Git:** default branch `main`. Zain runs commit and push commands himself, so
  Claude Code gives the commands with explanations but does not push.

## 6. Constraints to design around

- **Copyright:** Podcasts are copyrighted. Stream audio from the publisher's own
  enclosure URL and never rehost audio files. Store transcripts only for search
  and show only short snippets in the UI.
- **Free tiers:** Check Groq's current audio rate limits before milestone 2,
  because that number decides the batch size. Treat this as a real design
  trade-off to document.
- **Secrets:** API keys live in `.env` (gitignored). Only `.env.example` with
  placeholders is committed.

## 7. Milestones

Each milestone ends with: real tests passing, a git commit/push by Zain, and a
**LEARNINGS.md** entry (what we built, why, what broke and how we fixed it,
plus a 60-second interview script).

**M0. Setup and scaffold**
uv project (src layout, Python 3.12), pytest plus a smoke test, .gitignore,
.env.example, README stub, LEARNINGS.md, GitHub repo, first push.

**M1. Ingestion and job queue**
Fetch episodes from the Podcast Index API / RSS, deduplicate by content hash,
and queue jobs in a Postgres jobs table.
*Concepts:* idempotency, queues, why not just a for-loop, retries.

**M2. Speech pipeline**
VAD chunking → Whisper → stitch word timestamps across chunks.
*Eval:* word error rate (WER) on a small LibriSpeech subset plus about 10
minutes Zain hand-corrects.
*Concepts:* chunking, retries, fallbacks, rate limits, cost.

**M3. Enrichment**
Ad/sponsor detection (zero-shot), NER, chapters plus summaries.
*Eval:* ad-detection precision/recall against segments Zain labels.
*Concepts:* zero-shot vs fine-tuned, threshold tuning.

**M4. Retrieval**
Time-window chunks with overlap, BM25 plus embeddings (hybrid), and a reranker.
*Eval:* recall@k, MRR, and timestamp hit rate (does the cited clip land within
±15s of the true answer?).
*Concepts:* why hybrid beats pure vectors, reranker cost/latency trade-off.

**M5. Agents**
Router (search one show / compare across shows / summarize episode / find
quote) → retriever → answerer with timestamp citations → critic whose quote
check is deterministic (string match against the transcript), not LLM opinion.
*Eval:* faithfulness and citation accuracy on a golden Q&A set.

**M6. Live product**
FastAPI on Render, Next.js on Vercel, an audio player that jumps to cited
timestamps, and per-visitor rate limiting.

**M7. LLMOps**
Langfuse tracing, CI eval gate, prompt versioning, cost per audio-hour, and
**embedding versioning** (what happens to old vectors when the embedding model
changes).

**Stretch:** speaker diarization ("who said it"), TTS answer briefing, Urdu
podcasts.

## 8. Current status

- [x] `code`, `git`, `uv` working
- [x] Project folder created, `git init` done, opened in VS Code
- [x] Claude Code installed, signed in, in Manual mode
- [x] `CLAUDE.md` created; Claude Code confirmed it understands the rules
- [x] Branch renamed to `main` (`git branch -M main`)
- [x] Repo scaffolded with uv, smoke test passing (2 tests)
- [x] GitHub repo created (`MoZainUlAbideen/Earshot`) and first commit pushed
- [x] **M0 complete** — LEARNINGS.md entry written
- [x] M1 step 1: Docker Desktop installed; Postgres 17 runs via
      `docker-compose.yml` (`docker compose up -d --wait`), credentials in `.env`
- [x] M1 step 2: `src/earshot/db.py` (connect with timeout, `apply_schema`),
      `src/earshot/schema.sql` (`episodes` with UNIQUE `dedup_key`, `jobs` with
      status/attempts/`run_after`, UNIQUE `(episode_id, kind)`), 9 tests passing
      against a separate `earshot_test` DB
- [x] M1 step 3: `src/earshot/queue.py`: `enqueue` (idempotent), `claim`
      (`FOR UPDATE SKIP LOCKED`, attempts counted on claim), `complete`, and `fail`
      (exponential backoff 30s/60s/120s, then `failed`). 24 tests passing,
      including concurrency tests: two-worker lock skip, and 4 threads × 40
      jobs claimed exactly once. Mutation-tested: removing SKIP LOCKED makes
      the concurrency tests fail.
- [x] M1 step 4: `src/earshot/ingest.py` (RSS fetch with timeout → parse →
      `dedup_key` → episode + job saved in one transaction) and `src/earshot/cli.py`
      (`earshot ingest <feed> --limit N`, `earshot status`). 40 tests passing.
      Live-tested on the Lex Fridman feed: 5 new, then 0 new on re-run.
- [x] README rewritten as the public project overview
- [x] M1 step 5: `reclaim_stale` (crashed-worker recovery, poison pills fail
      after max attempts) plus fencing: `complete`/`fail` take the claimed `Job`
      and match on `attempts`, raising `LeaseLost` for out-of-date claims.
      46 tests passing; fencing mutation-tested.
- [x] **M1 complete**: LEARNINGS.md entry written, README roadmap updated

## 9. Next step

### M2 plan (agreed 2026-10-05)

**Facts checked 2026-10-05:**
- Groq free tier (both Whisper models): 20 RPM, 2,000 RPD, 7,200 audio-s/hour,
  **28,800 audio-s/day (8 h of audio/day, the bottleneck)**, 25 MB per file,
  10 s minimum billed.
- Paid: large-v3 $0.111/h (WER 10.3%), turbo $0.04/h (WER 12%).
- Groq key verified (HTTP 200 on /models).
- Dev laptop: i7-8650U (4 cores), 8 GB RAM, no NVIDIA GPU, no system ffmpeg.

**Trade-offs:**
- **Budget:** 100 episodes × ~1.5 h ≈ 150 h ≈ 19 days on the free tier, or
  about $6–17 paid. One Lex Fridman episode (5 h 22 m) uses 2/3 of a day's quota,
  so pick curated shows with ~1 h episodes. Decide before the batch run.
- **Local fallback (faster-whisper on CPU):** only a small model is practical,
  so lower quality. It's a safety net, not the workhorse. Measure it.
- **Model:** default `whisper-large-v3`; the WER eval compares it with turbo.

**Design:** temp download → decode to 16 kHz mono → Silero VAD → group speech
into ≤10-min chunks cut inside silences → FLAC (~10 MB < 25 MB) → Groq with
word timestamps → offset by chunk start → stitched transcript → delete temp
audio.
- Library: `faster-whisper`, which bundles Silero VAD (ONNX, no torch),
  PyAV decoding (no system ffmpeg) and the local fallback.
- A `chunks` table checkpoints progress, so retries skip finished chunks
  (idempotent under at-least-once delivery).
- Rate limits: a 429 defers the job until `retry-after` **without consuming an
  attempt** (it's not the episode's fault).

**Steps:**
1. VAD chunking (local, no API)
2. Groq client and rate-limit handling
3. `chunks` table and stitching
4. Worker loop (`reclaim_stale` → claim → process → complete/fail/defer)
5. WER eval (LibriSpeech subset plus ~10 min hand-corrected by Zain)

**Progress:**
- [x] Step 1: `src/earshot/audio.py`: `load_audio` (16 kHz mono), `find_speech`
  (Silero VAD, 500 ms min silence, splits monologues at 600 s), `plan_chunks`
  (≤600 s, cuts only in silence, skips silence between chunks), `encode_flac`.
  TTS speech fixture (`tests/fixtures/speech_sample.flac`, 3 sentences with
  3 s pauses). 57 tests passing, including a randomized invariant test.

- [x] Step 2: `src/earshot/transcribe.py`: `transcribe_chunk` (verbose_json,
  word timestamps, temperature 0, 120 s timeout). Errors: 429 → `RateLimited`
  (`retry_after`); 5xx/network → `TranscriptionError(retryable=True)`; other
  4xx → `retryable=False`. Errors never contain the key. 15 mocked tests
  (`httpx.MockTransport`), plus a live contract test (`pytest -m live`, skipped
  by default). Live result on the fixture: word-perfect, 1.1 s for 16 s of
  audio, word times inside the VAD segments after the offset.

**Current step: M2 step 3, the `chunks` table and stitching.**
- A `chunks` table: `(episode_id, idx)` UNIQUE, `start_s`/`end_s`, status,
  words as JSONB (times already absolute). It's the checkpoint, so retries skip
  done chunks.
- `transcribe_episode(conn, episode_id, audio_path)`: plan chunks → for each
  chunk not done: transcribe, offset words by `chunk.start`, save.
  Idempotent: re-running does no new API calls.
- A stitched transcript = all words ordered by time. Store `duration_seconds`
  measured from the audio.

*Gotchas for the M2 LEARNINGS entry:*
- `faster-whisper` 1.2.1 declares `av>=11` with no upper bound. uv installed
  av 19, which removed `av.open(metadata_errors=...)`, so we got a TypeError.
  Proved av 18.1.0 works in a throwaway overlay (`uv run --with`), then pinned
  `av>=11,<19` with a comment. Lesson: open-ended dependency ranges break, so
  lockfiles matter.
- pytest's default traceback printed the full `DATABASE_URL` **including the
  password** when Postgres was down. Reproduced with a fake password (1
  occurrence with `--tb=auto`, 0 with `--tb=short`), then set
  `addopts = "--tb=short"`. Matters because CI logs on a public repo are public.
- Docker Desktop doesn't auto-start after a reboot. The DB tests failed fast
  with "Can't reach Postgres" (the timeout guard worked). Fix: start Docker
  Desktop, then `docker compose up -d --wait`.

*Data note:* some real feeds omit `<itunes:duration>` (3 of the 5 Lex Fridman
episodes), so `duration_seconds` can be NULL. M2 should measure it from the audio.

*Dev notes:*
- In Windows PowerShell 5.1, `docker compose exec ... -c "..."` mangles nested
  quotes. Use Git Bash for one-off `psql` commands.
- `DATABASE_URL` must use `127.0.0.1`, not `localhost` (IPv6 hang; see the M1
  entry in LEARNINGS.md).
