# apps/api/Dockerfile
# ─────────────────────────────────────────────────────────────────────
# Backup deployment image for the demo scenario.
#
# The primary demo path is `scripts/start-demo.{ps1,sh}` running on
# the host machine (no Docker). This image exists for when:
#   · the host machine is unreliable (judge venue WiFi flapping)
#   · you want to deploy to an ECS / VPS for a longer-running demo
#   · you need to reproduce the runtime locally exactly as prod runs
#
# Multi-stage build keeps the final image small:
#   stage 1: `uv` resolves + locks deps into a venv
#   stage 2: thin Python runtime image, copy in venv + app source
#
# Build:  docker build -f apps/api/Dockerfile -t careercoach-api:demo .
# Run :   docker run --rm -p 8000:8000 --env-file apps/api/.env careercoach-api:demo
# ─────────────────────────────────────────────────────────────────────

# ── Stage 1 · Dependency resolution ──────────────────────────────────
FROM python:3.12-slim AS builder

# uv is pinned via the official multi-arch image — fetched binary, no
# Cargo / pip needed at build time.
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /usr/local/bin/uv

WORKDIR /app

# Copy only the lock + pyproject first so layer caches when source code
# changes but deps don't. The full source comes in stage 2.
COPY apps/api/pyproject.toml apps/api/uv.lock* ./

# `--frozen` honours the lockfile exactly. `--no-install-project` skips
# installing the app itself — we just want its deps in the venv.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
RUN uv sync --frozen --no-install-project --no-dev

# ── Stage 2 · Runtime image ──────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Non-root user — defence-in-depth even though demo only runs on LAN.
RUN useradd --create-home --shell /bin/bash careercoach

WORKDIR /app

# Carry the pre-built venv from stage 1.
COPY --from=builder --chown=careercoach:careercoach /app/.venv /app/.venv

# Application source.
COPY --chown=careercoach:careercoach apps/api/app /app/app
COPY --chown=careercoach:careercoach apps/api/alembic /app/alembic
COPY --chown=careercoach:careercoach apps/api/alembic.ini /app/alembic.ini

# Make the venv binaries first on PATH so `uvicorn` resolves without
# `uv run` wrapping (kept lean — uv isn't needed at runtime).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER careercoach

EXPOSE 8000

# Healthcheck mirrors the demo readiness probe.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

# Single worker is the right shape for demo / single ECS node deploys
# per careercoach-foundation §4.5. Scale horizontally later by raising
# `--workers` or fronting multiple containers behind nginx.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
