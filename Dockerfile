# =============================================================================
# Dockerfile — BARQ Agentic Incident Resolution Platform
# Unified Container Image for FastAPI Ingestion API & Celery Workers
# =============================================================================

FROM python:3.12-slim AS base


# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast, reliable dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency specifications first for optimal layer caching
COPY pyproject.toml uv.lock README.md /app/

# Install locked application dependencies into system environment
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Copy application source code, configuration, and migrations
COPY src/ /app/src/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

# Create non-root user for least-privilege security
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose production HTTP port
EXPOSE 8000

# Container healthcheck using liveness probe
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# Production ASGI Entrypoint
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

# =============================================================================
# UI stage — base image plus the optional chat dependency group (Streamlit).
# Built only for the chat-ui compose service (profile "ui"); the api/worker
# image above never carries the UI stack.
# =============================================================================
FROM base AS ui

USER root
RUN uv export --frozen --no-dev --no-hashes --no-emit-project --group chat -o /tmp/requirements.txt && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt
USER appuser

EXPOSE 8501

# Override the inherited api health check: Streamlit serves its liveness
# endpoint on 8501, so the base check against :8000/health would mark a
# perfectly working UI container unhealthy.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/app/chat_ui/app.py", "--server.address", "0.0.0.0", "--server.port", "8501", "--browser.gatherUsageStats", "false"]

# Production target stays LAST so a targetless `docker build .` still yields
# the api/worker image; the ui stage above is opt-in via build target.
FROM base AS api
