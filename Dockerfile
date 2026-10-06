# syntax=docker/dockerfile:1
# Earshot API image (Render). Serves search + answers; the worker/ingestion run elsewhere.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    FASTEMBED_CACHE_PATH=/app/models

# Same uv version as development, pinned (reproducible builds).
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first (cached layer while only source code changes). --no-dev: eval tools
# (jiwer, pyarrow, ...) are not installed in production.
# The cache mount keeps uv's download cache OUT of the image: the first build put it in a
# layer and the image was 2.49 GB.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

# Bake the embedding model into the image: a cold start (Render free sleeps after 15 min)
# must not download 67 MB before it can answer.
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

# Least privilege: don't run the server as root.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

# Render sets PORT (default 10000).
CMD ["sh", "-c", ".venv/bin/uvicorn earshot.api:app_factory --factory --host 0.0.0.0 --port ${PORT:-10000}"]
