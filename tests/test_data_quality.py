"""Tests for §12.1 — Data Doctor: factual profiler + LLM-driven snippet generation +
sandboxed apply_pandas_fix."""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import server.app as app_module
from data_agent_baseline.agents.model import ScriptedModelAdapter
from data_agent_baseline.tools.data_quality import (
    AGENT_BY_NAME,
    DOMAIN_AGENTS,
    apply_pandas_fix,
    inspect_sqlite_schema,
    llm_suggest_fixes,
    llm_suggest_fixes_diag,
    preview_table,
    profile_quality,
    profile_quality_df,
    read_table,
    sqlite_tables,
)
from server.app import app
from server.sessions import SessionStore

DIRTY_CSV = (
    "id,age,country,note,price\n"
    "1,30,USA,x,$5\n"
    "2, 40 ,usa,x,$6\n"
    "3,,USA,x,$7\n"
    "3,,USA,x,$7\n"   # exact duplicate of the previous row
    "4,50,Canada,x,$8\n"
)


def _dirty_df() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(DIRTY_CSV), dtype=str, keep_default_na=True, na_values=[""])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "STORE", SessionStore(tmp_path / "sessions"))
    return TestClient(app)


# ---- factual profiler (no hard-coded issue rules) --------------------------
def test_profile_is_factual():
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    assert report["rows"] == 5
    assert report["duplicate_rows"] == 1
    cols = {c["column"]: c for c in report["column_reports"]}
    assert "issues" not in cols["age"]          # rule-based detection is gone
    assert "summary" not in report
    assert cols["age"]["nulls"] == 2
    assert cols["age"]["kind"] == "numeric"
    assert cols["note"]["unique"] == 1
    assert cols["country"]["unique"] == 3
    assert cols["country"]["samples"]           # sample values present
    assert "mean" in cols["price"]              # "$.." parses → numeric stats


# ---- LLM-driven suggestions (validated against the sandbox) ----------------
def test_llm_suggest_fixes_validates_code_and_columns():
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    scripted = ScriptedModelAdapter(['''```json
    {"suggestions": [
      {"column": "age", "issue": "missing", "severity": "warn",
       "title": "Fill missing age with the median",
       "rationale": "Median is robust to the 50 outlier.",
       "pandas_code": "df['age'] = pd.to_numeric(df['age'], errors='coerce'); df['age'] = df['age'].fillna(df['age'].median())",
       "expected_effect": "All nulls in age become 40."},
      {"column": null, "issue": "duplicates", "severity": "warn",
       "title": "Drop exact duplicate rows",
       "rationale": "Row 3 is repeated.",
       "pandas_code": "df = df.drop_duplicates().reset_index(drop=True)",
       "expected_effect": "5 rows → 4 rows."},
      {"column": "ghost", "issue": "x", "severity": "info",
       "title": "no", "rationale": "no",
       "pandas_code": "df['ghost'] = df['ghost'].str.strip()",
       "expected_effect": "n/a"},
      {"column": "country", "issue": "import attempt", "severity": "warn",
       "title": "bad", "rationale": "bad",
       "pandas_code": "import os; df['country'] = df['country'].str.lower()",
       "expected_effect": "n/a"},
      {"column": "country", "issue": "dunder attempt", "severity": "info",
       "title": "bad", "rationale": "bad",
       "pandas_code": "df.__class__; df['country'] = df['country'].str.lower()",
       "expected_effect": "n/a"}
    ]}
    ```'''])
    sugs = llm_suggest_fixes(report, scripted)
    # ghost column + `import os` + `__class__` all dropped → only the two valid ones remain
    assert len(sugs) == 2
    cols = {s["column"] for s in sugs}
    assert cols == {"age", None}
    assert all("pandas_code" in s and s["title"] for s in sugs)


def test_llm_suggest_fixes_handles_garbage():
    report = profile_quality_df(_dirty_df(), "d.csv")
    assert llm_suggest_fixes(report, ScriptedModelAdapter(["sorry, no json here"])) == []


# ---- multi-agent pipeline (one specialist per cluster of issues) -----------
def _sug(col, code, *, title="x", sev="warn"):
    return {"column": col, "issue": "i", "severity": sev,
            "title": title, "rationale": "r",
            "pandas_code": code, "expected_effect": "e"}


def _wrap(items):
    return json.dumps({"suggestions": items})


