"""Tests for §12.2c — the Explore (data-scientist statistics) view."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from data_agent_baseline.tools.data_quality import excel_sheets
from data_agent_baseline.tools.explore import profile_statistics, profile_statistics_df
from server.app import app
from server.sessions import SessionStore


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "x": [str(i) for i in range(1, 21)],                       # numeric 1..20
        "y": [str(2 * i) for i in range(1, 21)],                   # perfectly correlated with x
        "cat": (["a", "b", "a", "c"] * 5),                         # categorical (a appears 10x)
        "m": ["1", "2", None, "4"] + [str(i) for i in range(5, 21)],  # numeric, one missing
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "STORE", SessionStore(tmp_path / "sessions"))
    return TestClient(app)


def test_classifies_columns_and_numeric_stats():
    rep = profile_statistics_df(_df(), "t.csv")
    kinds = {c["column"]: c["kind"] for c in rep["column_stats"]}
    assert kinds["x"] == "numeric"
    assert kinds["cat"] == "categorical"
    xs = next(c for c in rep["column_stats"] if c["column"] == "x")
    assert xs["min"] == 1.0 and xs["max"] == 20.0 and xs["mean"] == 10.5
    assert sum(xs["histogram"]["counts"]) == 20  # every value lands in a bin


def test_top_categories_and_missingness():
    rep = profile_statistics_df(_df(), "t.csv")
    cat = next(c for c in rep["column_stats"] if c["column"] == "cat")
    assert {d["value"]: d["count"] for d in cat["top"]}["a"] == 10
    assert {m["column"]: m["missing"] for m in rep["missingness"]}["m"] == 1


def test_correlation_and_scatter_pairs():
    rep = profile_statistics_df(_df(), "t.csv")
    corr = rep["correlation"]
    assert {"x", "y"} <= set(corr["columns"])
    i, j = corr["columns"].index("x"), corr["columns"].index("y")
    assert round(corr["matrix"][i][j], 3) == 1.0  # y == 2x
    assert any({s["x"], s["y"]} == {"x", "y"} for s in rep["scatter_suggestions"])


def test_excel_loader_lists_and_reads_sheets(tmp_path):
    xlsx = tmp_path / "book.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        pd.DataFrame({"a": [1, 2, 3]}).to_excel(writer, sheet_name="one", index=False)
        pd.DataFrame({"b": [4, 5]}).to_excel(writer, sheet_name="two", index=False)
    assert excel_sheets(xlsx) == ["one", "two"]
    rep = profile_statistics(xlsx, sheet="two")
    assert rep["rows"] == 2 and rep["sheets"] == ["one", "two"]


def test_explore_endpoint(client):
    sid = client.post("/api/sessions", json={"name": "explore"}).json()["id"]
    client.post(f"/api/sessions/{sid}/files", params={"filename": "t.csv"},
                content=_df().to_csv(index=False).encode())
    r = client.post(f"/api/sessions/{sid}/explore", json={"filename": "t.csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == 20
    assert body["numeric_columns"] >= 2
    assert body["correlation"] is not None
