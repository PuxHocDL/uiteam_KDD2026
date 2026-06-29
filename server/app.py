"""Data Agent Studio — live API gateway.

Wraps the Phase-1 ReAct engine and streams its real trace to the frontend over
Server-Sent Events (SSE). Supports two interaction modes:
  • autopilot — runs end-to-end.
  • co-pilot  — pauses before every tool (AWAITING_USER); the UI sends a decision
                back via POST /api/decide (approve / edit / reject / cancel).
Also exposes /api/chat for conversational small-talk replies via the LLM.

Credentials come from the request (the UI Settings) — nothing is hard-coded. Run:
    uv run uvicorn server.app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import queue
import re
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from data_agent_baseline.agents.model import ModelMessage
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig
from data_agent_baseline.benchmark.dataset import DABenchPublicDataset
from data_agent_baseline.config import PROJECT_ROOT, AgentConfig, AppConfig
from data_agent_baseline.run.runner import _select_agent_routing, build_model_adapter
from data_agent_baseline.tools.data_quality import (
    apply_pandas_fix, inspect_sqlite_schema, llm_suggest_fixes_diag, preview_table,
    profile_quality, read_table, sqlite_tables,
)
from data_agent_baseline.tools.explore import profile_statistics
from data_agent_baseline.tools.knowledge_graph import build_knowledge_graph
from data_agent_baseline.tools.registry import create_default_tool_registry
from data_agent_baseline.tools.text_kg import build_text_knowledge_graph, read_text_document
from server.auth import AuthError, AuthStore
from server.sessions import SessionStore, build_session_task

app = FastAPI(title="Data Agent Studio API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DATASET = DABenchPublicDataset(AppConfig().dataset.root_path)

# §12.0 — user workspaces, decoupled from the benchmark dataset. A run can target
# either a benchmark `task_id` (unchanged) or a `session_id` (uploaded files).
STORE = SessionStore(PROJECT_ROOT / "artifacts" / "studio_sessions")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # per-file upload limit

# Real (single-tenant) auth: salted password hashes + signed tokens on disk.
AUTH = AuthStore(PROJECT_ROOT / "artifacts" / "auth")

SAMPLES_DIR = PROJECT_ROOT / "assets" / "samples"
_SAMPLE_DESC = {
    "sales_2024.csv":         "Clean transactional sales — 30 orders across 4 regions for aggregation demos.",
    "employees_messy.csv":    "Dirty HR data — duplicates, mixed casing, currency strings, multi-format dates.",
    "weather_observations.csv": "Numeric series with NaNs and outliers (e.g. 99.9, -50.0) — great for IQR detection.",
    "survey_responses.tsv":   "TSV survey with inconsistent casing (yes/YES/Yes) and an empty comment.",
    "customers_dirty.csv":    "Dirty customer roster — NULLs, dup rows, mixed case, multi-format dates, currency strings.",
    "shop.db":                "Mini e-commerce SQLite — customers→orders→order_items→products with FK links, some quality issues.",
    "crm.db":                 "CRM SQLite — customers & interactions; shares customer_id with billing.db for cross-DB ER demo.",
    "billing.db":             "Billing SQLite — invoices & payments; links to crm.db via customer_id (multi-DB ER view).",
    "project_falcon_brief.pdf": "3-page PDF — people, organisations, places & dates for the text → knowledge-graph demo.",
}

# Active runs awaiting co-pilot decisions: run_id -> Queue of decision dicts.
RUNS: dict[str, "queue.Queue[dict]"] = {}


class RunRequest(BaseModel):
    # Provide exactly one target: a benchmark task_id OR a session_id (uploaded files).
    task_id: str | None = None
    session_id: str | None = None
    question: str | None = None  # required for a session run; overrides the task question
    mode: str = "autopilot"  # "autopilot" | "copilot"
    solution: str = "react"  # react | dragin | multi | hybrid_b — full routing is §12.5
    model: str
    api_base: str
    api_key: str
    api_version: str = ""
    max_steps: int = 18
    temperature: float = 0.0


class RecommendRequest(BaseModel):
    # §12.5 — advise which solution fits a question + the workspace data, before running.
    question: str
    session_id: str | None = None
    task_id: str | None = None
    # Model creds are optional: with a key we ask the LLM; without, we fall back
    # to the deterministic heuristic.
    model: str = ""
    api_base: str = ""
    api_key: str = ""
    api_version: str = ""


class SessionCreate(BaseModel):
    name: str | None = None


class SessionPatch(BaseModel):
    name: str | None = None
    settings: dict | None = None


class QualityRequest(BaseModel):
    filename: str
    table: str | None = None   # for .db files — picks a table; default = first user table
    # LLM creds — the analysis is LLM-driven (no hard-coded rules).
    model: str = ""
    api_base: str = ""
    api_key: str = ""
    api_version: str = ""


class ApplyFixRequest(BaseModel):
    filename: str
    fix: dict
    out_filename: str | None = None
    table: str | None = None   # for .db files — picks the source table
    dry_run: bool = False  # preview the before→after change without writing


class ExploreRequest(BaseModel):
    filename: str
    sheet: str | None = None
    table: str | None = None   # for .db files


class AuthRequest(BaseModel):
    username: str
    password: str


class TextKGRequest(BaseModel):
    filename: str
    max_pages: int = 25
    # Re-run the LLM extraction even if a cached graph exists for this file.
    force: bool = False
    # LLM creds — the extraction is LLM-driven (no rules / templates).
    model: str = ""
    api_base: str = ""
    api_key: str = ""
    api_version: str = ""


class DecideRequest(BaseModel):
    run_id: str
    decision: str  # approve | edit | reject | cancel
    action_input: dict | None = None
    note: str | None = None


class ChatRequest(BaseModel):
    text: str
    session_id: str | None = None
    model: str
    api_base: str
    api_key: str
    api_version: str = ""


class SummarizeAnswerRequest(BaseModel):
    """Ask the LLM to turn a finished agent answer-table into a friendly,
    conversational reply (in the user's own language) — this is what gets shown
    as the final assistant bubble, separate from the raw table in Results."""
    question: str
    columns: list[str]
    rows: list[list[Any]]
    session_id: str | None = None
    model: str
    api_base: str
    api_key: str
    api_version: str = ""


def _adapter(req) -> Any:
    return build_model_adapter(AppConfig(agent=AgentConfig(
        model=req.model, api_base=req.api_base, api_key=req.api_key, api_version=req.api_version)))


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "tasks": len(DATASET.list_task_ids())}


@app.get("/api/tasks")
def tasks(limit: int = 60) -> list[dict[str, Any]]:
    out = []
    for tid in DATASET.list_task_ids()[:limit]:
        t = DATASET.get_task(tid)
        out.append({"task_id": t.task_id, "difficulty": t.difficulty, "question": t.question})
    return out


@app.get("/api/tools")
def tools_catalog() -> dict[str, Any]:
    """The agent's REAL tool registry — the single source of truth for the UI's
    Tools panel, so it always shows every tool the engine can call (no drift from
    a hardcoded frontend list). Read-only; built once per request."""
    reg = create_default_tool_registry()
    items = [
        {
            "name": spec.name,
            "description": spec.description,
            "requires_approval": bool(spec.requires_approval),
            "input_schema": spec.input_schema,
        }
        for name, spec in sorted(reg.specs.items())
    ]
    return {"tools": items, "count": len(items)}


# --- Auth (real, single-tenant) ---------------------------------------------
def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def _current_user(request: Request) -> str:
    """Validate the bearer token; raise 401 if missing/invalid/expired."""
    username = AUTH.validate_token(_bearer_token(request))
    if username is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


@app.post("/api/auth/register")
def auth_register(req: AuthRequest) -> dict[str, Any]:
    try:
        AUTH.register(req.username, req.password)
        username = AUTH.verify(req.username, req.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return {"username": username, "token": AUTH.issue_token(username)}


@app.post("/api/auth/login")
def auth_login(req: AuthRequest) -> dict[str, Any]:
    try:
        username = AUTH.verify(req.username, req.password)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return {"username": username, "token": AUTH.issue_token(username)}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    return {"username": _current_user(request)}


# --- Sessions & files (§12.0) ------------------------------------------------
def _require_session(sid: str):
    try:
        return STORE.get(sid)
    except KeyError:
        raise HTTPException(status_code=404, detail="session not found")


@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    return [s.to_dict() for s in STORE.list()]


@app.post("/api/sessions")
def create_session(req: SessionCreate) -> dict[str, Any]:
    return STORE.create(req.name).to_dict()


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    return _require_session(sid).to_dict()


@app.patch("/api/sessions/{sid}")
def patch_session(sid: str, req: SessionPatch) -> dict[str, Any]:
    _require_session(sid)
    if req.name is not None:
        STORE.rename(sid, req.name)
    if req.settings is not None:
        STORE.update_settings(sid, req.settings)
    return STORE.get(sid).to_dict()


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str) -> dict[str, Any]:
    STORE.delete(sid)
    return {"ok": True}


@app.get("/api/sessions/{sid}/files")
def list_files(sid: str) -> list[dict[str, Any]]:
    return _require_session(sid).files


@app.post("/api/sessions/{sid}/files")
async def upload_file(sid: str, filename: str, request: Request) -> dict[str, Any]:
    """Upload one file as the raw request body (no multipart dependency needed).
    From the browser:  fetch(url + '?filename=' + name, {method:'POST', body: file})."""
    _require_session(sid)
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload body")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    return STORE.add_file(sid, filename, data)


@app.delete("/api/sessions/{sid}/files/{file_id}")
def delete_file(sid: str, file_id: str) -> dict[str, Any]:
    _require_session(sid)
    STORE.remove_file(sid, file_id)
    return {"ok": True}


@app.get("/api/samples")
def list_samples() -> list[dict[str, Any]]:
    """Built-in sample datasets bundled with the app (assets/samples)."""
    out: list[dict[str, Any]] = []
    if SAMPLES_DIR.is_dir():
        for path in sorted(SAMPLES_DIR.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                out.append({
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "kind": path.suffix.lower().lstrip(".") or "file",
                    "description": _SAMPLE_DESC.get(path.name, ""),
                })
    return out


@app.post("/api/sessions/{sid}/files/from_sample")
def add_from_sample(sid: str, name: str) -> dict[str, Any]:
    """Copy a bundled sample file (assets/samples/<name>) into the session."""
    _require_session(sid)
    safe = Path(name).name  # strip any path component before resolving
    src = SAMPLES_DIR / safe
    if not src.is_file():
        raise HTTPException(status_code=404, detail=f"sample '{safe}' not found")
    return STORE.add_file(sid, safe, src.read_bytes())


@app.get("/api/sessions/{sid}/files/{file_id}/download")
def download_file(sid: str, file_id: str):
    _require_session(sid)
    path = STORE.path_of(sid, file_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.get("/api/sessions/{sid}/files/{file_id}/preview")
def preview_file(sid: str, file_id: str, rows: int = 50,
                 table: str | None = None, sheet: str | None = None) -> dict[str, Any]:
    """Lightweight preview for the Files panel: a table for CSV/Excel/SQLite, text otherwise.
    For .db files, pass `?table=<name>` to pick a table; we also return the list of tables
    so the UI can offer a picker (analogous to Excel sheets)."""
    _require_session(sid)
    path = STORE.path_of(sid, file_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".xlsx", ".xls"}:
        try:
            return {"kind": "table", **preview_table(path, rows, sheet=sheet)}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=415, detail=f"could not read file: {exc}")
    if suffix in {".sqlite", ".db", ".sqlite3"}:
        try:
            tables = sqlite_tables(path)
            if not tables:
                return {"kind": "unsupported", "note": "SQLite file has no user tables."}
            chosen = table or tables[0]
            body = preview_table(path, rows, table=chosen)
            return {"kind": "table", "tables": tables, "table": chosen, **body}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=415, detail=f"could not read database: {exc}")
    if suffix in {".md", ".txt", ".json"}:
        return {"kind": "text", "text": path.read_text(encoding="utf-8", errors="ignore")[:6000]}
    if suffix == ".pdf":
        try:
            doc = read_text_document(path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=415, detail=f"could not read PDF: {exc}")
        pages = doc.get("pages", [])
        # Stitch the first few pages into one text preview so the panel can show
        # at-a-glance content; the full per-page list is still available via /textkg.
        preview_text = "\n\n".join(f"— Page {p['page']} —\n{p['text']}" for p in pages[:5])
        return {"kind": "text", "text": preview_text[:6000], "pages": len(pages), "chars": doc.get("n_chars", 0)}
    return {"kind": "unsupported", "note": f"No preview available for {suffix or 'this'} files."}


@app.get("/api/sessions/{sid}/files/{file_id}/schema")
def inspect_db_schema(sid: str, file_id: str) -> dict[str, Any]:
    """Return the ER schema of a SQLite file (tables, columns, PK/FK, row counts).
    Used by the relationship graph (§12.2b) — read-only, no LLM."""
    _require_session(sid)
    path = STORE.path_of(sid, file_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    try:
        return inspect_sqlite_schema(path)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc))


@app.get("/api/sessions/{sid}/schema")
def inspect_session_schema(sid: str) -> dict[str, Any]:
    """Aggregate ER schema across EVERY SQLite file in the session — plus
    heuristic cross-database FK links inferred from matching `<table>_id` /
    primary-key column names. Powers the multi-DB ER view (§12.2b)."""
    session = _require_session(sid)
    bundles: list[dict[str, Any]] = []
    for meta in session.files:
        if meta.get("kind") != "sqlite":
            continue
        path = STORE.path_of(sid, meta["id"])
        if path is None or not path.is_file():
            continue
        try:
            schema = inspect_sqlite_schema(path)
        except ValueError:
            continue
        bundles.append({"file_id": meta["id"], "file": meta["name"], **schema})

    # Heuristic cross-DB joins: for every column ending in `_id` (or named `id`),
    # look for a table in ANOTHER file whose primary key column matches and whose
    # table name (singular/plural) matches the prefix. Cheap; only flagged when
    # both sides exist. Real cross-file FKs aren't recorded in SQLite — this is the
    # best you can do without LLM help.
    cross_links: list[dict[str, Any]] = []
    if len(bundles) > 1:
        pk_index: dict[tuple[str, str], dict[str, Any]] = {}
        for b in bundles:
            for t in b["tables"]:
                for c in t["columns"]:
                    if c["pk"]:
                        pk_index[(t["name"].lower(), c["name"].lower())] = {"file": b["file"], "table": t["name"], "column": c["name"]}
        for b in bundles:
            for t in b["tables"]:
                for c in t["columns"]:
                    cname = c["name"].lower()
                    if not cname.endswith("_id") or cname == "id":
                        continue
                    prefix = cname[:-3]
                    candidates = [prefix, prefix + "s", prefix + "es"]
                    for cand in candidates:
                        target = pk_index.get((cand, "id")) or pk_index.get((cand, cname))
                        if target and target["file"] != b["file"]:
                            cross_links.append({
                                "from_file": b["file"], "from_table": t["name"], "from_column": c["name"],
                                "to_file": target["file"], "to_table": target["table"], "to_column": target["column"],
                            })
                            break
    return {"databases": bundles, "cross_links": cross_links}


# --- Analysis history (§12.1) — persist Data Doctor results per session ------
@app.get("/api/sessions/{sid}/analyses")
def list_analyses(sid: str) -> list[dict[str, Any]]:
    _require_session(sid)
    return STORE.list_analyses(sid)


@app.get("/api/sessions/{sid}/analyses/{aid}")
def get_analysis(sid: str, aid: str) -> dict[str, Any]:
    _require_session(sid)
    entry = STORE.get_analysis(sid, aid)
    if entry is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return entry


@app.delete("/api/sessions/{sid}/analyses/{aid}")
def delete_analysis(sid: str, aid: str) -> dict[str, Any]:
    _require_session(sid)
    if not STORE.remove_analysis(sid, aid):
        raise HTTPException(status_code=404, detail="analysis not found")
    return {"ok": True}


@app.delete("/api/sessions/{sid}/analyses")
def clear_analyses(sid: str) -> dict[str, Any]:
    _require_session(sid)
    return {"ok": True, "removed": STORE.clear_analyses(sid)}


# --- Data Doctor (§12.1) -----------------------------------------------------
def _session_file_path(sid: str, filename: str) -> Path:
    path = STORE.context_dir(sid) / Path(filename).name  # .name guards against traversal
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"file '{filename}' not found in session")
    return path


@app.post("/api/sessions/{sid}/quality")
def analyze_quality(sid: str, req: QualityRequest) -> dict[str, Any]:
    """Profile a session file (factual) and let the LLM find issues + propose fixes.
    For .db files, `req.table` picks the table to inspect (default: first user table)."""
    _require_session(sid)
    path = _session_file_path(sid, req.filename)
    try:
        # Read once so we can both profile AND dry-run candidate snippets on the
        # real data (catches dtype errors before they reach the UI).
        df = read_table(path, table=req.table)
        report = profile_quality(path, table=req.table)
    except ValueError as exc:  # unsupported file type
        raise HTTPException(status_code=415, detail=str(exc))
    if not req.api_key:
        return {"report": report, "suggestions": [],
                "note": "Add your model API key in Settings to get AI fix suggestions."}
    try:
        suggestions, diag = llm_suggest_fixes_diag(report, _adapter(req), df=df)
    except Exception as exc:  # noqa: BLE001 — surface as a note, keep the profile
        return {"report": report, "suggestions": [], "note": f"AI analysis failed: {exc}"}

    # Empty result: explain WHY instead of letting the UI claim "looks clean".
    note: str | None = None
    if not suggestions:
        raw = diag.get("raw_count", 0)
        agents = diag.get("agents", []) or []
        agent_summary = ", ".join(
            f"{a['label']}: {a['raw']} raw"
            + (f" ({a['error']})" if a.get("error") else "")
            for a in agents
        )
        if not diag.get("parse_ok", True) and raw == 0:
            note = ("None of the specialist agents returned valid JSON. Try Re-analyze — "
                    "the model usually recovers on the second pass. "
                    + (f"Agents: {agent_summary}" if agent_summary else ""))
        elif raw == 0:
            note = ("All specialist agents looked at the file and proposed no fixes — "
                    "it may genuinely be clean for those domains. "
                    + (f"Agents: {agent_summary}" if agent_summary else ""))
        else:
            dropped = diag.get("dropped", {}) or {}
            reasons = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in dropped.items() if v)
            examples = "; ".join(
                f"[{x.get('agent') or '?'}] \"{x.get('title') or '?'}\" — {x.get('reason')}"
                for x in diag.get("dropped_examples", []) if x
            )
            note = (f"The specialists proposed {raw} fix(es) in total, but all were dropped "
                    f"({reasons}). Try Re-analyze. " + (f"Examples: {examples}" if examples else ""))
    body: dict[str, Any] = {"report": report, "suggestions": suggestions, "diag": diag}
    if note:
        body["note"] = note
    # Persist so users can revisit past analyses (§12.1 — "save analysis history").
    # Don't save the no-creds early-return — that's not a real analysis.
    entry = STORE.add_analysis(sid, {
        "filename": req.filename, "report": report,
        "suggestions": suggestions, "diag": diag, "note": note,
        "table": req.table,
    })
    body["analysis_id"] = entry["id"]
    body["saved_at"] = entry["when"]
    return body


@app.post("/api/sessions/{sid}/quality/apply")
def apply_quality_fix(sid: str, req: ApplyFixRequest) -> dict[str, Any]:
    """Apply ONE approved LLM-generated pandas snippet; writes a cleaned copy
    (never touches the original). The snippet runs in the sandbox defined in
    data_quality._validate_code / _exec_with_timeout. For .db inputs we read the
    chosen `table` and write the cleaned rows out as a sibling CSV — the original
    database file is left untouched."""
    _require_session(sid)
    path = _session_file_path(sid, req.filename)
    code = (req.fix or {}).get("pandas_code")
    if not code or not str(code).strip():
        raise HTTPException(status_code=400, detail="fix.pandas_code is required")
    try:
        new_df, result = apply_pandas_fix(read_table(path, table=req.table), code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.dry_run:  # preview only — show the change, write nothing
        return {"result": result, "preview": True}
    stem = Path(req.filename).stem
    suffix = path.suffix.lower()
    if suffix in {".sqlite", ".db", ".sqlite3"}:
        # Never overwrite a binary DB with a CSV blob — export to a sibling CSV.
        out_name = req.out_filename or f"{stem}__{req.table or 'table'}_clean.csv"
    else:
        out_name = req.out_filename or (req.filename if stem.endswith("_clean") else f"{stem}_clean.csv")
    meta = STORE.upsert_file(sid, out_name, new_df.to_csv(index=False).encode("utf-8"))
    return {"file": meta, "result": result}


@app.post("/api/sessions/{sid}/explore")
def explore_statistics(sid: str, req: ExploreRequest) -> dict[str, Any]:
    """Data-scientist statistics for a CSV/Excel/SQLite file: distributions,
    correlation, missingness, scatter suggestions (read-only). For .db files,
    `req.table` picks the table to profile (default: first user table)."""
    _require_session(sid)
    path = _session_file_path(sid, req.filename)
    try:
        return profile_statistics(path, sheet=req.sheet, table=req.table)
    except ValueError as exc:  # unsupported file type
        raise HTTPException(status_code=415, detail=str(exc))


def _textkg_cache_path(sid: str, filename: str, key: str) -> Path:
    """Where a built text-KG is cached — a hidden sibling of the context dir so
    the agent never sees it. Keyed by filename + a content/params hash so an
    edited file (or a different max_pages) rebuilds automatically."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)[:80]
    cache_dir = STORE.context_dir(sid).parent / ".textkg"
    return cache_dir / f"{safe}__{key}.json"


@app.post("/api/sessions/{sid}/textkg")
def build_text_kg(sid: str, req: TextKGRequest) -> dict[str, Any]:
    """§12.2a — build a knowledge graph from a PDF / Markdown / text file using
    the LLM. Returns `{nodes, edges, doc, note}` where `doc` carries the page count
    and file kind so the UI can show a citation panel grounded in the source.

    The graph is cached to disk per (file content, max_pages); reopening the modal
    LOADS the saved graph instead of paying for another LLM extraction. Pass
    `force` to rebuild.
    """
    _require_session(sid)
    path = _session_file_path(sid, req.filename)
    try:
        doc = read_text_document(path)
    except ValueError as exc:  # unsupported file type
        raise HTTPException(status_code=415, detail=str(exc))
    doc_meta = {"kind": doc["kind"], "n_chars": doc["n_chars"], "pages": len(doc["pages"])}
    if not doc["pages"]:
        return {"nodes": [], "edges": [], "doc": doc_meta,
                "note": "The file has no extractable text — nothing to graph."}

    max_pages = max(1, min(50, req.max_pages))
    cache_key = hashlib.sha1(
        f"{doc['n_chars']}:{max_pages}:{repr(doc['pages'])[:200000]}".encode("utf-8", "ignore")
    ).hexdigest()[:16]
    cache_path = _textkg_cache_path(sid, req.filename, cache_key)

    # Load the saved graph unless the caller forces a rebuild.
    if not req.force and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return {**cached, "doc": doc_meta, "cached": True}
        except Exception:  # noqa: BLE001 — corrupt cache → fall through and rebuild
            pass

    if not req.api_key:
        return {"nodes": [], "edges": [], "doc": doc_meta,
                "note": "Add your model API key in Settings to extract a knowledge graph."}
    creds = {"model": req.model, "api_base": req.api_base,
             "api_key": req.api_key, "api_version": req.api_version}
    try:
        graph = build_text_knowledge_graph(doc, creds, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — surface as a note, no 500
        return {"nodes": [], "edges": [], "doc": doc_meta,
                "note": f"Knowledge-graph extraction failed: {exc}"}
    doc_meta["engine"] = graph.get("engine")
    # Cache only a real result (nodes extracted) — never cache an error/empty note.
    if graph.get("nodes"):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001 — caching is best-effort
            pass
    return {**graph, "doc": doc_meta, "cached": False}


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    """Conversational reply for greetings / small talk, grounded ONLY in the user's
    uploaded workspace files (never in a benchmark task)."""
    ctx = ""
    if req.session_id:
        try:
            files = STORE.get(req.session_id).files
            if files:
                names = ", ".join(f.get("name", "?") for f in files[:10])
                more = f" (+{len(files) - 10} more)" if len(files) > 10 else ""
                ctx = f" The user has uploaded these files into the workspace: {names}{more}."
            else:
                ctx = " The user has not uploaded any files yet."
        except Exception:
            pass
    system = (
        "You are a friendly, concise data-analysis assistant chatbot inside a tool that "
        "answers questions over the user's uploaded tabular data files (CSV/Excel/SQLite/JSON)." + ctx +
        " The user just sent a greeting or small talk — reply warmly in 1-3 sentences and, if "
        "helpful, invite them to ask a concrete question about their uploaded data (or to upload "
        "a file if they have none). Do NOT mention or invent any other dataset, and do NOT answer "
        "as if you already ran an analysis."
    )
    try:
        reply = _adapter(req).complete([
            ModelMessage(role="system", content=system),
            ModelMessage(role="user", content=req.text or "hi"),
        ])
        return {"reply": reply.strip()}
    except Exception as exc:  # noqa: BLE001
        return {"reply": "", "error": f"{type(exc).__name__}: {exc}"}


@app.post("/api/decide")
def decide(req: DecideRequest) -> dict[str, Any]:
    q = RUNS.get(req.run_id)
    if q is None:
        return {"ok": False, "error": "unknown or finished run"}
    q.put({"decision": req.decision, "action_input": req.action_input, "note": req.note})
    return {"ok": True}


@app.post("/api/summarize-answer")
def summarize_answer(req: SummarizeAnswerRequest) -> dict[str, Any]:
    """Convert a finished answer table into a 1-3 sentence conversational reply
    in the *same language* as the user's question. We pass at most ~40 rows so
    the prompt stays small; the table itself stays the source of truth in UI.

    The prompt is intentionally strict — earlier iterations let the model leak
    facts from other files in the workspace (e.g. country codes from crm.db)
    into the reply. Now we forbid anything that isn't a literal cell value.
    """
    cols = [str(c) for c in (req.columns or [])]
    rows = req.rows or []
    preview_rows = rows[:40]
    # Render the table as a numbered list of `column: value` pairs per row.
    # This is harder for the model to ignore than a piped header and keeps
    # individual cell values clearly attributable.
    def _fmt_row(r: list[Any]) -> str:
        parts = []
        for i, val in enumerate(r):
            col = cols[i] if i < len(cols) else f"col{i+1}"
            cell = "" if val is None else str(val)
            parts.append(f"{col}={cell}")
        return ", ".join(parts)
    table_lines = [f"  {i+1}. {_fmt_row(r)}" for i, r in enumerate(preview_rows)]
    table_text = "\n".join(table_lines) if table_lines else "  (empty table)"
    omitted = max(0, len(rows) - len(preview_rows))
    if omitted:
        table_text += f"\n  … plus {omitted} more rows (not shown)"

    # Collect the literal cell values so we can also tell the model explicitly
    # which strings it is allowed to quote.
    allowed_values = sorted({str(v) for r in preview_rows for v in r if v is not None and str(v) != ""})

    system = (
        "You convert a finished data-analysis result into a SHORT spoken-style reply.\n"
        "HARD RULES (must follow exactly):\n"
        "1. Reply in 1-3 sentences, ~60 words maximum. No markdown headers, no bullet lists, no preamble.\n"
        "2. Reply in the SAME LANGUAGE the user used in the question.\n"
        "3. Use ONLY values that literally appear in the provided answer table. "
        "Do NOT mention any name, ID, country, code, number, file or fact that is not in the table — "
        "even if you saw it in another file or earlier message. If the table is small, just list its rows.\n"
        "4. Do NOT describe how you computed the answer. Do NOT mention tools, files, SQL, code, or steps.\n"
        "5. If the table has 10 rows or fewer, mention each row's key value. If more, summarise (count + a few examples)."
    )
    allowed_block = ", ".join(allowed_values[:80]) if allowed_values else "(none)"
    user_msg = (
        f"User question:\n{req.question}\n\n"
        f"Answer table — {len(rows)} row(s), columns: [{', '.join(cols)}]\n"
        f"{table_text}\n\n"
        f"Allowed values you may quote (do not introduce any other proper noun, ID or number):\n"
        f"  {allowed_block}\n\n"
        "Now write the conversational reply."
    )
    try:
        reply = _adapter(req).complete([
            ModelMessage(role="system", content=system),
            ModelMessage(role="user", content=user_msg),
        ])
        return {"reply": (reply or "").strip()}
    except Exception as exc:  # noqa: BLE001
        return {"reply": "", "error": f"{type(exc).__name__}: {exc}"}


def _summarize(content: Any) -> str:
    s = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    return s[:1800]


def _events_for_step(step, propose_mode: bool) -> list[dict[str, Any]]:
    """Translate one StepRecord into frontend events. In co-pilot mode the thought
    and proposal were already streamed by on_propose, so emit only the result."""
    d = step.to_dict()
    obs = d.get("observation", {}) or {}
    content = obs.get("content", obs.get("error", ""))
    result = {
        "type": "TOOL_EXECUTION_SUCCESS" if d["ok"] else "TOOL_EXECUTION_ERROR",
        "step_index": d["step_index"],
        "payload": {"action": d["action"], "ok": d["ok"], "observation": _summarize(content)},
    }
    if propose_mode:
        return [result]
    evs: list[dict[str, Any]] = []
    if d.get("thought"):
        evs.append({"type": "AGENT_THOUGHT", "step_index": d["step_index"], "payload": {"thought": d["thought"]}})
    evs.append({"type": "TOOL_EXECUTION_START", "step_index": d["step_index"],
                "payload": {"action": d["action"], "action_input": d["action_input"]}})
    evs.append(result)
    return evs


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False, default=str)}\n\n"


