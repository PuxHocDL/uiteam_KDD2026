from __future__ import annotations

import csv
import json
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask


def resolve_context_path(task: PublicTask, relative_path: str) -> Path:
    candidate = (task.context_dir / relative_path).resolve()
    context_root = task.context_dir.resolve()
    if context_root not in candidate.parents and candidate != context_root:
        raise ValueError(f"Path escapes context dir: {relative_path}")
    if candidate.name.lower() == "gold.csv":
        raise PermissionError("Refusing to read ground-truth file gold.csv from task context.")
    if not candidate.exists():
        raise FileNotFoundError(f"Missing context asset: {relative_path}")
    return candidate


def list_context_tree(task: PublicTask, *, max_depth: int = 4) -> dict[str, object]:
    entries: list[dict[str, object]] = []

    def walk(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name)):
            if child.is_file() and child.name.lower() == "gold.csv":
                continue
            # Hide hidden/cache dirs (e.g. the .kg knowledge-graph cache).
            if child.name.startswith("."):
                continue
            rel_path = child.relative_to(task.context_dir).as_posix()
            entry: dict[str, object] = {
                "path": rel_path,
                "kind": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
            # Flag potentially sampled databases
            if child.is_file():
                name_lower = child.name.lower()
                if any(tag in name_lower for tag in ("_1k", "_sample", "_subset", "_small")):
                    entry["warning"] = "This file may be a SAMPLED subset — not the full dataset. Prefer CSV files for complete data."
            entries.append(entry)
            if child.is_dir():
                walk(child, depth + 1)

    walk(task.context_dir, 1)

    # Build a quick summary of file types
    type_counts: dict[str, int] = {}
    for e in entries:
        if e["kind"] == "file":
            ext = Path(str(e["path"])).suffix.lower()
            type_counts[ext] = type_counts.get(ext, 0) + 1
    has_knowledge = any(
        str(e["path"]).endswith("knowledge.md") for e in entries
    )

    return {
        "root": str(task.context_dir),
        "summary": type_counts,
        "has_knowledge_md": has_knowledge,
        "entries": entries,
    }


def read_csv_preview(task: PublicTask, relative_path: str, *, max_rows: int = 5) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        rows = list(reader)

    if not rows:
        return {
            "path": relative_path,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }

    header = rows[0]
    data_rows = rows[1:]
    return {
        "path": relative_path,
        "columns": header,
        "rows": data_rows[:max_rows],
        "row_count": len(data_rows),
        "truncated": len(data_rows) > max_rows,
    }


def profile_csv(task: PublicTask, relative_path: str, *, sample_size: int = 1000) -> dict[str, object]:
    """Profile a CSV file: column types, null counts, unique values, and basic statistics."""
    import pandas as pd

    path = resolve_context_path(task, relative_path)
    df = pd.read_csv(path, low_memory=False)
    total_rows = len(df)

    columns_profile: list[dict[str, object]] = []
    for col in df.columns:
        series = df[col]
        profile: dict[str, object] = {
            "name": col,
            "dtype": str(series.dtype),
            "null_count": int(series.isna().sum()),
            "unique_count": int(series.nunique()),
        }
        if pd.api.types.is_numeric_dtype(series):
            desc = series.describe()
            profile["min"] = None if pd.isna(desc.get("min")) else float(desc["min"])
            profile["max"] = None if pd.isna(desc.get("max")) else float(desc["max"])
            profile["mean"] = None if pd.isna(desc.get("mean")) else round(float(desc["mean"]), 4)
            profile["std"] = None if pd.isna(desc.get("std")) else round(float(desc["std"]), 4)
        else:
            top_values = series.dropna().value_counts().head(5)
            profile["top_values"] = {str(k): int(v) for k, v in top_values.items()}

        columns_profile.append(profile)

    return {
        "path": relative_path,
        "total_rows": total_rows,
        "total_columns": len(df.columns),
        "columns": columns_profile,
    }


def read_json_preview(task: PublicTask, relative_path: str, *, max_chars: int = 1000) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        # Fallback to try latin-1 if it was corrupt
        payload = json.loads(path.read_text(encoding="latin-1", errors="replace"))
        
    preview = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "path": relative_path,
        "preview": preview[:max_chars],
        "truncated": len(preview) > max_chars,
    }


