from pathlib import Path
from time import perf_counter

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.benchmark.scoring import score_run
from data_agent_baseline.config import load_app_config, setup_eval_logging
from data_agent_baseline.run.runner import (
    TaskRunArtifacts,
    _configured_tasks,
    create_run_output_dir,
    run_benchmark,
    run_consensus,
    run_single_task,
)
from data_agent_baseline.tools.filesystem import list_context_tree

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
ARTIFACT_RUNS_DIR = ARTIFACTS_DIR / "runs"

app = typer.Typer(add_completion=False, no_args_is_help=False)
console = Console()


def _status_value(path: Path) -> str:
    return "present" if path.exists() else "missing"


def _format_compact_rate(completed_count: int, elapsed_seconds: float) -> str:
    if completed_count <= 0 or elapsed_seconds <= 0:
        return "rate=0.0 task/min"
    return f"rate={(completed_count / elapsed_seconds) * 60:.1f} task/min"


def _format_last_task(artifact: TaskRunArtifacts | None) -> str:
    if artifact is None:
        return "last=-"
    status = "ok" if artifact.succeeded else "fail"
    return f"last={artifact.task_id} ({status})"


def _build_compact_progress_fields(
    *,
    completed_count: int,
    succeeded_count: int,
    failed_count: int,
    task_total: int,
    max_workers: int,
    elapsed_seconds: float,
    last_artifact: TaskRunArtifacts | None,
) -> dict[str, str]:
    remaining_count = max(task_total - completed_count, 0)
    running_count = min(max_workers, remaining_count)
    queued_count = max(remaining_count - running_count, 0)
    return {
        "ok": str(succeeded_count),
        "fail": str(failed_count),
        "run": str(running_count),
        "queue": str(queued_count),
        "speed": _format_compact_rate(completed_count, elapsed_seconds),
        "last": _format_last_task(last_artifact),
    }


@app.callback()
def cli() -> None:
    """Utilities for working with the local DABench baseline project."""
    setup_eval_logging()


@app.command()
def status(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="YAML config path."),
) -> None:
    """Show the local project layout and public dataset presence."""
    app_config = load_app_config(config)
    config_path = config.resolve()
    public_dataset = DABenchPublicDataset(app_config.dataset.root_path)

    table = Table(title="DABench Baseline Status")
    table.add_column("Item")
    table.add_column("Path")
    table.add_column("State")

    table.add_row("project_root", str(PROJECT_ROOT), "ready")
    table.add_row("data_dir", str(DATA_DIR), _status_value(DATA_DIR))
    table.add_row("configs_dir", str(CONFIGS_DIR), _status_value(CONFIGS_DIR))
    table.add_row("artifacts_dir", str(ARTIFACTS_DIR), _status_value(ARTIFACTS_DIR))
    table.add_row("runs_dir", str(ARTIFACT_RUNS_DIR), _status_value(ARTIFACT_RUNS_DIR))
    table.add_row("dataset_root", str(app_config.dataset.root_path), _status_value(app_config.dataset.root_path))
    table.add_row("config_path", str(config_path), _status_value(config_path))

    console.print(table)

    if public_dataset.exists:
        console.print(f"Public tasks: {len(public_dataset.list_task_ids())}")
        counts = public_dataset.task_counts()
        if counts:
            rendered_counts = ", ".join(
                f"{difficulty}={count}" for difficulty, count in sorted(counts.items())
            )
            console.print(f"Public task counts: {rendered_counts}")


@app.command("inspect-task")
def inspect_task(
    task_id: str,
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="YAML config path."),
) -> None:
    """Show task metadata and available context files."""
    app_config = load_app_config(config)
    dataset = DABenchPublicDataset(app_config.dataset.root_path)
    task = dataset.get_task(task_id)
    console.print(f"Task: {task.task_id}")
    console.print(f"Difficulty: {task.difficulty}")
    console.print(f"Question: {task.question}")
    context_listing = list_context_tree(task)
    table = Table(title=f"Context Files for {task.task_id}")
    table.add_column("Path")
    table.add_column("Kind")
    table.add_column("Size")
    for entry in context_listing["entries"]:
        table.add_row(str(entry["path"]), str(entry["kind"]), str(entry["size"] or ""))
    console.print(table)


