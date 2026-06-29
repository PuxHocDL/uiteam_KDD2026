"""Tests for §12.2a — PDF/text → knowledge graph.

Three groups:
  • document loading (PDF + Markdown chunking)
  • LLM extraction with a scripted model — verifies merge, quote-grounding,
    canonical-label dedup, hallucination drop, and entity-type normalisation
  • the /api/sessions/{sid}/textkg endpoint — happy path + no-key fallback +
    unsupported file type

Uses pypdf to mint a tiny one-page PDF on the fly so we don't ship a binary
fixture, and the existing ScriptedModelAdapter to make the LLM deterministic.
"""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from data_agent_baseline.agents.model import ScriptedModelAdapter
from data_agent_baseline.tools.text_kg import (
    build_text_knowledge_graph, read_text_document, _extract_json, _legacy_extract,
    _merge_triplets, _canon, _best_quote,
)


PASSAGE = (
    "Acme Corp launched Project Falcon in 2023. "
    "Jack Ma leads Acme Corp and signed the partnership with Globex Ltd. "
    "Globex Ltd is headquartered in Berlin."
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _make_pdf(tmp_path: Path, text: str = PASSAGE) -> Path:
    """Render a single text page into a real PDF (pypdf parses what it writes)."""
    from pypdf import PageObject, PdfWriter
    from pypdf.generic import (
        ArrayObject, ContentStream, DictionaryObject, FloatObject, NameObject,
        NumberObject, TextStringObject,
    )

    writer = PdfWriter()
    page = PageObject.create_blank_page(width=612, height=792)
    # Embed Helvetica (a PDF built-in core font — no font file needed).
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font)
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})})
    page[NameObject("/Resources")] = resources

    # Wrap text in a basic BT..ET stream — one line is enough for the loader.
    safe = text.replace("(", "[").replace(")", "]")
    stream = (
        f"BT /F1 12 Tf 50 740 Td ({safe}) Tj ET\n"
    ).encode("latin-1")
    content = ContentStream(None, writer)
    content.set_data(stream)
    page[NameObject("/Contents")] = writer._add_object(content)

    writer.add_page(page)
    path = tmp_path / "report.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def _kg_response() -> str:
    """A plausible LLM reply matching the schema documented in text_kg._SYSTEM."""
    return json.dumps({
        "nodes": [
            {"label": "Acme Corp", "type": "Organisation", "summary": "An organisation."},
            {"label": "Project Falcon", "type": "Project", "summary": "A 2023 initiative."},
            {"label": "Jack Ma", "type": "Person", "summary": ""},
            {"label": "Globex Ltd", "type": "Organisation", "summary": ""},
            {"label": "Berlin", "type": "Place", "summary": ""},
        ],
        "edges": [
            {"source": "Acme Corp", "target": "Project Falcon", "relation": "launched",
             "quote": "Acme Corp launched Project Falcon in 2023"},
            {"source": "Jack Ma", "target": "Acme Corp", "relation": "leads",
             "quote": "Jack Ma leads Acme Corp"},
            {"source": "Acme Corp", "target": "Globex Ltd", "relation": "partnered with",
             "quote": "signed the partnership with Globex Ltd"},
            {"source": "Globex Ltd", "target": "Berlin", "relation": "headquartered in",
             "quote": "Globex Ltd is headquartered in Berlin"},
            # Hallucinated edge — source label not in the node list ⇒ should be dropped.
            {"source": "Mystery Inc", "target": "Acme Corp", "relation": "owns",
             "quote": "Mystery Inc owns Acme Corp"},
            # Edge with a paraphrased "quote" that doesn't appear verbatim — keep
            # the edge but clear the quote (still useful, just no evidence).
            {"source": "Jack Ma", "target": "Globex Ltd", "relation": "met with",
             "quote": "Jack Ma had a meeting with Globex executives"},
        ],
    })


# --------------------------------------------------------------------------- #
# loader tests
# --------------------------------------------------------------------------- #
def test_read_text_document_handles_markdown(tmp_path: Path) -> None:
    md = tmp_path / "notes.md"
    md.write_text("Para 1.\n\nPara 2.\n\nPara 3.")
    doc = read_text_document(md)
    assert doc["kind"] == "text"
    assert doc["pages"]
    assert doc["n_chars"] > 0
    assert "Para 1" in doc["pages"][0]["text"]


def test_read_text_document_rejects_unsupported(tmp_path: Path) -> None:
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        read_text_document(p)


