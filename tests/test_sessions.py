"""Tests for §12.0 — session workspaces decoupled from the benchmark dataset."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from server.app import app
from server.sessions import SessionStore, build_session_task


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app's store at a throwaway dir so tests never touch artifacts/.
    monkeypatch.setattr(app_module, "STORE", SessionStore(tmp_path / "sessions"))
    return TestClient(app)


def test_session_and_file_lifecycle(client):
    # create
    created = client.post("/api/sessions", json={"name": "Demo"})
    assert created.status_code == 200, created.text
    sid = created.json()["id"]
    assert created.json()["name"] == "Demo"

    # list contains it
    assert any(s["id"] == sid for s in client.get("/api/sessions").json())

    # rename + settings
    assert client.patch(f"/api/sessions/{sid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    patched = client.patch(f"/api/sessions/{sid}", json={"settings": {"solution": "dragin"}})
    assert patched.json()["settings"]["solution"] == "dragin"

    # upload a CSV as the raw request body (no multipart needed)
    csv = b"name,age\nAlice,30\nBob,40\n"
    up = client.post(f"/api/sessions/{sid}/files", params={"filename": "people.csv"}, content=csv)
    assert up.status_code == 200, up.text
    meta = up.json()
    assert meta["kind"] == "csv"
    assert meta["rowCount"] == 2
    assert meta["name"] == "people.csv"

    # the file physically lands in the session's context dir (the engine's context_dir)
    assert (app_module.STORE.context_dir(sid) / "people.csv").read_bytes() == csv

    # list / delete file
    files = client.get(f"/api/sessions/{sid}/files").json()
    assert len(files) == 1 and files[0]["id"] == meta["id"]
    assert client.delete(f"/api/sessions/{sid}/files/{meta['id']}").json()["ok"]
    assert client.get(f"/api/sessions/{sid}/files").json() == []

    # delete session
    assert client.delete(f"/api/sessions/{sid}").json()["ok"]
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def test_unknown_session_returns_404(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.get("/api/sessions/nope/files").status_code == 404
    miss = client.post("/api/sessions/nope/files", params={"filename": "x.csv"}, content=b"a\n1\n")
    assert miss.status_code == 404


def test_upload_filename_is_sanitised(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    up = client.post(f"/api/sessions/{sid}/files", params={"filename": "../../evil.csv"}, content=b"a,b\n1,2\n")
    assert up.status_code == 200
    name = up.json()["name"]
    assert "/" not in name and ".." not in name
    assert (app_module.STORE.context_dir(sid) / name).exists()


def test_empty_upload_rejected(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/files", params={"filename": "x.csv"}, content=b"").status_code == 400


def test_run_requires_a_target(client):
    # neither session_id nor task_id
    r = client.post("/api/run", json={"model": "m", "api_base": "b", "api_key": "k"})
    assert r.status_code == 400


def test_run_session_requires_question(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    r = client.post("/api/run", json={"session_id": sid, "model": "m", "api_base": "b", "api_key": "k"})
    assert r.status_code == 400


def test_build_session_task(tmp_path):
    ctx = tmp_path / "context"
    ctx.mkdir()
    task = build_session_task("What is X?", ctx, "abc123")
    assert task.context_dir == ctx
    assert task.question == "What is X?"
    assert task.task_id == "session_abc123"
