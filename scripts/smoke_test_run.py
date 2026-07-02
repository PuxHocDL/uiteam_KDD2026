"""One-off smoke test: run a fixed list of task_ids into one shared run
directory (the `dabench run-task` CLI insists on a fresh directory per
invocation, which doesn't fit a "N tasks, 1 run dir" smoke test).

Credentials are never baked into a config file — pass them the same way
`dabench` itself accepts them: individual flags (--model/--api-key/...) or
process env vars (DABENCH_*/AZURE_OPENAI_*).

Usage:
    export AZURE_OPENAI_API_KEY=...
    export AZURE_OPENAI_ENDPOINT=...
    export AZURE_OPENAI_API_VERSION=...
    uv run python scripts/smoke_test_run.py \
        --config configs/hybrid_b_baseline.example.yaml \
        --run-id smoke_test_gpt4o --model gpt-4o \
        task_11 task_145 task_330 task_418
"""

from __future__ import annotations

import argparse
from pathlib import Path

from data_agent_baseline.config import CredentialOverrides, load_app_config
from data_agent_baseline.run.runner import run_single_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--api-version", default=None)
    parser.add_argument("task_ids", nargs="+")
    args = parser.parse_args()

    app_config = load_app_config(
        args.config,
        credentials=CredentialOverrides(
            model=args.model,
            api_base=args.api_base,
            api_key=args.api_key,
            api_version=args.api_version,
        ),
    )
    run_output_dir = app_config.run.output_dir / args.run_id
    run_output_dir.mkdir(parents=True, exist_ok=True)

    for task_id in args.task_ids:
        artifact = run_single_task(task_id=task_id, config=app_config, run_output_dir=run_output_dir)
        status = "OK" if artifact.succeeded else f"FAIL ({artifact.failure_reason})"
        print(f"{task_id}: {status} -> {artifact.task_output_dir}")


if __name__ == "__main__":
    main()
