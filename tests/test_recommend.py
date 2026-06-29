"""Tests for §12.5 — the solution-recommendation endpoint.

The advice must always name a real solution, list the other three as
alternatives, and shift with the question + the workspace's data shape.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from server.app import app
from server.sessions import SessionStore

VALID = {"react", "dragin", "multi", "hybrid_b"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "STORE", SessionStore(tmp_path / "sessions"))
    return TestClient(app)


def _new_session(client) -> str:
    return client.post("/api/sessions", json={"name": "rec"}).json()["id"]


def _upload(client, sid, name, content=b"a,b\n1,2\n"):
    return client.post(f"/api/sessions/{sid}/files", params={"filename": name}, content=content)


def test_simple_single_source_recommends_react(client):
    sid = _new_session(client)
    _upload(client, sid, "people.csv", b"name,age\nA,1\n")
    r = client.post("/api/recommend-solution", json={"question": "how many rows are there?", "session_id": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["recommended"] == "react"
    # always exactly the other three, none duplicating the recommendation
    assert [a["id"] for a in body["alternatives"]]  # non-empty
    assert len(body["alternatives"]) == 3
    assert all(a["id"] in VALID for a in body["alternatives"])
    assert body["recommended"] not in {a["id"] for a in body["alternatives"]}
    assert body["reason"]


def test_multiple_databases_recommend_multi(client):
    sid = _new_session(client)
    _upload(client, sid, "crm.db", b"not-a-real-db-but-suffix-counts")
    _upload(client, sid, "billing.db", b"not-a-real-db-but-suffix-counts")
    r = client.post("/api/recommend-solution",
                    json={"question": "which customers have unpaid invoices?", "session_id": sid})
    assert r.json()["recommended"] == "multi"


def test_multistep_question_recommends_multi(client):
    sid = _new_session(client)
    _upload(client, sid, "sales.csv", b"region,total\nA,1\n")
    r = client.post("/api/recommend-solution",
                    json={"question": "Compare revenue across regions for each product", "session_id": sid})
    assert r.json()["recommended"] == "multi"


def test_document_question_recommends_dragin(client):
    sid = _new_session(client)
    _upload(client, sid, "report.pdf", b"%PDF-1.4 fake")
    r = client.post("/api/recommend-solution",
                    json={"question": "Who is involved in the project?", "session_id": sid})
    assert r.json()["recommended"] == "dragin"


def test_blank_question_rejected(client):
    sid = _new_session(client)
    assert client.post("/api/recommend-solution", json={"question": "   ", "session_id": sid}).status_code == 400


def test_no_target_rejected(client):
    assert client.post("/api/recommend-solution", json={"question": "hello"}).status_code == 400