def profile_json(task: PublicTask, relative_path: str, *, max_depth: int = 3) -> dict[str, object]:
    """Profile a JSON file: extract schema and structure without loading all values."""
    path = resolve_context_path(task, relative_path)
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = json.loads(path.read_text(encoding="latin-1", errors="replace"))
    except Exception as e:
        return {"error": str(e)}

    def extract_schema(obj: object, depth: int) -> object:
        if depth > max_depth:
            return type(obj).__name__
        
        if isinstance(obj, dict):
            return {k: extract_schema(v, depth + 1) for k, v in obj.items()}
        elif isinstance(obj, list):
            if not obj:
                return "list(empty)"
            # Sample first item to get schema type
            return [f"list(len={len(obj)})", extract_schema(obj[0], depth + 1)]
        else:
            return type(obj).__name__

    schema = extract_schema(data, 1)
    return {
        "path": relative_path, 
        "schema": schema, 
        "note": "Truncated to schema-only overview to save memory. Use execute_python to exact values."
    }


def read_doc_preview(task: PublicTask, relative_path: str, *, max_chars: int = 8000) -> dict[str, object]:
    path = resolve_context_path(task, relative_path)
    text = path.read_text(errors="replace")
    return {
        "path": relative_path,
        "preview": text[:max_chars],
        "truncated": len(text) > max_chars,
        "total_chars": len(text),
    }


def read_doc_chunk(
    task: PublicTask,
    relative_path: str,
    *,
    start: int = 0,
    length: int = 8000,
) -> dict[str, object]:
    """Read an arbitrary byte-offset slice of a document. Use for paging through long files."""
    path = resolve_context_path(task, relative_path)
    text = path.read_text(errors="replace")
    total = len(text)
    start = max(0, min(start, total))
    end = min(total, start + max(1, length))
    return {
        "path": relative_path,
        "start": start,
        "end": end,
        "total_chars": total,
        "content": text[start:end],
        "has_more": end < total,
        "next_start": end if end < total else None,
    }


def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[A-Za-z0-9_]+", text.lower())


