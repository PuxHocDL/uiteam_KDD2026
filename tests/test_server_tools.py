"""Server tests for the Tools catalog endpoint and the text-KG disk cache."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from server.app import app
from server.sessions import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "STORE", SessionStore(tmp_path / "sessions"))
    return TestClient(app)


def test_tools_endpoint_lists_full_registry(client):
    body = client.get("/api/tools").json()
    names = {t["name"] for t in body["tools"]}
    assert body["count"] == len(body["tools"])
    # the new data-reasoning tools must be present, not just the old static set
    assert {"read_knowledge_graph", "map_sources", "read_pdf",
            "classify_question", "plan_task"} <= names
    # every tool carries the fields the UI needs
    for t in body["tools"]:
        assert t["name"] and "description" in t and "requires_approval" in t
    answer = next(t for t in body["tools"] if t["name"] == "answer")
    assert answer["requires_approval"] is False  # answer isn't approval-gated in the registry


def _session_with_doc(client) -> str:
    sid = client.post("/api/sessions", json={"name": "kg"}).json()["id"]
    client.post(
        f"/api/sessions/{sid}/files",
        params={"filename": "review.md"},
        content=b"Acme Corp owns subsidiaries Acme Paris and Acme Berlin.",
    )
    return sid


def test_textkg_is_cached_and_force_rebuilds(client, monkeypatch):
    calls = {"n": 0}

    def fake_build(doc, creds, max_pages=25):
        calls["n"] += 1
        return {"nodes": [{"id": "n1", "label": "Acme Corp", "type": "Organisation", "pages": [1]}],
                "edges": [], "engine": "builtin"}

    monkeypatch.setattr(app_module, "build_text_knowledge_graph", fake_build)
    sid = _session_with_doc(client)
    body = {"filename": "review.md", "model": "gpt-4o", "api_base": "x", "api_key": "KEY"}

    r1 = client.post(f"/api/sessions/{sid}/textkg", json=body).json()
    assert r1["cached"] is False and len(r1["nodes"]) == 1 and calls["n"] == 1

    r2 = client.post(f"/api/sessions/{sid}/textkg", json=body).json()
    assert r2["cached"] is True and len(r2["nodes"]) == 1
    assert calls["n"] == 1  # served from cache — no second LLM call

    r3 = client.post(f"/api/sessions/{sid}/textkg", json={**body, "force": True}).json()
    assert r3["cached"] is False and calls["n"] == 2  # force rebuilds


def test_textkg_does_not_cache_empty_result(client, monkeypatch):
    # No api_key → returns a note, no graph; nothing should be cached.
    sid = _session_with_doc(client)
    r = client.post(f"/api/sessions/{sid}/textkg", json={"filename": "review.md"}).json()
    assert r["nodes"] == [] and "note" in r
    cache_dir = app_module.STORE.context_dir(sid).parent / ".textkg"
    assert not cache_dir.exists() or not any(cache_dir.iterdir())
