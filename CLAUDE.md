# Earshot — project rules

## What this project is
Earshot: ask questions across podcasts and get answers with timestamp
citations that play the exact moment in the original audio.
Pipeline: Podcast Index API/RSS → job queue (Postgres) → VAD chunking →
Whisper ASR → enrichment (ad detection, NER, chapters) → hybrid search
(BM25 + embeddings + reranker) → multi-agent answering with a critic →
FastAPI backend (Render) + Next.js frontend (Vercel). LLMOps: Langfuse,
CI eval gate, cost tracking.

## About me
I'm learning AI engineering and preparing for system design interviews.
I want to understand EVERY change, however small.

## How to work with me
- Before editing, explain in simple words WHAT you'll change and WHY,
  as if teaching a beginner. Use everyday analogies.
- After each change, give a 2–3 sentence "how I'd explain this in an
  interview" summary.
- Name the system design concept involved (e.g. idempotency, queues,
  caching, rate limiting) whenever one appears.
- Make small changes, one step at a time. Never rewrite many files at once.
- Never guess at a bug's cause: reproduce it, read the error, then fix it.
- Write real tests for every feature and run them.

## Tools
- Python with uv only (uv init, uv add, uv run). Never pip or python -m venv.
- I'm on Windows, using PowerShell.
- Git: after each finished step, tell me the exact git commands to commit
  and push, and explain what each one does. Don't push for me.

## Learning log
At the end of each milestone, add an entry to LEARNINGS.md: what we built,
why, what broke and how we fixed it, plus a 60-second interview script.