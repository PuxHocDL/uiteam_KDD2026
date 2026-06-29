"""Studio session store (§12.0) — filesystem-backed user workspaces.

This decouples a *workspace* from the benchmark dataset. Each session owns a folder:

    <root>/<session_id>/
        session.json     # metadata: id, name, created, modified, files[], settings
        context/         # the files the user uploaded — this is the engine's context_dir

`build_session_task` wraps a session's `context/` folder in a synthetic `PublicTask`
so the existing ReAct engine can run over uploaded files instead of a fixed task_id
(see server/app.py `/api/run`). No engine change is required — the engine only ever
reads `task.context_dir` and `task.question`.
"""
from __future__ import annotations

import gc
import json
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask, TaskAssets, TaskRecord

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_KIND_BY_SUFFIX = {
    ".csv": "csv", ".tsv": "csv",
    ".json": "json",
    ".md": "md", ".txt": "md",
    ".sqlite": "sqlite", ".db": "sqlite", ".sqlite3": "sqlite",
    ".xlsx": "excel", ".xls": "excel",
    ".pdf": "pdf",
}
# Skip the row-count line scan for files larger than this (keeps uploads snappy).
_ROWCOUNT_MAX_BYTES = 20 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unlink_resilient(path: Path) -> None:
    """Delete a file, tolerating a transient Windows lock (WinError 32).

    On Windows an open file handle blocks deletion. A SQLite connection that
    was opened to inspect/preview an uploaded `.db` and has since gone out of
    scope can still hold the handle until it is garbage-collected. So on the
    first PermissionError we force a GC pass (which finalizes those orphaned
    connections, releasing the handle) and retry briefly. If it still can't be
    removed we leave the orphan for `cleanup_old()` rather than failing the
    request — the file is already dropped from the session metadata."""
    if not path.is_file():
        return
    for attempt in range(12):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 0:
                gc.collect()
            else:
                time.sleep(0.1)


def _safe_filename(name: str) -> str:
    """Strip any path and unsafe characters — prevents path traversal on upload."""
    base = Path(str(name)).name.strip()
    base = _SAFE.sub("_", base).strip("._") or "file"
    return base[:120]


def _kind_of(name: str) -> str:
    return _KIND_BY_SUFFIX.get(Path(name).suffix.lower(), "file")


def _human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num} B"


def _csv_row_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as fh:
            n = sum(1 for _ in fh)
        return max(0, n - 1)  # minus the header row
    except Exception:  # noqa: BLE001 - row count is best-effort metadata only
        return None


def _file_meta(path: Path, nbytes: int, file_id: str | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "id": file_id or uuid.uuid4().hex[:8],
        "name": path.name,
        "kind": _kind_of(path.name),
        "size": _human_size(nbytes),
        "bytes": nbytes,
    }
    if meta["kind"] == "csv" and nbytes < _ROWCOUNT_MAX_BYTES:
        rows = _csv_row_count(path)
        if rows is not None:
            meta["rowCount"] = rows
    return meta