def test_read_text_document_loads_pdf(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path)
    doc = read_text_document(pdf)
    assert doc["kind"] == "pdf"
    assert doc["pages"], "PDF must yield at least one extracted page"
    assert "Acme" in doc["pages"][0]["text"], "extractor should recover the embedded text"


# --------------------------------------------------------------------------- #
# extractor tests
# --------------------------------------------------------------------------- #
def test_build_kg_extracts_merges_and_drops_hallucinations() -> None:
    doc = {"kind": "text", "pages": [{"page": 1, "text": PASSAGE}], "n_chars": len(PASSAGE)}
    model = ScriptedModelAdapter([_kg_response()])
    graph = _legacy_extract(doc, model)

    labels = {n["label"] for n in graph["nodes"]}
    assert labels == {"Acme Corp", "Project Falcon", "Jack Ma", "Globex Ltd", "Berlin"}

    # Hallucinated "Mystery Inc" edge must be dropped (source not in node set).
    edges = [(e["source"], e["target"], e["relation"]) for e in graph["edges"]]
    assert ("mystery inc", "acme corp", "owns") not in edges
    assert ("acme corp", "project falcon", "launched") in edges
    assert ("jack ma", "acme corp", "leads") in edges
    assert len(graph["edges"]) == 5  # the 6th raw edge is the dropped hallucination

    # Every kept edge with a quote must cite text actually present in the passage.
    for e in graph["edges"]:
        if e["quote"]:
            assert e["quote"].lower() in PASSAGE.lower()
    # The paraphrased quote ("had a meeting with") is not in the passage and must be cleared.
    paraphrase = [e for e in graph["edges"] if e["source"] == "jack ma" and e["target"] == "globex ltd"][0]
    assert paraphrase["quote"] == ""

    assert graph["pages_used"] == 1
    assert graph["note"] is None


def test_build_kg_merges_duplicate_labels_across_pages() -> None:
    doc = {"kind": "text", "n_chars": 200, "pages": [
        {"page": 1, "text": "Acme Corp partnered with Globex. Jack Ma signed."},
        {"page": 2, "text": "Acme Corp opened a Berlin office. Jack Ma toured the site."},
    ]}
    r1 = json.dumps({
        "nodes": [{"label": "Acme Corp", "type": "Organisation", "summary": ""},
                  {"label": "Jack Ma", "type": "Person", "summary": "CEO."}],
        "edges": [{"source": "Jack Ma", "target": "Acme Corp", "relation": "signed for",
                   "quote": "Jack Ma signed"}],
    })
    r2 = json.dumps({
        "nodes": [{"label": "Acme Corp", "type": "Organisation", "summary": "An org."},
                  {"label": "Berlin", "type": "Place", "summary": ""}],
        "edges": [{"source": "Acme Corp", "target": "Berlin", "relation": "opened office in",
                   "quote": "Acme Corp opened a Berlin office"}],
    })
    graph = _legacy_extract(doc, ScriptedModelAdapter([r1, r2]))
    acme = [n for n in graph["nodes"] if n["label"] == "Acme Corp"][0]
    assert acme["pages"] == [1, 2], "duplicate nodes across pages should merge with both page numbers"
    assert acme["summary"] == "An org.", "merged node should adopt the first non-empty summary"
    assert len(graph["nodes"]) == 3


def test_build_kg_normalises_entity_types() -> None:
    # Common LLM-invented type names ("Company", "Location") must map to the
    # canonical set; "Alien" → "Other".
    raw = json.dumps({
        "nodes": [{"label": "Foo Corp", "type": "Company", "summary": ""},
                  {"label": "Paris", "type": "Location", "summary": ""},
                  {"label": "Topic X", "type": "Alien", "summary": ""}],
        "edges": [],
    })
    doc = {"kind": "text", "pages": [{"page": 1, "text": "Foo Corp Paris Topic X"}], "n_chars": 30}
    graph = _legacy_extract(doc, ScriptedModelAdapter([raw]))
    type_of = {n["label"]: n["type"] for n in graph["nodes"]}
    assert type_of == {"Foo Corp": "Organisation", "Paris": "Place", "Topic X": "Other"}


