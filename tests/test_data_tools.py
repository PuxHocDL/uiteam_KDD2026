"""Tests for the data-reasoning tools: KG persistence, cross-file source map,
and the classify/plan primitives.

Imports the tool functions directly (not the registry) to avoid the pre-existing
registry→data_quality→agents import cycle, and because the logic lives in these
modules anyway.
"""
from __future__ import annotations

# Load the agents package first so the pre-existing tools↔agents import cycle
# (tools/__init__ → registry → data_quality → agents → react → tools.registry)
# resolves in the right order when this is the first test module collected.
import data_agent_baseline.agents  # noqa: F401  (import-order side effect)

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_agent_baseline.tools.kg_store import (
    ensure_knowledge_graph,
    kg_db_path,
    load_knowledge_graph,
    search_graph,
)
from data_agent_baseline.tools.planning import classify_question, plan_task
from data_agent_baseline.tools.source_map import (
    locate_terms,
    map_sources,
    read_any_text,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A trace-like workspace: a customers table + a document holding the answer."""
    (tmp_path / "customers.csv").write_text(
        "customer_id,name,city\n1,Acme Corp,Paris\n2,Globex,Berlin\n", encoding="utf-8"
    )
    (tmp_path / "orders.csv").write_text(
        "order_id,customer_id,total\n10,1,500\n11,2,300\n", encoding="utf-8"
    )
    (tmp_path / "review.md").write_text(
        "# Acme Global Review 2025\n\n"
        "Acme Corp owns subsidiaries Acme Paris SARL (Paris) and Acme Berlin GmbH (Berlin).\n",
        encoding="utf-8",
    )
    con = sqlite3.connect(tmp_path / "crm.db")
    con.execute("CREATE TABLE leads(lead_id INTEGER, company TEXT)")
    con.execute("INSERT INTO leads VALUES (1, 'Initech')")
    con.commit()
    con.close()
    return tmp_path


# --------------------------------------------------------------------------- #
# kg_store                                                                     #
# --------------------------------------------------------------------------- #

def test_ensure_persists_and_reloads(workspace: Path):
    graph = ensure_knowledge_graph(workspace)
    assert kg_db_path(workspace).exists()
    assert len(graph["entities"]) >= 3  # customers, orders, leads
    reloaded = load_knowledge_graph(workspace)
    assert reloaded is not None
    assert reloaded["source"] == "persisted_sqlite"
    assert {e["name"] for e in reloaded["entities"]} >= {"customers", "orders", "leads"}


def test_search_graph_finds_value(workspace: Path):
    hit = search_graph(workspace, "Acme")
    assert hit["match_count"] >= 1
    assert any("customers" == m["entity"] for m in hit["entities"])


def test_search_graph_miss_points_to_documents(workspace: Path):
    hit = search_graph(workspace, "subsidiaries")
    assert hit["match_count"] == 0
    assert "document" in hit["note"].lower()


def test_rebuild_does_not_ingest_kg_cache(workspace: Path):
    from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph
    from data_agent_baseline.tools.kg_store import persist_knowledge_graph

    kg1 = build_knowledge_graph(SimpleNamespace(context_dir=workspace))
    persist_knowledge_graph(workspace, kg1)
    kg2 = build_knowledge_graph(SimpleNamespace(context_dir=workspace))
    # the .kg/graph.db cache must NOT show up as entities on a rebuild
    assert not any(e.name.startswith("kg_") for e in kg2.entities)
    assert {e.name for e in kg1.entities} == {e.name for e in kg2.entities}


# --------------------------------------------------------------------------- #
# source_map                                                                   #
# --------------------------------------------------------------------------- #

def test_read_any_text_reads_markdown(workspace: Path):
    out = read_any_text(workspace / "review.md")
    assert "subsidiaries" in out["text"].lower()
    assert out["total_chars"] > 0


def test_map_sources_links_document_to_table(workspace: Path):
    m = map_sources(workspace)
    docs = {d["path"]: d for d in m["documents"]}
    assert "review.md" in docs
    links = docs["review.md"]["links_to_tables"]
    # the doc mentions the literal value 'Acme Corp' from customers.name
    assert any(link["entity"] == "customers" and link["via"] == "value" for link in links)


def test_map_sources_focus_verdict_document_only(workspace: Path):
    focus = map_sources(workspace, focus="subsidiary")["focus"]
    assert focus["documents"], "should find the term in the document"
    assert not focus["structured"]
    assert "do not keep probing" in focus["verdict"].lower()


def test_map_sources_focus_plural_root_match(workspace: Path):
    # singular query must still match the plural 'subsidiaries' in the doc
    focus = map_sources(workspace, focus="subsidiary")["focus"]
    assert any(d["path"] == "review.md" for d in focus["documents"])


def test_locate_terms_separates_tables_and_docs(workspace: Path):
    located = locate_terms(workspace, ["Acme Corp", "subsidiaries", "Nonexistent"])
    assert located["Acme Corp"]["structured"]
    assert located["subsidiaries"]["documents"]
    assert not located["Nonexistent"]["structured"] and not located["Nonexistent"]["documents"]


# --------------------------------------------------------------------------- #
# planning                                                                     #
# --------------------------------------------------------------------------- #

def test_classify_question_recommends_known_solution(workspace: Path):
    out = classify_question(workspace, "How many customers are there?", difficulty="easy")
    assert out["recommended"] in {"react", "dragin", "multi", "hybrid_b"}
    assert len(out["alternatives"]) == 3
    assert out["recommended"] not in {a["id"] for a in out["alternatives"]}


def test_classify_question_routes_multistep_to_multi(workspace: Path):
    out = classify_question(
        workspace, "For each city, compare total orders across customers", difficulty="hard"
    )
    assert out["recommended"] == "multi"


def test_plan_task_locates_entities(workspace: Path):
    plan = plan_task(
        workspace, "What are the subsidiaries of Acme Corp and the city each is located in?"
    )
    assert "subsidiaries" in plan["located"]["in_documents"]
    assert "review.md" in plan["located"]["documents"]
    assert "customers" in plan["located"]["tables"]
    assert "read_pdf" in plan["recommended_tools"]
    # interrogatives are not treated as entities to locate
    assert "What" not in plan["key_terms"]


def test_plan_task_plan_text_warns_against_db_probing(workspace: Path):
    plan = plan_task(workspace, "subsidiaries of Acme Corp")
    assert "Do NOT keep querying databases" in plan["plan"]
