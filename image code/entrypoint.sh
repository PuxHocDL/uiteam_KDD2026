#!/bin/sh
# KDD Cup 2026 baseline (cloned from release/v1.0.6) entrypoint.
# Persists stdout/stderr to /logs (rule 3.7) and runs benchmark over /input.
set -e

mkdir -p /logs /output

exec dabench run-benchmark --config /app/configs/eval.yaml 2>&1 | tee -a /logs/runtime.log