def test_build_kg_skips_pages_that_dont_parse() -> None:
    doc = {"kind": "text", "n_chars": 200, "pages": [
        {"page": 1, "text": "x"}, {"page": 2, "text": "y"}, {"page": 3, "text": "z"}]}
    model = ScriptedModelAdapter([
        json.dumps({"nodes": [{"label": "A", "type": "Other", "summary": ""}], "edges": []}),
        "Sorry, I can't help with that.",  # not JSON — gets dropped
        json.dumps({"nodes": [{"label": "B", "type": "Other", "summary": ""}], "edges": []}),
    ])
    graph = _legacy_extract(doc, model)
    labels = sorted(n["label"] for n in graph["nodes"])
    assert labels == ["A", "B"]
    assert "2" in (graph["note"] or "")
    assert graph["pages_used"] == 2


def test_extract_json_tolerates_fences_and_prefix() -> None:
    fenced = "Sure!\n```json\n" + json.dumps({"x": 1}) + "\n```"
    assert _extract_json(fenced) == {"x": 1}
    suffixed = json.dumps({"y": 2}) + "\n\nLet me know if you need more."
    assert _extract_json(suffixed) == {"y": 2}


# --------------------------------------------------------------------------- #
# LlamaIndex path — mapping + merge + grounding (no LLM, no network)
# --------------------------------------------------------------------------- #
def test_llamaindex_path_maps_entitynodes_and_relations(monkeypatch):
    """`build_text_knowledge_graph` should drive the LlamaIndex path, read back
    EntityNode/Relation objects from node metadata, and ground a verbatim quote.
    We stub the LLM + extractor so no API call happens."""
    from data_agent_baseline.tools import text_kg as tk
    from llama_index.core.graph_stores.types import (
        KG_NODES_KEY, KG_RELATIONS_KEY, EntityNode, Relation,
    )

    doc = {"kind": "text", "n_chars": len(PASSAGE), "pages": [{"page": 1, "text": PASSAGE}]}

    def fake_builder(_llm, _num_workers):
        def _run(tnodes):
            for tn in tnodes:
                tn.metadata[KG_NODES_KEY] = [
                    EntityNode(name="Acme Corp", label="Organisation"),
                    EntityNode(name="Project Falcon", label="Project"),
                ]
                tn.metadata[KG_RELATIONS_KEY] = [
                    Relation(label="launched", source_id="Acme Corp", target_id="Project Falcon"),
                    # hallucinated target (not an emitted entity) → must drop
                    Relation(label="owns", source_id="Acme Corp", target_id="Ghost Co"),
                ]
            return tnodes
        return _run, "dynamic"

    monkeypatch.setattr(tk, "_build_li_llm", lambda _creds: object())
    monkeypatch.setattr(tk, "_build_li_extractor", fake_builder)

    creds = {"model": "m", "api_base": "http://x", "api_key": "k", "api_version": ""}
    graph = build_text_knowledge_graph(doc, creds)

    assert graph["engine"] == "llamaindex:dynamic"
    assert {n["label"] for n in graph["nodes"]} == {"Acme Corp", "Project Falcon"}
    edges = [(e["source"], e["target"], e["relation"]) for e in graph["edges"]]
    assert ("acme corp", "project falcon", "launched") in edges
    assert all(e[2] != "owns" for e in edges), "edge to a non-entity must be dropped"
    quote = graph["edges"][0]["quote"]
    assert "Acme Corp launched Project Falcon" in quote
    assert graph["edges"][0]["page"] == 1


def test_llamaindex_failure_falls_back_to_builtin(monkeypatch):
    """If the LlamaIndex path raises, we must transparently fall back to the
    builtin per-page extractor (scripted here) and tag engine='builtin'."""
    from data_agent_baseline.tools import text_kg as tk

    def _boom(*_a, **_k):
        raise RuntimeError("no llama-index here")

    monkeypatch.setattr(tk, "_extract_with_llamaindex", _boom)
    monkeypatch.setattr(tk, "_legacy_adapter", lambda _creds: ScriptedModelAdapter([_kg_response()]))

    doc = {"kind": "text", "n_chars": len(PASSAGE), "pages": [{"page": 1, "text": PASSAGE}]}
    graph = build_text_knowledge_graph(doc, {"api_key": "k"})
    assert graph["engine"] == "builtin"
    assert "Acme Corp" in {n["label"] for n in graph["nodes"]}


def test_canon_collapses_punctuation_and_space_variants():
    assert _canon("Acme Inc.") == _canon("Acme Inc") == "acme inc"
    assert _canon("  Project   Falcon ") == "project falcon"
    assert _canon("") == ""