@app.command("run-task")
def run_task_command(
    task_id: str,
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="YAML config path."),
) -> None:
    """Run the configured agent on one task."""
    app_config = load_app_config(config)
    if app_config.run.eval_mode:
        run_output_dir = app_config.run.output_dir
        run_output_dir.mkdir(parents=True, exist_ok=True)
    else:
        try:
            _, run_output_dir = create_run_output_dir(app_config.run.output_dir, run_id=app_config.run.run_id)
        except (ValueError, FileExistsError) as exc:
            raise typer.BadParameter(str(exc), param_hint="run.run_id") from exc
    artifacts = run_single_task(task_id=task_id, config=app_config, run_output_dir=run_output_dir)

    console.print(f"Run output: {run_output_dir}")
    console.print(f"Task output: {artifacts.task_output_dir}")
    if artifacts.prediction_csv_path is not None:
        console.print(f"Prediction CSV: {artifacts.prediction_csv_path}")
    else:
        console.print("Prediction CSV: not generated")
    if artifacts.failure_reason is not None:
        console.print(f"Failure: {artifacts.failure_reason}")


@app.command("run-benchmark")
def run_benchmark_command(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="YAML config path."),
    limit: int | None = typer.Option(None, min=1, help="Maximum number of tasks to run."),
    official_only: bool = typer.Option(
        False,
        "--official-only",
        help="Run only official organizer tasks (task_<number> without variant suffixes like _e, _x, _h, _m).",
    ),
) -> None:
    """Run the configured agent on multiple tasks from the config selection."""
    app_config = load_app_config(config)
    dataset = DABenchPublicDataset(app_config.dataset.root_path)
    task_total = len(_configured_tasks(dataset, app_config))
    if official_only:
        import re as _re
        all_tasks = _configured_tasks(dataset, app_config)
        all_tasks = [t for t in all_tasks if _re.fullmatch(r"task_\d+", t.task_id)]
        task_total = len(all_tasks)
        console.print(f"[dim]Filtered to official tasks only: {task_total} tasks.[/dim]")
    if limit is not None:
        task_total = min(task_total, limit)
    effective_workers = app_config.run.max_workers

    progress_columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]|[/dim]"),
        TextColumn("[green]ok={task.fields[ok]}[/green]"),
        TextColumn("[red]fail={task.fields[fail]}[/red]"),
        TextColumn("[cyan]run={task.fields[run]}[/cyan]"),
        TextColumn("[yellow]queue={task.fields[queue]}[/yellow]"),
        TextColumn("[dim]|[/dim]"),
        TextColumn("{task.fields[speed]}"),
        TextColumn("[dim]| elapsed[/dim]"),
        TimeElapsedColumn(),
        TextColumn("[dim]| eta[/dim]"),
        TimeRemainingColumn(),
        TextColumn("[dim]|[/dim]"),
        TextColumn("{task.fields[last]}"),
    ]
    with Progress(*progress_columns, console=console) as progress:
        progress_task_id = progress.add_task(
            "Benchmark",
            total=task_total,
            completed=0,
            **_build_compact_progress_fields(
                completed_count=0,
                succeeded_count=0,
                failed_count=0,
                task_total=task_total,
                max_workers=effective_workers,
                elapsed_seconds=0.0,
                last_artifact=None,
            ),
        )

        completion_count = 0
        succeeded_count = 0
        failed_count = 0
        start_time = perf_counter()

        def on_task_complete(artifact) -> None:
            nonlocal completion_count, succeeded_count, failed_count
            completion_count += 1
            if artifact.succeeded:
                succeeded_count += 1
            else:
                failed_count += 1
            progress.update(
                progress_task_id,
                completed=completion_count,
                description="Benchmark",
                refresh=True,
                **_build_compact_progress_fields(
                    completed_count=completion_count,
                    succeeded_count=succeeded_count,
                    failed_count=failed_count,
                    task_total=task_total,
                    max_workers=effective_workers,
                    elapsed_seconds=perf_counter() - start_time,
                    last_artifact=artifact,
                ),
            )

        try:
            run_output_dir, artifacts = run_benchmark(
                config=app_config,
                limit=limit,
                progress_callback=on_task_complete,
                official_only=official_only,
            )
        except (ValueError, FileExistsError) as exc:
            raise typer.BadParameter(str(exc), param_hint="run.run_id") from exc
        progress.update(
            progress_task_id,
            completed=task_total,
            description="Benchmark",
            refresh=True,
            **_build_compact_progress_fields(
                completed_count=task_total,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                task_total=task_total,
                max_workers=effective_workers,
                elapsed_seconds=perf_counter() - start_time,
                last_artifact=artifacts[-1] if artifacts else None,
            ),
        )
    console.print(f"Run output: {run_output_dir}")
    console.print(f"Tasks attempted: {len(artifacts)}")
    console.print(f"Succeeded tasks: {sum(1 for item in artifacts if item.succeeded)}")


