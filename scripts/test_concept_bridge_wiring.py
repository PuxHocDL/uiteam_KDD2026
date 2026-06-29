"""Tests for wiring the LLM concept-bridge into the read_knowledge_graph TOOL.

Run from the repo root:  python scripts/test_concept_bridge_wiring.py
No network: a scripted stub stands in for the chat model. Asserts the request-
scoped model flows into the tool, that a concept ('unpaid') bridges to the real
status values, that without a model it degrades to fuzzy, and that the LLM cost
guard skips high-cardinality columns.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph
from data_agent_baseline.tools.kg_store import persist_knowledge_graph
from data_agent_baseline.tools.registry import create_default_tool_registry
from data_agent_baseline.tools.semantic_match import (
    reset_request_model, resolve_model, set_request_model,
)

SAMPLES = Path("assets/samples")
_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


class CountingModel:
    """Stub chat model: returns a fixed JSON array, counts calls."""
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


def read_kg(reg, ctx, query):
    task = SimpleNamespace(context_dir=ctx)
    return reg.execute(task, "read_knowledge_graph", {"query": query}).content


def main() -> int:
    reg = create_default_tool_registry()
    ctx = make_ctx("crm.db", "billing.db")  # invoices.status ∈ {open, overdue, paid, void}

    # ===================== contextvar plumbing =====================
    print("\n=== request-scoped model (contextvar) ===")
    check("resolve_model() None by default", resolve_model() is None)
    m = CountingModel("[]")
    tok = set_request_model(m)
    try:
        check("resolve_model() returns pinned model", resolve_model() is m)
    finally:
        reset_request_model(tok)
    check("resolve_model() None after reset", resolve_model() is None)

    # ===================== no model → fuzzy only (degrade) =====================
    print("\n=== read_knowledge_graph WITHOUT model (degrades to fuzzy) ===")
    res = read_kg(reg, ctx, "unpaid")
    check("'unpaid' NOT found by fuzzy (concept ≠ literal)", res["match_count"] == 0,
          f"match_count={res['match_count']} via={res.get('matched_via')}")

    # ===================== with model → concept bridged =====================
    print("\n=== read_knowledge_graph WITH model (concept-bridge) ===")
    bridge = CountingModel('["overdue","open"]')
    tok = set_request_model(bridge)
    try:
        res = read_kg(reg, ctx, "unpaid")
    finally:
        reset_request_model(tok)
    ents = res.get("entities", [])
    matched_on = ents[0]["matched_on"] if ents else []
    check("'unpaid' bridged to real status values", res["match_count"] >= 1
          and any("status=" in s for s in matched_on),
          f"via={res.get('matched_via')} matched_on={matched_on}")
    check("model was actually called", bridge.calls > 0, f"calls={bridge.calls}")
    check("cost guard: LLM calls bounded (≤ 8)", bridge.calls <= 8, f"calls={bridge.calls}")

    # ===================== real value still cheap (no LLM) =====================
    print("\n=== real value resolves via exact, no LLM call ===")
    spy = CountingModel('["should-not-be-called"]')
    tok = set_request_model(spy)
    try:
        res = read_kg(reg, ctx, "overdue")  # a real literal value
    finally:
        reset_request_model(tok)
    check("real value 'overdue' found via exact", res["match_count"] >= 1
          and res.get("matched_via") == "exact", f"via={res.get('matched_via')}")
    check("model NOT called for an exact hit", spy.calls == 0, f"calls={spy.calls}")

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