def test_best_quote_needs_both_endpoints_in_one_sentence():
    text = "Acme launched Falcon in 2023. Globex is unrelated here."
    assert "Acme launched Falcon" in _best_quote(text, "Acme", "Falcon")
    assert _best_quote(text, "Acme", "Globex") == ""  # different sentences → no quote
    assert _best_quote("", "Acme", "Falcon") == ""


def test_merge_triplets_dedupes_edges_and_unions_pages():
    records = [
        {"s_name": "Acme Inc.", "s_type": "Organisation", "t_name": "Falcon", "t_type": "Project",
         "relation": "launched", "page": 1, "text": "Acme Inc. launched Falcon."},
        # same edge, variant source spelling, new page, weaker type
        {"s_name": "Acme Inc", "s_type": "Other", "t_name": "Falcon", "t_type": "Project",
         "relation": "Launched", "page": 2, "text": "Acme Inc launched Falcon again."},
    ]
    g = _merge_triplets(records, pages_total=2, pages_used=2, max_pages=25)
    assert len(g["edges"]) == 1, "identical (src,tgt,relation) should dedupe"
    acme = next(n for n in g["nodes"] if n["id"] == "acme inc")
    assert acme["pages"] == [1, 2], "spelling variants merge and keep both pages"
    assert acme["type"] == "Organisation", "the more specific type wins over 'Other'"
    assert g["note"] is None


def test_merge_triplets_emits_clusters_and_hierarchy():
    """Two disconnected sub-graphs must end up as two communities, each labelled
    by its hub and grouped under a super-type. Every node carries its cluster_id
    so the UI can colour/hull/collapse it without a second pass."""
    records = [
        # cluster A — people around Acme Corp
        {"s_name": "Acme Corp", "s_type": "Organisation", "t_name": "Jack", "t_type": "Person",
         "relation": "employs", "page": 1, "text": "Acme Corp employs Jack."},
        {"s_name": "Acme Corp", "s_type": "Organisation", "t_name": "Alice", "t_type": "Person",
         "relation": "employs", "page": 1, "text": "Acme Corp employs Alice."},
        {"s_name": "Jack", "s_type": "Person", "t_name": "Alice", "t_type": "Person",
         "relation": "knows", "page": 1, "text": "Jack knows Alice."},
        # cluster B — independent triple about Globex / Berlin
        {"s_name": "Globex", "s_type": "Organisation", "t_name": "Berlin", "t_type": "Place",
         "relation": "based in", "page": 2, "text": "Globex based in Berlin."},
        {"s_name": "Globex", "s_type": "Organisation", "t_name": "Mark", "t_type": "Person",
         "relation": "led by", "page": 2, "text": "Globex led by Mark."},
    ]
    g = _merge_triplets(records, pages_total=2, pages_used=2, max_pages=25)
    assert {n["cluster_id"] for n in g["nodes"]} == {n["cluster_id"] for n in g["nodes"]}, "every node tagged"
    # Two disconnected sub-graphs ⇒ at least two clusters; biggest is the Acme one.
    assert len(g["clusters"]) >= 2
    sizes = sorted((c["size"] for c in g["clusters"]), reverse=True)
    assert sizes[0] >= 3 and sizes[1] >= 2
    # Hub of the bigger cluster should be one of the high-degree nodes in it.
    top = max(g["clusters"], key=lambda c: c["size"])
    assert top["hub_id"] in {"acme corp", "jack", "alice"}
    assert top["dominant_type"] in {"Organisation", "Person"}
    # Hierarchy groups clusters by dominant entity type.
    types = {h["type"] for h in g["hierarchy"]}
    assert types  # at least one super-cluster emitted
    all_children = [cid for h in g["hierarchy"] for cid in h["children"]]
    assert sorted(all_children) == sorted(c["id"] for c in g["clusters"])
    # Cross-cluster edges count zero internal — Acme and Globex share no edges.
    by_id = {c["id"]: c for c in g["clusters"]}
    acme_cluster = by_id[next(n for n in g["nodes"] if n["id"] == "acme corp")["cluster_id"]]
    globex_cluster = by_id[next(n for n in g["nodes"] if n["id"] == "globex")["cluster_id"]]
    assert acme_cluster["id"] != globex_cluster["id"]
    assert acme_cluster["external_edges"] == 0
    assert globex_cluster["external_edges"] == 0