def search_doc(
    task: PublicTask,
    relative_path: str,
    *,
    query: str,
    mode: str = "auto",
    max_matches: int = 10,
    context_chars: int = 400,
    embedder: object | None = None,
) -> dict[str, object]:
    """Search a long document for passages relevant to a query.

    Modes:
      - "regex":    `query` is a Python regex; returns matching windows.
      - "keyword":  BM25-lite ranking of paragraph chunks by keyword overlap with `query`.
      - "auto":     If query looks like regex (contains .*, |, \\d, [, (, ^, $) use regex;
                    otherwise use keyword.

    Returns up to `max_matches` passages, each with an offset, score (keyword mode), and a
    ±`context_chars` window around the match. Use this instead of paging 8000 chars at a time.
    """
    import math
    import re

    path = resolve_context_path(task, relative_path)
    text = path.read_text(errors="replace")
    total = len(text)

    # Auto-detect mode
    if mode == "auto":
        if any(tok in query for tok in (".*", "|", r"\d", r"\w", r"\s", "[", "(", "^", "$", "+?")):
            mode = "regex"
        else:
            mode = "keyword"

    matches: list[dict[str, object]] = []

    if mode == "regex":
        try:
            pattern = re.compile(query, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            return {"path": relative_path, "error": f"Invalid regex: {exc}", "mode": mode}
        for m in pattern.finditer(text):
            s = max(0, m.start() - context_chars)
            e = min(total, m.end() + context_chars)
            matches.append({
                "offset": m.start(),
                "match": m.group(0)[:200],
                "context": text[s:e],
            })
            if len(matches) >= max_matches:
                break
    else:
        # Keyword mode: split doc into paragraphs (blank-line separated), score each by BM25-lite.
        paragraphs: list[tuple[int, str]] = []
        cursor = 0
        for chunk in re.split(r"\n\s*\n", text):
            paragraphs.append((cursor, chunk))
            cursor += len(chunk) + 2  # approximate; fine for offsets

        q_tokens = _tokenize(query)
        if not q_tokens:
            return {"path": relative_path, "error": "Empty query", "mode": mode}

        # Hybrid path: when an embedder is configured, fuse BM25 with vector
        # similarity (RRF) so passages with no keyword overlap can still surface.
        # Degrades transparently to the BM25 path below when embedder is None.
        if embedder is not None:
            from data_agent_baseline.tools.hybrid_retriever import HybridDocRetriever

            offsets = [off for off, _ in paragraphs]
            passages = [p for _, p in paragraphs]
            retriever = HybridDocRetriever(passages, embedder=embedder)
            if retriever.is_hybrid:
                for hit in retriever.retrieve(query, top_k=max_matches):
                    idx = hit["index"]
                    s = offsets[idx]
                    e = s + len(passages[idx])
                    ctx_s = max(0, s - context_chars // 2)
                    ctx_e = min(total, e + context_chars // 2)
                    matches.append({
                        "offset": s,
                        "score": hit["score"],
                        "matched_by": hit["matched_by"],
                        "context": text[ctx_s:ctx_e],
                    })
                return {
                    "path": relative_path,
                    "mode": mode,
                    "retriever": "hybrid",
                    "query": query,
                    "total_chars": total,
                    "match_count": len(matches),
                    "matches": matches,
                }

        # Document frequencies for IDF
        N = len(paragraphs) or 1
        df: dict[str, int] = {}
        para_tokens: list[list[str]] = []
        for _, p in paragraphs:
            toks = _tokenize(p)
            para_tokens.append(toks)
            seen = set(toks) & set(q_tokens)
            for t in seen:
                df[t] = df.get(t, 0) + 1

        avgdl = sum(len(t) for t in para_tokens) / max(1, N)
        k1, b = 1.5, 0.75

        scored: list[tuple[float, int, int, str]] = []
        for (off, p), toks in zip(paragraphs, para_tokens):
            if not toks:
                continue
            tf: dict[str, int] = {}
            for t in toks:
                if t in q_tokens:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue
            score = 0.0
            dl = len(toks)
            for t, f in tf.items():
                idf = math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / max(1, avgdl)))
            scored.append((score, off, off + len(p), p))

        scored.sort(key=lambda x: -x[0])
        for score, s, e, p in scored[:max_matches]:
            # Expand window slightly with surrounding paragraphs for context
            ctx_s = max(0, s - context_chars // 2)
            ctx_e = min(total, e + context_chars // 2)
            matches.append({
                "offset": s,
                "score": round(float(score), 4),
                "context": text[ctx_s:ctx_e],
            })

    return {
        "path": relative_path,
        "mode": mode,
        "query": query,
        "total_chars": total,
        "match_count": len(matches),
        "matches": matches,
    }


def extract_info(
    task: PublicTask,
    *,
    query: str,
    max_results: int = 10,
    context_chars: int = 300,
) -> dict[str, object]:
    """Search across ALL files in context for keywords or regex patterns.

    Unlike search_doc (single file), this scans CSVs, text docs, JSON files,
    and SQLite databases to find rows/passages matching the query.
    Returns the nearest relevant information from any file.
    """
    import re
    import math
    import sqlite3

    results: list[dict[str, object]] = []
    context_root = task.context_dir.resolve()

    # Determine mode
    is_regex = any(tok in query for tok in (".*", "|", r"\d", r"\w", r"\s", "[", "(", "^", "$", "+?"))

    if is_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as exc:
            return {"error": f"Invalid regex: {exc}", "query": query}
    else:
        q_tokens = set(t.lower() for t in re.split(r"[^a-z0-9]+", query.lower()) if t)
        if not q_tokens:
            return {"error": "Empty query", "query": query}

    def _score_text(text: str) -> float:
        """BM25-lite score for a short text snippet."""
        if is_regex:
            return len(pattern.findall(text))
        tokens = [t.lower() for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
        if not tokens:
            return 0.0
        return sum(1 for t in tokens if t in q_tokens) / max(1, len(tokens)) * len(q_tokens)

    def _search_text_file(rel_path: str, full_path: Path) -> None:
        try:
            text = full_path.read_text(errors="replace")
        except Exception:
            return
        if not text.strip():
            return

        if is_regex:
            for m in pattern.finditer(text):
                s = max(0, m.start() - context_chars)
                e = min(len(text), m.end() + context_chars)
                results.append({
                    "source": rel_path,
                    "type": "text",
                    "match": m.group(0)[:200],
                    "context": text[s:e],
                    "score": 1.0,
                })
                if len(results) >= max_results * 3:
                    return
        else:
            # Split into paragraphs and score
            for chunk in re.split(r"\n\s*\n|\n", text):
                chunk = chunk.strip()
                if not chunk:
                    continue
                score = _score_text(chunk)
                if score > 0:
                    results.append({
                        "source": rel_path,
                        "type": "text",
                        "context": chunk[:context_chars * 2],
                        "score": score,
                    })

    def _search_csv_file(rel_path: str, full_path: Path) -> None:
        try:
            with full_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header is None:
                    return
                for row_idx, row in enumerate(reader):
                    row_text = " | ".join(row)
                    if is_regex:
                        if pattern.search(row_text):
                            results.append({
                                "source": rel_path,
                                "type": "csv_row",
                                "row_index": row_idx,
                                "columns": header,
                                "values": row,
                                "score": 1.0,
                            })
                    else:
                        score = _score_text(row_text)
                        if score > 0:
                            results.append({
                                "source": rel_path,
                                "type": "csv_row",
                                "row_index": row_idx,
                                "columns": header,
                                "values": row,
                                "score": score,
                            })
                    if len(results) >= max_results * 3:
                        return
        except Exception:
            return

    def _search_json_file(rel_path: str, full_path: Path) -> None:
        try:
            text = full_path.read_text(errors="replace")
            data = json.loads(text)
        except Exception:
            # Fallback: treat as text
            _search_text_file(rel_path, full_path)
            return

        # Flatten JSON records into searchable strings
        records = data if isinstance(data, list) else [data]
        for rec_idx, record in enumerate(records[:5000]):
            if isinstance(record, dict):
                row_text = " | ".join(f"{k}: {v}" for k, v in record.items())
            else:
                row_text = str(record)

            if is_regex:
                if pattern.search(row_text):
                    results.append({
                        "source": rel_path,
                        "type": "json_record",
                        "record_index": rec_idx,
                        "data": record if isinstance(record, dict) else str(record)[:500],
                        "score": 1.0,
                    })
            else:
                score = _score_text(row_text)
                if score > 0:
                    results.append({
                        "source": rel_path,
                        "type": "json_record",
                        "record_index": rec_idx,
                        "data": record if isinstance(record, dict) else str(record)[:500],
                        "score": score,
                    })
            if len(results) >= max_results * 3:
                return

    def _search_sqlite_file(rel_path: str, full_path: Path) -> None:
        try:
            conn = sqlite3.connect(f"file:{full_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            for table_name in tables[:20]:
                cursor.execute(f"PRAGMA table_info('{table_name}')")
                columns = [row[1] for row in cursor.fetchall()]

                # Build search condition
                if is_regex:
                    # Can't use regex in SQLite easily, just scan
                    cursor.execute(f"SELECT * FROM '{table_name}' LIMIT 2000")
                    for row in cursor.fetchall():
                        row_text = " | ".join(str(v) for v in row if v is not None)
                        if pattern.search(row_text):
                            results.append({
                                "source": f"{rel_path} -> {table_name}",
                                "type": "db_row",
                                "columns": columns,
                                "values": [str(v) for v in row],
                                "score": 1.0,
                            })
                            if len(results) >= max_results * 3:
                                conn.close()
                                return
                else:
                    # Use LIKE for each keyword
                    conditions = []
                    for token in list(q_tokens)[:5]:
                        col_conds = [f"\"{col}\" LIKE '%{token}%'" for col in columns]
                        conditions.append(f"({' OR '.join(col_conds)})")
                    if conditions:
                        where = " AND ".join(conditions)
                        try:
                            cursor.execute(
                                f"SELECT * FROM '{table_name}' WHERE {where} LIMIT 50"
                            )
                            for row in cursor.fetchall():
                                row_text = " | ".join(str(v) for v in row if v is not None)
                                results.append({
                                    "source": f"{rel_path} -> {table_name}",
                                    "type": "db_row",
                                    "columns": columns,
                                    "values": [str(v) for v in row],
                                    "score": _score_text(row_text),
                                })
                                if len(results) >= max_results * 3:
                                    conn.close()
                                    return
                        except Exception:
                            pass
            conn.close()
        except Exception:
            return

    # Walk all files in context
    for child in sorted(context_root.rglob("*")):
        if not child.is_file():
            continue
        if child.name.lower() == "gold.csv":
            continue
        rel = child.relative_to(context_root).as_posix()
        suffix = child.suffix.lower()

        if suffix == ".csv":
            _search_csv_file(rel, child)
        elif suffix == ".json":
            _search_json_file(rel, child)
        elif suffix in {".sqlite", ".db", ".sqlite3"}:
            _search_sqlite_file(rel, child)
        elif suffix in {".md", ".txt", ".log", ".text"}:
            _search_text_file(rel, child)

        if len(results) >= max_results * 3:
            break

    # Sort by score descending and return top results
    results.sort(key=lambda x: -float(x.get("score", 0)))
    top_results = results[:max_results]

    # Truncate large values for output
    for r in top_results:
        if "values" in r and isinstance(r["values"], list):
            r["values"] = [str(v)[:200] for v in r["values"]]
        if "data" in r and isinstance(r["data"], dict):
            r["data"] = {k: str(v)[:200] for k, v in r["data"].items()}

    return {
        "query": query,
        "mode": "regex" if is_regex else "keyword",
        "total_matches_found": len(results),
        "results_returned": len(top_results),
        "results": top_results,
    }