def _with_question(task, question: str):
    """Override a task's question (question is a property, so replace the record)."""
    return dataclasses.replace(task, record=dataclasses.replace(task.record, question=question))


def _prime_knowledge_graph(task) -> tuple[str, dict[str, Any] | None]:
    """Build the KG once at run start so the agent reasons over a real data map
    from step 1 (no wasted `build_knowledge_graph` tool call). Returns:
      • a `memory_context` block prepended to the task prompt, and
      • a structured payload for the `KNOWLEDGE_GRAPH` event (None on failure).
    Pure file/schema scan — no LLM cost. Defensive: never blocks a run.
    """
    try:
        kg = build_knowledge_graph(task)
    except Exception:  # noqa: BLE001 — KG priming must never break a run
        return "", None
    if not kg.entities and not kg.constraints and not kg.metrics:
        return "", None
    # Persist so the agent can `read_knowledge_graph` (with a query) instantly.
    try:
        from data_agent_baseline.tools.kg_store import persist_knowledge_graph
        persist_knowledge_graph(task.context_dir, kg)
    except Exception:  # noqa: BLE001 — persistence is best-effort
        pass
    compact = kg.to_compact_text().strip()
    if not compact:
        return "", None

    # Documents (pdf/md/txt) are NOT in the tabular graph — and that's exactly
    # where an answer often hides. Surface them so the agent reads them instead
    # of probing every database for an entity that lives in a report.
    _DOC_SUFFIXES = {".pdf", ".md", ".txt"}
    doc_files: list[str] = []
    try:
        for child in sorted(task.context_dir.rglob("*")):
            if (child.is_file() and child.suffix.lower() in _DOC_SUFFIXES
                    and child.name.lower() != "knowledge.md" and child.parent.name != ".kg"):
                doc_files.append(child.relative_to(task.context_dir).as_posix())
    except Exception:  # noqa: BLE001
        pass
    doc_note = ""
    if doc_files:
        doc_note = (
            "\n\n⚠ DOCUMENTS present (NOT in the graph above): "
            f"{', '.join(doc_files[:8])}\n"
            "These hold prose/report data the tables don't. If the question names an entity "
            "you can't find in any table/column above, it likely lives in one of these — call "
            "`map_sources` with `focus='<entity>'` (or `read_knowledge_graph` with `query=`) to "
            "confirm WHERE it is, then `read_pdf`/`search_doc` to read it. Do NOT keep running "
            "`LIKE '%...%'` against the databases for it."
        )

    primer = (
        "## KNOWLEDGE GRAPH (pre-built — use BEFORE calling tools)\n"
        "This is the real data map for your workspace, already extracted by scanning every file. "
        "Trust it: every entity, column and join path below has been verified. "
        "Do NOT call `build_knowledge_graph` again — read what's here, then jump straight to"
        " analysis (`execute_python` / `execute_context_sql` / `execute_universal_sql`).\n\n"
        f"{compact}\n\n"
        "How to use it in Step 1:\n"
        "• Match the question's entities to the **entity names** above (file/table = entity).\n"
        "• If ONE entity already has both the subject AND the metric, answer from it alone — do NOT join.\n"
        "• If you must join, copy the JOIN PATHS literally — they show the exact column pairs.\n"
        "• Apply CONSTRAINTS and METRIC formulas verbatim — they came from knowledge.md."
        f"{doc_note}"
    )
    payload = kg.to_dict()
    payload.pop("compact_text", None)  # the UI doesn't need the raw text dump
    payload["summary"] = {
        "entities": len(kg.entities),
        "relationships": len(kg.relationships),
        "constraints": len(kg.constraints),
        "metrics": len(kg.metrics),
    }
    return primer, payload


