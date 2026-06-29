"""Shared reasoning-control helpers used across every engine.

The engines differed not in the model they call but in the *scaffolding* around
it: ReAct had rich error-recovery hints (classify the failure, tell the model
exactly how to recover), while DRAGIN and the multi-agent analyst had almost
none. With GPT-4o that gap shows up as "weak reasoning" — a recoverable tool
error on DRAGIN silently wastes a step, whereas on ReAct it gets a targeted
nudge. These helpers lift that recovery logic out of ReAct so every engine
applies the same tightened reasoning stages (P1/P3).
"""

from __future__ import annotations

import json
import re

__all__ = [
    "classify_tool_error",
    "empty_filter_hint",
    "step_signature",
    "detect_repeat_loop",
]

_ZERO_ROWS_RE = re.compile(
    r"""["']rows["']\s*:\s*(\[\s*\]|\[\s*\[\s*(0(\.0)?|none|null)\s*\]\s*\])""",
    re.IGNORECASE,
)
_PY_ZERO_OUTPUTS = {"0", "0.0", "[]", "{}", "[[0]]", "[[0.0]]", "[[null]]", "[[none]]"}


def classify_tool_error(action: str, ok: bool, content: str) -> str | None:
    """Return a targeted recovery hint for a hard failure or a silent one.

    A *hard* failure is ``ok=False`` (e.g. a SQL ``no such column``). A *silent*
    failure is ``ok=True`` but the result is empty (a query that matched zero
    rows). Both are common ways the agent burns steps without realising it is
    stuck; the returned hint tells the model the concrete next move.
    Returns ``None`` when nothing is wrong.
    """
    c = content.lower() if content else ""
    if not ok:
        if action in ("execute_context_sql", "execute_universal_sql", "execute_python"):
            if "no such column" in c or "keyerror" in c:
                return (
                    "\n⚠️ COLUMN NOT FOUND: The column name in your query doesn't exist. "
                    "Call `inspect_sqlite_schema`/`profile_database` (SQL) or `print(df.columns)` "
                    "(Python) and copy-paste the EXACT column name — never guess."
                )
            if "no such table" in c:
                return (
                    "\n⚠️ TABLE NOT FOUND: The table or database path is wrong. "
                    "Call `list_context` to find the correct .db file path, then "
                    "call `inspect_sqlite_schema` with that path."
                )
            if "syntax error" in c or "syntaxerror" in c:
                return (
                    "\n⚠️ SYNTAX ERROR: Fix the specific line in the traceback — "
                    "don't rewrite from scratch. Check: missing commas, unmatched "
                    "parentheses, invalid aggregation, or reserved keyword used as "
                    "column name. Or switch to execute_python + pandas."
                )
        return None

    # ok=True but silent failure
    if action in ("execute_context_sql", "execute_universal_sql"):
        if "0 rows" in c or "0 matches" in c:
            return (
                "\n⚠️ EMPTY RESULT (0 rows): Your query ran but matched nothing. "
                "→ First sample actual values: `SELECT DISTINCT <col> FROM <table> LIMIT 15`. "
                "→ Then remove one WHERE condition at a time to find which filter is wrong."
            )
    if action == "execute_python":
        if any(pat in c for pat in ("no matches", "no data found", "no matching", "empty dataframe")):
            return (
                "\n⚠️ EMPTY RESULT: Your Python filter matched nothing. "
                "→ Sample actual values: `print(df['col'].value_counts().head(10))`. "
                "→ Check for type mismatch: string '1' ≠ integer 1."
            )
    return None


def empty_filter_hint(action: str, content: object) -> str | None:
    """Catch a silent failure the ``0 rows`` check misses: an aggregate that
    returns a single row whose only value is 0 / NULL.

    ``SELECT COUNT(*) ... WHERE status = 'unpaid'`` returns ONE row holding ``0``
    when the literal doesn't exist — so the agent reads "1 row" and wrongly answers
    0. This nudges it to verify the filter value (or bridge the concept) first.
    Returns ``None`` when the result isn't a suspicious single-cell zero.
    """
    if not isinstance(content, dict):
        return None

    hint = (
        "\n⚠️ AGGREGATE = 0 / EMPTY: a COUNT/SUM of 0 (or an empty merge) usually means your "
        "filter value does not exist — NOT that the true answer is 0. "
        "Verify the real values first: `SELECT DISTINCT <col> FROM <table> LIMIT 20` "
        "(or `df['<col>'].unique()` in pandas), or call `read_knowledge_graph` with the "
        "concept (e.g. query='unpaid') to map it to the actual stored values. "
        "Only answer 0 after confirming the filter is valid."
    )

    if action in ("execute_context_sql", "execute_universal_sql"):
        rows = content.get("rows")
        if (isinstance(rows, list) and len(rows) == 1
                and isinstance(rows[0], list) and len(rows[0]) == 1
                and rows[0][0] in (0, 0.0, None)):
            return hint
        return None

    # I1 — the same silent failure happens in pandas: a literal filter yields an
    # empty DataFrame, so sum()/count() prints 0. The agent then answers 0.
    if action == "execute_python":
        if not content.get("success"):
            return None
        out = str(content.get("output", "")).strip()
        if out.lower() in _PY_ZERO_OUTPUTS or _ZERO_ROWS_RE.search(out):
            return hint
        return None

    return None


def step_signature(action: str, action_input: dict[str, object]) -> str:
    """Stable identity for an (action, action_input) pair.

    ``execute_python`` is keyed on its code body so two runs of the same script
    collapse to one signature regardless of dict ordering.
    """
    if action == "execute_python":
        code = action_input.get("code", "")
        if isinstance(code, str):
            return f"{action}:{code}"
    return f"{action}:{json.dumps(action_input, sort_keys=True, ensure_ascii=False)}"


def detect_repeat_loop(
    signatures: list[str],
    *,
    window: int = 5,
    threshold: int = 3,
) -> str | None:
    """Detect a near-identical action repeated within a sliding window.

    ``signatures`` is the ordered list of :func:`step_signature` values for the
    non-error steps so far. If any single signature occurs at least
    ``threshold`` times in the last ``window`` steps, returns a recovery hint;
    otherwise ``None``. Engines that already maintain a richer per-tool loop
    detector (ReAct) keep theirs; this is the shared default for the others.
    """
    if len(signatures) < threshold:
        return None
    recent = signatures[-window:]
    most_common = max(set(recent), key=recent.count)
    count = recent.count(most_common)
    if count < threshold:
        return None
    looped_action = most_common.split(":", 1)[0]
    return (
        f"\n\n⚠️ LOOP DETECTED: your last {count} `{looped_action}` calls used the same "
        "input and produced the same result. You are stuck.\n"
        "→ Do NOT repeat this call. Switch to a fundamentally different tool or data source "
        "(SQL→Python, reading a doc→querying a table), or submit your best `answer` now."
    )