def test_multi_agent_runs_each_specialist_and_tags_cards():
    """Each of the 5 specialists contributes one valid suggestion; every kept card
    carries the `agent` it came from, and `diag.agents` reports per-specialist
    counts (so the UI can show chips like Missing 1/1 · Duplicates 1/1 …)."""
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    scripted = ScriptedModelAdapter([
        _wrap([_sug("age", "df['age'] = df['age'].fillna(0)")]),                              # missing
        _wrap([_sug(None, "df = df.drop_duplicates().reset_index(drop=True)")]),               # duplicates
        _wrap([_sug("price", "df['price'] = pd.to_numeric(df['price'].astype(str).str.replace('$','', regex=False), errors='coerce')")]),  # types
        _wrap([_sug("country", "df['country'] = df['country'].astype('string').str.strip().str.title()")]),  # formatting
        _wrap([_sug("age", "df['age'] = df['age'].clip(0, 120)")]),                            # outliers
    ])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False)
    assert len(sugs) == 5
    assert {s["agent"] for s in sugs} == {"missing", "duplicates", "types", "formatting", "outliers"}
    assert all(s.get("agent_label") for s in sugs)
    assert len(diag["agents"]) == 5
    assert sum(a["kept"] for a in diag["agents"]) == 5


def test_multi_agent_cross_dedup_collapses_identical_fix():
    """When two specialists propose the EXACT same (column, pandas_code) we keep one
    and bump the cross-agent `duplicate` counter so the UI can explain it."""
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    same_code = "df = df.drop_duplicates().reset_index(drop=True)"
    scripted = ScriptedModelAdapter([
        _wrap([_sug(None, same_code)]),
        _wrap([_sug(None, same_code)]),
        _wrap([]), _wrap([]), _wrap([]),
    ])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False)
    assert len(sugs) == 1
    assert diag["dropped"]["duplicate"] >= 1


def test_repair_pass_rescues_unsafe_snippet():
    """A snippet using `with` is rejected by the sandbox → the repair pass asks the
    model to rewrite it → the new (valid) code is kept and `repaired` is bumped."""
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    bad = _sug("age", "with open('x') as f:\n    df['age'] = 1", title="bad with-block")
    scripted = ScriptedModelAdapter([
        _wrap([bad]),                                                                # missing — bad
        '{"pandas_code": "df[\\"age\\"] = df[\\"age\\"].fillna(0)"}',                # repair payload
        _wrap([]), _wrap([]), _wrap([]), _wrap([]),
    ])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False, allow_repair=True)
    assert len(sugs) == 1
    assert sugs[0]["pandas_code"] == 'df["age"] = df["age"].fillna(0)'
    assert sugs[0]["agent"] == "missing"
    assert diag["agents"][0]["repaired"] == 1
    assert diag["agents"][0]["kept"] == 1


def test_repair_disabled_drops_unsafe_snippet():
    report = profile_quality_df(_dirty_df(), "dirty.csv")
    bad = _sug("age", "with open('x') as f:\n    df['age'] = 1")
    scripted = ScriptedModelAdapter([_wrap([bad])] + [_wrap([]) for _ in DOMAIN_AGENTS[1:]])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False, allow_repair=False)
    assert sugs == []
    assert diag["agents"][0]["dropped"]["unsafe_code"] == 1


def test_dry_run_drops_snippet_that_crashes_at_runtime():
    """A snippet can pass the AST sandbox AND target a real column, yet still raise
    at runtime (reading a missing column, dtype mismatch, …). The dry-run catches
    that before the card reaches the UI."""
    df = _dirty_df()
    report = profile_quality_df(df, "d.csv")
    # Column field is real ('age') so the unknown_column gate doesn't fire, but
    # the code READS from a non-existent column → KeyError at runtime.
    crashing = _sug("age", "df['age'] = df['ghost'].str.strip()")
    scripted = ScriptedModelAdapter([_wrap([crashing])] + [_wrap([]) for _ in DOMAIN_AGENTS[1:]])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False, allow_repair=False, df=df)
    assert sugs == []
    assert diag["agents"][0]["dropped"]["runtime_error"] == 1
    assert any("KeyError" in (x.get("reason") or "")
               for x in diag["agents"][0]["dropped_examples"])


