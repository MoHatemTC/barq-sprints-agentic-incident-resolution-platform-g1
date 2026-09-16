# =============================================================================
# Worker image (S2.3) — Celery execution substrate
# Slim runtime for the incident-processing workers. Sprint 4 replaces this with
# the full deployable application image built by GitHub Actions.
# =============================================================================

FROM python:3.12-slim

# uv for fast, locked dependency installation
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Application source
COPY src ./src
RUN uv sync --locked --no-dev

# Run as the built project so `app.*` imports resolve from site-packages
CMD ["uv", "run", "--no-dev", "celery", "-A", "app.workers.celery_app", "worker", "--loglevel=INFO"]