# --- §12.5 Solution recommendation -----------------------------------------
# Short "what each solution is good at" — surfaced in the recommendation card.
_SOLUTION_FIT = {
    "react": "Fast Reason→Act loop — best for direct lookups, filters and single-table aggregations.",
    "dragin": "Adaptive retrieval — pulls extra context when unsure; best for knowledge-heavy or document questions.",
    "multi": "Planner drafts the steps, Analyst executes — best for complex, multi-step or cross-source work.",
    "hybrid_b": "Routes by difficulty — a light path for easy asks, deeper reasoning for hard ones.",
}
_MULTI_WORDS = ("for each", "compare", "across", "combine", " join", "relationship between",
                "correlat", "trend", "group by", "multiple", "and then", "breakdown", "per ")
_DRAGIN_WORDS = ("why", "explain", "according to", "in the document", "summar", "describe",
                 "who is", "what is", "definition", "reason for")
_DOC_SUFFIXES = {".pdf", ".md", ".txt"}
_DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def _recommend_solution(task) -> dict:
    """Advise a solution from the question + the workspace's data shape. Reuses
    `_select_agent_routing` for its difficulty/doc reasoning, but counts data
    families itself so the suggestion is right even when difficulty is unset."""
    try:
        decision = _select_agent_routing(task, AppConfig(agent=AgentConfig(agent_mode="hybrid_b")))
        base_mode, base_signals = decision.agent_mode, list(decision.signals)
    except Exception:  # noqa: BLE001 — never let routing internals break the advice
        base_mode, base_signals = "react", []

    files = [p for p in task.context_dir.rglob("*") if p.is_file() and p.name.lower() != "gold.csv"]
    suffixes = {p.suffix.lower() for p in files}
    db_count = sum(1 for p in files if p.suffix.lower() in _DB_SUFFIXES)
    families = sum(x for x in (".csv" in suffixes, ".json" in suffixes, bool(suffixes & _DB_SUFFIXES)))
    multi_source = families >= 2 or db_count >= 2 or "multi_source" in base_signals
    has_doc = bool(suffixes & _DOC_SUFFIXES) or "long_doc" in base_signals

    q = (task.question or "").lower()
    multi_q = any(w in q for w in _MULTI_WORDS)
    dragin_q = any(w in q for w in _DRAGIN_WORDS)

    if multi_source:
        rec = "multi"
        reason = "Your workspace spans multiple data sources, so a Planner → Analyst split handles the cross-source steps best."
    elif multi_q:
        rec = "multi"
        reason = "The question asks for a multi-step / cross-cut analysis — a planner-led run keeps the steps organised."
    elif has_doc or base_mode == "dragin" or dragin_q:
        rec = "dragin"
        reason = "This looks knowledge-heavy (documents or open-ended), so adaptive retrieval can pull supporting context when the model is unsure."
    else:
        rec = "react"
        reason = "A direct Reason → Act loop is the fastest fit for this compact, single-source question."

    alternatives = [{"id": s, "why": _SOLUTION_FIT[s]} for s in ("react", "dragin", "multi", "hybrid_b") if s != rec]
    return {"recommended": rec, "reason": reason, "signals": base_signals, "alternatives": alternatives}


