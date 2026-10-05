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
- **Dynamic ad insertion (DAI):** many hosts stitch different ads into each
  download, so the audio a listener streams may not match the audio we
  transcribed, and citation timestamps can drift by the ad-length difference.
  M2 handles the pipeline side (checkpoints are tied to the audio's sha256).
  The product side is open: in M3, detect ad segments; in M6, consider
  anchoring citations to nearby text and re-syncing in the player, or showing
  a "timestamps may be offset by ads" note. Measure how often it happens on
  the curated shows.

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

- [x] Step 3: `chunks` table (row exists = done; UNIQUE `(episode_id, idx)`;
  absolute word times in JSONB; `audio_sha256`) and `src/earshot/pipeline.py`:
  `transcribe_episode` (checkpointed; a saved chunk is reused only if the audio
  hash AND its idx/start/end match the current plan, otherwise all of the
  episode's chunks are discarded; stores the measured duration) and
  `get_transcript`. Test DB reset now drops the whole schema. 80 tests passing;
  the fingerprint check is mutation-tested.

- [x] Step 4: `src/earshot/download.py` (streamed, 60 s timeout, 400 MB cap,
  partial file removed; 4xx permanent, 5xx/429/network retryable) and
  `src/earshot/worker.py` (`process_one` routes errors by whose fault it is;
  429 → `queue.defer`, which refunds the attempt AND pauses the whole worker,
  since quota is per account; Ctrl+C hands the job back; temp dir always
  deleted). `queue.fail(permanent=True)`. CLI `earshot worker [--once] [--id]`.
  **Fencing moved from `attempts` to a new monotonic `lease` column**, because
  defer's refund made attempt numbers reusable, so a stale claim could
  collide with a live one (mutation-tested). 113 tests passing.
- [x] **First real end-to-end run:** NPR *Up First* (35 min) → 4 chunks of
  ~10 min → 5,927 words in 61 s wall time (~37 s download/decode/VAD, ~5 s per
  Groq call). The Lex Fridman demo episodes were deleted from the dev DB
  (Zain's choice) because 3–5 h episodes are too costly for the free tier.

**Findings from the real run:**
- **DAI confirmed:** the feed declared 2,115 s and the measured audio was
  2,318 s (+203 s, matching +3.2 MB in `x-total-bytes`). Downloads differ per
  listener (`listeningSessionID`). See the constraint in section 6.
- **Whisper timestamp jitter:** 29 of 5,927 words step back in time, all
  *within* a chunk (0 at chunk boundaries, so stitching is correct); mostly
  0.02–0.4 s.
- **Likely hallucination/alignment failure** at ~14:57: the phrase "…shape."
  is repeated, and inserted words ("Andrey Kuvshinov, Father") have 0.02 s
  durations. We store Whisper output raw. Signals for the eval: near-zero word
  durations, backwards timestamps, repeated n-grams.
- httpx logged full audio URLs (tracking IDs; private feeds carry tokens), so
  the CLI now sets the httpx logger to WARNING (tested).

- [x] Step 5a, LibriSpeech eval (`src/earshot/evals/`): 73 utterances from
  `hf-internal-testing/librispeech_asr_dummy` (1,169 words, 555 s), joined with
  1 s gaps and run through the real pipeline. WER uses `whisper-normalizer`
  (OpenAI's English normalizer). Scoring is **align by text, then measure time**.
  Raw transcripts are cached in `evals/cache/` (gitignored), so re-scoring
  costs no quota. CLI: `earshot eval librispeech --model …`. 126 tests passing.
  **Results:**
  - large-v3: 2.65% WER (S26 D5 I0); 99.1% of correct words inside their true
    utterance; max offset 1.35 s
  - turbo: 2.65% WER (S27 D4 I0); 99.5%; max 0.55 s
  - **Decision:** keep large-v3 as the default (same free quota); the podcast
    eval decides. If they tie there too, use turbo for the paid batch (2.8×
    cheaper).

- [x] Step 5b, hallucination signals (`src/earshot/evals/signals.py`,
  `earshot eval signals <episode_id>`): compressed runs (≥3 consecutive words
  under 0.05 s), backward jumps (>0.5 s), and immediately repeated phrases of
  ≥3 words. Thresholds come from data: clean LibriSpeech has 0 runs and
  0 flags; NPR has 46 scattered short words (normal) and one run at the known
  glitch. NPR result: 4 flags in 39 min. The known glitch at 14:57 is caught
  twice. **Unverified:** 18:33 (likely a real human repetition, a false
  positive) and 33:35 (compressed run).
- [x] **Local fallback measured** (`src/earshot/local_asr.py`, faster-whisper,
  int8, CPU). Word-perfect on the TTS fixture with small.en at 1.1× real time.
  **On the real 10-min NPR clip, small.en could not run reliably on this
  laptop:** only ~0.75 GB of RAM was free (Docker VM, VS Code, etc.), and
  small.en commits ~2.5 GB at load. It failed 3 of 4 tries with
  `mkl_malloc: failed to allocate memory`, plus one 183 MiB allocation failure
  from computing features for a whole 600 s chunk at once. Fixes:
  - **Chunk size is now per backend:** `LocalTranscriber.max_chunk_seconds = 120`
    (RAM-bound), and Groq keeps 600 s (upload-bound). `transcribe_audio()`
    reads it.
  - **base.en works:** 600 s in 112 s (**5.4× real time**), peak 1.6 GB
    committed. Groq: 10–11 s for the same clip (~55–60×).
  - Conclusion: on this laptop the local fallback is viable with base.en (or
    with more free RAM). Throughput 24/7 ≈ 130 audio-h/day, so capacity isn't
    the issue; quality is (measure WER in 5c).
- [x] Step 5c tooling (`src/earshot/evals/podcast.py`): `earshot eval
  podcast-prepare <name> --episode-id N` cuts ONE fixed clip (so every model
  and the human hear identical audio, despite DAI), transcribes it with both
  Groq models and local small.en, and writes a draft `reference.txt` from the
  **local** model (neutral, so correcting doesn't favour either Groq model).
  It never overwrites an existing reference.txt. `earshot eval podcast <name>`
  scores and warns if the reference is still the uncorrected draft. Clip,
  transcripts and reference stay in `evals/podcast/` (gitignored, for
  copyright); only metrics are committed.

- [x] 5c prepared: `evals/podcast/npr-upfirst/` has clip.flac (600 s), cached
  transcripts from large-v3, turbo and local base.en, and reference.txt (base.en
  draft, 1,629 words). Preview vs the *uncorrected* draft (agreement, not
  accuracy): turbo 5.0%, large-v3 8.3%. Check why large-v3 differs more once
  the real reference exists.

**Current step: M2 step 5c, waiting on Zain:** correct
`evals/podcast/npr-upfirst/reference.txt` while listening to `clip.flac`, then
run `uv run earshot eval podcast npr-upfirst`. Then write the M2 LEARNINGS
entry, update the README results, and close M2.

*Gotchas for the M2 LEARNINGS entry (plus the findings above):*
- **Eval methodology bug (mine):** the first scoring assigned words to
  utterances by timestamp. Whisper starts the first word after a pause ~0.2–0.55 s
  early, so correct words became fake deletions, and "nearest utterance"
  turned them into fake insertions. WER read 3.59%/3.17% and looked like turbo
  was better; fair scoring gives 2.65%/2.65%. Fixed with align-by-text, then
  measure time, plus a regression test. Lesson: don't let one metric's error
  leak into another's.
- `whisper-normalizer` merges counting words ("one two three" → "123"), which
  changes word counts. My test sentence hit it; the code was right.
- Each scoring fix cost ~18 min of quota until raw transcripts were cached.
  Separate inference from scoring.
- **A pipe hid a crash:** `cmd | grep ...` reported exit code 0 while Python
  had crashed (a pipeline's status is the last command's). Capture `$?` from
  the real command, or redirect to a file and grep afterwards.
- **Out of memory loading small.en:** diagnosed by instrumenting the *real*
  failing command with psutil (process committed and system available memory
  at model load). A reconstruction didn't reproduce it, and my first ctypes
  memory probe silently returned 0 MB, so I didn't trust it. The fix was in
  design, not retries: per-backend chunk size, and a smaller local model.
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
