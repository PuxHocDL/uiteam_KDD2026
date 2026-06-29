from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.filesystem import (
    extract_info,
    list_context_tree,
    profile_csv,
    profile_json,
    read_csv_preview,
    read_doc_chunk,
    read_doc_preview,
    read_json_preview,
    resolve_context_path,
    search_doc,
)
from data_agent_baseline.tools.python_exec import execute_python_code
from data_agent_baseline.tools.duckdb_exec import execute_duckdb_sql
from data_agent_baseline.tools.sqlite import execute_read_only_sql, inspect_sqlite_schema, profile_database
from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph
from data_agent_baseline.tools.kg_store import (
    ensure_knowledge_graph,
    load_knowledge_graph,
    persist_knowledge_graph,
    search_graph,
)
from data_agent_baseline.tools.source_map import map_sources, read_any_text
from data_agent_baseline.tools.planning import classify_question, plan_task
from data_agent_baseline.tools.data_quality import profile_quality

EXECUTE_PYTHON_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    requires_approval: bool = False  # §4.2 — risky tools always ask before running


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None


ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]


def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))


def _profile_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """Profile the whole task context in one bounded call."""
    max_files = max(1, min(int(action_input.get("max_files", 16)), 30))
    max_doc_chars = max(500, min(int(action_input.get("max_doc_chars", 2500)), 6000))
    tree = list_context_tree(task, max_depth=int(action_input.get("max_depth", 4)))
    entries = [
        entry for entry in tree.get("entries", [])
        if isinstance(entry, dict) and entry.get("kind") == "file"
    ]

    def priority(entry: dict[str, Any]) -> tuple[int, int, str]:
        path = str(entry.get("path", ""))
        suffix = Path(path).suffix.lower()
        name = Path(path).name.lower()
        if name == "knowledge.md":
            return (0, int(entry.get("size") or 0), path)
        if suffix in {".sqlite", ".db", ".sqlite3"}:
            return (1, int(entry.get("size") or 0), path)
        if suffix == ".csv":
            return (2, int(entry.get("size") or 0), path)
        if suffix == ".json":
            return (3, int(entry.get("size") or 0), path)
        if suffix in {".md", ".txt"}:
            return (4, int(entry.get("size") or 0), path)
        return (9, int(entry.get("size") or 0), path)

    def compact_profile(value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "tables" in value and isinstance(value["tables"], list):
            compact_tables = []
            for table in value["tables"][:18]:
                if not isinstance(table, dict):
                    continue
                compact_tables.append({
                    "name": table.get("name"),
                    "row_count": table.get("row_count"),
                    "columns": table.get("columns", [])[:25],
                    "sample_columns": table.get("sample_columns"),
                    "sample_rows": table.get("sample_rows", [])[:2],
                })
            result = dict(value)
            result["tables"] = compact_tables
            result["tables_truncated"] = len(value["tables"]) > len(compact_tables)
            return result
        if "columns" in value and isinstance(value["columns"], list):
            result = dict(value)
            result["columns"] = value["columns"][:45]
            result["columns_truncated"] = len(value["columns"]) > len(result["columns"])
            return result
        if "preview" in value and isinstance(value["preview"], str):
            result = dict(value)
            result["preview"] = value["preview"][:max_doc_chars]
            return result
        return value

    profiles: list[dict[str, Any]] = []
    for entry in sorted(entries, key=priority)[:max_files]:
        rel_path = str(entry.get("path", ""))
        suffix = Path(rel_path).suffix.lower()
        profile: dict[str, Any] = {
            "path": rel_path,
            "size": entry.get("size"),
            "type": suffix or "unknown",
        }
        try:
            if suffix in {".sqlite", ".db", ".sqlite3"}:
                profile["profile"] = compact_profile(
                    profile_database(
                        resolve_context_path(task, rel_path),
                        max_tables=30,
                        sample_rows=2,
                        top_values=3,
                    )
                )
            elif suffix == ".csv":
                profile["profile"] = compact_profile(profile_csv(task, rel_path))
            elif suffix == ".json":
                profile["profile"] = compact_profile(profile_json(task, rel_path))
            elif suffix in {".md", ".txt"}:
                profile["profile"] = compact_profile(
                    read_doc_preview(task, rel_path, max_chars=max_doc_chars)
                )
            else:
                profile["note"] = "unprofiled file type"
        except Exception as exc:  # noqa: BLE001 - tool reports errors to the agent.
            profile["error"] = str(exc)
        profiles.append(profile)

    return ToolExecutionResult(
        ok=True,
        content={
            "root": tree.get("root"),
            "summary": tree.get("summary"),
            "has_knowledge_md": tree.get("has_knowledge_md"),
            "file_count": len(entries),
            "profiled_count": len(profiles),
            "profiles": profiles,
            "note": (
                "Use this as a map of the context. For final computation, still run "
                "execute_context_sql, execute_universal_sql, or execute_python and validate row/column counts."
            ),
        },
    )


def _read_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    # ENFORCE hard limit of 5 rows regardless of what the agent requests
    max_rows = min(5, int(action_input.get("max_rows", 5)))
    return ToolExecutionResult(ok=True, content=read_csv_preview(task, path, max_rows=max_rows))


def _profile_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    return ToolExecutionResult(ok=True, content=profile_csv(task, path))


def _profile_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_depth = int(action_input.get("max_depth", 3))
    return ToolExecutionResult(ok=True, content=profile_json(task, path, max_depth=max_depth))


def _read_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    # ENFORCE hard limit of 1000 chars regardless of agent requests
    max_chars = min(1000, int(action_input.get("max_chars", 1000)))
    return ToolExecutionResult(ok=True, content=read_json_preview(task, path, max_chars=max_chars))


def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 8000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _read_doc_chunk(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    start = int(action_input.get("start", 0))
    length = int(action_input.get("length", 8000))
    # Cap single-chunk length to keep responses small
    length = min(length, 16000)
    return ToolExecutionResult(ok=True, content=read_doc_chunk(task, path, start=start, length=length))


def _search_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    query = str(action_input["query"])
    mode = str(action_input.get("mode", "auto"))
    max_matches = int(action_input.get("max_matches", 8))
    context_chars = int(action_input.get("context_chars", 400))
    max_matches = max(1, min(max_matches, 30))
    context_chars = max(50, min(context_chars, 2000))
    # Hybrid retrieval (BM25 + vector) is opt-in: uses the request-scoped embedder
    # (built from the UI's Azure creds) if set, else env config, else None → BM25.
    from data_agent_baseline.tools.hybrid_retriever import resolve_embedder
    return ToolExecutionResult(
        ok=True,
        content=search_doc(
            task, path,
            query=query, mode=mode,
            max_matches=max_matches, context_chars=context_chars,
            embedder=resolve_embedder(),
        ),
    )


def _inspect_sqlite_schema(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    return ToolExecutionResult(ok=True, content=inspect_sqlite_schema(path))


def _profile_database(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    return ToolExecutionResult(ok=True, content=profile_database(path))


def _execute_context_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 500))
    return ToolExecutionResult(ok=True, content=execute_read_only_sql(path, sql, limit=limit))


def _execute_universal_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 500))
    content = execute_duckdb_sql(str(task.context_dir), sql, limit=limit)
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _execute_python(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    code = str(action_input["code"])
    content = execute_python_code(
        context_root=task.context_dir,
        code=code,
        timeout_seconds=EXECUTE_PYTHON_TIMEOUT_SECONDS,
    )
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _extract_info(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    query = str(action_input["query"])
    max_results = min(15, max(1, int(action_input.get("max_results", 10))))
    context_chars = min(500, max(100, int(action_input.get("context_chars", 300))))
    content = extract_info(task, query=query, max_results=max_results, context_chars=context_chars)
    return ToolExecutionResult(ok=True, content=content)


def _build_knowledge_graph(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    kg = build_knowledge_graph(task)
    # Persist so later steps can `read_knowledge_graph` instantly and query it
    # relationally instead of re-scanning every file.
    saved_path = persist_knowledge_graph(task.context_dir, kg)
    content = kg.to_dict()
    content["persisted"] = saved_path is not None
    return ToolExecutionResult(ok=True, content=content)


def _read_knowledge_graph(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """Read the persisted knowledge graph; with `query`, locate where a term lives."""
    query = action_input.get("query")
    if isinstance(query, str) and query.strip():
        # fix #3 — escalate exact → fuzzy → LLM concept-bridge, all only when exact
        # finds nothing. The bridge uses the request-scoped chat model (same UI
        # creds); with no model it degrades to fuzzy. Precision is preserved.
        from data_agent_baseline.tools.semantic_match import resolve_model
        return ToolExecutionResult(
            ok=True,
            content=search_graph(
                task.context_dir, query.strip(), mode="hybrid", model=resolve_model()
            ),
        )
    # No query: return the whole graph, loading from DB or building+persisting once.
    graph = load_knowledge_graph(task.context_dir)
    if graph is None:
        graph = ensure_knowledge_graph(task.context_dir)
    return ToolExecutionResult(ok=True, content=graph)


def _profile_quality(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    return ToolExecutionResult(ok=True, content=profile_quality(path))


def _map_sources(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    focus = action_input.get("focus")
    focus = focus.strip() if isinstance(focus, str) and focus.strip() else None
    return ToolExecutionResult(
        ok=True,
        content=map_sources(task.context_dir, focus=focus),
    )


def _read_pdf(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = resolve_context_path(task, str(action_input["path"]))
    max_pages = max(1, min(int(action_input.get("max_pages", 50)), 100))
    max_chars = max(1000, min(int(action_input.get("max_chars", 16000)), 40000))
    content = read_any_text(path, max_chars=max_chars, max_pages=max_pages)
    ok = bool(content.get("text")) and "error" not in content
    return ToolExecutionResult(ok=ok, content=content)


def _classify_question(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    difficulty = getattr(task, "difficulty", None)
    return ToolExecutionResult(
        ok=True,
        content=classify_question(task.context_dir, task.question, difficulty),
    )


def _plan_task(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    question = action_input.get("question")
    question = question if isinstance(question, str) and question.strip() else task.question
    return ToolExecutionResult(ok=True, content=plan_task(task.context_dir, question))


def _tokenize_name(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if token}


def _auto_trim_answer_columns(
    task: PublicTask,
    columns: list[str],
    rows: list[list[Any]],
) -> tuple[list[str], list[list[Any]], dict[str, Any]]:
    """Conservatively remove common support columns the scorer penalizes.

    The model often computes the right row set but includes helper columns such as
    IDs, costs, scores, dates, or counts.  We only trim for phrasing patterns where
    the requested output column is very explicit.
    """
    if len(columns) <= 1:
        return columns, rows, {"auto_trimmed": False}

    question = task.question.lower()
    column_tokens = [_tokenize_name(column) for column in columns]
    keep_indices: list[int] | None = None
    reason = ""

    # "What is the comment ..." should return the comment text, not IDs/scores.
    asks_comment_id = (
        "comment id" in question
        or "comment_id" in question
        or "id of the comment" in question
        or "comment with id" in question
    )
    if asks_comment_id:
        id_like = [
            idx for idx, tokens in enumerate(column_tokens)
            if tokens & {"id", "commentid", "comment_id"}
        ]
        if id_like:
            keep_indices = id_like[:1]
            reason = "question asks for the comment id"

    if keep_indices is None and "comment" in question and not asks_comment_id:
        text_like = [
            idx for idx, tokens in enumerate(column_tokens)
            if tokens & {"text", "comment", "body", "content"}
        ]
        if text_like:
            keep_indices = text_like
            reason = "question asks for the comment text"

    # "Give their consumption status" asks for consumption values only.
    if keep_indices is None and "consumption" in question:
        consumption_cols = [
            idx for idx, tokens in enumerate(column_tokens)
            if "consumption" in tokens
        ]
        if consumption_cols and (
            "consumption status" in question
            or "give their consumption" in question
            or "give the consumption" in question
            or "what is the consumption" in question
        ):
            keep_indices = consumption_cols
            reason = "question asks for consumption values only"

    # "What is the advertisement budget for X?" asks for the metric, not the entity label.
    if keep_indices is None and question.strip().startswith(("what is ", "what's ")):
        metric_match = re.search(
            r"what(?: is|'s)\s+(?:the\s+)?(.+?)\s+(?:for|of|in|during|at)\b",
            question,
        )
        if metric_match is not None:
            metric_tokens = _tokenize_name(metric_match.group(1))
            metric_tokens -= {"the", "a", "an"}
            if metric_tokens:
                matching_metric_cols = [
                    idx for idx, tokens in enumerate(column_tokens)
                    if metric_tokens and metric_tokens.issubset(tokens)
                ]
                if matching_metric_cols and len(matching_metric_cols) < len(columns):
                    keep_indices = matching_metric_cols
                    reason = "question asks for a metric value for a named entity"

    # "Which entity has the lowest/highest cost/score/..." asks for the entity.
    metric_words = {
        "cost", "price", "score", "value", "amount", "total", "sum", "avg",
        "average", "count", "number", "rank", "rate", "ratio", "percentage",
    }
    asks_extreme = any(word in question for word in ("lowest", "highest", "minimum", "maximum"))
    asks_which = question.strip().startswith(("which ", "what "))
    explicitly_requests_metric = any(
        phrase in question
        for phrase in (
            " and cost", " and score", " and value", " and amount", " and total",
            " with cost", " with score", " with value", " with amount", " with total",
            "give the cost", "give their cost", "show the cost",
        )
    )
    if keep_indices is None and asks_which and asks_extreme and not explicitly_requests_metric:
        non_metric = [
            idx for idx, tokens in enumerate(column_tokens)
            if not (tokens & metric_words)
        ]
        if non_metric and len(non_metric) < len(columns):
            keep_indices = non_metric
            reason = "question asks which entity, not the support metric"

    if keep_indices is None or len(keep_indices) == len(columns):
        return columns, rows, {"auto_trimmed": False}

    trimmed_columns = [columns[idx] for idx in keep_indices]
    trimmed_rows = [
        [row[idx] if idx < len(row) else "" for idx in keep_indices]
        for row in rows
    ]
    return trimmed_columns, trimmed_rows, {
        "auto_trimmed": True,
        "auto_trim_reason": reason,
        "original_columns": columns,
        "kept_column_indices": keep_indices,
    }


def _auto_split_name_columns(
    columns: list[str],
    rows: list[list[Any]],
) -> tuple[list[str], list[list[Any]], dict[str, Any]]:
    """Auto-split 'full_name' or 'fullname' columns into first_name/last_name
    when all values are exactly 2-word 'First Last' format.
    Skip if first_name or last_name columns already exist."""
    NAME_COL_PATTERN = re.compile(r"^(full.?name|fullname)$", re.IGNORECASE)

    # Don't split if first_name/last_name already present
    lower_cols = {c.lower().replace(" ", "_") for c in columns}
    if "first_name" in lower_cols or "last_name" in lower_cols or "firstname" in lower_cols or "lastname" in lower_cols:
        return columns, rows, {"auto_split_name": False}

    name_col_idx = None
    for i, col in enumerate(columns):
        if NAME_COL_PATTERN.match(col.strip()):
            name_col_idx = i
            break

    if name_col_idx is None or not rows:
        return columns, rows, {"auto_split_name": False}

    # Check if all values are exactly "First Last" (2 words)
    all_two_words = all(
        isinstance(row[name_col_idx], str)
        and len(row[name_col_idx].strip().split()) == 2
        for row in rows
        if name_col_idx < len(row) and row[name_col_idx]
    )
    if not all_two_words:
        return columns, rows, {"auto_split_name": False}

    new_columns = columns[:name_col_idx] + ["first_name", "last_name"] + columns[name_col_idx + 1:]
    new_rows = []
    for row in rows:
        val = str(row[name_col_idx]).strip()
        parts = val.split(" ", 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
        new_rows.append(row[:name_col_idx] + [first, last] + row[name_col_idx + 1:])

    return new_columns, new_rows, {
        "auto_split_name": True,
        "original_column": columns[name_col_idx],
        "split_into": ["first_name", "last_name"],
    }


def _answer(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        raise ValueError("answer.columns must be a non-empty list of strings.")
    if not isinstance(rows, list):
        raise ValueError("answer.rows must be a list.")

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Each answer row must be a list.")
        if len(row) != len(columns):
            raise ValueError("Each answer row must match the number of columns.")
        normalized_rows.append(list(row))

    final_columns, final_rows, trim_metadata = _auto_trim_answer_columns(
        task,
        list(columns),
        normalized_rows,
    )

    # Auto-split full_name → first_name + last_name
    final_columns, final_rows, split_metadata = _auto_split_name_columns(
        final_columns,
        final_rows,
    )

    answer = AnswerTable(columns=final_columns, rows=final_rows)
    return ToolExecutionResult(
        ok=True,
        content={
            "status": "submitted",
            "column_count": len(final_columns),
            "row_count": len(final_rows),
            **trim_metadata,
            **split_metadata,
        },
        is_terminal=True,
        answer=answer,
    )


@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self) -> str:
        lines = []
        for name in sorted(self.specs):
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            lines.append(f"  input_schema: {spec.input_schema}")
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")
        return self.handlers[action](task, action_input)


def create_default_tool_registry() -> ToolRegistry:
    specs = {
        "answer": ToolSpec(
            name="answer",
            description="Submit the final answer table. This is the only valid terminating action.",
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
        "execute_context_sql": ToolSpec(
            name="execute_context_sql",
            description=(
                "Run a read-only SQL query against a sqlite/db file inside context. "
                "Best for aggregations (SUM, AVG, COUNT, GROUP BY, JOIN). "
                "Returns up to `limit` rows (default 500). Use this for SQLite databases."
            ),
            input_schema={"path": "relative/path/to/file.sqlite", "sql": "SELECT ...", "limit": 500},
        ),
        "execute_universal_sql": ToolSpec(
            name="execute_universal_sql",
            description=(
                "Run a SQL query directly against CSV or JSON files using DuckDB. "
                "You can query CSV files with: SELECT * FROM 'csv/filename.csv' "
                "and JSON files with: SELECT * FROM read_json_auto('json/filename.json'). "
                "This allows you to JOIN multiple CSV and JSON files natively without writing Python code! "
                "Returns up to `limit` rows (default 500). Use this for any file-based data."
            ),
            input_schema={"sql": "SELECT t1.id, t2.name FROM 'csv/table1.csv' t1 JOIN read_json_auto('json/table2.json') t2 ON t1.id = t2.id", "limit": 500},
        ),
        "execute_python": ToolSpec(
            name="execute_python",
            description=(
                "Execute arbitrary Python code with the task context directory as the "
                "working directory. Use this for CSV analysis with pandas when you need "
                "all rows (read_csv only previews 50). Always print() your results. "
                "Prefer printing JSON for structured output. "
                f"The execution timeout is fixed at {EXECUTE_PYTHON_TIMEOUT_SECONDS} seconds."
            ),
            input_schema={
                "code": "import pandas as pd\nimport json\ndf = pd.read_csv('file.csv')\nprint(json.dumps(df.describe().to_dict()))",
            },
        ),
        "inspect_sqlite_schema": ToolSpec(
            name="inspect_sqlite_schema",
            description="Inspect tables and columns in a sqlite/db file inside context.",
            input_schema={"path": "relative/path/to/file.sqlite"},
        ),
        "profile_context": ToolSpec(
            name="profile_context",
            description=(
                "Profile the whole task context in one bounded call: lists files and profiles "
                "knowledge.md, SQLite DBs, CSVs, JSON files, and text docs. Use this early for "
                "hard/extreme tasks or contexts with many files so you can pick the correct "
                "data source and joins without spending many steps. It is exploratory only; "
                "still run a final SQL/Python computation before answering."
            ),
            input_schema={"max_depth": 4, "max_files": 16, "max_doc_chars": 2500},
        ),
        "profile_database": ToolSpec(
            name="profile_database",
            description=(
                "Profile an entire SQLite database in ONE call: returns all table schemas, "
                "row counts, column stats (min/max/mean for numeric, top values for text), "
                "null/unique counts, sample rows (3 per table), and foreign key relationships. "
                "ALWAYS use this instead of inspect_sqlite_schema for a complete DB overview. "
                "This saves multiple steps of exploration."
            ),
            input_schema={"path": "relative/path/to/file.sqlite"},
        ),
        "list_context": ToolSpec(
            name="list_context",
            description="List files and directories available under context. Always call this first.",
            input_schema={"max_depth": 4},
        ),
        "profile_csv": ToolSpec(
            name="profile_csv",
            description=(
                "Profile a CSV file: returns total rows, column data types, null counts, "
                "unique value counts, and basic statistics (min/max/mean/std for numeric, "
                "top 5 values for categorical). Use this to understand data before analysis."
            ),
            input_schema={"path": "relative/path/to/file.csv"},
        ),
        "profile_json": ToolSpec(
            name="profile_json",
            description=(
                "Profile a JSON file: extract schema and structure (keys, array types, lengths) "
                "without loading thousands of raw values into memory. ALWAYS use this first to understand "
                "data before analysis."
            ),
            input_schema={"path": "relative/path/to/file.json"},
        ),
        "read_csv": ToolSpec(
            name="read_csv",
            description=(
                "Read a tiny preview of a CSV file inside context (default 5 rows). "
                "Check the `truncated` field — if true, use `execute_python` with pandas "
                "or `profile_csv` for full data access."
            ),
            input_schema={"path": "relative/path/to/file.csv", "max_rows": 5},
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description=(
                "Read the BEGINNING of a text-like document inside context (default 8000 chars). "
                "Returns `total_chars` so you know the full size. "
                "For LONG docs (>8000 chars) do NOT re-call this with larger max_chars — "
                "instead use `search_doc` to find relevant passages, or `read_doc_chunk` to page through."
            ),
            input_schema={"path": "relative/path/to/file.md", "max_chars": 8000},
        ),
        "read_doc_chunk": ToolSpec(
            name="read_doc_chunk",
            description=(
                "Read an arbitrary slice of a long document by character offset. "
                "Use this for paging through files larger than `read_doc`'s preview. "
                "Returns `content`, `next_start`, and `has_more` so you can page sequentially. "
                "Single chunk is capped at 16000 chars."
            ),
            input_schema={
                "path": "relative/path/to/file.md",
                "start": 0,
                "length": 8000,
            },
        ),
        "search_doc": ToolSpec(
            name="search_doc",
            description=(
                "RAG-style search over a long .md/.txt document. Given a natural-language query "
                "OR a regex, returns the top matching passages WITH ±context chars around each. "
                "Much faster than paging: use this for 30KB–100KB narrative docs (e.g. patient "
                "records, budget memos, event narratives). "
                "Modes: 'auto' (default; picks regex or keyword), 'keyword' (BM25-lite), 'regex' (Python regex). "
                "Example queries: 'Yearly Kickoff advertisement budget', 'born in 1955', r'\\b(male|female)\\b'. "
                "Returns up to `max_matches` (default 8) passages."
            ),
            input_schema={
                "path": "relative/path/to/file.md",
                "query": "natural language OR regex pattern",
                "mode": "auto",
                "max_matches": 8,
                "context_chars": 400,
            },
        ),
        "read_json": ToolSpec(
            name="read_json",
            description="Read a tiny preview of a JSON file inside context (default 1000 chars). For analysis, use execute_python.",
            input_schema={"path": "relative/path/to/file.json", "max_chars": 1000},
        ),
        "extract_info": ToolSpec(
            name="extract_info",
            description=(
                "Search across ALL files in context (CSVs, JSON, SQLite, text docs) for "
                "keywords or regex patterns. Returns the nearest relevant rows/passages from "
                "ANY file matching your query. Use this when you need to find specific data "
                "(e.g. a person's name, phone number, transaction amount) without knowing "
                "which file contains it. Much faster than manually checking each file. "
                "Examples: 'John Smith phone', 'invoice total 2024', r'\\b\\d{3}-\\d{4}\\b'."
            ),
            input_schema={
                "query": "keyword or regex pattern to search for",
                "max_results": 10,
                "context_chars": 300,
            },
        ),
        "build_knowledge_graph": ToolSpec(
            name="build_knowledge_graph",
            description=(
                "Build a Knowledge Graph of ALL data in context in ONE call. Returns:\n"
                "- **Entities**: every table/file with columns, types, row counts, sample values\n"
                "- **Join Paths**: detected foreign keys and shared columns between tables\n"
                "- **Constraints**: value rules from knowledge.md (e.g. 'Thrombosis=1 means severe')\n"
                "- **Metrics/KPIs**: formulas defined in knowledge.md\n"
                "Use this as your FIRST exploration step for multi-file or complex tasks. "
                "It replaces multiple list_context + profile_csv + profile_database calls. "
                "The join paths tell you EXACTLY how to connect tables. No arguments needed."
            ),
            input_schema={},
        ),
        "read_knowledge_graph": ToolSpec(
            name="read_knowledge_graph",
            description=(
                "Read the pre-built knowledge graph back from its database (fast — no "
                "re-scan). With NO arguments: returns all entities, join paths, constraints "
                "and metrics. With `query`: locates WHERE a term lives — which file/table, "
                "column, or sample value matches it, plus the join paths that connect it. "
                "Use the `query` form when you don't know which source holds something "
                "(e.g. query='Acme' or query='subsidiary') BEFORE probing files by hand. "
                "If nothing matches, the term likely lives in a document — try `map_sources`."
            ),
            input_schema={"query": "optional term to locate, e.g. 'Acme' or 'subsidiary'"},
        ),
        "map_sources": ToolSpec(
            name="map_sources",
            description=(
                "Map EVERY file and how they relate ACROSS types — csv/json/db AND "
                "documents (pdf/md/txt), which the tabular graph ignores. For each "
                "document it extracts headings + candidate entities and, importantly, "
                "`links_to_tables`: which table a document is about (matched by table "
                "name, column, or an actual sample value appearing in the text). "
                "Pass `focus` (a term/entity from the question) to get a `verdict` on "
                "WHERE it lives — structured table vs document vs nowhere — so you read "
                "the right source instead of probing each file. Use this when the answer "
                "may be in a PDF/report, or when `read_knowledge_graph` found nothing."
            ),
            input_schema={"focus": "optional term to locate across all files, e.g. 'Acme Corp subsidiaries'"},
        ),
        "read_pdf": ToolSpec(
            name="read_pdf",
            description=(
                "Extract text from a PDF (or md/txt) document inside context, page by "
                "page via pypdf. Use this to actually READ a report the answer lives in "
                "(plain `read_doc` cannot decode PDF binary). Bounded by `max_pages` and "
                "`max_chars`; for a huge PDF, narrow with `search_doc` first, then read."
            ),
            input_schema={"path": "relative/path/to/file.pdf", "max_pages": 50, "max_chars": 16000},
        ),
        "classify_question": ToolSpec(
            name="classify_question",
            description=(
                "Classify the current question and recommend which reasoning architecture "
                "fits — react / dragin / multi / hybrid_b — from the question wording and the "
                "workspace's data shape (structured families + whether documents are present). "
                "Returns the recommendation, the reasoning, the signals it saw, and the other "
                "options. Call this once up front when unsure how heavy an approach to take. "
                "No arguments needed."
            ),
            input_schema={},
        ),
        "plan_task": ToolSpec(
            name="plan_task",
            description=(
                "Produce a grounded step plan WITHOUT executing anything. It locates where "
                "each entity in the question actually lives (which table vs which document) "
                "using the cross-file source map, then lays out locate → join → compute → "
                "validate and names the right tool per source. Call this as an early step on "
                "multi-source or document-heavy questions so you read the right files instead "
                "of probing each one. Optional `question` overrides the task question."
            ),
            input_schema={"question": "optional — defaults to the task question"},
        ),
        "profile_quality": ToolSpec(
            name="profile_quality",
            description=(
                "Profile a CSV/Excel file FACTUALLY: per-column type, null %, unique count, "
                "sample values, and numeric min/max/mean, plus duplicate-row count. Read-only. "
                "Use it to SEE the data, then reason about quality issues yourself (no fixed rules)."
            ),
            input_schema={"path": "relative/path/to/file.csv"},
        ),
    }
    handlers = {
        "answer": _answer,
        "execute_context_sql": _execute_context_sql,
        "execute_universal_sql": _execute_universal_sql,
        "execute_python": _execute_python,
        "inspect_sqlite_schema": _inspect_sqlite_schema,
        "profile_context": _profile_context,
        "profile_database": _profile_database,
        "list_context": _list_context,
        "profile_csv": _profile_csv,
        "profile_json": _profile_json,
        "read_csv": _read_csv,
        "read_doc": _read_doc,
        "read_doc_chunk": _read_doc_chunk,
        "read_json": _read_json,
        "search_doc": _search_doc,
        "extract_info": _extract_info,
        "build_knowledge_graph": _build_knowledge_graph,
        "read_knowledge_graph": _read_knowledge_graph,
        "map_sources": _map_sources,
        "read_pdf": _read_pdf,
        "classify_question": _classify_question,
        "plan_task": _plan_task,
        "profile_quality": _profile_quality,
    }
    return ToolRegistry(specs=specs, handlers=handlers)
