# Data Agent Studio — Creative Track submission image.
#
# Packages the FULL interactive app (FastAPI gateway + React/Vite UI) AND the
# `dabench` benchmark/eval CLI + test suite in ONE runnable image, so this one
# artifact covers both the demo and the reproducibility/verification path
# (report section 8 "Reproducibility statement", disclosure form artifact
# requirement). There is no separate headless benchmark image anymore.
#
# Build (from repo root):
#   docker build -t creative_<team_id>:v1 .
#
# Run — interactive Studio (default, no args):
#   docker run --rm -p 8000:8000 -p 5173:5173 creative_<team_id>:v1
#   Then open http://localhost:5173 and enter your own LLM API key in the UI
#   Settings panel — no key is baked into this image (see server/app.py: creds
#   come from the request, never hard-coded).
#
# Run — test suite (verification, no external data/credentials needed):
#   docker run --rm creative_<team_id>:v1 pytest -q
#
# Run — benchmark/eval (mount the dataset + your config, pass creds via -e):
#   docker run --rm \
#     -v "$(pwd)/data/public:/app/data/public" \
#     -v "$(pwd)/configs:/app/configs" \
#     -e AZURE_OPENAI_API_KEY=... -e AZURE_OPENAI_ENDPOINT=... \
#     creative_<team_id>:v1 dabench run-benchmark --config /app/configs/hybrid_b.yaml
#   docker run --rm -v "$(pwd)/artifacts:/app/artifacts" creative_<team_id>:v1 dabench eval
#
# See docs/DOCKER_REPRODUCE.md for the full walkthrough.

# ---------------------------------------------------------------------------
# Stage 1 — build the frontend into static assets
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python backend + the built frontend, served together
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 1. Backend source + dependency manifest.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY server ./server
COPY assets ./assets

# --extra dev pulls in pytest/ruff too, so this one image can also run the
# test suite and lint for verification (small footprint, no GPU/model weights).
RUN uv sync --frozen --extra dev

# 2. Benchmark/eval + verification surface: example configs only (explicit
# glob, NOT `COPY configs ./configs` — real configs with API keys are
# git-ignored but may still exist locally, and must never land in the image),
# analysis scripts, and the pytest suite. The dataset itself (data/public/,
# ~1.8GB) is NOT baked in — mount it at runtime (see docs/DOCKER_REPRODUCE.md).
COPY configs/*.example.yaml ./configs/
COPY scripts ./scripts
COPY tests ./tests

# 3. Built frontend (static files) from stage 1.
COPY --from=frontend-builder /app/frontend/dist ./frontend_dist

# 4. Writable app-state dirs (sessions, auth, run outputs) created up front so
#    the container works read-only-rootfs-friendly if the runner adds that flag.
RUN mkdir -p /app/artifacts/studio_sessions /app/artifacts/auth /app/artifacts/runs /app/data/public

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
