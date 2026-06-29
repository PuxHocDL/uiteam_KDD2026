from __future__ import annotations

import csv
import json
import multiprocessing
import shutil
import threading
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from time import perf_counter
from typing import Any

from data_agent_baseline.agents.model import AzureOpenAIModelAdapter, ModelMessage, OpenAIModelAdapter
from data_agent_baseline.agents.parsing import extract_json_object
from data_agent_baseline.agents.memory import AgentMemoryStore, memory_root_for_run_output
from data_agent_baseline.agents.orchestrator import MultiAgentConfig, MultiAgentOrchestrator
from data_agent_baseline.agents.dragin import DRAGINAgent, DRAGINAgentConfig
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig
from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.config import AppConfig
from data_agent_baseline.tools.registry import ToolRegistry, create_default_tool_registry


@dataclass(frozen=True, slots=True)
class TaskRunArtifacts:
    task_id: str
    task_output_dir: Path
    prediction_csv_path: Path | None
    trace_path: Path
    succeeded: bool
    failure_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_output_dir": str(self.task_output_dir),
            "prediction_csv_path": str(self.prediction_csv_path) if self.prediction_csv_path else None,
            "trace_path": str(self.trace_path),
            "succeeded": self.succeeded,
            "failure_reason": self.failure_reason,
        }


def create_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def resolve_run_id(run_id: str | None = None) -> str:
    if run_id is None:
        return create_run_id()

    normalized = run_id.strip()
    if not normalized:
        raise ValueError("run_id must not be empty.")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError("run_id must be a single directory name, not a path.")
    return normalized


def create_run_output_dir(output_root: Path, *, run_id: str | None = None) -> tuple[str, Path]:
    effective_run_id = resolve_run_id(run_id)
    run_output_dir = output_root / effective_run_id
    run_output_dir.mkdir(parents=True, exist_ok=False)
    return effective_run_id, run_output_dir


