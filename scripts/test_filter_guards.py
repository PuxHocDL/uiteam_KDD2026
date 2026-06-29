"""Tests for I1 (zero/empty guard covers execute_python) and I2 (suggest the REAL
filter value via fuzzy + concept-bridge across SQL *and* python).

Run from the repo root:  python scripts/test_filter_guards.py
Reproduces the failures seen in the UI: the SMB query filtered `status='unpaid'`
inside pandas → empty → answered 0, with no nudge. No network — a stub model
stands in for the concept-bridge.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.agents.reasoning import empty_filter_hint
from data_agent_baseline.tools.kg_store import (
    literal_filter_hint, persist_knowledge_graph,
)
from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph

SAMPLES = Path("assets/samples")
_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


class ConstModel:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(self, messages, *, json_object: bool = False) -> str:  # noqa: ANN001
        self.calls += 1
        return self.reply


def make_ctx(*dbs: str) -> Path:
    ctx = Path(tempfile.mkdtemp())
    for db in dbs:
        shutil.copy(SAMPLES / db, ctx / db)
    persist_knowledge_graph(ctx, build_knowledge_graph(SimpleNamespace(context_dir=ctx)))
    return ctx


def main() -> int:
    # ===================== I1: empty/zero guard for execute_python ==============
    print("\n=== I1: zero/empty guard now covers execute_python ===")
    # The exact shape the SMB query produced: pandas sum over an empty merge.
    py_zero = {"success": True, "output": '{"columns": ["total_unpaid_amount"], "rows": [[0]]}\n'}
    check("python {'rows':[[0]]} → hint", empty_filter_hint("execute_python", py_zero) is not None)
    check("python bare '0' → hint",
          empty_filter_hint("execute_python", {"success": True, "output": "0"}) is not None)
    check("python empty rows → hint",
          empty_filter_hint("execute_python", {"success": True, "output": '{"rows": []}'}) is not None)
    check("python real value → quiet",
          empty_filter_hint("execute_python", {"success": True, "output": '{"rows": [[62203.99]]}'}) is None)
    check("python failed run → quiet",
          empty_filter_hint("execute_python", {"success": False, "output": ""}) is None)

    # ===================== I2: suggest the real value (SQL + python) ============
    print("\n=== I2: name the wrong literal, suggest the real value ===")
    ctx = make_ctx("crm.db", "billing.db")
    bridge = ConstModel('["overdue","open"]')

    # The SMB pandas case: code filters status == "unpaid"; result empty.
    py_code = {"code": 'df = pd.read_sql("SELECT * FROM invoices WHERE status = \'unpaid\'", c)'}
    hint = literal_filter_hint("execute_python", py_code, py_zero, ctx, model=bridge)
    check("python: 'unpaid' flagged + real status suggested",
          hint is not None and "unpaid" in hint and "status" in hint, repr(hint[:120] if hint else None))

    # The SQL case: COUNT(*) WHERE status='unpaid' → [[0]].
    sql_in = {"sql": "SELECT COUNT(*) FROM invoices WHERE status = 'unpaid'", "path": "billing.db"}
    sql_zero = {"columns": ["c"], "rows": [[0]], "row_count": 1}
    hint2 = literal_filter_hint("execute_context_sql", sql_in, sql_zero, ctx, model=ConstModel('["overdue","open"]'))
    check("sql: 'unpaid' flagged + real status suggested",
          hint2 is not None and "unpaid" in hint2 and "status" in hint2, repr(hint2[:120] if hint2 else None))

    # ===================== no false positives =================================
    print("\n=== no false positives ===")
    # A real value that exists → no 'not found' hint even on a (hypothetical) zero.
    ok_in = {"sql": "SELECT COUNT(*) FROM invoices WHERE status = 'overdue'"}
    check("real literal 'overdue' → no not-found hint",
          literal_filter_hint("execute_context_sql", ok_in, sql_zero, ctx, model=ConstModel("[]")) is None)
    # Non-empty result → guard stays silent regardless of literals.
    check("non-empty result → silent",
          literal_filter_hint("execute_context_sql",
                              {"sql": "... WHERE status='unpaid'"},
                              {"rows": [[5]], "row_count": 1}, ctx, model=bridge) is None)
    # No model → fuzzy can't bridge the concept 'unpaid' → no suggestion (degrades).
    check("no model → concept 'unpaid' not bridged (degrades quietly)",
          literal_filter_hint("execute_python", py_code, py_zero, ctx, model=None) is None)

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
