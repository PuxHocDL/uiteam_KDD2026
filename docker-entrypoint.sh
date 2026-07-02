#!/bin/bash
# Data Agent Studio — container entrypoint.
# Requires bash (for `wait -n`) — present by default in python:3.11-slim
# (Debian bookworm-slim base).
#
# Default (no args): runs the FastAPI gateway (port 8000) and the built React
# UI (port 5173) as two processes in one container, forwarding SIGTERM/SIGINT
# to both so `docker stop` shuts down cleanly.
#
# With args: execs them instead, so the same image doubles as a verification
# artifact for the benchmark/eval path and the test suite, e.g.
#   docker run --rm <image> pytest -q
#   docker run --rm -v "$(pwd)/data/public:/app/data/public" \
#     -v "$(pwd)/configs:/app/configs" -e AZURE_OPENAI_API_KEY=... \
#     <image> dabench run-benchmark --config /app/configs/hybrid_b.yaml
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

uvicorn server.app:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

python -m http.server 5173 --directory /app/frontend_dist --bind 0.0.0.0 &
FRONTEND_PID=$!

trap 'kill -TERM "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null' TERM INT

wait -n "$BACKEND_PID" "$FRONTEND_PID"
EXIT_CODE=$?

kill -TERM "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null

exit "$EXIT_CODE"
