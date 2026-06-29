"""Tests for the zero-aggregate silent-failure hint (the COUNT='unpaid'=0 trap).

Run from the repo root:  python scripts/test_zero_aggregate_hint.py
A `SELECT COUNT(*) ... WHERE status='unpaid'` returns ONE row holding 0 — the
existing "0 rows" detector misses it, so the agent wrongly answers 0. This checks
the new `empty_filter_hint` fires on that shape and stays quiet on real results.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.agents.reasoning import classify_tool_error, empty_filter_hint

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def main() -> int:
    # The exact result that fooled the agent: COUNT(*) WHERE status='unpaid' → [[0]]
    count_zero = {"columns": ["unpaid_invoices_count"], "rows": [[0]], "row_count": 1, "truncated": False}

    print("\n=== the trap: COUNT = 0 (1 row) ===")
    # The existing detector misses it (it only looks for the literal "0 rows").
    check("existing classify_tool_error MISSES zero-count",
          classify_tool_error("execute_context_sql", True, str(count_zero)) is None)
    # The new hint catches it.
    hint = empty_filter_hint("execute_context_sql", count_zero)
    check("empty_filter_hint FIRES on zero-count", hint is not None and "AGGREGATE = 0" in (hint or ""))
    check("hint suggests verifying real values / concept bridge",
          hint is not None and "DISTINCT" in hint and "read_knowledge_graph" in hint)

    print("\n=== variants ===")
    check("fires on SUM = 0.0", empty_filter_hint("execute_context_sql",
          {"rows": [[0.0]], "row_count": 1}) is not None)
    check("fires on NULL aggregate", empty_filter_hint("execute_context_sql",
          {"rows": [[None]], "row_count": 1}) is not None)
    check("fires for execute_universal_sql too", empty_filter_hint("execute_universal_sql",
          {"rows": [[0]], "row_count": 1}) is not None)

    print("\n=== stays quiet on legitimate results (no false positives) ===")
    check("quiet on a real non-zero count", empty_filter_hint("execute_context_sql",
          {"rows": [[69]], "row_count": 1}) is None)
    check("quiet on multi-row results", empty_filter_hint("execute_context_sql",
          {"rows": [["open", 43], ["overdue", 26]], "row_count": 2}) is None)
    check("quiet on multi-column single row", empty_filter_hint("execute_context_sql",
          {"rows": [[0, "x"]], "row_count": 1}) is None)
    check("quiet for non-SQL tools", empty_filter_hint("execute_python",
          {"rows": [[0]], "row_count": 1}) is None)
    check("quiet on empty rows (other detector owns this)", empty_filter_hint(
          "execute_context_sql", {"rows": [], "row_count": 0}) is None)

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
