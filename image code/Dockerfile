# Build context = the original/ folder (the cloned v1.0.6 repo root).
# Build command (from outer repo root):
#   docker build -t team1339-baseline:v1 -f "original/image code/Dockerfile" original
#
# Source comes straight from the cloned repo's `src/`. Only `config.py` is
# overlaid with the eval-mode-aware version under `image code/overrides/`.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:/usr/local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 1. Repo files from the cloned baseline.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# 2. Overlay: replace ONLY the eval-mode-aware files.
COPY ["image code/overrides/config.py", "./src/data_agent_baseline/config.py"]

# 3. Install deps.
RUN uv sync --frozen --no-dev

# 4. Eval-only assets.
COPY ["image code/configs", "./configs"]
COPY ["image code/entrypoint.sh", "/entrypoint.sh"]
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
