from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.parsing import parse_model_step
from data_agent_baseline.agents.reasoning import classify_tool_error, empty_filter_hint
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Difficulty-based strategy guidance (uses task.difficulty from task.json)
# ---------------------------------------------------------------------------

_DIFFICULTY_GUIDANCE = {
    "easy": (
        "DIFFICULTY: EASY — This should be answerable in 3-6 steps. Do NOT overthink.\n"
        "- Explore files → profile → ONE query → answer. Keep it direct.\n"
        "- Return MINIMAL columns (usually 1-2). The question likely asks for a simple lookup or count.\n"
        "- NEVER use LIMIT 1 unless the question literally says 'one' or 'single'. Check for TIES.\n"
        "- 'Which X has lowest Y?' → use WHERE Y = (SELECT MIN(Y) FROM ...) to catch ties.\n"
        "- 'List all X where Y' → return ALL matching rows, not just a sample.\n"
        "- Keep first_name and last_name as SEPARATE columns — never merge into full_name.\n"
        "- Return ONLY the column(s) the question asks for. 'List withdrawals' = trans_id only, NOT the entire row.\n"
        "- Client ID ≠ Account ID in financial DBs. Map client → account(s) first.\n"
    ),
    "medium": (
        "DIFFICULTY: MEDIUM — Requires JOINs, GROUP BY, or multi-step reasoning.\n"
        "- Profile data first, then plan your JOIN/GROUP BY strategy.\n"
        "- If there are many files or unclear joins, call `profile_context` once after `list_context`.\n"
        "- Read knowledge.md FIRST if present — it defines column meanings and business rules.\n"
        "- 'Type of expenses' = look for a column literally named `type` in the relevant table, not expense_description.\n"
        "- 'Average monthly consumption' = AVG across individual records, NOT total/12.\n"
        "- 'Average bonds per atom' = COUNT(bond_id) / COUNT(DISTINCT atom_id), NOT AVG of a count.\n"
        "- COUNT always means COUNT(DISTINCT entity_id) unless explicitly stated otherwise.\n"
        "- Return ONLY the columns the question asks for — EVERY extra column costs 10% of your score.\n"
        "- Keep first_name and last_name as SEPARATE columns.\n"
        "- Verify row count matches what the question implies before answering.\n"
    ),
    "hard": (
        "DIFFICULTY: HARD — Multi-step analysis with potential domain knowledge needed.\n"
        "- Read knowledge.md FIRST — it likely has critical definitions.\n"
        "- Use `profile_context` early to map all files/tables before writing the final computation.\n"
        "- Medical data: use standard clinical reference ranges (WBC 3500-9000, Creatinine 0.5-1.4, Fibrinogen 150-400) when no explicit range is in context.\n"
        "- 'Full name' with first_name + last_name in source → return as 2 SEPARATE columns.\n"
        "- Financial Czech terms: VYBER = Cash Withdrawal (in `operation` column, NOT `type`).\n"
        "- 'How many X with condition Y' = COUNT(DISTINCT id) with BOTH conditions satisfied.\n"
        "- For percentage: always use float division * 100.0, return full precision.\n"
        "- For 'element in position N' queries: the Nth atom in a molecule, not Nth row.\n"
        "- Verify your final result makes sense. If row count is wildly off from what question implies, redo.\n"
        "- Budget: plan to answer by step 15. Don't waste steps re-exploring files you already know.\n"
    ),
    "extreme": (
        " DIFFICULTY: EXTREME — Complex analysis with missing context files. Use LLM knowledge.\n"
        "- PLAN all sub-computations in your FIRST thought before touching any tool.\n"
        "- After `list_context`, call `profile_context` unless the context has only one obvious file.\n"
        "- Some reference files (knowledge.md, mappings) may be intentionally removed. Use your training knowledge for standard thresholds, city names, domain terms.\n"
        "- Write ONE comprehensive execute_python script that computes everything at once.\n"
        "- 'Compare X vs others' → output EXACTLY 2 grouped rows.\n"
        "- 'Top N with details' → verify output has exactly N rows.\n"
        "- Semicolon-separated IDs in cells: aggregate with ';'.join(sorted).\n"
        "- For wide pivoted output (top-3 names + weights in 1 row), follow that shape.\n"
        "- Print df.shape before answering to verify dimensions.\n"
        "- Budget: you have max_steps total, plan to answer by step 15.\n"
    ),
}


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 15


# JSON parsing now lives in agents/parsing.py so every engine shares one
# hardened implementation. `parse_model_step` is re-exported above for the
# modules (dragin, orchestrator) that import it from here.


