"""Before/after harness for the knowledge-graph retrieval fixes (#1 join detection,
#2 full-value lookup in search_graph).

Run from the repo root:  python scripts/test_kg_fixes.py
It builds a fresh KG over the sample DBs and asserts concrete behaviours, printing
PASS/FAIL per case so the same script documents the bug (before) and the fix (after).
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

SAMPLES = Path("assets/samples")

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def make_ctx(*db_names: str) -> Path:
    ctx = Path(tempfile.mkdtemp())
    for db in db_names:
        shutil.copy(SAMPLES / db, ctx / db)
    return ctx


def build(ctx: Path):
    task = SimpleNamespace(context_dir=ctx)
    kg = build_knowledge_graph(task)
    persist_knowledge_graph(ctx, kg)
    return kg


def rels(kg):
    return [
        (r.from_entity, r.from_column, r.to_entity, r.to_column, r.relationship_type, round(r.confidence, 2))
        for r in kg.relationships
    ]


def has_fk(kg, child_e, child_c, parent_e, parent_c) -> tuple[bool, float]:
    """True if a relationship links child.child_c <-> parent.parent_c (either direction)."""
    best = 0.0
    found = False
    for fe, fc, te, tc, _t, conf in rels(kg):
        if {(fe, fc), (te, tc)} == {(child_e, child_c), (parent_e, parent_c)}:
            found = True
            best = max(best, conf)
    return found, best


def bogus_pk_fks(kg) -> list:
    """Relationships that join two bare 'id' columns as a (candidate) foreign key."""
    out = []
    for fe, fc, te, tc, t, conf in rels(kg):
        if fc.lower() == "id" and tc.lower() == "id" and t in {"confirmed_fk", "fk_candidate"}:
            out.append((fe, fc, te, tc, t, conf))
    return out


def main() -> int:
    # ============================ JOIN DETECTION (#1) ============================
    print("\n=== crm.db + billing.db : join detection ===")
    ctx = make_ctx("crm.db", "billing.db")
    kg = build(ctx)
    for r in rels(kg):
        print("    ", r)

    bogus = bogus_pk_fks(kg)
    check("no bare id<->id pretend-FK", not bogus, f"found {len(bogus)}: {bogus[:3]}")

    found, conf = has_fk(kg, "invoices", "customer_id", "customers", "id")
    check("real FK invoices.customer_id -> customers.id detected", found, f"confidence={conf}")

    # The correct FK must out-rank any id<->id link that survives.
    id_conf = max([c for *_rest, c in bogus], default=0.0)
    check("real FK ranks above any id<->id link", found and conf > id_conf,
          f"real={conf} vs id<->id={id_conf}")

    print("\n=== shop.db : join detection (declared FKs exist) ===")
    ctx2 = make_ctx("shop.db")
    kg2 = build(ctx2)
    for r in rels(kg2):
        print("    ", r)
    for child_e, child_c, parent_e in [
        ("orders", "customer_id", "customers"),
        ("order_items", "order_id", "orders"),
        ("order_items", "product_id", "products"),
    ]:
        f, c = has_fk(kg2, child_e, child_c, parent_e, "id")
        check(f"FK {child_e}.{child_c} -> {parent_e}.id detected", f, f"confidence={c}")
    check("no bare id<->id pretend-FK (shop)", not bogus_pk_fks(kg2))

    # ============================ VALUE LOOKUP (#2) ==============================
    print("\n=== crm.db + billing.db : search_graph value lookup ===")
    # term -> (must be found?, hint about where it lives)
    cases = [
        ("Enterprise", True, "crm.customers.segment (in first rows)"),
        ("overdue", True, "billing.invoices.status (real value, beyond first 3 rows)"),
        ("Account 050", True, "crm.customers.name (row 50)"),
        ("VN", True, "crm.customers.country"),
        # Lexical limit (documented): 'unpaid' is a CONCEPT, the literal value is
        # 'overdue'/'open' — substring search cannot bridge that. Stays a true miss.
        ("unpaid", False, "concept, no literal value — lexical limit"),
        ("zzqq-not-present", False, "truly absent"),
    ]
    for term, expect_found, where in cases:
        res = search_graph(ctx, term)
        ok = (res["match_count"] > 0) == expect_found
        check(f"search '{term}' ({where})", ok,
              f"match_count={res['match_count']}, expected_found={expect_found}")

    print("\n=== shop.db : search_graph value lookup ===")
    for term, expect_found, where in [
        ("Electronics", True, "products.category"),
        ("Customer 040", True, "customers.name (row 40, beyond samples)"),
        ("nope-absent-xyz", False, "truly absent"),
    ]:
        res = search_graph(ctx2, term)
        ok = (res["match_count"] > 0) == expect_found
        check(f"search '{term}' ({where})", ok,
              f"match_count={res['match_count']}, expected_found={expect_found}")

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