@app.command("run-consensus")
def run_consensus_command(
    config: Path = typer.Option(..., exists=True, dir_okay=False, help="YAML config path."),
    max_rounds: int = typer.Option(3, min=3, max=10, help="Max rounds before stopping."),
    use_selector: bool = typer.Option(
        True,
        "--selector/--no-selector",
        help="Use one extra LLM pass per ambiguous task to choose and trim the best consensus candidate.",
    ),
    official_only: bool = typer.Option(
        False,
        "--official-only",
        help="Run only official organizer tasks (task_<number> without variant suffixes).",
    ),
) -> None:
    """Run benchmark multiple times and merge the best consensus candidates."""
    app_config = load_app_config(config)
    dataset = DABenchPublicDataset(app_config.dataset.root_path)
    all_tasks = _configured_tasks(dataset, app_config)
    if official_only:
        import re as _re
        all_tasks = [t for t in all_tasks if _re.fullmatch(r"task_\\d+", t.task_id)]
        console.print(f"[dim]Filtered to official tasks only: {len(all_tasks)} tasks.[/dim]")
    task_total = len(all_tasks)

    progress_columns = [
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]|[/dim]"),
        TextColumn("{task.fields[info]}"),
        TextColumn("[dim]| elapsed[/dim]"),
        TimeElapsedColumn(),
    ]
    completed_total = 0
    locked_count = 0
    current_round = 0
    start_time = perf_counter()

    with Progress(*progress_columns, console=console) as progress:
        progress_task_id = progress.add_task(
            "Consensus",
            total=task_total * max_rounds,
            completed=0,
            info=f"round=1/{max_rounds}  locked=0/{task_total}  pending={task_total}",
        )

        def on_consensus_progress(
            label: str,
            round_num: int,
            round_total: int,
            current_locked: int,
            artifact: TaskRunArtifacts | None,
        ) -> None:
            nonlocal completed_total, current_round, locked_count
            if artifact is not None:
                completed_total += 1
                current_round = round_num
                locked_count = current_locked
                status = "ok" if artifact.succeeded else "fail"
                progress.update(
                    progress_task_id,
                    completed=completed_total,
                    description=f"Consensus R{round_num}",
                    info=(
                        f"round={round_num}/{max_rounds}  "
                        f"locked={locked_count}/{task_total}  "
                        f"last={artifact.task_id}({status})"
                    ),
                    refresh=True,
                )

        try:
            final_dir, locked = run_consensus(
                config=app_config,
                max_rounds=max_rounds,
                use_selector=use_selector,
                progress_callback=on_consensus_progress,
                official_only=official_only,
            )
        except (ValueError, FileExistsError) as exc:
            raise typer.BadParameter(str(exc)) from exc

    elapsed = perf_counter() - start_time
    console.print(f"\n[bold green]Consensus complete[/bold green] in {elapsed:.0f}s")
    console.print(f"Final merged output: [cyan]{final_dir}[/cyan]")
    console.print(f"Tasks locked: {len(locked)}/{task_total}")
    console.print(f"\nRun [cyan]uv run dabench eval --run-dir {final_dir} --official-only[/cyan] to score.")