_VALID_SOLUTIONS = ("react", "dragin", "multi", "hybrid_b")


def _data_profile(task) -> str:
    """Compact description of the workspace files for the LLM router."""
    files = [p for p in task.context_dir.rglob("*") if p.is_file() and p.name.lower() != "gold.csv"]
    if not files:
        return "No files uploaded yet."
    lines = []
    for p in sorted(files)[:25]:
        kb = max(1, p.stat().st_size // 1024)
        lines.append(f"- {p.name} ({p.suffix.lower().lstrip('.') or 'file'}, ~{kb} KB)")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e < s:
        raise ValueError("no JSON object in model reply")
    return json.loads(text[s:e + 1])


def _llm_recommend(task, question: str, req) -> dict | None:
    """Ask the LLM to pick a solution; returns None if the reply is unusable so
    the caller can fall back to the heuristic."""
    catalog = "\n".join(f"- {sid}: {_SOLUTION_FIT[sid]}" for sid in _VALID_SOLUTIONS)
    system = (
        "You route a data-analysis agent: pick the SINGLE best solution (agent strategy) "
        "for the user's question over their uploaded data.\n\n"
        f"Solutions:\n{catalog}\n\n"
        f"Workspace data:\n{_data_profile(task)}\n\n"
        "Choose the one whose strengths best fit the question and the data. Reply with STRICT JSON "
        "only — no code fences, no prose: "
        '{"recommended": "<react|dragin|multi|hybrid_b>", '
        '"reason": "<one short sentence, written in the SAME language as the user\'s question>"}.'
    )
    reply = _adapter(req).complete([
        ModelMessage(role="system", content=system),
        ModelMessage(role="user", content=question),
    ])
    data = _extract_json(reply)
    rec = str(data.get("recommended", "")).strip().lower()
    if rec not in _VALID_SOLUTIONS:
        return None
    reason = str(data.get("reason", "")).strip() or _SOLUTION_FIT[rec]
    return {
        "recommended": rec,
        "reason": reason,
        "signals": ["llm"],
        "alternatives": [{"id": s, "why": _SOLUTION_FIT[s]} for s in _VALID_SOLUTIONS if s != rec],
        "by": "llm",
    }


@app.post("/api/recommend-solution")
def recommend_solution(req: RecommendRequest):
    q = (req.question or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="question is required")
    if req.session_id:
        _require_session(req.session_id)
        task = build_session_task(q, STORE.context_dir(req.session_id), req.session_id)
    elif req.task_id:
        task = _with_question(DATASET.get_task(req.task_id), q)
    else:
        raise HTTPException(status_code=400, detail="provide either session_id or task_id")

    # LLM-first when a key is present; the heuristic is the safety net.
    if req.api_key:
        try:
            llm = _llm_recommend(task, q, req)
            if llm:
                return llm
        except Exception:  # noqa: BLE001 — never 500 the advice; fall back
            pass
    result = _recommend_solution(task)
    result["by"] = "heuristic"
    return result


@app.post("/api/run")
async def run(req: RunRequest):
    # Resolve the target: a session workspace (uploaded files) OR a benchmark task.
    if req.session_id:
        _require_session(req.session_id)
        if not req.question:
            raise HTTPException(status_code=400, detail="question is required for a session run")
        task = build_session_task(req.question, STORE.context_dir(req.session_id), req.session_id)
    elif req.task_id:
        task = DATASET.get_task(req.task_id)
        if req.question:
            task = _with_question(task, req.question)
    else:
        raise HTTPException(status_code=400, detail="provide either session_id or task_id")

    config = AppConfig(agent=AgentConfig(
        model=req.model, api_base=req.api_base, api_key=req.api_key,
        api_version=req.api_version, agent_mode="react",
        max_steps=req.max_steps, temperature=req.temperature,
    ))
    model = build_model_adapter(config)
    tools = create_default_tool_registry()

    # Pre-build the Knowledge Graph so the agent sees the data map BEFORE step 1.
    # Saves a `build_knowledge_graph` tool call AND grounds the first thought in
    # real entities/joins/constraints instead of guesswork.
    kg_primer, kg_payload = _prime_knowledge_graph(task)

    copilot = req.mode == "copilot"
    run_id = uuid.uuid4().hex[:12]
    q: queue.Queue = queue.Queue()
    decisions: queue.Queue = queue.Queue()
    RUNS[run_id] = decisions
    SENTINEL = object()

    def on_propose(thought, action, action_input):
        q.put({"type": "AGENT_THOUGHT", "payload": {"thought": thought}})
        q.put({"type": "AWAITING_USER", "payload": {"action": action, "action_input": action_input}})
        try:
            return decisions.get(timeout=900)  # block until the UI decides
        except queue.Empty:
            return {"decision": "cancel"}

    def on_step(step):
        for ev in _events_for_step(step, copilot):
            q.put(ev)

    def worker():
        # Reuse the UI's (Azure) creds for the embedding endpoint too, so hybrid
        # doc retrieval needs no extra UI fields — only the embedding deployment
        # name (DABENCH_EMBED_MODEL). Without it, search_doc stays BM25-only.
        from data_agent_baseline.tools.hybrid_retriever import (
            build_embedder_from_creds, reset_request_embedder, set_request_embedder,
        )
        from data_agent_baseline.tools.semantic_match import (
            reset_request_model, set_request_model,
        )
        embed_token = set_request_embedder(build_embedder_from_creds(
            api_key=req.api_key, api_base=req.api_base, api_version=req.api_version,
        ))
        # Same chat model the agent uses → powers the LLM concept-bridge in
        # read_knowledge_graph (concept → real column value). No extra UI fields.
        model_token = set_request_model(model)
        try:
            agent = ReActAgent(model=model, tools=tools, config=ReActAgentConfig(max_steps=req.max_steps),
                               system_prompt=None, memory_context=kg_primer,
                               on_step=on_step, on_propose=on_propose if copilot else None)
            result = agent.run(task)
            answer = result.answer.to_dict() if result.answer is not None else None
            q.put({"type": "RUN_FINISHED", "payload": {
                "succeeded": result.succeeded, "failure_reason": result.failure_reason, "answer": answer}})
        except Exception as exc:  # noqa: BLE001
            q.put({"type": "RUN_FINISHED", "payload": {
                "succeeded": False, "failure_reason": f"{type(exc).__name__}: {exc}", "answer": None}})
        finally:
            reset_request_embedder(embed_token)
            reset_request_model(model_token)
            RUNS.pop(run_id, None)
            q.put(SENTINEL)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        yield _sse({"type": "RUN_STARTED", "payload": {
            "run_id": run_id, "question": task.question, "task_id": task.task_id,
            "session_id": req.session_id, "solution": req.solution,
            "model": req.model, "mode": req.mode}})
        if kg_payload is not None:
            yield _sse({"type": "KNOWLEDGE_GRAPH", "payload": kg_payload})
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if item is SENTINEL:
                break
            yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
