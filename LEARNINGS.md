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