DATA_OUTPUT_DIR = DATA_DIR / "public" / "output"
DATA_INPUT_DIR = DATA_DIR / "public" / "input"

DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2, "extreme": 3}
DIFFICULTY_STYLES = {"easy": "green", "medium": "yellow", "hard": "red", "extreme": "magenta"}


@app.command("eval")
def eval_command(
    run_dir: Path = typer.Option(
        None,
        help="Path to a specific run directory. If omitted, uses the latest run.",
    ),
    gold_dir: Path = typer.Option(
        None,
        help="Path to gold output directory. Defaults to data/public/output.",
    ),
    lam: float = typer.Option(0.1, "--lambda", help="Penalty weight for extra columns."),
    official_only: bool = typer.Option(
        False,
        "--official-only",
        help="Score only official organizer tasks (task_<number> without variant suffixes).",
    ),
) -> None:
    """Evaluate predictions against gold answers and show scores."""
    import re

    if gold_dir is None:
        gold_dir = DATA_OUTPUT_DIR
    if not gold_dir.exists():
        console.print(f"[red]Gold directory not found: {gold_dir}[/red]")
        raise typer.Exit(1)

    if run_dir is None:
        # Pick the latest run
        run_dirs = sorted(ARTIFACT_RUNS_DIR.glob("*"), key=lambda p: p.name)
        if not run_dirs:
            console.print("[red]No runs found in artifacts/runs/[/red]")
            raise typer.Exit(1)
        run_dir = run_dirs[-1]
        console.print(f"Using latest run: [cyan]{run_dir.name}[/cyan]")

    if not run_dir.exists():
        console.print(f"[red]Run directory not found: {run_dir}[/red]")
        raise typer.Exit(1)

    results = score_run(run_dir, gold_dir, lam=lam, input_dir=DATA_INPUT_DIR)
    if not results:
        console.print("[yellow]No tasks found to evaluate.[/yellow]")
        raise typer.Exit(1)

    if official_only:
        results = [r for r in results if re.fullmatch(r"task_\d+", r.task_id)]
        if not results:
            console.print("[yellow]No official tasks found after filtering.[/yellow]")
            raise typer.Exit(1)
        console.print("[dim]Filtered to official tasks only (task_<number>).[/dim]")

    table = Table(title=f"Eval: {run_dir.name}  (lambda={lam})" + (" [official only]" if official_only else ""))
    table.add_column("Task", style="cyan")
    table.add_column("Diff", justify="center")
    table.add_column("Time(s)", justify="right")
    table.add_column("Steps", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("Gold R×C", justify="right")
    table.add_column("Pred R×C", justify="right")
    table.add_column("Matched", justify="right")
    table.add_column("Note")

    def _task_sort_key(r):
        s = r.task_id.removeprefix("task_")
        parts = s.split("_", 1)
        diff_order = DIFFICULTY_ORDER.get((r.difficulty or "").lower(), 99)
        return (diff_order, int(parts[0]), parts[1] if len(parts) > 1 else "")

    for r in sorted(results, key=_task_sort_key):
        score_style = "green" if r.score >= 0.9 else ("yellow" if r.score > 0 else "red")
        diff_style = DIFFICULTY_STYLES.get(r.difficulty, "dim")
        diff_label = r.difficulty[:3].upper() if r.difficulty else "?"
        
        time_str = f"{r.e2e_elapsed_seconds:.1f}" if r.e2e_elapsed_seconds is not None else "-"
        steps_str = str(r.num_steps) if r.num_steps is not None else "-"
        
        notes = []
        if r.error:
            notes.append(r.error)
        if r.run_error:
            notes.append(r.run_error)
        note_str = " | ".join(notes)

        table.add_row(
            r.task_id,
            f"[{diff_style}]{diff_label}[/{diff_style}]",
            time_str,
            steps_str,
            f"[{score_style}]{r.score:.4f}[/{score_style}]",
            f"{r.recall:.4f}",
            f"{r.gold_rows}×{r.gold_cols}",
            f"{r.pred_rows}×{r.pred_cols}",
            str(r.matched_cols),
            note_str,
        )

    console.print(table)

    scores = [r.score for r in results]
    avg = sum(scores) / len(scores)
    perfect = sum(1 for s in scores if s >= 1.0)
    console.print(f"\nTasks evaluated: {len(results)}")
    console.print(f"Average score:  [bold]{avg:.4f}[/bold]")
    console.print(f"Perfect (1.0):  {perfect}/{len(results)}")

    # Per-difficulty breakdown
    diff_groups: dict[str, list[float]] = {}
    for r in results:
        key = r.difficulty or "unknown"
        diff_groups.setdefault(key, []).append(r.score)

    if len(diff_groups) > 1 or "" not in diff_groups:
        diff_table = Table(title="Score by Difficulty")
        diff_table.add_column("Difficulty", justify="center")
        diff_table.add_column("Count", justify="right")
        diff_table.add_column("Avg Score", justify="right")
        diff_table.add_column("Perfect", justify="right")
        diff_table.add_column("Zero", justify="right")

        for diff in sorted(diff_groups, key=lambda d: DIFFICULTY_ORDER.get(d, 99)):
            s = diff_groups[diff]
            d_avg = sum(s) / len(s)
            d_perfect = sum(1 for x in s if x >= 1.0)
            d_zero = sum(1 for x in s if x == 0.0)
            style = DIFFICULTY_STYLES.get(diff, "dim")
            score_style = "green" if d_avg >= 0.7 else ("yellow" if d_avg >= 0.4 else "red")
            diff_table.add_row(
                f"[{style}]{diff.upper()}[/{style}]",
                str(len(s)),
                f"[{score_style}]{d_avg:.4f}[/{score_style}]",
                f"{d_perfect}/{len(s)}",
                f"{d_zero}/{len(s)}",
            )

        console.print(diff_table)


@app.command("eval-consensus")
def eval_consensus_command(
    run_dir: Path = typer.Option(
        None,
        help="Path to a consensus run directory. If omitted, uses the latest consensus run.",
    ),
    gold_dir: Path = typer.Option(
        None,
        help="Path to gold output directory. Defaults to data/public/output.",
    ),
    lam: float = typer.Option(0.1, "--lambda", help="Penalty weight for extra columns."),
    official_only: bool = typer.Option(
        False,
        "--official-only",
        help="Score only official organizer tasks (task_<number> without variant suffixes).",
    ),
) -> None:
    """Evaluate a consensus run, showing scores for all rounds and the final output."""
    import re
    import json

    if gold_dir is None:
        gold_dir = DATA_OUTPUT_DIR
    if not gold_dir.exists():
        console.print(f"[red]Gold directory not found: {gold_dir}[/red]")
        raise typer.Exit(1)

    if run_dir is None:
        run_dirs = sorted(ARTIFACT_RUNS_DIR.glob("consensus_*"), key=lambda p: p.name)
        if not run_dirs:
            console.print("[red]No consensus runs found in artifacts/runs/[/red]")
            raise typer.Exit(1)
        run_dir = run_dirs[-1]
        console.print(f"Using latest consensus run: [cyan]{run_dir.name}[/cyan]")

    if not run_dir.exists():
        console.print(f"[red]Run directory not found: {run_dir}[/red]")
        raise typer.Exit(1)

    summary_file = run_dir / "summary.json"
    max_rounds = 0
    if summary_file.exists():
        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            max_rounds = data.get("max_rounds", 0)
        except Exception:
            pass
            
    if max_rounds == 0:
        round_dirs = list(run_dir.glob("round_*"))
        max_rounds = len(round_dirs)
        
    if max_rounds == 0:
        console.print("[yellow]No rounds found in this consensus run directory.[/yellow]")
        raise typer.Exit(1)

    # Score each round
    round_scores: dict[int, dict[str, float]] = {}
    for i in range(1, max_rounds + 1):
        r_dir = run_dir / f"round_{i}"
        if r_dir.exists():
            scores = score_run(r_dir, gold_dir, lam=lam, input_dir=DATA_INPUT_DIR)
            round_scores[i] = {r.task_id: r.score for r in scores}

    # Score final
    final_dir = run_dir / "final"
    final_results = score_run(final_dir, gold_dir, lam=lam, input_dir=DATA_INPUT_DIR) if final_dir.exists() else []
    
    all_tasks = {}
    if final_results:
        all_tasks = {r.task_id: r for r in final_results}
    else:
        # Use round_1 to get task metadata if final is missing (e.g. still running)
        r1_dir = run_dir / "round_1"
        if r1_dir.exists():
            all_tasks = {r.task_id: r for r in score_run(r1_dir, gold_dir, lam=lam, input_dir=DATA_INPUT_DIR)}
            for r in all_tasks.values():
                r.score = 0.0

    results = list(all_tasks.values())
    if not results:
        console.print("[yellow]No tasks found to evaluate.[/yellow]")
        raise typer.Exit(1)

    if official_only:
        results = [r for r in results if re.fullmatch(r"task_\d+", r.task_id)]
        if not results:
            console.print("[yellow]No official tasks found after filtering.[/yellow]")
            raise typer.Exit(1)

    table = Table(title=f"Consensus Eval: {run_dir.name}  (lambda={lam})" + (" [official only]" if official_only else ""))
    table.add_column("Task", style="cyan")
    table.add_column("Diff", justify="center")
    
    for i in range(1, max_rounds + 1):
        table.add_column(f"R{i}", justify="right")
        
    table.add_column("Final", justify="right")
    table.add_column("Time(s)", justify="right")
    table.add_column("Steps", justify="right")
    table.add_column("Note")

    def _task_sort_key(r):
        s = r.task_id.removeprefix("task_")
        parts = s.split("_", 1)
        diff_order = DIFFICULTY_ORDER.get((r.difficulty or "").lower(), 99)
        return (diff_order, int(parts[0]), parts[1] if len(parts) > 1 else "")

    for r in sorted(results, key=_task_sort_key):
        diff_style = DIFFICULTY_STYLES.get(r.difficulty, "dim")
        diff_label = r.difficulty[:3].upper() if r.difficulty else "?"
        
        row_data = [
            r.task_id,
            f"[{diff_style}]{diff_label}[/{diff_style}]",
        ]
        
        for i in range(1, max_rounds + 1):
            s = round_scores.get(i, {}).get(r.task_id)
            if s is None:
                row_data.append("-")
            else:
                c = "green" if s >= 0.9 else ("yellow" if s > 0 else "red")
                row_data.append(f"[{c}]{s:.4f}[/{c}]")
                
        final_score = r.score if final_dir.exists() else 0.0
        fc = "green" if final_score >= 0.9 else ("yellow" if final_score > 0 else "red")
        row_data.append(f"[{fc}]{final_score:.4f}[/{fc}]" if final_dir.exists() else "-")
        
        time_str = f"{r.e2e_elapsed_seconds:.1f}" if r.e2e_elapsed_seconds is not None else "-"
        steps_str = str(r.num_steps) if r.num_steps is not None else "-"
        row_data.append(time_str)
        row_data.append(steps_str)
        
        notes = []
        if r.error:
            notes.append(r.error)
        if r.run_error:
            notes.append(r.run_error)
        row_data.append(" | ".join(notes))
        
        table.add_row(*row_data)

    console.print(table)
    
    console.print(f"\nTasks evaluated: {len(results)}")
    
    valid_task_ids = {r.task_id for r in results}
    
    avg_table = Table(title="Average Scores by Round")
    avg_table.add_column("Metric", style="cyan")
    for i in range(1, max_rounds + 1):
        avg_table.add_column(f"R{i}", justify="right")
    avg_table.add_column("Final", justify="right")
    
    avgs = ["Avg Score"]
    perfs = ["Perfect (1.0)"]
    
    for i in range(1, max_rounds + 1):
        scores = [score for tid, score in round_scores.get(i, {}).items() if tid in valid_task_ids]
        if not scores:
            avgs.append("-")
            perfs.append("-")
        else:
            avg = sum(scores) / len(scores)
            perf = sum(1 for s in scores if s >= 1.0)
            avgs.append(f"{avg:.4f}")
            perfs.append(f"{perf}/{len(scores)}")
            
    if final_dir.exists():
        f_scores = [r.score for r in final_results if r.task_id in valid_task_ids]
        if not f_scores:
            avgs.append("-")
            perfs.append("-")
        else:
            f_avg = sum(f_scores) / len(f_scores)
            f_perf = sum(1 for s in f_scores if s >= 1.0)
            avgs.append(f"[bold]{f_avg:.4f}[/bold]")
            perfs.append(f"[bold]{f_perf}/{len(f_scores)}[/bold]")
    else:
        avgs.append("-")
        perfs.append("-")
        
    avg_table.add_row(*avgs)
    avg_table.add_row(*perfs)
    console.print(avg_table)

    # Per-difficulty breakdown
    diff_groups: dict[str, list[str]] = {}
    for r in results:
        key = r.difficulty or "unknown"
        diff_groups.setdefault(key, []).append(r.task_id)

    if len(diff_groups) > 1 or "unknown" not in diff_groups:
        final_score_map = {r.task_id: r.score for r in final_results} if final_results else {}
        
        diff_table = Table(title="Average Scores by Difficulty")
        diff_table.add_column("Difficulty", justify="center")
        diff_table.add_column("Count", justify="right")
        
        for i in range(1, max_rounds + 1):
            diff_table.add_column(f"R{i}", justify="right")
        diff_table.add_column("Final", justify="right")

        for diff in sorted(diff_groups, key=lambda d: DIFFICULTY_ORDER.get(d, 99)):
            task_ids = diff_groups[diff]
            style = DIFFICULTY_STYLES.get(diff, "dim")
            
            row_data = [
                f"[{style}]{diff.upper()}[/{style}]",
                str(len(task_ids)),
            ]
            
            for i in range(1, max_rounds + 1):
                scores = [round_scores.get(i, {}).get(tid) for tid in task_ids]
                valid_scores = [s for s in scores if s is not None]
                if not valid_scores:
                    row_data.append("-")
                else:
                    d_avg = sum(valid_scores) / len(valid_scores)
                    c = "green" if d_avg >= 0.7 else ("yellow" if d_avg >= 0.4 else "red")
                    row_data.append(f"[{c}]{d_avg:.4f}[/{c}]")
                    
            if final_dir.exists():
                f_scores = [final_score_map.get(tid) for tid in task_ids]
                valid_f_scores = [s for s in f_scores if s is not None]
                if not valid_f_scores:
                    row_data.append("-")
                else:
                    f_avg = sum(valid_f_scores) / len(valid_f_scores)
                    c = "green" if f_avg >= 0.7 else ("yellow" if f_avg >= 0.4 else "red")
                    row_data.append(f"[{c}][bold]{f_avg:.4f}[/bold][/{c}]")
            else:
                row_data.append("-")
                
            diff_table.add_row(*row_data)

        console.print(diff_table)


def main() -> None:
    app()
