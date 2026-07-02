"""Side-by-side cost-quality tradeoff table between two run directories
(e.g. gpt-4o vs gpt-4o-mini) that cover the same task_ids.

Usage:
    uv run python scripts/compare_cost_quality.py \
        artifacts/runs/smoke_test_gpt4o artifacts/runs/smoke_test_gpt4o_mini \
        --gold-dir data/public/output
"""

from __future__ import annotations

import argparse
from pathlib import Path

from data_agent_baseline.benchmark.scoring import score_task


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir_a", type=Path, help="e.g. expensive model run")
    parser.add_argument("run_dir_b", type=Path, help="e.g. cheap model run")
    parser.add_argument("--gold-dir", type=Path, default=Path("data/public/output"))
    parser.add_argument("--lambda", dest="lam", type=float, default=0.1)
    parser.add_argument("--label-a", default=None)
    parser.add_argument("--label-b", default=None)
    args = parser.parse_args()

    label_a = args.label_a or args.run_dir_a.name
    label_b = args.label_b or args.run_dir_b.name

    task_ids = sorted(
        {p.name for p in args.run_dir_a.glob("task_*")} & {p.name for p in args.run_dir_b.glob("task_*")}
    )
    if not task_ids:
        print("No overlapping task_ids between the two run directories.")
        return

    header = f"{'Task':<12} {label_a + ' score':>14} {label_a + ' $':>10} {label_b + ' score':>14} {label_b + ' $':>10} {'Δscore':>8} {'Δcost':>10}"
    print(header)
    print("-" * len(header))

    totals = {"score_a": 0.0, "score_b": 0.0, "cost_a": 0.0, "cost_b": 0.0, "n": 0}
    for task_id in task_ids:
        gold_path = args.gold_dir / task_id / "gold.csv"
        if not gold_path.exists():
            continue
        result_a = score_task(gold_path, args.run_dir_a / task_id / "prediction.csv", lam=args.lam)
        result_b = score_task(gold_path, args.run_dir_b / task_id / "prediction.csv", lam=args.lam)

        import json

        def _cost(run_dir: Path, tid: str) -> float:
            trace_path = run_dir / tid / "trace.json"
            if not trace_path.exists():
                return 0.0
            try:
                data = json.loads(trace_path.read_text(encoding="utf-8"))
                usage = data.get("token_usage") or {}
                return float(usage.get("estimated_cost_usd") or 0.0)
            except Exception:
                return 0.0

        cost_a = _cost(args.run_dir_a, task_id)
        cost_b = _cost(args.run_dir_b, task_id)

        totals["score_a"] += result_a.score
        totals["score_b"] += result_b.score
        totals["cost_a"] += cost_a
        totals["cost_b"] += cost_b
        totals["n"] += 1

        print(
            f"{task_id:<12} {result_a.score:>14.4f} {cost_a:>10.5f} "
            f"{result_b.score:>14.4f} {cost_b:>10.5f} "
            f"{result_b.score - result_a.score:>+8.4f} {cost_b - cost_a:>+10.5f}"
        )

    n = totals["n"]
    if n:
        print("-" * len(header))
        avg_score_a = totals["score_a"] / n
        avg_score_b = totals["score_b"] / n
        avg_cost_a = totals["cost_a"] / n
        avg_cost_b = totals["cost_b"] / n
        print(f"{'AVG':<12} {avg_score_a:>14.4f} {avg_cost_a:>10.5f} {avg_score_b:>14.4f} {avg_cost_b:>10.5f}")
        if avg_cost_b > 0:
            print(f"\n{label_a} is {avg_cost_a / avg_cost_b:.1f}x more expensive than {label_b} on this sample.")
        print(f"Score delta ({label_b} - {label_a}): {avg_score_b - avg_score_a:+.4f}")


if __name__ == "__main__":
    main()