def test_dry_run_repair_rescues_int64_dtype_error():
    """Regression for the Int64-vs-float bug — `df['age'].clip(0.5, 100.5)` on an
    Int64 column raises `TypeError: Invalid value '100.5' for dtype 'Int64'`. The
    runtime repair pass should rewrite the snippet (cast to Float64) and the
    rescued card should reach the UI."""
    df = pd.DataFrame({"age": pd.array([20, 30, 40, 50, 999], dtype="Int64")})
    report = profile_quality_df(df, "ages.csv")
    bad = _sug("age", "df['age'] = df['age'].clip(0.5, 100.5)")
    repair_code = "df['age'] = df['age'].astype('Float64').clip(0.5, 100.5)"
    scripted = ScriptedModelAdapter([
        _wrap([bad]),
        json.dumps({"pandas_code": repair_code}),
    ])
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False, df=df,
                                         agents=(AGENT_BY_NAME["outliers"],))
    assert len(sugs) == 1
    assert sugs[0]["pandas_code"] == repair_code
    assert diag["agents"][0]["repaired"] == 1
    assert diag["agents"][0]["kept"] == 1


def test_dry_run_skipped_when_no_df_supplied():
    """Back-compat: callers that don't have the dataframe still get the old AST-only
    pipeline (used by the legacy `llm_suggest_fixes` wrapper) — a snippet that would
    crash at runtime is kept because dry-run never runs."""
    df = _dirty_df()
    report = profile_quality_df(df, "d.csv")
    crashing = _sug("age", "df['age'] = df['ghost'].str.strip()")
    scripted = ScriptedModelAdapter([_wrap([crashing])] + [_wrap([]) for _ in DOMAIN_AGENTS[1:]])
    sugs, _ = llm_suggest_fixes_diag(report, scripted, parallel=False, allow_repair=False)
    assert len(sugs) == 1  # ran AST only, dry-run was skipped


def test_agent_error_is_recorded_not_raised():
    """If a specialist's model call blows up, the pipeline must not abort — it just
    records the error in that agent's diag so the UI can show an amber dot."""
    report = profile_quality_df(_dirty_df(), "d.csv")
    scripted = ScriptedModelAdapter([_wrap([])])  # only the first agent gets a response
    sugs, diag = llm_suggest_fixes_diag(report, scripted, parallel=False)
    assert sugs == []
    assert diag["agents"][0]["error"] is None
    failing = [a for a in diag["agents"][1:] if a["error"]]
    assert len(failing) == len(DOMAIN_AGENTS) - 1


def test_diag_agents_metadata_matches_registry():
    report = profile_quality_df(_dirty_df(), "d.csv")
    scripted = ScriptedModelAdapter([_wrap([]) for _ in DOMAIN_AGENTS])
    _, diag = llm_suggest_fixes_diag(report, scripted, parallel=False)
    assert [a["name"] for a in diag["agents"]] == [a.name for a in DOMAIN_AGENTS]
    for a in diag["agents"]:
        assert a["name"] in AGENT_BY_NAME
        assert a["label"] and a["role"]


# ---- sandboxed apply_pandas_fix --------------------------------------------
def test_apply_runs_pandas_snippet_and_diffs_result():
    df = _dirty_df()
    code = (
        "df['age'] = pd.to_numeric(df['age'], errors='coerce')\n"
        "df['age'] = df['age'].fillna(df['age'].median())"
    )
    new_df, result = apply_pandas_fix(df, code)
    assert result["nulls_before"] == 2 and result["nulls_after"] == 0
    assert result["rows_before"] == result["rows_after"] == 5
    assert result["pandas_code"] == code
    assert all("column" in c and c["column"] == "age" for c in result["changed"])
    assert new_df["age"].isna().sum() == 0


def test_apply_drop_duplicates_via_snippet():
    df = _dirty_df()
    new_df, result = apply_pandas_fix(df, "df = df.drop_duplicates().reset_index(drop=True)")
    assert result["rows_before"] == 5 and result["rows_after"] == 4
    assert len(new_df) == 4