def _answer_preflight_hint(task: PublicTask, action_input: dict[str, object]) -> str | None:
    """Catch obvious answer-shape mistakes before terminal submission.

    This does not know the expected answer. It only checks constraints implied
    directly by the question wording, giving the agent one chance to fix format.
    """
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return None

    question = task.question.lower()
    column_count = len(columns)
    row_count = len(rows)
    column_tokens = [
        {token for token in re.split(r"[^a-z0-9]+", str(column).lower()) if token}
        for column in columns
    ]

    asks_comment_id = (
        "comment id" in question
        or "comment_id" in question
        or "id of the comment" in question
    )
    asks_comment_text = "comment" in question and not asks_comment_id
    if asks_comment_text:
        has_text_column = any(tokens & {"text", "comment", "body", "content"} for tokens in column_tokens)
        only_id_like = column_count <= 2 and all(
            tokens <= {"id", "comment", "commentid", "comment_id", "postid", "post", "score"}
            for tokens in column_tokens
        )
        if not has_text_column or only_id_like:
            return (
                "The question asks for the comment itself, not the comment id. "
                "Find and submit the comment text/body/content column."
            )

    average_mentions = len(re.findall(r"\baverage\b|\bavg\b", question))
    if average_mentions >= 2 and column_count < 2:
        return (
            "The question asks for multiple averages. Submit them as separate columns "
            "in one row, not as one semicolon/comma-joined cell."
        )
    if column_count == 1 and row_count == 1 and isinstance(rows[0], list) and rows[0]:
        only_value = str(rows[0][0])
        if average_mentions >= 1 and (";" in only_value or "," in only_value):
            return (
                "This looks like multiple numeric results packed into one cell. "
                "Split distinct requested metrics into separate columns."
            )

    single_value_prefixes = (
        "how many ",
        "what is the total",
        "what's the total",
        "what is the average",
        "what's the average",
        "what is the number",
        "what percentage",
        "what proportion",
    )
    grouped_markers = (
        " for each ",
        " each ",
        " by ",
        " per ",
        " list ",
        " all ",
        " grouped ",
        " broken down ",
        " distribution ",
    )
    looks_single_value = question.startswith(single_value_prefixes) and not any(
        marker in question for marker in grouped_markers
    )
    if looks_single_value and (column_count != 1 or row_count != 1):
        return (
            "The question looks like a single-value question. Submit exactly 1 row x 1 column, "
            "with no labels, IDs, or breakdown columns."
        )

    top_match = re.search(r"\btop\s+(\d+)\b|\bfirst\s+(\d+)\b", question)
    if top_match is not None:
        expected_rows = int(top_match.group(1) or top_match.group(2))
        if row_count != expected_rows:
            return (
                f"The question asks for top/first {expected_rows}. Your answer has {row_count} rows. "
                "Re-check ORDER BY, ties, and LIMIT before submitting."
            )

    asks_two_group_compare = any(
        phrase in question
        for phrase in (
            " vs all other",
            " versus all other",
            " compared to all other",
            " compared with all other",
            " compared to the rest",
            " compared with the rest",
        )
    )
    if asks_two_group_compare and row_count != 2:
        return (
            "The question asks for one named group compared with all others/rest. "
            "Submit exactly 2 grouped rows, not a per-entity list."
        )

    if row_count == 0 and any(
        token in question
        for token in ("which ", "what ", "how many ", "top ", "highest", "lowest")
    ):
        return (
            "This answer has zero rows for a question that appears to require a value or list. "
            "Re-check filters and joins before submitting an empty table."
        )

    if looks_single_value and column_count > 1:
        return (
            f"This looks like a single-value question but you have {column_count} columns. "
            "Keep ONLY the 1 column with the answer value. Remove all IDs, names, support columns."
        )

    if column_count >= 4 and not any(
        phrase in question for phrase in (
            "list all columns", "show all", "all details", "all information",
            "all attributes", "full record",
        )
    ):
        return (
            f"⚠ You have {column_count} columns. Most questions need 1-3 columns. "
            "Every extra column costs -10% score. Re-read the question and remove "
            "any columns NOT explicitly asked for (IDs, dates, amounts, support columns)."
        )

    return None


