"""Tests for the real (single-tenant) auth: hashed passwords + signed tokens."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from server.app import app
from server.auth import AuthStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "AUTH", AuthStore(tmp_path / "auth"))
    return TestClient(app)


def test_register_returns_token_and_me_works(client):
    r = client.post("/api/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["username"] == "alice" and token

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["username"] == "alice"


def test_passwords_are_hashed_on_disk(client, tmp_path):
    client.post("/api/auth/register", json={"username": "bob", "password": "hunter2pass"})
    users_file = next((tmp_path / "auth").glob("users.json"))
    text = users_file.read_text(encoding="utf-8")
    assert "hunter2pass" not in text          # never stored in clear
    assert '"hash"' in text and '"salt"' in text


def test_login_rejects_wrong_password(client):
    client.post("/api/auth/register", json={"username": "carol", "password": "secret123"})
    ok = client.post("/api/auth/login", json={"username": "carol", "password": "secret123"})
    bad = client.post("/api/auth/login", json={"username": "carol", "password": "nope"})
    assert ok.status_code == 200
    assert bad.status_code == 401


def test_duplicate_username_is_rejected_case_insensitively(client):
    client.post("/api/auth/register", json={"username": "Dave", "password": "secret123"})
    dup = client.post("/api/auth/register", json={"username": "dave", "password": "secret123"})
    assert dup.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/auth/register", json={"username": "erin", "password": "x"})
    assert r.status_code == 400


def test_me_requires_valid_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.token"}).status_code == 401


def test_tampered_token_is_rejected(client):
    token = client.post(
        "/api/auth/register", json={"username": "frank", "password": "secret123"}
    ).json()["token"]
    payload, sig = token.split(".")
    forged = f"{payload}.{sig[:-2]}xx"
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