def test_apply_quantile_works_on_currency_string_column():
    """Regression: `read_table` loads everything as strings, so an outlier snippet that
    calls `.quantile()` directly on a string column used to blow up with
    `ArrowNotImplementedError: Function 'quantile' has no kernel matching input types
    (large_string)`. `apply_pandas_fix` now coerces inferred-numeric columns first."""
    df = _dirty_df()  # `price` looks like '$5', '$6'… all strings
    code = (
        "q1, q3 = df['price'].quantile([0.25, 0.75]); iqr = q3 - q1\n"
        "df['price'] = df['price'].clip(q1 - 1.5*iqr, q3 + 1.5*iqr)"
    )
    new_df, _ = apply_pandas_fix(df, code)
    assert pd.api.types.is_numeric_dtype(new_df["price"])
    assert new_df["price"].between(5, 8).all()


def test_apply_rejects_imports():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "import os\ndf = df.drop_duplicates()")


def test_apply_rejects_dunder_attribute_access():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "df.__class__")


def test_apply_rejects_open_and_eval():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "open('x', 'w')")
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "eval('1+1')")


def test_apply_rejects_empty_code():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "   ")


def test_apply_surfaces_runtime_errors_as_value_error():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "df = df['does_not_exist']")


def test_apply_requires_df_to_remain_a_dataframe():
    with pytest.raises(ValueError):
        apply_pandas_fix(_dirty_df(), "df = 1")


# ---- endpoints -------------------------------------------------------------
def _session_with_dirty_csv(client) -> str:
    sid = client.post("/api/sessions", json={"name": "QA"}).json()["id"]
    up = client.post(f"/api/sessions/{sid}/files", params={"filename": "dirty.csv"}, content=DIRTY_CSV.encode())
    assert up.status_code == 200, up.text
    return sid


def test_quality_without_key_returns_profile_and_note(client):
    sid = _session_with_dirty_csv(client)
    body = client.post(f"/api/sessions/{sid}/quality", json={"filename": "dirty.csv"}).json()
    assert body["report"]["duplicate_rows"] == 1
    assert body["suggestions"] == []
    assert "API key" in body["note"]


def test_apply_writes_clean_copy_and_is_downloadable(client):
    sid = _session_with_dirty_csv(client)
    r = client.post(
        f"/api/sessions/{sid}/quality/apply",
        json={"filename": "dirty.csv",
              "fix": {"pandas_code": "df = df.drop_duplicates().reset_index(drop=True)"}},
    )
    assert r.status_code == 200, r.text
    meta, result = r.json()["file"], r.json()["result"]
    assert meta["name"] == "dirty_clean.csv"
    assert result["rows_after"] == 4
    names = {f["name"] for f in client.get(f"/api/sessions/{sid}/files").json()}
    assert {"dirty.csv", "dirty_clean.csv"} <= names
    dl = client.get(f"/api/sessions/{sid}/files/{meta['id']}/download")
    assert dl.status_code == 200 and dl.text.count("\n") == 4 + 1


def test_apply_requires_pandas_code(client):
    sid = _session_with_dirty_csv(client)
    r = client.post(f"/api/sessions/{sid}/quality/apply", json={"filename": "dirty.csv", "fix": {}})
    assert r.status_code == 400


def test_apply_returns_400_on_unsafe_snippet(client):
    sid = _session_with_dirty_csv(client)
    r = client.post(
        f"/api/sessions/{sid}/quality/apply",
        json={"filename": "dirty.csv", "fix": {"pandas_code": "import os"}},
    )
    assert r.status_code == 400