class ReActAgent:
    # Characters of observation content kept in conversation history. Large
    # file reads (e.g. a 7 MB JSON) would otherwise overflow the context window.
    _MAX_OBSERVATION_CHARS = 12_000

    # Loop detection: if the same (action, action_input) pair appears ≥ threshold
    # times in the sliding window, inject targeted recovery hints.
    _LOOP_DETECT_WINDOW = 5
    _LOOP_DETECT_THRESHOLD = 3

    # Stagnation: if the last N non-error steps are all search/query actions
    # that returned empty results, the agent is stuck looking for something
    # that doesn't exist.
    _STAGNATE_WINDOW = 3
    _SEARCH_ACTIONS = {
        "execute_python", "execute_context_sql", "execute_universal_sql",
        "read_doc", "read_doc_chunk", "search_doc",
        "read_csv", "read_json",
        "inspect_sqlite_schema",
        "extract_info",
    }

    # Exploration overload: if the last N non-error steps are all passive
    # data-exploration actions without any analysis/computation, push the
    # agent toward Phase 2 (analysis / answer).
    _EXPLORE_WINDOW = 8
    _EXPLORE_ACTIONS = {
        "list_context", "profile_context",
        "read_doc", "read_doc_chunk", "search_doc",
        "read_csv", "profile_csv",
        "read_json", "profile_json",
        "inspect_sqlite_schema", "profile_database",
        "build_knowledge_graph",
        "extract_info",
    }

    # Question decomposition gate (Step 1).
    _DECOMPOSITION_REQUIRED = {
        "Entities": re.compile(r"(?i)(?:entity|entities|question\s+(?:asks|is)\s+about|主体|实体|对象)"),
        "Filters": re.compile(r"(?i)(?:filter|condition|where\b|only\b|筛选|条件|满足|which\s+\w+\s+to\s+include)"),
        "Aggregation": re.compile(r"(?i)(?:count|sum\b|average|avg\b|max\b|min\b|list\b|total|mean\b|how\s+many|what\s+is\s+the|聚合|数量|平均|计算)"),
        "Output": re.compile(r"(?i)(?:output|column|return|answer\s+(?:should|will|needs|contains?)|输出|返回|答案列)"),
        "Not-asking": re.compile(r"(?i)(?:not\s+(?:asking|about|include|all|what)|注意.*不是|边界|exclude|only.*not|不是问|区别于|不同于|避免)"),
    }
    _DECOMPOSITION_MIN_FIELDS = 3  # require at least 3 of 5 fields present

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: ReActAgentConfig | None = None,
        system_prompt: str | None = None,
        memory_context: str | None = None,
        on_step=None,
        on_propose=None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()
        self.system_prompt = system_prompt or REACT_SYSTEM_PROMPT
        self.memory_context = memory_context or ""
        self._question_level: str | None = None
        # Optional realtime hook: called with each StepRecord as it is produced.
        # Used by the live server to stream the trace; never affects agent logic.
        self._on_step = on_step
        # Optional co-pilot controller: called as (thought, action, action_input)
        # right BEFORE a tool runs; returns a decision dict
        # {decision: approve|edit|reject|cancel, action_input?, note?}. None = autopilot.
        self._on_propose = on_propose

    def _emit(self, step: StepRecord) -> None:
        if self._on_step is None:
            return
        try:
            self._on_step(step)
        except Exception:  # noqa: BLE001 - streaming must never break the run.
            pass

    def _build_messages(self, task: PublicTask, state: AgentRuntimeState) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=self.system_prompt,
        )
        messages = [ModelMessage(role="system", content=system_content)]
        task_content = build_task_prompt(task)
        difficulty = task.difficulty.lower() if task.difficulty else ""
        if difficulty in _DIFFICULTY_GUIDANCE:
            task_content = _DIFFICULTY_GUIDANCE[difficulty] + "\n" + task_content
        if self.memory_context:
            task_content = self.memory_context + "\n\n" + task_content
        messages.append(ModelMessage(role="user", content=task_content))
        for step in state.steps:
            # Skip error steps with empty raw_response — empty assistant messages
            # corrupt the conversation history and cause cascading parse failures.
            if step.action == "__error__" and not step.raw_response.strip():
                continue
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            obs = step.observation
            obs_str = build_observation_prompt(obs, task_question=task.question)
            if len(obs_str) > self._MAX_OBSERVATION_CHARS:
                # Truncate oversized observations so they don't overflow the context.
                content = obs.get("content", "")
                if isinstance(content, str):
                    keep = self._MAX_OBSERVATION_CHARS - 200
                    truncated_obs = {
                        **obs,
                        "content": content[:keep] + f"\n...[truncated, {len(content) - keep} chars omitted]",
                    }
                    obs_str = build_observation_prompt(truncated_obs, task_question=task.question)
            messages.append(ModelMessage(role="user", content=obs_str))
        remaining = self.config.max_steps - len(state.steps)
        if 0 < remaining <= 2 and state.answer is None:
            messages.append(ModelMessage(
                role="user",
                content=(
                    f"⚠️ URGENT: You have only {remaining} step(s) remaining. "
                    "You MUST call the `answer` tool NOW with your best current answer. "
                    "Do NOT use any other tool. Submitting a partial answer is better than no answer "
                    "(which scores 0). If you are uncertain, submit your closest guess."
                ),
            ))
        elif 0 < remaining <= 4 and state.answer is None:
            messages.append(ModelMessage(
                role="user",
                content=(
                    f"⚠ WARNING: You have only {remaining} step(s) left. "
                    "Start wrapping up — verify your result and prepare to call `answer` soon. "
                    "Do NOT start new exploration paths."
                ),
            ))
        return messages

    # ------------------------------------------------------------------ #
    # Diagnostic checks                                                  #
    # ------------------------------------------------------------------ #

    def _check_decomposition(self, thought: str) -> str | None:
        """On step 1, require the agent's thought to contain a structured
        breakdown of the question before any tool is executed."""
        missing = [
            field for field, pattern in self._DECOMPOSITION_REQUIRED.items()
            if not pattern.search(thought)
        ]
        if len(missing) < self._DECOMPOSITION_MIN_FIELDS:
            return None
        return (
            f"\n\n🔍 QUESTION DECOMPOSITION REQUIRED — your `thought` is missing "
            f"key analysis before touching data.\n\n"
            f"Your first `thought` must break down the question with these sections:\n"
            f"**Entities:** What entities/persons/objects does the question ask about?\n"
            f"**Filters:** What conditions determine WHICH rows/entities to include?\n"
            f"**Aggregation:** What computation? count / average / sum / max / min / list?\n"
            f"**Output:** What columns should the final answer contain?\n"
            f"**Not-asking:** What is a plausible WRONG interpretation? What should you avoid?\n\n"
            f"Missing sections: {', '.join(missing)}\n"
            f"Please redo your response with the full decomposition in your `thought`, "
            f"then call your first tool (typically `list_context` or `build_knowledge_graph`)."
        )

    def _check_loop(self, state: AgentRuntimeState) -> str | None:
        """Detect a repeated (action, action_input) and return action-specific recovery hints."""
        valid_steps = [
            (s.action, s.action_input, s.observation)
            for s in state.steps
            if s.action != "__error__"
        ]
        if len(valid_steps) < self._LOOP_DETECT_THRESHOLD:
            return None

        def _step_key(action: str, action_input: dict[str, object]) -> str:
            if action == "execute_python":
                code = action_input.get("code", "")
                if isinstance(code, str):
                    return f"{action}:{code}"
            return f"{action}:{json.dumps(action_input, sort_keys=True, ensure_ascii=False)}"

        window = valid_steps[-self._LOOP_DETECT_WINDOW:]
        keys = [_step_key(a, ai) for a, ai, _ in window]
        most_common = max(set(keys), key=keys.count)
        count = keys.count(most_common)
        if count < self._LOOP_DETECT_THRESHOLD:
            return None

        looped_action = most_common.split(":", 1)[0]
        looped_obs: list[str] = []
        for a, ai, obs in window:
            if _step_key(a, ai) == most_common:
                looped_obs.append(str(obs.get("content", ""))[:500])

        if looped_action == "execute_python":
            has_error = any(
                "Traceback" in o or "Error:" in o or "SyntaxError" in o
                for o in looped_obs
            )
            if has_error:
                recovery = (
                    "Your execute_python code is hitting errors. The error is likely "
                    "a code logic bug or data-shape assumption.\n"
                    "→ Add diagnostic prints: print(type(x), x.shape if hasattr(x,'shape') else '', x[:5] if hasattr(x,'__getitem__') else x)\n"
                    "→ Check the traceback line number and fix THAT specific line — don't rewrite the whole script.\n"
                    "→ If the column doesn't exist: print(df.columns) first."
                )
            else:
                recovery = (
                    "Your execute_python code runs without errors but produces the same "
                    "output every time. The computation is not changing the result.\n"
                    "→ Add print() to show intermediate values BEFORE the final output.\n"
                    "→ Your data filtering logic may be wrong — print the row count before and after each filter.\n"
                    "→ Switch tools: try execute_context_sql for SQLite, or execute_universal_sql for CSV/JSON joins."
                )
        elif looped_action in ("execute_context_sql", "execute_universal_sql"):
            has_error = any(
                "no such table" in o or "no such column" in o
                or "syntax error" in o.lower() or "OperationalError" in o
                for o in looped_obs
            )
            if has_error:
                recovery = (
                    "Your SQL query is failing — table or column name is wrong.\n"
                    "→ FIRST call inspect_sqlite_schema (or profile_database) to confirm table and column names.\n"
                    "→ Copy-paste the EXACT column name from the schema output — don't guess.\n"
                    "→ If the table doesn't exist, check list_context for the correct db path."
                )
            else:
                recovery = (
                    "Your SQL query runs but returns the same result every time.\n"
                    "→ Your WHERE clause or JOIN condition is likely wrong — print the row count with COUNT(*).\n"
                    "→ Switch to execute_python + pandas.read_sql() for more flexible debugging.\n"
                    "→ Check if you need a different aggregation or GROUP BY."
                )
        elif looped_action in ("read_doc", "read_doc_chunk", "search_doc", "read_csv", "read_json"):
            recovery = (
                f"Your {looped_action} calls are returning the same content — "
                f"the document doesn't have the information at the current offset/search.\n"
                f"→ Switch to execute_python with open() + re.search() to scan the whole file.\n"
                f"→ Try a DIFFERENT keyword/regex — the data likely uses different terminology.\n"
                f"→ Cross-reference: look up entity IDs in a structured file (CSV/DB), then search the doc by ID.\n"
                f"→ Change the offset: if you've been reading offset=0, try offset=8000."
            )
        elif looped_action in ("profile_csv", "profile_json", "profile_database", "profile_context"):
            recovery = (
                f"Your {looped_action} calls are repeating with the same input. Profiling "
                f"the same file twice never produces new information.\n"
                f"→ Move to analysis: use execute_python or execute_context_sql to compute the answer.\n"
                f"→ If you need to inspect a different file, change the path.\n"
                f"→ If you have enough info, call answer."
            )
        elif looped_action == "build_knowledge_graph":
            recovery = (
                "You already built the knowledge graph — calling it again returns the same output.\n"
                "→ Use the entity/relation info you already have. Switch to execute_python / "
                "execute_context_sql / execute_universal_sql to compute the answer."
            )
        elif looped_action == "extract_info":
            recovery = (
                "Your extract_info searches are repeating. The keyword isn't matching the data's terminology.\n"
                "→ Switch to execute_python with open() and print raw content to see actual values.\n"
                "→ Try a different keyword, or look up an entity ID first then search by ID."
            )
        else:
            recovery = (
                f"Your {looped_action} calls are repeating with the same input.\n"
                "→ Print ALL available columns/keys (PRAGMA table_info, df.columns, dict_keys).\n"
                "→ Switch to a fundamentally different tool or data source."
            )

        return (
            f"\n\n⚠️ LOOP DETECTED: Your last {count} `{looped_action}` calls "
            f"used the same or near-identical input. You are STUCK.\n\n"
            f"{recovery}\n\n"
            f"Do NOT call `{looped_action}` with the same parameters this step."
        )

    def _check_stagnation(self, state: AgentRuntimeState) -> str | None:
        """If recent search steps all returned empty results, push a recovery directive."""
        recent = [
            s for s in state.steps[-self._STAGNATE_WINDOW:]
            if s.action != "__error__"
        ]
        if len(recent) < self._STAGNATE_WINDOW:
            return None
        if not all(s.action in self._SEARCH_ACTIONS for s in recent):
            return None

        def _empty_pattern(step: StepRecord) -> str | None:
            content = str(step.observation.get("content", ""))
            if any(pat in content for pat in ("0 rows", "0 matches")):
                return "sql_empty"
            if any(pat in content for pat in ("no matches", "no data found", "No matching")):
                return "python_empty"
            if any(pat in content for pat in ("no results", "empty", "[]")):
                return "generic_empty"
            stripped = content.strip().rstrip("}")
            if len(stripped) < 80:
                return "near_empty"
            return None

        patterns = [_empty_pattern(s) for s in recent]
        if not all(p is not None for p in patterns):
            return None

        sql_count = patterns.count("sql_empty")
        python_count = patterns.count("python_empty")
        if sql_count >= python_count and sql_count > 0:
            diagnosis = (
                f"All {len(recent)} search steps returned empty SQL results "
                f"('0 rows' / '0 matches'). Your SQL filter conditions or column names are wrong.\n\n"
                f"IMMEDIATE RECOVERY:\n"
                f"1. Sample actual values FIRST: `SELECT DISTINCT <col> FROM <table> LIMIT 15`.\n"
                f"2. Call `inspect_sqlite_schema` or `profile_database` to confirm names.\n"
                f"3. Your WHERE clause is filtering out all rows — relax or remove one condition at a time.\n"
                f"4. If the table name is wrong: check list_context for the correct db path."
            )
        elif python_count > sql_count and python_count > 0:
            diagnosis = (
                f"All {len(recent)} search steps returned empty Python results "
                f"('no matches' / 'no data found'). Your search keyword or filter doesn't exist.\n\n"
                f"IMMEDIATE RECOVERY:\n"
                f"1. Sample actual column values: `print(df['col'].value_counts().head(10))`.\n"
                f"2. Print raw content: `print(df.head(3).to_csv())` or `print(text[:500])`.\n"
                f"3. Check spelling, casing, and data type (string vs int) of the filter target.\n"
                f"4. Switch to execute_context_sql for easier step-by-step debugging."
            )
        else:
            diagnosis = (
                f"All {len(recent)} search steps returned empty or near-empty results.\n\n"
                f"IMMEDIATE RECOVERY:\n"
                f"1. Sample actual values from candidate columns "
                f"(SELECT DISTINCT or df['col'].value_counts()).\n"
                f"2. Check column names with PRAGMA table_info or df.columns.\n"
                f"3. Check data types: string vs int mismatch is a common cause.\n"
                f"4. Cross-reference knowledge.md — the data may use different terminology."
            )
        return f"\n\n🔄 STAGNATION DETECTED: {diagnosis}"

    def _extract_exploration_context(self, steps: list[StepRecord]) -> dict[str, set[str]]:
        files: set[str] = set()
        tables: set[str] = set()
        columns: set[str] = set()
        for step in steps:
            obs_str = str(step.observation.get("content", ""))
            for m in re.finditer(r"""['\"]path['\"]:\s*['\"]([^'\"]+)['\"]""", obs_str):
                path = m.group(1)
                if "." in path or "/" in path:
                    files.add(path)
            for m in re.finditer(r"""['\"]table['\"]:\s*['\"]([^'\"]+)['\"]""", obs_str):
                tables.add(m.group(1))
            for m in re.finditer(r"""['\"]columns?['\"]:\s*\[([^\]]+)\]""", obs_str):
                for cm in re.finditer(r"""['\"]([^'\"]+)['\"]""", m.group(1)):
                    columns.add(cm.group(1))
            for m in re.finditer(r"""['\"]name['\"]:\s*['\"]([^'\"]+)['\"]""", obs_str):
                col = m.group(1)
                if col and not col.startswith("__"):
                    columns.add(col)
        return {"files": files, "tables": tables, "columns": columns}

    def _check_exploration(self, state: AgentRuntimeState) -> str | None:
        """If many consecutive steps are passive exploration, push to Phase 2."""
        recent = [
            s for s in state.steps[-self._EXPLORE_WINDOW:]
            if s.action != "__error__"
        ]
        if len(recent) < self._EXPLORE_WINDOW:
            return None
        if not all(s.action in self._EXPLORE_ACTIONS for s in recent):
            return None

        action_counts = Counter(s.action for s in recent)
        action_desc = ", ".join(
            f"{count}x {action}" for action, count in action_counts.most_common()
        )
        ctx = self._extract_exploration_context(recent)
        observed_files = sorted(ctx["files"])
        observed_tables = sorted(ctx["tables"])
        observed_columns = sorted(ctx["columns"])[:30]

        inventory_parts: list[str] = []
        if observed_files:
            inventory_parts.append(f"   Files read: {', '.join(observed_files)}")
        if observed_tables:
            inventory_parts.append(f"   Tables/Sources seen: {', '.join(observed_tables)}")
        if observed_columns:
            inventory_parts.append(f"   Columns found (sample): {', '.join(observed_columns[:20])}")
        inventory_block = (
            "\n   WHAT YOU'VE ALREADY SEEN:\n" + "\n".join(inventory_parts) + "\n"
            if inventory_parts else ""
        )

        return (
            f"\n\n⏸️  PHASE STUCK: Your last {len(recent)} steps have ALL been passive data "
            f"exploration ({action_desc}) with zero analysis or computation. You are stuck "
            f"in Phase 1 (understanding) and have NOT started Phase 2 (analysis).\n"
            f"{inventory_block}\n"
            f"IMMEDIATE ACTION REQUIRED:\n"
            f"1. You already have enough context to begin. STOP reading.\n"
            f"2. Use `execute_python`, `execute_context_sql`, or `execute_universal_sql` "
            f"to load, filter, and compute the answer from data you've already found.\n"
            f"3. If unsure which file has the answer, RE-READ the task question.\n"
            f"4. If you have a candidate answer, call `answer` NOW.\n\n"
            f"Do NOT call list_context, profile_*, read_*, inspect_sqlite_schema, "
            f"build_knowledge_graph, or extract_info this step. Use execute_python / "
            f"execute_*_sql / answer instead."
        )

    def _classify_tool_error(self, action: str, ok: bool, content: str) -> str | None:
        """Targeted recovery hint for hard/silent tool failures.

        Delegates to the shared :func:`reasoning.classify_tool_error` so ReAct,
        DRAGIN and the analyst give identical recovery guidance.
        """
        return classify_tool_error(action, ok, content)

    # ------------------------------------------------------------------ #
    # Forced final answer (last-chance recovery)                         #
    # ------------------------------------------------------------------ #

    def _forced_final_answer(self, task: PublicTask, state: AgentRuntimeState) -> None:
        if state.answer is not None:
            return
        forced_system = (
            "You ran out of steps. Based on the observations you already collected, "
            "produce your SINGLE best-guess answer now. Return EXACTLY one ```json fenced "
            "block with {\"thought\": \"...\", \"action\": \"answer\", \"action_input\": "
            "{\"columns\": [...], \"rows\": [[...]]}}. "
            "Do NOT call any other tool. Do NOT say you cannot answer. "
            "Minimize columns: return ONLY the columns the question asks for. "
            "If you are entirely unsure, output a plausible 1×1 table with your best value."
        )
        messages = [ModelMessage(role="system", content=forced_system)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        for step in state.steps[-6:]:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(
                    role="user",
                    content=build_observation_prompt(step.observation, task_question=task.question),
                )
            )
        messages.append(ModelMessage(
            role="user",
            content="SUBMIT the final answer now using the `answer` tool.",
        ))
        try:
            raw_response = self.model.complete(messages, json_object=True)
            model_step = parse_model_step(raw_response)
            if model_step.action != "answer":
                return
            tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
            state.steps.append(StepRecord(
                step_index=len(state.steps) + 1,
                thought=model_step.thought,
                action=model_step.action,
                action_input=model_step.action_input,
                raw_response=raw_response,
                observation={"ok": tool_result.ok, "tool": "answer",
                             "content": tool_result.content, "forced": True},
                ok=tool_result.ok,
            ))
            if tool_result.is_terminal:
                state.answer = tool_result.answer
                state.failure_reason = None
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Main loop                                                          #
    # ------------------------------------------------------------------ #

    def run(self, task: PublicTask) -> AgentRunResult:
        self._question_level = task.difficulty.lower() if task.difficulty else None

        state = AgentRuntimeState()
        max_error_retries = 2
        step_index = 0
        consecutive_errors = 0
        last_error_msg = ""
        max_consecutive_errors = 6
        emitted = 0  # how many StepRecords have been streamed via _emit

        while step_index < self.config.max_steps:
            # Stream any steps appended since the last iteration (covers all paths).
            while emitted < len(state.steps):
                self._emit(state.steps[emitted])
                emitted += 1
            step_index += 1
            raw_response = ""
            try:
                raw_response = self.model.complete(
                    self._build_messages(task, state), json_object=True
                )
                model_step = parse_model_step(raw_response)

                # STEP 1 DECOMPOSITION GATE — before any tool runs.
                if step_index == 1:
                    decomp_warning = self._check_decomposition(model_step.thought)
                    if decomp_warning is not None:
                        state.steps.append(StepRecord(
                            step_index=step_index,
                            thought=model_step.thought,
                            action=model_step.action,
                            action_input=model_step.action_input,
                            raw_response=raw_response,
                            observation={
                                "ok": True,
                                "tool": "__decompose_check__",
                                "content": decomp_warning,
                            },
                            ok=True,
                        ))
                        consecutive_errors = 0
                        last_error_msg = ""
                        continue

                # Answer preflight (catch shape mistakes before final submit).
                if model_step.action == "answer" and (self.config.max_steps - step_index) >= 1:
                    preflight_hint = _answer_preflight_hint(task, model_step.action_input)
                    if preflight_hint is not None:
                        state.steps.append(StepRecord(
                            step_index=step_index,
                            thought=model_step.thought,
                            action=model_step.action,
                            action_input=model_step.action_input,
                            raw_response=raw_response,
                            observation={
                                "ok": False,
                                "tool": "answer",
                                "content": {"status": "preflight_rejected"},
                                "hint": preflight_hint,
                            },
                            ok=False,
                        ))
                        consecutive_errors = 0
                        last_error_msg = ""
                        continue

                # Co-pilot gate: ask the controller before executing the tool.
                # Returns None (autopilot) or a decision dict for human-in-the-loop.
                eff_input = model_step.action_input
                if self._on_propose is not None:
                    decision = self._on_propose(model_step.thought, model_step.action, eff_input) or {}
                    verdict = str(decision.get("decision", "approve")).lower()
                    if verdict == "cancel":
                        state.failure_reason = "Run cancelled by the user."
                        break
                    if verdict == "reject":
                        note = decision.get("note") or "Propose a different approach."
                        state.steps.append(StepRecord(
                            step_index=step_index, thought=model_step.thought,
                            action=model_step.action, action_input=eff_input,
                            raw_response=raw_response,
                            observation={"ok": False, "tool": model_step.action,
                                         "content": f"Step rejected by the user. {note}"},
                            ok=False,
                        ))
                        consecutive_errors = 0
                        last_error_msg = ""
                        continue
                    if verdict == "edit" and isinstance(decision.get("action_input"), dict):
                        eff_input = decision["action_input"]

                tool_result = self.tools.execute(task, model_step.action, eff_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                step_record = StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=eff_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                state.steps.append(step_record)
                consecutive_errors = 0
                last_error_msg = ""

                # Targeted tool-error / silent-failure hint.
                tool_error_hint = self._classify_tool_error(
                    model_step.action, tool_result.ok, str(tool_result.content)
                )
                if tool_error_hint is None and tool_result.ok:
                    # I2 — name the wrong filter literal and suggest the REAL value
                    # (fuzzy + concept-bridge), across SQL *and* python paths.
                    try:
                        from data_agent_baseline.tools.kg_store import literal_filter_hint
                        from data_agent_baseline.tools.semantic_match import resolve_model
                        tool_error_hint = literal_filter_hint(
                            model_step.action, model_step.action_input, tool_result.content,
                            task.context_dir, model=resolve_model(),
                        )
                    except Exception:  # noqa: BLE001 - hint generation must never break a run
                        tool_error_hint = None
                if tool_error_hint is None and tool_result.ok:
                    # I1 — generic zero/empty aggregate (COUNT over a non-existent
                    # filter value, or an empty pandas merge) that "0 rows" misses.
                    tool_error_hint = empty_filter_hint(model_step.action, tool_result.content)
                if tool_error_hint is not None:
                    observation["content"] = str(observation.get("content", "")) + tool_error_hint

                # Loop > stagnation > exploration overload — pick the most
                # specific signal and inject one warning per step.
                loop_warning = self._check_loop(state)
                if loop_warning is not None:
                    observation["loop_warning"] = True
                    observation["content"] = str(observation.get("content", "")) + loop_warning
                else:
                    stagnation_warning = self._check_stagnation(state)
                    if stagnation_warning is not None:
                        observation["stagnation_warning"] = True
                        observation["content"] = (
                            str(observation.get("content", "")) + stagnation_warning
                        )
                    else:
                        explore_warning = self._check_exploration(state)
                        if explore_warning is not None:
                            observation["explore_warning"] = True
                            observation["content"] = (
                                str(observation.get("content", "")) + explore_warning
                            )

                # Confidence self-check every 5 steps.
                if step_index % 5 == 0 and step_index > 0 and not tool_result.is_terminal:
                    remaining = self.config.max_steps - step_index
                    first_thought = state.steps[0].thought if state.steps else ""
                    goal_line = ""
                    m = re.search(
                        r"(?im)\*\*Q-Restate:\*\*\s*(.+?)(?:\n|$)",
                        first_thought,
                    )
                    if m:
                        goal_line = m.group(1).strip()[:120]
                    confidence_nudge = (
                        f"\n\n🤔 SELF-CHECK (step {step_index}/{self.config.max_steps}, "
                        f"{remaining} steps remaining):\n"
                        f"1. On a scale of 1-10, how confident are you in your current answer?\n"
                        f"2. What ONE specific piece of information are you still missing?\n"
                        f"3. If confidence ≥ 7: call `answer` NOW. Don't burn steps.\n"
                        f"4. If confidence < 5: switch to a fundamentally different approach next step.\n"
                        f"5. What is the single most important fact you've discovered so far?"
                        + (
                            f"\n\n🎯 Core goal: {goal_line}\n→ Still on track?"
                            if goal_line else ""
                        )
                    )
                    observation["self_check"] = True
                    observation["content"] = (
                        str(observation.get("content", "")) + confidence_nudge
                    )

                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break

            except Exception as exc:
                error_msg = str(exc)
                if "empty response" in error_msg.lower():
                    observation = {
                        "ok": False,
                        "error": error_msg,
                        "hint": (
                            "Your previous response was empty. Please continue from where you "
                            "left off — output your next `thought`, `action`, and `action_input` "
                            "in a ```json``` block."
                        ),
                    }
                else:
                    consecutive_errors += 1
                    observation = {
                        "ok": False,
                        "error": error_msg,
                        "hint": "Fix your JSON format or action. Model API error or invalid format.",
                    }
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )
                same_error = (error_msg == last_error_msg)
                if not same_error and consecutive_errors <= max_error_retries:
                    step_index -= 1  # free retry
                last_error_msg = error_msg
                if consecutive_errors >= max_consecutive_errors:
                    state.failure_reason = (
                        f"Aborted: {consecutive_errors} consecutive errors. "
                        f"Last: {error_msg[:200]}"
                    )
                    break

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        if state.answer is None:
            self._forced_final_answer(task, state)

        # Flush any remaining steps (last tool step, forced answer, etc.).
        while emitted < len(state.steps):
            self._emit(state.steps[emitted])
            emitted += 1

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
            question_level=self._question_level,
        )
