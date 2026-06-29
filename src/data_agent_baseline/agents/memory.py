from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask

_MEMORY_LOCK = threading.Lock()


def memory_key_for_task(task_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in task_id)


def memory_root_for_run_output(run_output_dir: Path) -> Path:
    if run_output_dir.name.startswith("round_"):
        return run_output_dir.parent / "_agent_memory" / "by_task_v2"
    return run_output_dir / "_agent_memory" / "by_task_v2"


def _truncate(value: Any, max_chars: int = 800) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    content = observation.get("content")
    if not isinstance(content, dict):
        return {}

    compact: dict[str, Any] = {}
    for key in (
        "path",
        "success",
        "error",
        "row_count",
        "rows_returned",
        "column_count",
        "columns",
        "table_count",
        "total_rows",
        "total_columns",
        "truncated",
    ):
        if key in content:
            compact[key] = content[key]

    output = content.get("output")
    if isinstance(output, str) and output.strip():
        compact["output"] = _truncate(output.strip(), 600)

    rows = content.get("rows")
    if isinstance(rows, list):
        compact["rows_preview"] = rows[:5]

    tables = content.get("tables")
    if isinstance(tables, list):
        compact["tables"] = [
            {
                "name": table.get("name"),
                "row_count": table.get("row_count"),
                "columns": [
                    column.get("name")
                    for column in table.get("columns", [])[:12]
                    if isinstance(column, dict)
                ],
            }
            for table in tables[:8]
            if isinstance(table, dict)
        ]

    return compact


def _compact_action_input(action: str, action_input: dict[str, Any]) -> dict[str, Any]:
    if action == "execute_python":
        return {"code": _truncate(action_input.get("code", ""), 1200)}
    if "sql" in action_input:
        compact = dict(action_input)
        compact["sql"] = _truncate(compact.get("sql", ""), 1200)
        return compact
    return {key: _truncate(value, 500) for key, value in action_input.items()}


class AgentMemoryStore:
    """Small JSONL memory of prior attempts for the exact same task.

    Memory is intentionally untrusted: it stores schemas, useful queries, and
    answer shapes from earlier consensus rounds of this task. Prompts tell the
    agent to verify everything against the current context.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path_for_task(self, task_id: str) -> Path:
        return self.root / f"{memory_key_for_task(task_id)}.jsonl"

    def build_context(self, task: PublicTask, *, max_records: int = 6, max_chars: int = 5000) -> str:
        path = self._path_for_task(task.task_id)
        if not path.exists():
            return ""

        records: list[dict[str, Any]] = []
        try:
            with _MEMORY_LOCK:
                lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines[-max_records * 3:]:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if record.get("task_id") == task.task_id:
                        records.append(record)
                except json.JSONDecodeError:
                    continue
        except Exception:
            return ""

        if not records:
            return ""

        selected = records[-max_records:]
        chunks = [
            "## Agent Memory (unverified hints)",
            "Prior attempts from this exact task in earlier consensus rounds. "
            "These are NOT ground truth. Use them only to reuse schemas, file paths, "
            "joins, failure fixes, and validation ideas; verify every filter and result "
            "against the current context before answering.",
        ]
        for record in selected:
            chunks.append(
                "\n".join(
                    [
                        f"- Prior attempt for task: {record.get('task_id')} ({record.get('difficulty')})",
                        f"  Question: {_truncate(record.get('question', ''), 350)}",
                        "  Answer shape: "
                        f"{record.get('answer_row_count', '?')} rows x "
                        f"{len(record.get('answer_columns', []))} cols "
                        f"{record.get('answer_columns', [])}",
                    ]
                )
            )
            preview = record.get("answer_preview")
            if isinstance(preview, list) and preview:
                chunks.append("  Answer preview: " + _truncate(preview[:3], 500))
            actions = record.get("useful_actions")
            if isinstance(actions, list) and actions:
                for action in actions[:4]:
                    chunks.append(
                        "  Useful action: "
                        + _truncate(json.dumps(action, ensure_ascii=False), 900)
                    )

        rendered = "\n".join(chunks)
        return rendered[:max_chars]

    def add_from_run(self, task: PublicTask, run_result: dict[str, Any]) -> None:
        steps = run_result.get("steps")
        if not isinstance(steps, list) or not steps:
            return

        answer = run_result.get("answer")
        answer_columns: list[str] = []
        answer_row_count: int | None = None
        answer_preview: list[Any] = []
        if isinstance(answer, dict):
            columns = answer.get("columns")
            rows = answer.get("rows")
            if isinstance(columns, list):
                answer_columns = [str(column) for column in columns]
            if isinstance(rows, list):
                answer_row_count = len(rows)
                answer_preview = rows[:5]

        useful_actions: list[dict[str, Any]] = []
        interesting = {
            "profile_context",
            "profile_database",
            "profile_csv",
            "profile_json",
            "execute_context_sql",
            "execute_universal_sql",
            "execute_python",
            "answer",
        }
        for step in steps:
            if not isinstance(step, dict):
                continue
            action = str(step.get("action", ""))
            if action not in interesting:
                continue
            action_input = step.get("action_input")
            observation = step.get("observation")
            useful_actions.append(
                {
                    "action": action,
                    "action_input": _compact_action_input(
                        action,
                        action_input if isinstance(action_input, dict) else {},
                    ),
                    "observation": _compact_observation(
                        observation if isinstance(observation, dict) else {},
                    ),
                }
            )

        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_id": task.task_id,
            "difficulty": task.difficulty,
            "question": task.question,
            "succeeded": bool(run_result.get("succeeded")),
            "failure_reason": run_result.get("failure_reason"),
            "answer_columns": answer_columns,
            "answer_row_count": answer_row_count,
            "answer_preview": answer_preview,
            "useful_actions": useful_actions[-6:],
        }

        path = self._path_for_task(task.task_id)
        try:
            with _MEMORY_LOCK:
                self.root.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            return
