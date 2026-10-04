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

## 9. Next step

M1 step 4: the Podcast Index / RSS client.
- Zain needs a Podcast Index API key and secret (free at api.podcastindex.org)
  in `.env` before the live call. Tests must not hit the real API (use saved
  sample responses).
- Parse episodes (guid, title, enclosure audio URL, published date, duration),
  compute `dedup_key = sha256(feed_url + guid)`, insert with
  `ON CONFLICT (dedup_key) DO NOTHING`, and `enqueue(episode_id, 'transcribe')`
- Re-running ingestion must be a no-op (idempotency test)

Still to do before closing M1: reclaim jobs stuck in `running` (worker crashed)
using `locked_at` plus a timeout; then the M1 LEARNINGS entry.

Explain each part and its concept before writing code.

*Gotchas (include in the M1 LEARNINGS entry):*
- In Windows PowerShell 5.1, `docker compose exec ... -c "..."` mangles nested
  quotes. Use Git Bash for one-off `psql` commands.
- Tests hung forever: `localhost` resolved to IPv6 `::1` first, and Docker
  accepted that connection but never answered, because the port is published on
  127.0.0.1 only. Diagnosed with `pg_stat_activity` (zero connections) and
  timed per-address connects. Fixed with `127.0.0.1` in `DATABASE_URL` plus a
  `connect_timeout` on every connection, so it fails fast instead of hanging.
