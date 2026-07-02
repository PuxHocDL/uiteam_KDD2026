"""Reliability metrics that aren't computed by `dabench eval` yet: tool-error
rate, self-recovery rate (from `trace.json.steps[]`, already recorded by the
engine — this only aggregates it), and cross-run score stability (variance).

Standalone script, not wired into the `dabench` CLI yet — promote it into
`cli.py` once the metric set stabilizes.

Usage:
    uv run python scripts/analyze_reliability.py error-recovery <run_dir> [<run_dir> ...]
    uv run python scripts/analyze_reliability.py stability <run_dir1> <run_dir2> [...] --gold-dir data/public/output
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from data_agent_baseline.benchmark.scoring import score_task


def _load_trace(task_dir: Path) -> dict:
    trace_path = task_dir / "trace.json"
    if not trace_path.exists():
        return {}
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _step_has_error(step: dict) -> bool:
    observation = step.get("observation")
    if not isinstance(observation, dict):
        return False
    if observation.get("ok") is False:
        return True
    if observation.get("stagnation_warning"):
        return True
    content = observation.get("content")
    if isinstance(content, dict) and content.get("error"):
        return True
    return False


_RATE_LIMIT_MARKERS = ("429", "rate_limit", "rate limit")


def _step_error_is_rate_limit(step: dict) -> bool:
    """True if an error step's text mentions a 429/rate-limit — i.e. it's an
    infrastructure artifact (Azure/OpenAI quota), not a genuine tool/reasoning
    mistake by the agent. Lets error-rate numbers be reported without
    conflating "model made a bad call" with "endpoint throttled us"."""
    text = json.dumps(step.get("observation")).lower()
    return any(marker in text for marker in _RATE_LIMIT_MARKERS)


def error_recovery_report(run_dirs: list[Path]) -> None:
    total_steps = 0
    total_error_steps = 0
    rate_limit_error_steps = 0
    total_recovered = 0
    total_tasks = 0
    tasks_with_error = 0
    tasks_fully_recovered = 0  # every error step in the task was followed by a non-error step

    for run_dir in run_dirs:
        for task_dir in sorted(run_dir.glob("task_*")):
            trace = _load_trace(task_dir)
            steps = trace.get("steps")
            if not isinstance(steps, list) or not steps:
                continue
            total_tasks += 1
            total_steps += len(steps)

            error_indices = [i for i, s in enumerate(steps) if isinstance(s, dict) and _step_has_error(s)]
            if not error_indices:
                continue
            tasks_with_error += 1
            total_error_steps += len(error_indices)
            rate_limit_error_steps += sum(1 for i in error_indices if _step_error_is_rate_limit(steps[i]))

            task_recovered = 0
            for idx in error_indices:
                if idx + 1 < len(steps) and not _step_has_error(steps[idx + 1]):
                    task_recovered += 1
            total_recovered += task_recovered
            if task_recovered == len(error_indices):
                tasks_fully_recovered += 1

    print(f"Tasks analyzed:        {total_tasks}")
    print(f"Tasks with >=1 error:  {tasks_with_error} ({tasks_with_error / total_tasks:.1%})" if total_tasks else "n/a")
    print(f"Total steps:           {total_steps}")
    print(f"Total error steps:     {total_error_steps}")
    if total_error_steps:
        genuine_error_steps = total_error_steps - rate_limit_error_steps
        print(f"  of which rate-limit (429, infra artifact): {rate_limit_error_steps}")
        print(f"  of which genuine tool/reasoning errors:    {genuine_error_steps}")
        if total_steps:
            print(f"Genuine error rate (rate-limit excluded): {genuine_error_steps / total_steps:.2%}")
    if total_steps:
        print(f"Tool error rate:       {total_error_steps / total_steps:.2%}  (error steps / all steps)")
    if total_error_steps:
        print(f"Self-recovery rate:    {total_recovered / total_error_steps:.2%}  (next step had no error / total error steps)")
    if tasks_with_error:
        print(f"Tasks fully recovered: {tasks_fully_recovered}/{tasks_with_error}")


def stability_report(run_dirs: list[Path], gold_dir: Path, lam: float) -> None:
    # task_id -> list of scores across runs
    scores_by_task: dict[str, list[float]] = {}

    for run_dir in run_dirs:
        for task_dir in sorted(run_dir.glob("task_*")):
            task_id = task_dir.name
            gold_path = gold_dir / task_id / "gold.csv"
            pred_path = task_dir / "prediction.csv"
            if not gold_path.exists():
                continue
            result = score_task(gold_path, pred_path, lam=lam)
            scores_by_task.setdefault(task_id, []).append(result.score)

    print(f"{'Task':<12} {'Runs':>5} {'Mean':>8} {'Stdev':>8}  Scores")
    all_stdevs = []
    for task_id, scores in sorted(scores_by_task.items()):
        if len(scores) < 2:
            print(f"{task_id:<12} {len(scores):>5} {scores[0]:>8.4f} {'n/a':>8}  {scores}  (need >=2 runs for stdev)")
            continue
        mean = statistics.mean(scores)
        stdev = statistics.stdev(scores)
        all_stdevs.append(stdev)
        print(f"{task_id:<12} {len(scores):>5} {mean:>8.4f} {stdev:>8.4f}  {[round(s, 4) for s in scores]}")

    if all_stdevs:
        print(f"\nAvg stdev across {len(all_stdevs)} multi-run tasks: {statistics.mean(all_stdevs):.4f}")
    else:
        print("\nNo task had >=2 runs — provide multiple run_dirs covering the same task_ids to measure stability.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_err = sub.add_parser("error-recovery", help="Aggregate tool-error and self-recovery rate.")
    p_err.add_argument("run_dirs", nargs="+", type=Path)

    p_stab = sub.add_parser("stability", help="Score variance for the same task across repeated runs.")
    p_stab.add_argument("run_dirs", nargs="+", type=Path)
    p_stab.add_argument("--gold-dir", type=Path, default=Path("data/public/output"))
    p_stab.add_argument("--lambda", dest="lam", type=float, default=0.1)

    args = parser.parse_args()
    if args.command == "error-recovery":
        error_recovery_report(args.run_dirs)
    elif args.command == "stability":
        stability_report(args.run_dirs, args.gold_dir, args.lam)


if __name__ == "__main__":
    main()