@dataclass
class StudioSession:
    id: str
    name: str
    created: str
    modified: str
    files: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)
    analyses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SessionStore:
    """Filesystem-backed CRUD for studio sessions and their uploaded files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- paths -------------------------------------------------------------
    def _dir(self, sid: str) -> Path:
        return self.root / sid

    def context_dir(self, sid: str) -> Path:
        """The folder the engine reads as its `context_dir`."""
        return self._dir(sid) / "context"

    def _meta_path(self, sid: str) -> Path:
        return self._dir(sid) / "session.json"

    # ---- persistence -------------------------------------------------------
    def _read(self, sid: str) -> StudioSession:
        meta = self._meta_path(sid)
        if not meta.exists():
            raise KeyError(sid)
        data = json.loads(meta.read_text(encoding="utf-8"))
        # Ignore unknown keys so a new field added later doesn't break older sessions.
        known = {f.name for f in StudioSession.__dataclass_fields__.values()}
        return StudioSession(**{k: v for k, v in data.items() if k in known})

    def _write(self, session: StudioSession) -> None:
        session.modified = _now()
        self._meta_path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- session CRUD ------------------------------------------------------
    def create(self, name: str | None = None) -> StudioSession:
        sid = uuid.uuid4().hex[:12]
        self.context_dir(sid).mkdir(parents=True, exist_ok=True)
        now = _now()
        session = StudioSession(
            id=sid, name=(name or "New Session").strip() or "New Session",
            created=now, modified=now, files=[], settings={},
        )
        self._write(session)
        return session

    def list(self) -> list[StudioSession]:
        sessions: list[StudioSession] = []
        if not self.root.is_dir():
            return sessions
        for entry in self.root.iterdir():
            if entry.is_dir() and (entry / "session.json").exists():
                try:
                    sessions.append(self._read(entry.name))
                except Exception:  # noqa: BLE001 - skip corrupt metadata
                    continue
        sessions.sort(key=lambda s: s.modified, reverse=True)
        return sessions

    def get(self, sid: str) -> StudioSession:
        return self._read(sid)

    def rename(self, sid: str, name: str) -> StudioSession:
        session = self._read(sid)
        session.name = (name or "").strip() or session.name
        self._write(session)
        return session

    def update_settings(self, sid: str, settings: dict[str, Any]) -> StudioSession:
        session = self._read(sid)
        session.settings = settings or {}
        self._write(session)
        return session

    def delete(self, sid: str) -> None:
        shutil.rmtree(self._dir(sid), ignore_errors=True)

    # ---- file CRUD ---------------------------------------------------------
    def add_file(self, sid: str, filename: str, data: bytes) -> dict[str, Any]:
        session = self._read(sid)  # raises KeyError if the session is unknown
        ctx = self.context_dir(sid)
        ctx.mkdir(parents=True, exist_ok=True)

        target = ctx / _safe_filename(filename)
        stem, suffix, i = target.stem, target.suffix, 1
        while target.exists():  # never clobber an existing upload
            target = ctx / f"{stem}_{i}{suffix}"
            i += 1
        target.write_bytes(data)

        meta = _file_meta(target, len(data))
        session.files.append(meta)
        self._write(session)
        return meta

    def upsert_file(self, sid: str, filename: str, data: bytes) -> dict[str, Any]:
        """Write (overwriting) a derived file; replace its metadata if the name already
        exists. Used by the Data Doctor to keep a single, updatable `*_clean.csv`."""
        session = self._read(sid)
        ctx = self.context_dir(sid)
        ctx.mkdir(parents=True, exist_ok=True)
        target = ctx / _safe_filename(filename)
        target.write_bytes(data)
        for idx, existing in enumerate(session.files):
            if existing.get("name") == target.name:
                meta = _file_meta(target, len(data), file_id=existing.get("id"))
                session.files[idx] = meta
                self._write(session)
                return meta
        meta = _file_meta(target, len(data))
        session.files.append(meta)
        self._write(session)
        return meta

    def path_of(self, sid: str, file_id: str) -> Path | None:
        """Resolve an uploaded file's path by its id (None if unknown)."""
        for entry in self._read(sid).files:
            if entry.get("id") == file_id:
                return self.context_dir(sid) / entry.get("name", "")
        return None

    def remove_file(self, sid: str, file_id: str) -> None:
        session = self._read(sid)
        kept: list[dict[str, Any]] = []
        for entry in session.files:
            if entry.get("id") == file_id:
                path = self.context_dir(sid) / entry.get("name", "")
                _unlink_resilient(path)
            else:
                kept.append(entry)
        session.files = kept
        self._write(session)

    # ---- analysis history (§12.1) — Data Doctor results so users can revisit ----
    _ANALYSIS_KEEP = 30  # rolling cap so session.json doesn't grow unbounded

    def add_analysis(self, sid: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append one Data Doctor result (newest first). Returns the stored entry."""
        session = self._read(sid)
        entry = {
            "id": uuid.uuid4().hex[:10],
            "when": _now(),
            "filename": str(payload.get("filename") or ""),
            "report": payload.get("report"),
            "suggestions": payload.get("suggestions") or [],
            "diag": payload.get("diag"),
            "note": payload.get("note"),
        }
        session.analyses = ([entry] + list(session.analyses or []))[: self._ANALYSIS_KEEP]
        self._write(session)
        return entry

    def list_analyses(self, sid: str) -> list[dict[str, Any]]:
        """Index view — strip heavy fields so the list call stays small."""
        out: list[dict[str, Any]] = []
        for a in self._read(sid).analyses or []:
            out.append({
                "id": a.get("id"),
                "when": a.get("when"),
                "filename": a.get("filename"),
                "suggestion_count": len(a.get("suggestions") or []),
                "rows": (a.get("report") or {}).get("rows"),
                "columns": (a.get("report") or {}).get("columns"),
                "has_note": bool(a.get("note")),
            })
        return out

    def get_analysis(self, sid: str, aid: str) -> dict[str, Any] | None:
        for a in self._read(sid).analyses or []:
            if a.get("id") == aid:
                return a
        return None

    def remove_analysis(self, sid: str, aid: str) -> bool:
        session = self._read(sid)
        before = len(session.analyses or [])
        session.analyses = [a for a in (session.analyses or []) if a.get("id") != aid]
        if len(session.analyses) == before:
            return False
        self._write(session)
        return True

    def clear_analyses(self, sid: str) -> int:
        session = self._read(sid)
        n = len(session.analyses or [])
        session.analyses = []
        self._write(session)
        return n

    # ---- housekeeping ------------------------------------------------------
    def cleanup_old(self, max_age_days: int = 30) -> int:
        """Delete session folders not modified within `max_age_days`. Returns count."""
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        if not self.root.is_dir():
            return 0
        for entry in self.root.iterdir():
            try:
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        return removed


def build_session_task(question: str, context_dir: Path, session_id: str) -> PublicTask:
    """Wrap a session workspace as a `PublicTask` the ReAct engine can run on."""
    context_dir = Path(context_dir)
    record = TaskRecord(task_id=f"session_{session_id}", difficulty="custom", question=question or "")
    assets = TaskAssets(task_dir=context_dir.parent, context_dir=context_dir)
    return PublicTask(record=record, assets=assets)
