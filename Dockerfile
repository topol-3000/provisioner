# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------
# Multi-stage build for provisioning-worker.
#
# Stage 1 (`builder`): uv-managed virtualenv with all runtime deps
#                      installed from the committed uv.lock.
# Stage 2 (`runtime`): slim Python, non-root user, .venv copied in.
#
# Key deltas from platform-api:
#   - ENTRYPOINT is python -m provisioning_worker (no Granian, no ASGI)
#   - EXPOSE 8001 (worker health port, not platform-api's 8000)
#   - HEALTHCHECK targets http://127.0.0.1:8001/healthz
#   - alembic.ini + migrations/ copied in for docker-migrate one-shot
#
# Image size target: < 250 MB.
# ---------------------------------------------------------------------

ARG PYTHON_VERSION=3.14

# =====================================================================
# Stage 1: builder
# =====================================================================
FROM python:${PYTHON_VERSION}-slim-trixie AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

# uv installer pinned by version — bump deliberately, not on rebuild.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

# Build in /app so the venv it produces (/app/.venv) is at the SAME path
# as the final runtime image. uv bakes the venv path into the shebang of
# every Python entry-point script (alembic, etc.) — if the builder used
# /build/.venv and runtime copied to /app/.venv, those shebangs would
# point at a path that doesn't exist in the runtime image.
WORKDIR /app

# Install runtime deps first (cached layer) — only `pyproject.toml` +
# `uv.lock` invalidate it, not source changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Now bring in sources and install the project itself (editable=false).
# README.md is required because pyproject.toml declares it as the project
# readme — hatchling reads it during the build and errors if missing.
COPY README.md ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# =====================================================================
# Stage 2: runtime
# =====================================================================
FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Non-root user. UID/GID 10001 to avoid colliding with common host UIDs.
RUN groupadd --system --gid 10001 platform \
    && useradd --system --uid 10001 --gid platform --home /app --shell /usr/sbin/nologin platform

WORKDIR /app

# Copy the venv + sources from the builder stage. Both stages use /app
# as the project root so shebangs in /app/.venv/bin/* stay valid.
COPY --from=builder --chown=platform:platform /app/.venv /app/.venv
COPY --from=builder --chown=platform:platform /app/src /app/src

# Alembic config + migrations live alongside src so the image can run
# `alembic -n provisioning upgrade head` as a one-shot migration job.
COPY --chown=platform:platform alembic.ini /app/alembic.ini
COPY --chown=platform:platform migrations/ /app/migrations/

USER platform

EXPOSE 8001

# The worker boots four asyncio concerns under python -m provisioning_worker.
# No CMD — the worker takes no positional args (unlike platform-api's granian
# which accepts --host/--port flags).
ENTRYPOINT ["python", "-m", "provisioning_worker"]

# Healthcheck for orchestrators that respect HEALTHCHECK directives.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/healthz', timeout=2).status == 200 else 1)"
