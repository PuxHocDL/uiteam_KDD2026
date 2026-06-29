"""Tests for fix #3 — semantic-ish value matching (fuzzy / llm / hybrid).

Run from the repo root:  python scripts/test_semantic_match.py
Covers the three strategies as pure units (fuzzy), with a scripted stub model
(llm), combined (hybrid), and end-to-end through search_graph on the sample DBs.
No network / real model needed — the llm path uses a fake adapter.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401  break a circular import

from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph
from data_agent_baseline.tools.kg_store import persist_knowledge_graph, search_graph
from data_agent_baseline.tools.semantic_match import (
    fuzzy_match, llm_expand_values, match_values, tokens,
)

SAMPLES = Path("assets/samples")
_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


class ConstModel:
    """A stub ModelAdapter that returns the same JSON for every call."""
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls = 0

    def complete(self, messages, *, json_object: bool = False) -> str:  # noqa: ANN001
        self.calls += 1
        return self._reply


def make_ctx(*dbs: str) -> Path:
    ctx = Path(tempfile.mkdtemp())
    for db in dbs:
        shutil.copy(SAMPLES / db, ctx / db)
    task = SimpleNamespace(context_dir=ctx)
    persist_knowledge_graph(ctx, build_knowledge_graph(task))
    return ctx


def main() -> int:
    # ============================== 1. FUZZY (pure) =============================
    print("\n=== fuzzy: surface variants (offline, deterministic) ===")
    check("word order: 'Corp Acme' ~ 'Acme Corporation'",
          fuzzy_match("Corp Acme", "Acme Corporation"))
    check("zero-pad: 'Customer 40' ~ 'Customer 040'",
          fuzzy_match("Customer 40", "Customer 040"))
    check("case+punct: 'ACME-CORP' ~ 'Acme Corp'",
          fuzzy_match("ACME-CORP", "Acme Corp"))
    check("abbrev prefix: 'order' ~ 'orders'", fuzzy_match("order", "orders"))
    check("zero-pad tokens 040 -> 40", tokens("Customer 040") == {"customer", "40"})
    # precision: fuzzy must NOT over-match unrelated values
    check("precision: 'Acme Inc' !~ 'Beta Corp'",
          not fuzzy_match("Acme Inc", "Beta Corp"))
    # documented limit: fuzzy cannot bridge a CONCEPT to a different word
    check("limit: 'unpaid' !~ 'overdue' (needs llm)",
          not fuzzy_match("unpaid", "overdue"))

    print("\n=== fuzzy via match_values ===")
    statuses = ["open", "overdue", "paid", "void"]
    check("match_values fuzzy finds nothing for concept 'unpaid'",
          match_values("unpaid", statuses, mode="fuzzy") == [])
    check("match_values fuzzy finds 'paid' for query 'paid'",
          match_values("paid", statuses, mode="fuzzy") == ["paid"])

    # ============================== 2. LLM (stub) ==============================
    print("\n=== llm: concept -> value (scripted stub) ===")
    model = ConstModel('["overdue","open"]')
    res = llm_expand_values("unpaid", statuses, model)
    check("concept 'unpaid' -> {overdue, open}", res == ["overdue", "open"], str(res))

    halluc = ConstModel('["overdue","ghost-status"]')
    res = llm_expand_values("unpaid", statuses, halluc)
    check("hallucination guard: 'ghost-status' dropped", res == ["overdue"], str(res))

    check("llm with no model -> []", llm_expand_values("unpaid", statuses, None) == [])
    check("match_values mode=llm uses model",
          match_values("unpaid", statuses, mode="llm", model=ConstModel('["open"]')) == ["open"])

    # ============================== 3. HYBRID ==================================
    print("\n=== hybrid: fuzzy first, llm only on miss ===")
    # surface variant handled by fuzzy WITHOUT touching the model
    spy = ConstModel('["should-not-be-used"]')
    res = match_values("Customer 40", ["Customer 040", "Customer 041"], mode="hybrid", model=spy)
    check("hybrid uses fuzzy for surface variant (model not called)",
          res == ["Customer 040"] and spy.calls == 0, f"res={res} calls={spy.calls}")
    # concept handled by llm because fuzzy found nothing
    bridge = ConstModel('["overdue","open"]')
    res = match_values("unpaid", statuses, mode="hybrid", model=bridge)
    check("hybrid escalates to llm for concept (model called)",
          res == ["overdue", "open"] and bridge.calls == 1, f"res={res} calls={bridge.calls}")
    # graceful degrade: hybrid with no model == fuzzy
    check("hybrid degrades to fuzzy when no model",
          match_values("unpaid", statuses, mode="hybrid", model=None) == [])

    # ===================== 4. INTEGRATION via search_graph =====================
    print("\n=== search_graph integration (real sample DBs) ===")
    shop = make_ctx("shop.db")
    crmbill = make_ctx("crm.db", "billing.db")

    # exact still misses 'Customer 40' (the literal value is 'Customer 040')
    r_exact = search_graph(shop, "Customer 40", mode="exact")
    check("exact misses 'Customer 40' (literal is 'Customer 040')",
          r_exact["match_count"] == 0, f"via={r_exact['matched_via']}")
    # fuzzy escalation finds it
    r_fuzzy = search_graph(shop, "Customer 40", mode="fuzzy")
    check("fuzzy finds 'Customer 40' -> 'Customer 040'",
          r_fuzzy["match_count"] >= 1 and r_fuzzy["matched_via"] == "fuzzy",
          f"via={r_fuzzy['matched_via']}")

    # concept 'unpaid' — exact & fuzzy miss; hybrid+model bridges to overdue/open
    r_exact2 = search_graph(crmbill, "unpaid", mode="exact")
    check("exact misses concept 'unpaid'", r_exact2["match_count"] == 0)
    r_fuzzy2 = search_graph(crmbill, "unpaid", mode="fuzzy")
    check("fuzzy still misses concept 'unpaid'", r_fuzzy2["match_count"] == 0)
    r_hybrid = search_graph(crmbill, "unpaid", mode="hybrid", model=ConstModel('["overdue","open"]'))
    matched_on = r_hybrid["entities"][0]["matched_on"] if r_hybrid["entities"] else []
    check("hybrid+model bridges 'unpaid' -> status values",
          r_hybrid["match_count"] >= 1 and r_hybrid["matched_via"] == "hybrid",
          f"via={r_hybrid['matched_via']} matched_on={matched_on}")

    # don't regress: a real value still resolves via the cheap exact path
    r_real = search_graph(crmbill, "overdue", mode="hybrid", model=ConstModel("[]"))
    check("real value 'overdue' resolves via exact (no escalation)",
          r_real["match_count"] >= 1 and r_real["matched_via"] == "exact",
          f"via={r_real['matched_via']}")

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