def test_cluster_graph_is_deterministic_across_calls():
    """Same input ⇒ same cluster ids/labels. Guards against hash-order leakage
    that would make the UI shuffle cluster colours between renders."""
    records = [
        {"s_name": f"N{i}", "s_type": "Person", "t_name": f"N{i+1}", "t_type": "Person",
         "relation": "next", "page": 1, "text": f"N{i} N{i+1}"} for i in range(6)
    ]
    a = _merge_triplets(records, pages_total=1, pages_used=1, max_pages=25)
    b = _merge_triplets(records, pages_total=1, pages_used=1, max_pages=25)
    assert [(c["id"], c["hub_id"], c["size"]) for c in a["clusters"]] \
        == [(c["id"], c["hub_id"], c["size"]) for c in b["clusters"]]


# --------------------------------------------------------------------------- #
# API endpoint
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client_with_session(tmp_path, monkeypatch):
    """Spin up the FastAPI app with a temp session store + a stubbed model adapter
    so /api/textkg returns the canned LLM reply without hitting any network."""
    monkeypatch.setenv("DAB_DUMMY", "1")  # belt-and-braces, harmless if unused
    # Redirect the store to a fresh tmp directory so other tests stay clean.
    from server import app as server_app
    from server.sessions import SessionStore
    server_app.STORE = SessionStore(tmp_path / "sessions")
    # Force the deterministic builtin path (no LlamaIndex, no network): make the
    # primary extractor raise so the orchestrator falls back, and feed it a
    # scripted model instead of a real API client.
    from data_agent_baseline.tools import text_kg as tk

    def _no_llamaindex(*_a, **_k):
        raise RuntimeError("llamaindex disabled in test")

    monkeypatch.setattr(tk, "_extract_with_llamaindex", _no_llamaindex)
    monkeypatch.setattr(tk, "_legacy_adapter", lambda _creds: ScriptedModelAdapter([_kg_response()]))

    client = TestClient(server_app.app)
    sid = client.post("/api/sessions", json={"name": "kg-test"}).json()["id"]
    # Upload a tiny markdown doc so the loader has a real file to read.
    md_bytes = (PASSAGE + "\n").encode("utf-8")
    up = client.post(f"/api/sessions/{sid}/files?filename=passage.md", content=md_bytes,
                     headers={"Content-Type": "text/markdown"})
    assert up.status_code == 200, up.text
    return client, sid


def test_textkg_endpoint_returns_graph(client_with_session):
    client, sid = client_with_session
    r = client.post(f"/api/sessions/{sid}/textkg", json={
        "filename": "passage.md",
        "model": "stub", "api_base": "http://x", "api_key": "k", "api_version": "",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc"]["kind"] == "text"
    assert body["doc"]["pages"] >= 1
    labels = {n["label"] for n in body["nodes"]}
    assert "Acme Corp" in labels and "Jack Ma" in labels
    assert body["edges"], "expected at least one relation"


def test_textkg_endpoint_no_key_returns_note(tmp_path):
    """With no api_key the endpoint must short-circuit and tell the user why,
    instead of failing or returning an empty graph silently."""
    from server import app as server_app
    from server.sessions import SessionStore
    server_app.STORE = SessionStore(tmp_path / "sessions")
    client = TestClient(server_app.app)
    sid = client.post("/api/sessions", json={"name": "no-key"}).json()["id"]
    client.post(f"/api/sessions/{sid}/files?filename=p.md", content=b"hello world",
                headers={"Content-Type": "text/markdown"})
    r = client.post(f"/api/sessions/{sid}/textkg", json={
        "filename": "p.md", "model": "", "api_base": "", "api_key": "", "api_version": "",
    })
    assert r.status_code == 200
    assert r.json()["nodes"] == []
    assert "API key" in r.json()["note"]


def test_textkg_endpoint_rejects_unsupported(tmp_path):
    from server import app as server_app
    from server.sessions import SessionStore
    server_app.STORE = SessionStore(tmp_path / "sessions")
    client = TestClient(server_app.app)
    sid = client.post("/api/sessions", json={"name": "bad-type"}).json()["id"]
    client.post(f"/api/sessions/{sid}/files?filename=data.csv", content=b"a,b\n1,2\n",
                headers={"Content-Type": "text/csv"})
    r = client.post(f"/api/sessions/{sid}/textkg", json={
        "filename": "data.csv", "model": "stub", "api_base": "http://x", "api_key": "k",
    })
    assert r.status_code == 415