def test_quality_rejects_unsupported_file(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    client.post(f"/api/sessions/{sid}/files", params={"filename": "notes.md"}, content=b"# hi\n")
    r = client.post(f"/api/sessions/{sid}/quality", json={"filename": "notes.md"})
    assert r.status_code == 415


def test_preview_endpoint(client):
    sid = client.post("/api/sessions", json={}).json()["id"]
    client.post(f"/api/sessions/{sid}/files", params={"filename": "p.csv"}, content=b"a,b\n1,2\n3,4\n")
    fid = client.get(f"/api/sessions/{sid}/files").json()[0]["id"]
    body = client.get(f"/api/sessions/{sid}/files/{fid}/preview").json()
    assert body["kind"] == "table"
    assert body["columns"] == ["a", "b"]
    assert body["total_rows"] == 2
    assert body["rows"][0] == ["1", "2"]


def test_apply_dry_run_previews_without_writing(client):
    sid = _session_with_dirty_csv(client)
    r = client.post(
        f"/api/sessions/{sid}/quality/apply",
        json={"filename": "dirty.csv",
              "fix": {"pandas_code": "df = df.drop_duplicates().reset_index(drop=True)"},
              "dry_run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("preview") is True
    assert body["result"]["rows_after"] == 4
    names = {f["name"] for f in client.get(f"/api/sessions/{sid}/files").json()}
    assert "dirty_clean.csv" not in names   # dry-run wrote nothing


# ---- analysis history (persisted Data Doctor results) ----------------------
def test_analysis_is_saved_and_retrievable(client, monkeypatch):
    sid = _session_with_dirty_csv(client)
    scripted = ScriptedModelAdapter([_wrap([]) for _ in DOMAIN_AGENTS])
    monkeypatch.setattr(app_module, "_adapter", lambda req: scripted)
    body = client.post(f"/api/sessions/{sid}/quality",
                       json={"filename": "dirty.csv", "api_key": "x"}).json()
    aid = body["analysis_id"]
    assert aid and body["saved_at"]

    index = client.get(f"/api/sessions/{sid}/analyses").json()
    assert len(index) == 1
    assert index[0]["filename"] == "dirty.csv"
    assert index[0]["rows"] == 5

    full = client.get(f"/api/sessions/{sid}/analyses/{aid}").json()
    assert full["report"]["duplicate_rows"] == 1
    assert full["suggestions"] == []
    assert full["diag"]["agents"]


def test_delete_and_clear_analyses(client, monkeypatch):
    sid = _session_with_dirty_csv(client)
    monkeypatch.setattr(
        app_module, "_adapter",
        lambda req: ScriptedModelAdapter([_wrap([]) for _ in DOMAIN_AGENTS]),
    )
    # Two analyses → delete one → clear the rest.
    a1 = client.post(f"/api/sessions/{sid}/quality",
                     json={"filename": "dirty.csv", "api_key": "x"}).json()["analysis_id"]
    # New scripted adapter is needed for the second call (ScriptedModelAdapter pops responses).
    monkeypatch.setattr(
        app_module, "_adapter",
        lambda req: ScriptedModelAdapter([_wrap([]) for _ in DOMAIN_AGENTS]),
    )
    a2 = client.post(f"/api/sessions/{sid}/quality",
                     json={"filename": "dirty.csv", "api_key": "x"}).json()["analysis_id"]
    assert a1 != a2

    assert len(client.get(f"/api/sessions/{sid}/analyses").json()) == 2
    r = client.delete(f"/api/sessions/{sid}/analyses/{a1}")
    assert r.status_code == 200
    assert len(client.get(f"/api/sessions/{sid}/analyses").json()) == 1
    cleared = client.delete(f"/api/sessions/{sid}/analyses").json()
    assert cleared["removed"] == 1
    assert client.get(f"/api/sessions/{sid}/analyses").json() == []



# ---- SQLite (.db) support (�12.1 + �12.2b) --------------------------------
import sqlite3 as _sq


def _make_shop_like(path):
    """Tiny shop-like DB with FKs and a planted duplicate row."""
    with _sq.connect(path) as c:
        c.executescript(
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, country TEXT);"
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, "
            "FOREIGN KEY(customer_id) REFERENCES customers(id));"
        )
        c.executemany("INSERT INTO customers VALUES (?,?,?,?)", [
            (1, "Alice", "a@x.com", "VN"),
            (2, "Bob",   None,       "vn"),
            (3, "Cara",  "c@x.com", "VN"),
            (4, "Cara",  "c@x.com", "VN"),   # near-duplicate of row 3 (different PK)
        ])
        c.executemany("INSERT INTO orders VALUES (?,?,?)",
                      [(1, 1, 50.0), (2, 2, -10.0), (3, 3, 200.0)])


def test_sqlite_tables_lists_user_tables_only(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    assert sqlite_tables(db) == ["customers", "orders"]


def test_read_table_sqlite_default_first_table(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    df = read_table(db)
    assert list(df.columns) == ["id", "name", "email", "country"]
    assert len(df) == 4


def test_read_table_sqlite_rejects_unknown_table(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    with pytest.raises(ValueError):
        read_table(db, table="nope")


def test_preview_table_sqlite_with_table_argument(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    body = preview_table(db, rows=10, table="orders")
    assert body["columns"] == ["id", "customer_id", "total"]
    assert body["total_rows"] == 3


def test_profile_quality_sqlite_picks_named_table(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    report = profile_quality(db, table="orders")
    assert report["file"].endswith("#orders")
    assert report["rows"] == 3


def test_inspect_sqlite_schema_returns_columns_and_fks(tmp_path):
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    schema = inspect_sqlite_schema(db)
    names = {t["name"] for t in schema["tables"]}
    assert names == {"customers", "orders"}
    fks = schema["foreign_keys"]
    assert any(f["from_table"] == "orders" and f["to_table"] == "customers" for f in fks)
    orders = next(t for t in schema["tables"] if t["name"] == "orders")
    assert any(c["name"] == "customer_id" and c["fk"] for c in orders["columns"])
    assert any(c["pk"] for c in orders["columns"])


def test_preview_endpoint_for_sqlite(client, tmp_path):
    sid = client.post("/api/sessions", json={}).json()["id"]
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    client.post(f"/api/sessions/{sid}/files", params={"filename": "shop.db"},
                content=db.read_bytes())
    fid = client.get(f"/api/sessions/{sid}/files").json()[0]["id"]

    # Default � first table.
    body = client.get(f"/api/sessions/{sid}/files/{fid}/preview").json()
    assert body["kind"] == "table"
    assert body["tables"] == ["customers", "orders"]
    assert body["table"] == "customers"
    assert "name" in body["columns"]

    # Switch table via query param.
    body2 = client.get(f"/api/sessions/{sid}/files/{fid}/preview",
                       params={"table": "orders"}).json()
    assert body2["table"] == "orders"
    assert body2["columns"] == ["id", "customer_id", "total"]


def test_quality_endpoint_for_sqlite_with_table(client, tmp_path):
    sid = client.post("/api/sessions", json={}).json()["id"]
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    client.post(f"/api/sessions/{sid}/files", params={"filename": "shop.db"},
                content=db.read_bytes())
    body = client.post(f"/api/sessions/{sid}/quality",
                       json={"filename": "shop.db", "table": "orders"}).json()
    assert body["report"]["rows"] == 3
    assert body["report"]["file"].endswith("#orders")


def test_apply_fix_on_sqlite_writes_named_clean_csv(client, tmp_path):
    sid = client.post("/api/sessions", json={}).json()["id"]
    db = tmp_path / "shop.db"
    _make_shop_like(db)
    client.post(f"/api/sessions/{sid}/files", params={"filename": "shop.db"},
                content=db.read_bytes())
    r = client.post(
        f"/api/sessions/{sid}/quality/apply",
        json={"filename": "shop.db", "table": "customers",
              "fix": {"pandas_code": "df = df.drop_duplicates().reset_index(drop=True)"}},
    )
    assert r.status_code == 200, r.text
    meta = r.json()["file"]
    assert meta["name"] == "shop__customers_clean.csv"
    # Original DB still present untouched.
    names = {f["name"] for f in client.get(f"/api/sessions/{sid}/files").json()}
    assert "shop.db" in names and "shop__customers_clean.csv" in names


def test_session_schema_endpoint_aggregates_dbs_and_detects_cross_links(client, tmp_path):
    sid = client.post("/api/sessions", json={}).json()["id"]
    # crm.db has customers(id PK)
    crm = tmp_path / "crm.db"
    with _sq.connect(crm) as c:
        c.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)")
        c.executemany("INSERT INTO customers VALUES (?,?)", [(1, "A"), (2, "B")])
    # billing.db has invoices(customer_id ...) � heuristic should link to crm.customers
    billing = tmp_path / "billing.db"
    with _sq.connect(billing) as c:
        c.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL)")
        c.executemany("INSERT INTO invoices VALUES (?,?,?)", [(1, 1, 50.0), (2, 2, 75.0)])

    client.post(f"/api/sessions/{sid}/files", params={"filename": "crm.db"}, content=crm.read_bytes())
    client.post(f"/api/sessions/{sid}/files", params={"filename": "billing.db"}, content=billing.read_bytes())

    body = client.get(f"/api/sessions/{sid}/schema").json()
    files = {b["file"] for b in body["databases"]}
    assert files == {"crm.db", "billing.db"}
    assert any(
        link["from_file"] == "billing.db" and link["from_table"] == "invoices"
        and link["from_column"] == "customer_id"
        and link["to_file"] == "crm.db" and link["to_table"] == "customers"
        for link in body["cross_links"]
    )