def build_model_adapter(config: AppConfig):
    if config.agent.api_version:
        return AzureOpenAIModelAdapter(
            model=config.agent.model,
            azure_endpoint=config.agent.api_base,
            api_key=config.agent.api_key,
            api_version=config.agent.api_version,
            temperature=config.agent.temperature,
            json_mode=config.agent.force_json,
            seed=config.agent.seed,
            max_tokens=config.agent.request_max_tokens,
        )
    return OpenAIModelAdapter(
        model=config.agent.model,
        api_base=config.agent.api_base,
        api_key=config.agent.api_key,
        temperature=config.agent.temperature,
        json_mode=config.agent.force_json,
        seed=config.agent.seed,
        max_tokens=config.agent.request_max_tokens,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(row)


def _failure_run_result_payload(task_id: str, failure_reason: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "answer": None,
        "steps": [],
        "failure_reason": failure_reason,
        "succeeded": False,
    }


def _configured_tasks(dataset: DABenchPublicDataset, config: AppConfig) -> list[PublicTask]:
    configured_difficulties = list(config.run.difficulties)
    if configured_difficulties:
        return dataset.iter_tasks(difficulties=configured_difficulties)
    return dataset.iter_tasks()


@dataclass(frozen=True, slots=True)
class AgentRoutingDecision:
    agent_mode: str
    reason: str
    signals: tuple[str, ...] = ()


def _iter_context_files(task: PublicTask) -> list[Path]:
    files: list[Path] = []
    for path in task.context_dir.rglob("*"):
        if path.is_file() and path.name.lower() != "gold.csv":
            files.append(path)
    return files


def _select_agent_routing(task: PublicTask, config: AppConfig) -> AgentRoutingDecision:
    requested_mode = config.agent.agent_mode.lower()
    if requested_mode in {"single", "react"}:
        return AgentRoutingDecision("react", "explicit react mode")
    if requested_mode == "multi":
        return AgentRoutingDecision("multi", "explicit multi-agent mode")
    if requested_mode == "dragin":
        return AgentRoutingDecision("dragin", "explicit dragin mode")
    if requested_mode != "hybrid_b":
        raise ValueError(
            f"Unknown agent_mode={config.agent.agent_mode!r}. "
            "Expected one of: single, react, multi, dragin, hybrid_b."
        )


    difficulty = task.difficulty.lower()
    if difficulty in {"easy", "medium"}:
        return AgentRoutingDecision("react", f"hybrid_b routes {difficulty} tasks to react")
    if difficulty == "extreme":
        return AgentRoutingDecision("dragin", "hybrid_b routes extreme tasks to dragin")

    files = _iter_context_files(task)
    suffixes = {path.suffix.lower() for path in files}
    structured_families = 0
    if ".csv" in suffixes:
        structured_families += 1
    if ".json" in suffixes:
        structured_families += 1
    if suffixes & {".db", ".sqlite", ".sqlite3"}:
        structured_families += 1

    has_sampled_db = any(
        path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        and any(tag in path.name.lower() for tag in ("_1k", "_sample", "_subset", "_small"))
        for path in files
    )
    has_long_doc = any(
        path.suffix.lower() in {".md", ".txt"} and path.stat().st_size > 10_000
        for path in files
    )
    has_large_structured_file = any(
        path.suffix.lower() in {".csv", ".json"} and path.stat().st_size > 200_000
        for path in files
    )
    question_lower = task.question.lower()
    broad_reasoning_question = any(
        phrase in question_lower
        for phrase in (
            "across all",
            "based on the available",
            "compare ",
            "for each combination",
            "distribution",
            "percentage within",
        )
    )

    signals: list[str] = []
    if structured_families >= 2:
        signals.append("multi_source")
    if has_sampled_db:
        signals.append("sampled_db")
    if has_long_doc:
        signals.append("long_doc")
    if has_large_structured_file and structured_families >= 2:
        signals.append("large_multi_source")
    if broad_reasoning_question and structured_families >= 3:
        signals.append("broad_cross_source_question")

    hard_signal_budget = max(1, config.agent.hybrid_hard_min_signals)
    if "sampled_db" in signals:
        return AgentRoutingDecision(
            "dragin",
            "hybrid_b routes this hard task to dragin because the context includes a sampled DB",
            tuple(signals),
        )

    if "long_doc" in signals and "multi_source" in signals:
        return AgentRoutingDecision(
            "dragin",
            "hybrid_b routes this hard task to dragin because it is both doc-heavy and multi-source",
            tuple(signals),
        )

    if broad_reasoning_question and len(signals) >= hard_signal_budget:
        return AgentRoutingDecision(
            "dragin",
            "hybrid_b routes this hard task to dragin because it looks like a broad cross-source reasoning task",
            tuple(signals),
        )

    return AgentRoutingDecision(
        "react",
        "hybrid_b keeps this hard task on react because context looks compact enough",
        tuple(signals),
    )


def _run_react_agent(
    *,
    task: PublicTask,
    config: AppConfig,
    model: Any,
    tools: ToolRegistry,
    memory_context: str | None,
):
    agent = ReActAgent(
        model=model,
        tools=tools,
        config=ReActAgentConfig(max_steps=config.agent.max_steps),
        memory_context=memory_context,
    )
    return agent.run(task)


def _run_dragin_agent(
    *,
    task: PublicTask,
    config: AppConfig,
    model: Any,
    tools: ToolRegistry,
    memory_context: str | None,
):
    agent = DRAGINAgent(
        model=model,
        tools=tools,
        config=DRAGINAgentConfig(
            max_steps=config.agent.max_steps,
            rind_threshold=config.agent.dragin_rind_threshold,
            qfs_top_n=config.agent.dragin_qfs_top_n,
            max_retrievals=config.agent.dragin_max_retrievals,
        ),
        memory_context=memory_context,
    )
    return agent.run(task)


def _run_single_task_core(
    *,
    task_id: str,
    config: AppConfig,
    model=None,
    tools: ToolRegistry | None = None,
    memory_context: str | None = None,
) -> dict[str, Any]:
    public_dataset = DABenchPublicDataset(config.dataset.root_path)
    task = public_dataset.get_task(task_id)
    effective_tools = tools or create_default_tool_registry()
    routing = _select_agent_routing(task, config)
    effective_model = model or build_model_adapter(config)

    if routing.agent_mode == "multi":
        orchestrator = MultiAgentOrchestrator(
            model=effective_model,
            tools=effective_tools,
            memory_context=memory_context,
            config=MultiAgentConfig(
                planner_max_steps=min(10, config.agent.max_steps // 3),
                analyst_max_steps=config.agent.max_steps - min(10, config.agent.max_steps // 3),
            ),
        )
        run_result = orchestrator.run(task)
    elif routing.agent_mode == "dragin":
        run_result = _run_dragin_agent(
            task=task,
            config=config,
            model=effective_model,
            tools=effective_tools,
            memory_context=memory_context,
        )
    else:
        run_result = _run_react_agent(
            task=task,
            config=config,
            model=effective_model,
            tools=effective_tools,
            memory_context=memory_context,
        )

    payload = run_result.to_dict()
    payload["agent_route"] = {
        "requested_mode": config.agent.agent_mode.lower(),
        "selected_mode": routing.agent_mode,
        "reason": routing.reason,
        "signals": list(routing.signals),
    }
    return payload


def _run_single_task_in_subprocess(
    task_id: str,
    config: AppConfig,
    queue: multiprocessing.Queue[Any],
    memory_context: str | None = None,
) -> None:
    try:
        queue.put(
            {
                "ok": True,
                "run_result": _run_single_task_core(
                    task_id=task_id,
                    config=config,
                    memory_context=memory_context,
                ),
            }
        )
    except BaseException as exc:  # noqa: BLE001
        queue.put(
            {
                "ok": False,
                "error": str(exc),
            }
        )


def _run_single_task_with_timeout(
    *,
    task_id: str,
    config: AppConfig,
    memory_context: str | None = None,
) -> dict[str, Any]:
    timeout_seconds = config.run.task_timeout_seconds
    if timeout_seconds <= 0:
        return _run_single_task_core(
            task_id=task_id,
            config=config,
            memory_context=memory_context,
        )

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    # NOTE: we intentionally do NOT use `with ThreadPoolExecutor(...) as pool`
    # because the context-manager exit calls shutdown(wait=True), which blocks
    # until the (unkillable) inner thread finishes its LLM calls — defeating
    # the whole point of the timeout and stalling the outer worker slot.
    # Instead we shutdown(wait=False, cancel_futures=True) so the outer slot
    # is freed immediately; the leaked thread will die on its next API timeout.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"task-{task_id}")
    try:
        future = pool.submit(
            _run_single_task_core,
            task_id=task_id,
            config=config,
            memory_context=memory_context,
        )
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            return _failure_run_result_payload(task_id, f"Task timed out after {timeout_seconds} seconds.")
        except Exception as exc:
            return _failure_run_result_payload(task_id, f"Task failed with uncaught error: {exc}")
    finally:
        # Release the outer worker slot without waiting for the inner thread.
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            # cancel_futures kwarg only exists on Python >= 3.9
            pool.shutdown(wait=False)


def _write_task_outputs(task_id: str, run_output_dir: Path, run_result: dict[str, Any]) -> TaskRunArtifacts:
    task_output_dir = run_output_dir / task_id
    task_output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = task_output_dir / "trace.json"
    _write_json(trace_path, run_result)

    prediction_csv_path: Path | None = None
    answer = run_result.get("answer")
    if isinstance(answer, dict):
        prediction_csv_path = task_output_dir / "prediction.csv"
        _write_csv(
            prediction_csv_path,
            list(answer.get("columns", [])),
            [list(row) for row in answer.get("rows", [])],
        )

    return TaskRunArtifacts(
        task_id=task_id,
        task_output_dir=task_output_dir,
        prediction_csv_path=prediction_csv_path,
        trace_path=trace_path,
        succeeded=bool(run_result.get("succeeded")),
        failure_reason=run_result.get("failure_reason"),
    )


def run_single_task(
    *,
    task_id: str,
    config: AppConfig,
    run_output_dir: Path,
    model=None,
    tools: ToolRegistry | None = None,
) -> TaskRunArtifacts:
    started_at = perf_counter()
    memory_store = AgentMemoryStore(memory_root_for_run_output(run_output_dir))
    try:
        task_for_memory = DABenchPublicDataset(config.dataset.root_path).get_task(task_id)
        memory_context = memory_store.build_context(task_for_memory)
    except Exception:
        task_for_memory = None
        memory_context = ""

    if model is None and tools is None:
        run_result = _run_single_task_with_timeout(
            task_id=task_id,
            config=config,
            memory_context=memory_context,
        )
    else:
        run_result = _run_single_task_core(
            task_id=task_id,
            config=config,
            model=model,
            tools=tools,
            memory_context=memory_context,
        )
    run_result["e2e_elapsed_seconds"] = round(perf_counter() - started_at, 3)
    artifact = _write_task_outputs(task_id, run_output_dir, run_result)
    if task_for_memory is not None:
        memory_store.add_from_run(task_for_memory, run_result)
    return artifact


def run_benchmark(
    *,
    config: AppConfig,
    model=None,
    tools: ToolRegistry | None = None,
    limit: int | None = None,
    progress_callback: Callable[[TaskRunArtifacts], None] | None = None,
    official_only: bool = False,
) -> tuple[Path, list[TaskRunArtifacts]]:
    if config.run.eval_mode:
        run_output_dir = config.run.output_dir
        run_output_dir.mkdir(parents=True, exist_ok=True)
        effective_run_id = "eval"
    else:
        effective_run_id, run_output_dir = create_run_output_dir(config.run.output_dir, run_id=config.run.run_id)

    dataset = DABenchPublicDataset(config.dataset.root_path)
    tasks = _configured_tasks(dataset, config)
    if official_only:
        import re as _re
        tasks = [t for t in tasks if _re.fullmatch(r"task_\d+", t.task_id)]
    if limit is not None:
        tasks = tasks[:limit]

    effective_workers = config.run.max_workers
    if effective_workers < 1:
        raise ValueError("max_workers must be at least 1.")
    if model is not None or tools is not None:
        effective_workers = 1

    # Sort tasks to run easy tasks first
    _DIFFICULTY_PRIORITY = {"easy": 0, "medium": 1, "hard": 2, "extreme": 3}
    tasks.sort(key=lambda t: _DIFFICULTY_PRIORITY.get(t.difficulty.lower(), 2))
    task_ids = [task.task_id for task in tasks]

    task_artifacts: list[TaskRunArtifacts]
    if effective_workers == 1:
        shared_model = model or build_model_adapter(config)
        shared_tools = tools or create_default_tool_registry()
        task_artifacts = []
        for task_id in task_ids:
            artifact = run_single_task(
                task_id=task_id,
                config=config,
                run_output_dir=run_output_dir,
                model=shared_model,
                tools=shared_tools,
            )
            task_artifacts.append(artifact)
            if progress_callback is not None:
                progress_callback(artifact)
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            future_to_index = {
                executor.submit(
                    run_single_task,
                    task_id=task_id,
                    config=config,
                    run_output_dir=run_output_dir,
                ): index
                for index, task_id in enumerate(task_ids)
            }
            indexed_artifacts: list[TaskRunArtifacts | None] = [None] * len(task_ids)
            for future in as_completed(future_to_index):
                artifact = future.result()
                indexed_artifacts[future_to_index[future]] = artifact
                if progress_callback is not None:
                    progress_callback(artifact)
            task_artifacts = [artifact for artifact in indexed_artifacts if artifact is not None]

    summary_path = run_output_dir / "summary.json"
    _write_json(
        summary_path,
        {
            "run_id": effective_run_id,
            "task_count": len(task_artifacts),
            "succeeded_task_count": sum(1 for artifact in task_artifacts if artifact.succeeded),
            "max_workers": effective_workers,
            "tasks": [artifact.to_dict() for artifact in task_artifacts],
        },
    )
    return run_output_dir, task_artifacts


# ---------------------------------------------------------------------------
# Consensus (self-consistency) helpers
# ---------------------------------------------------------------------------

def _normalize_prediction_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    lowered = text.lower()
    if not lowered or lowered in {"null", "none", "nan", "nat", "<na>"}:
        return ""

    try:
        number = Decimal(text)
        return str(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        pass

    if len(text) >= 6 and any(char in text for char in ("-", "/", ":", ",")):
        try:
            import pandas as pd

            dt = pd.to_datetime(text, errors="coerce")
            if pd.notna(dt):
                if dt.tzinfo is not None:
                    dt = dt.tz_convert("UTC")
                    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
                    return dt.strftime("%Y-%m-%d")
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            pass

    return text.replace("\r", "").replace("\n", "")


def _read_prediction_column_signatures(path: Path) -> tuple[list[tuple[str, ...]], int]:
    columns, rows = _read_prediction_table(path)
    if not columns:
        return [], 0
    if not rows:
        return [tuple() for _ in columns], 0

    column_values = [[] for _ in columns]
    for row in rows:
        for idx in range(len(columns)):
            value = row[idx] if idx < len(row) else ""
            column_values[idx].append(_normalize_prediction_cell(value))

    return [tuple(sorted(values)) for values in column_values], len(rows)


def _predictions_match(path_a: Path, path_b: Path) -> bool:
    """Check if two prediction CSVs have identical normalized content.

    This uses only the candidate prediction files, never any gold/ground-truth file.
    It ignores row ordering, column ordering, headers, and normalizes dates/floats.
    """
    if not path_a.exists() and not path_b.exists():
        return True
    if not path_a.exists() or not path_b.exists():
        return False
        
    try:
        cols_a, num_rows_a = _read_prediction_column_signatures(path_a)
        cols_b, num_rows_b = _read_prediction_column_signatures(path_b)
        
        # Sort the columns so the column order doesn't matter
        cols_a.sort()
        cols_b.sort()
        
        return (cols_a == cols_b) and (num_rows_a == num_rows_b)
    except Exception:
        return False


@dataclass(frozen=True, slots=True)
class ConsensusCandidate:
    candidate_id: str
    task_dir: Path
    prediction_csv_path: Path | None
    succeeded: bool
    failure_reason: str | None
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    e2e_elapsed_seconds: float | None
    num_steps: int | None
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsensusSelection:
    candidate: ConsensusCandidate
    keep_column_indices: tuple[int, ...]
    keep_row_indices: tuple[int, ...] | None
    reason: str
    selected_by: str


def _truncate_text(value: Any, max_chars: int) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _read_prediction_table(path: Path) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    for encoding in ("utf-8", "latin-1"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                rows = list(csv.reader(handle))
            break
        except UnicodeDecodeError:
            continue
    else:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            rows = list(csv.reader(handle))

    if not rows:
        return tuple(), tuple()
    header = tuple(str(cell) for cell in rows[0])
    body = tuple(tuple(str(cell) for cell in row) for row in rows[1:])
    return header, body


def _safe_load_trace(task_dir: Path) -> dict[str, Any]:
    trace_path = task_dir / "trace.json"
    if not trace_path.exists():
        return {}
    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _summarize_trace_evidence(trace_data: dict[str, Any], *, max_items: int = 3) -> tuple[str, ...]:
    steps = trace_data.get("steps")
    if not isinstance(steps, list):
        return tuple()

    evidence: list[str] = []
    interesting_actions = {"execute_context_sql", "execute_universal_sql", "execute_python", "answer"}
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        action = str(step.get("action", ""))
        if action not in interesting_actions:
            continue

        action_input = step.get("action_input")
        observation = step.get("observation")
        parts = [f"action={action}"]

        if isinstance(action_input, dict):
            if action == "execute_python":
                code = action_input.get("code", "")
                parts.append("code=" + _truncate_text(code, 600))
            elif "sql" in action_input:
                parts.append("sql=" + _truncate_text(action_input.get("sql", ""), 600))
            elif action == "answer":
                cols = action_input.get("columns")
                rows = action_input.get("rows")
                row_count = len(rows) if isinstance(rows, list) else "?"
                parts.append(f"answer_columns={cols}; answer_rows={row_count}")

        if isinstance(observation, dict):
            content = observation.get("content")
            if isinstance(content, dict):
                compact_content = {
                    key: content.get(key)
                    for key in ("success", "error", "row_count", "column_count", "status")
                    if key in content
                }
                output = content.get("output")
                if isinstance(output, str) and output.strip():
                    compact_content["output"] = _truncate_text(output.strip(), 400)
                if compact_content:
                    parts.append("obs=" + _truncate_text(json.dumps(compact_content, ensure_ascii=False), 500))

        evidence.append(" | ".join(parts))
        if len(evidence) >= max_items:
            break

    return tuple(reversed(evidence))


def _artifact_to_consensus_candidate(round_index: int, artifact: TaskRunArtifacts) -> ConsensusCandidate:
    csv_path = artifact.prediction_csv_path or (artifact.task_output_dir / "prediction.csv")
    columns: tuple[str, ...] = tuple()
    rows: tuple[tuple[str, ...], ...] = tuple()
    if csv_path.exists():
        try:
            columns, rows = _read_prediction_table(csv_path)
        except Exception:
            columns, rows = tuple(), tuple()

    trace_data = _safe_load_trace(artifact.task_output_dir)
    steps = trace_data.get("steps")
    return ConsensusCandidate(
        candidate_id=f"R{round_index}",
        task_dir=artifact.task_output_dir,
        prediction_csv_path=csv_path if csv_path.exists() else None,
        succeeded=artifact.succeeded,
        failure_reason=artifact.failure_reason,
        columns=columns,
        rows=rows,
        e2e_elapsed_seconds=trace_data.get("e2e_elapsed_seconds"),
        num_steps=len(steps) if isinstance(steps, list) else None,
        evidence=_summarize_trace_evidence(trace_data),
    )


def _candidate_signature(candidate: ConsensusCandidate) -> str:
    if candidate.prediction_csv_path is None:
        return "__missing__"
    try:
        columns, row_count = _read_prediction_column_signatures(candidate.prediction_csv_path)
        return json.dumps([sorted(columns), row_count], ensure_ascii=False, default=str)
    except Exception:
        return json.dumps([candidate.columns, candidate.rows], ensure_ascii=False)


def _fallback_selection(
    candidates: list[ConsensusCandidate],
    preferred_dir: Path | None,
) -> ConsensusSelection | None:
    if not candidates:
        return None

    selected = None
    if preferred_dir is not None:
        for candidate in candidates:
            if candidate.task_dir == preferred_dir:
                selected = candidate
                break

    if selected is None:
        for candidate in reversed(candidates):
            if candidate.prediction_csv_path is not None:
                selected = candidate
                break

    if selected is None:
        selected = candidates[-1]

    keep_columns = tuple(range(len(selected.columns)))
    return ConsensusSelection(
        candidate=selected,
        keep_column_indices=keep_columns,
        keep_row_indices=None,
        reason="fallback selection",
        selected_by="fallback",
    )


def _render_candidate_for_selector(candidate: ConsensusCandidate) -> str:
    if candidate.prediction_csv_path is None:
        return (
            f"{candidate.candidate_id}: missing prediction.csv; "
            f"succeeded={candidate.succeeded}; failure={candidate.failure_reason}"
        )

    row_count = len(candidate.rows)
    column_lines = [
        f"{idx}: {name}"
        for idx, name in enumerate(candidate.columns)
    ]

    preview_rows: list[dict[str, Any]] = []
    preview_limit = 12
    for row_index, row in enumerate(candidate.rows[:preview_limit]):
        preview_rows.append({
            "row_index": row_index,
            "values": [_truncate_text(cell, 180) for cell in row],
        })
    if row_count > preview_limit:
        for row_index, row in enumerate(candidate.rows[-3:], start=max(row_count - 3, preview_limit)):
            preview_rows.append({
                "row_index": row_index,
                "values": [_truncate_text(cell, 180) for cell in row],
            })

    return json.dumps(
        {
            "candidate_id": candidate.candidate_id,
            "succeeded": candidate.succeeded,
            "failure_reason": candidate.failure_reason,
            "elapsed_seconds": candidate.e2e_elapsed_seconds,
            "steps": candidate.num_steps,
            "columns_by_index": column_lines,
            "row_count": row_count,
            "rows_preview": preview_rows,
            "evidence": list(candidate.evidence),
        },
        ensure_ascii=False,
        indent=2,
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    # Delegate to the shared hardened parser (fence-strip + bracket repair) so
    # the consensus selector tolerates the same malformed replies the engines do.
    return extract_json_object(text)


def _coerce_index_tuple(raw_value: Any, *, upper_bound: int, allow_none: bool = False) -> tuple[int, ...] | None:
    if raw_value is None and allow_none:
        return None
    if not isinstance(raw_value, list):
        raise ValueError("index field must be a list")

    values: list[int] = []
    for item in raw_value:
        if not isinstance(item, int):
            raise ValueError("index field must contain integers")
        if item < 0 or item >= upper_bound:
            raise ValueError(f"index {item} out of range 0..{upper_bound - 1}")
        if item not in values:
            values.append(item)
    if not values and not allow_none:
        raise ValueError("index field must not be empty")
    return tuple(values)


def _select_consensus_candidate_with_model(
    *,
    task: Any,
    config: AppConfig,
    candidates: list[ConsensusCandidate],
) -> ConsensusSelection | None:
    valid_candidates = [candidate for candidate in candidates if candidate.prediction_csv_path is not None]
    if not valid_candidates:
        return None

    system_prompt = (
        "You are a strict consensus selector for data-analysis benchmark answers. "
        "You do not know the hidden expected answer. Choose the candidate that best answers the question, "
        "using the candidate table shape, values, and trace evidence. The scorer compares column "
        "values and penalizes extra columns, so keep only the columns the question asks for. "
        "A semantically strong candidate with an extra support column can be selected and trimmed. "
        "Do not choose by majority alone: repeated candidates can share the same mistake. "
        "When numeric candidates disagree, prefer the one whose trace evidence shows a direct "
        "calculation from the relevant raw table/file and applies the question wording literally. "
        "Do not invent new values. You may only select a candidate, keep a subset of its columns, "
        "and optionally keep a subset of its rows when the preview makes an obvious extra row clear. "
        "Return only JSON with keys: candidate_id, keep_column_indices, keep_row_indices, reason. "
        "Use null for keep_row_indices to keep all rows."
    )
    rendered_candidates = "\n\n".join(_render_candidate_for_selector(candidate) for candidate in candidates)
    user_prompt = (
        f"Question: {task.question}\n"
        f"Difficulty: {task.difficulty}\n\n"
        "Candidates:\n"
        f"{rendered_candidates}\n\n"
        "Return JSON now. Example:\n"
        "{\"candidate_id\":\"R2\",\"keep_column_indices\":[0,2],"
        "\"keep_row_indices\":null,\"reason\":\"...\"}"
    )

    model = build_model_adapter(config)
    raw_response = model.complete([
        ModelMessage(role="system", content=system_prompt),
        ModelMessage(role="user", content=user_prompt),
    ], json_object=True)
    payload = _extract_json_object(raw_response)

    candidate_id = str(payload.get("candidate_id", ""))
    selected = next((candidate for candidate in valid_candidates if candidate.candidate_id == candidate_id), None)
    if selected is None:
        raise ValueError(f"selector chose unknown candidate_id: {candidate_id}")

    keep_columns = _coerce_index_tuple(
        payload.get("keep_column_indices"),
        upper_bound=len(selected.columns),
    )
    keep_rows = _coerce_index_tuple(
        payload.get("keep_row_indices"),
        upper_bound=len(selected.rows),
        allow_none=True,
    )
    reason = _truncate_text(payload.get("reason", ""), 1000)

    return ConsensusSelection(
        candidate=selected,
        keep_column_indices=keep_columns or tuple(range(len(selected.columns))),
        keep_row_indices=keep_rows,
        reason=reason,
        selected_by="llm_selector",
    )


def _write_projected_prediction(dest_csv_path: Path, selection: ConsensusSelection) -> None:
    candidate = selection.candidate
    if not candidate.columns:
        return

    column_indices = selection.keep_column_indices or tuple(range(len(candidate.columns)))
    if selection.keep_row_indices is None:
        row_indices = range(len(candidate.rows))
    else:
        row_indices = selection.keep_row_indices

    rows = [
        [candidate.rows[row_idx][col_idx] if col_idx < len(candidate.rows[row_idx]) else "" for col_idx in column_indices]
        for row_idx in row_indices
    ]
    columns = [candidate.columns[col_idx] for col_idx in column_indices]
    _write_csv(dest_csv_path, columns, rows)


def _write_selection_metadata(dest_dir: Path, selection: ConsensusSelection) -> None:
    _write_json(
        dest_dir / "consensus_selection.json",
        {
            "selected_by": selection.selected_by,
            "candidate_id": selection.candidate.candidate_id,
            "source_dir": str(selection.candidate.task_dir),
            "keep_column_indices": list(selection.keep_column_indices),
            "keep_row_indices": list(selection.keep_row_indices) if selection.keep_row_indices is not None else None,
            "reason": selection.reason,
        },
    )


def _run_tasks_parallel(
    *,
    task_ids: list[str],
    config: AppConfig,
    run_output_dir: Path,
    max_workers: int,
    progress_callback: Callable[[TaskRunArtifacts], None] | None = None,
) -> dict[str, TaskRunArtifacts]:
    """Run a subset of tasks in parallel and return {task_id: artifact}."""
    results: dict[str, TaskRunArtifacts] = {}
    if not task_ids:
        return results
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_tid = {
            executor.submit(
                run_single_task,
                task_id=tid,
                config=config,
                run_output_dir=run_output_dir,
            ): tid
            for tid in task_ids
        }
        for future in as_completed(future_to_tid):
            artifact = future.result()
            results[artifact.task_id] = artifact
            if progress_callback is not None:
                progress_callback(artifact)
    return results


def run_consensus(
    *,
    config: AppConfig,
    max_rounds: int = 3,
    use_selector: bool = True,
    progress_callback: Callable[[str, int, int, int, TaskRunArtifacts | None], None] | None = None,
    official_only: bool = False,
) -> tuple[Path, dict[str, Path]]:
    """Run benchmark up to *max_rounds* times with self-consistency.

    For each task, runs continuously until consensus is reached,
    preventing fast tasks from waiting for slow tasks.
    """
    dataset = DABenchPublicDataset(config.dataset.root_path)
    tasks = _configured_tasks(dataset, config)
    if official_only:
        import re as _re
        tasks = [t for t in tasks if _re.fullmatch(r"task_\d+", t.task_id)]
    effective_workers = max(config.run.max_workers, 1)

    _DIFF_PRI = {"easy": 0, "medium": 1, "hard": 2, "extreme": 3}
    tasks.sort(key=lambda t: _DIFF_PRI.get(t.difficulty.lower(), 2))
    all_task_ids = [t.task_id for t in tasks]

    main_id = f"consensus_{create_run_id()}"
    _, main_dir = create_run_output_dir(config.run.output_dir, run_id=main_id)
    
    round_dirs = []
    for i in range(max_rounds):
        r_dir = main_dir / f"round_{i+1}"
        r_dir.mkdir(parents=True, exist_ok=True)
        round_dirs.append(r_dir)

    final_dir = main_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    locked: dict[str, Path] = {}
    locked_counter = [0]
    selector_sem = threading.BoundedSemaphore(max(1, min(4, effective_workers)))
    selector_state = {"disabled": False, "disable_reason": ""}
    selector_state_lock = threading.Lock()

    def _selector_is_disabled() -> bool:
        with selector_state_lock:
            return bool(selector_state["disabled"])

    def _disable_selector(reason: str) -> None:
        with selector_state_lock:
            selector_state["disabled"] = True
            selector_state["disable_reason"] = reason

    def _select_for_task(
        tid: str,
        task,
        candidates: list[ConsensusCandidate],
        preferred_dir: Path | None,
    ) -> ConsensusSelection | None:
        fallback = _fallback_selection(candidates, preferred_dir)
        if not use_selector or _selector_is_disabled():
            return fallback

        valid_candidates = [candidate for candidate in candidates if candidate.prediction_csv_path is not None]
        if len(valid_candidates) < 2:
            return fallback

        signatures = Counter(_candidate_signature(candidate) for candidate in valid_candidates)
        has_disagreement = len(signatures) > 1
        has_extra_columns = any(len(candidate.columns) > 1 for candidate in valid_candidates)
        if not has_disagreement and not has_extra_columns:
            return fallback

        try:
            with selector_sem:
                selected = _select_consensus_candidate_with_model(
                    task=task,
                    config=config,
                    candidates=candidates,
                )
            return selected or fallback
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            lowered = error_text.lower()
            if (
                "billing cycle spend limit" in lowered
                or "insufficient_quota" in lowered
                or "missing model api key" in lowered
            ):
                _disable_selector(error_text[:300])
            if fallback is not None:
                return ConsensusSelection(
                    candidate=fallback.candidate,
                    keep_column_indices=fallback.keep_column_indices,
                    keep_row_indices=fallback.keep_row_indices,
                    reason=f"selector failed for {tid}: {_truncate_text(error_text, 300)}",
                    selected_by="fallback_after_selector_error",
                )
            return None

    def process_single_task(tid: str) -> tuple[str, Path | None]:
        task = dataset.get_task(tid)
        history: list[TaskRunArtifacts] = []
        locked_dir: Path | None = None
        runs_done = 0
        last_artifact = None

        for round_idx in range(max_rounds):
            artifact = run_single_task(
                task_id=tid,
                config=config,
                run_output_dir=round_dirs[round_idx],
            )
            last_artifact = artifact
            runs_done += 1
            csv_path = artifact.prediction_csv_path
            task_dir = artifact.task_output_dir
            history.append(artifact)

            if len(history) >= 3:
                current_csv = csv_path or (task_dir / "prediction.csv")
                match_count = 0
                for prev_artifact in history[:-1]:
                    prev_csv_path = prev_artifact.prediction_csv_path or (
                        prev_artifact.task_output_dir / "prediction.csv"
                    )
                    if _predictions_match(current_csv, prev_csv_path):
                        match_count += 1
                if match_count >= 2:
                    locked_dir = task_dir
                    locked_counter[0] += 1

            if progress_callback:
                progress_callback(f"R{round_idx+1}", round_idx+1, max_rounds, locked_counter[0], artifact)

            if locked_dir:
                break

        if not locked_dir:
            for artifact in reversed(history):
                if artifact.prediction_csv_path and artifact.prediction_csv_path.exists():
                    locked_dir = artifact.task_output_dir
                    break
            else:
                if history:
                    locked_dir = history[-1].task_output_dir

        # Emit fake progress for skipped rounds to keep the total count correct
        for round_idx in range(runs_done, max_rounds):
            if progress_callback:
                progress_callback("Skipped", round_idx+1, max_rounds, locked_counter[0], last_artifact)

        candidates = [
            _artifact_to_consensus_candidate(round_index, artifact)
            for round_index, artifact in enumerate(history, start=1)
        ]
        selection = _select_for_task(tid, task, candidates, locked_dir)

        if selection is not None:
            locked_dir = selection.candidate.task_dir

        if locked_dir and locked_dir.exists():
            dest = final_dir / tid
            try:
                shutil.copytree(locked_dir, dest)
            except FileExistsError:
                pass
            if selection is not None:
                _write_projected_prediction(dest / "prediction.csv", selection)
                _write_selection_metadata(dest, selection)
                
        return tid, locked_dir

    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        future_to_tid = {
            executor.submit(process_single_task, tid): tid
            for tid in all_task_ids
        }
        for future in as_completed(future_to_tid):
            tid, locked_dir = future.result()
            if locked_dir is not None:
                locked[tid] = locked_dir

    # Write summary
    _write_json(
        final_dir / "summary.json",
        {
            "run_id": main_id,
            "mode": "consensus",
            "max_rounds": max_rounds,
            "selector_enabled": use_selector,
            "selector_disabled_reason": selector_state["disable_reason"],
            "round_dirs": [str(d) for d in round_dirs],
            "task_count": len(locked),
            "tasks": [
                {"task_id": tid, "source_dir": str(locked[tid])}
                for tid in all_task_ids if tid in locked
            ],
        },
    )

    return final_dir, locked
