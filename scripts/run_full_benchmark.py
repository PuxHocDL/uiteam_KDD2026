"""Run the full Phase-1 benchmark (default: all 50 tasks in data/public) for
one model, then score it against gold and print a summary. Reads credentials
from `api profile.txt` (local, gitignored) so the key never appears in a
shell command — this script is the one place allowed to read that file
directly; everything else uses --model/--api-key/env-var overrides.

Usage:
    uv run python scripts/run_full_benchmark.py --model gpt-4o --run-id full_gpt4o
    uv run python scripts/run_full_benchmark.py --model gpt-4o-mini --run-id full_gpt4o_mini
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from data_agent_baseline.benchmark.scoring import score_run
from data_agent_baseline.config import CredentialOverrides, load_app_config
from data_agent_baseline.run.runner import run_benchmark

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "api profile.txt"
_RATE_LIMIT_MARKERS = ("429", "rate_limit", "rate limit")


def _count_rate_limited_steps(run_dir: Path) -> int:
    """Count trace.json.steps[] whose observation mentions 429/rate-limit —
    used to decide whether a run is "clean" enough for a fair model-vs-model
    comparison, or whether max_workers needs to go lower and be rerun."""
    count = 0
    for task_dir in sorted(run_dir.glob("task_*")):
        trace_path = task_dir / "trace.json"
        if not trace_path.exists():
            continue
        try:
            data = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for step in data.get("steps", []):
            if not isinstance(step, dict):
                continue
            text = json.dumps(step.get("observation")).lower()
            if any(marker in text for marker in _RATE_LIMIT_MARKERS):
                count += 1
    return count


def _load_profile() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in PROFILE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "hybrid_b_baseline.example.yaml")
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--max-workers", type=int, default=None,
        help="Override run.max_workers (lower this if the deployment hits 429 rate limits).",
    )
    args = parser.parse_args()

    profile = _load_profile()
    app_config = load_app_config(
        args.config,
        credentials=CredentialOverrides(
            model=args.model,
            api_base=profile.get("AZURE_OPENAI_ENDPOINT"),
            api_key=profile.get("AZURE_OPENAI_API_KEY"),
            api_version=profile.get("AZURE_OPENAI_API_VERSION"),
        ),
    )
    run_overrides: dict = {"run_id": args.run_id}
    if args.max_workers is not None:
        run_overrides["max_workers"] = args.max_workers
    app_config = replace(app_config, run=replace(app_config.run, **run_overrides))

    print(f"Starting run '{args.run_id}' with model={args.model}, max_workers={app_config.run.max_workers} ...")
    run_dir, artifacts = run_benchmark(config=app_config, limit=args.limit)
    succeeded = sum(1 for a in artifacts if a.succeeded)
    print(f"Run dir: {run_dir}")
    print(f"Succeeded: {succeeded}/{len(artifacts)}")

    gold_dir = ROOT / "data" / "public" / "output"
    input_dir = ROOT / "data" / "public" / "input"
    results = score_run(run_dir, gold_dir, input_dir=input_dir)
    scores = [r.score for r in results]
    costs = [r.estimated_cost_usd for r in results if r.estimated_cost_usd is not None]
    tokens = [r.total_tokens for r in results if r.total_tokens is not None]

    print(f"Tasks scored: {len(results)}")
    if scores:
        print(f"Avg score: {sum(scores) / len(scores):.4f}")
        print(f"Perfect (1.0): {sum(1 for s in scores if s >= 1.0)}/{len(scores)}")
        print(f"Zero: {sum(1 for s in scores if s == 0.0)}/{len(scores)}")
    if costs:
        print(f"Total cost: ${sum(costs):.4f}  (avg ${sum(costs) / len(costs):.5f}/task)")
    if tokens:
        print(f"Total tokens: {sum(tokens)}  (avg {sum(tokens) / len(tokens):.0f}/task)")

    rate_limited_steps = _count_rate_limited_steps(run_dir)
    if rate_limited_steps:
        print(
            f"\n⚠ {rate_limited_steps} step(s) hit a 429/rate-limit during this run — "
            f"NOT clean for a fair model-vs-model comparison. Rerun with a lower "
            f"--max-workers (currently {app_config.run.max_workers})."
        )
    else:
        print("\n✓ No rate-limit hits detected — this run is clean for comparison.")


if __name__ == "__main__":
    main()
