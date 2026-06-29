"""Persist a KnowledgeGraph to SQLite so it survives across steps and runs.

Building the graph (scanning every file) is the expensive part; once built it is
written to a small SQLite DB under the context dir. The agent can then *read* the
graph back instantly — and, crucially, **query it relationally**: "which source
holds an entity/column/value matching this term?". That is what stops the agent
from blind-probing every database with `LIKE '%Acme%'` and getting 0 rows when
the answer actually lives in a different file.

The store is deliberately schema-light (nodes / edges / constraints / metrics +
a meta table) and degrades gracefully: if the context dir is read-only the
persist call is a no-op and callers fall back to building in memory.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from data_agent_baseline.tools.knowledge_graph import KnowledgeGraph, build_knowledge_graph

__all__ = [
    "kg_db_path",
    "persist_knowledge_graph",
    "load_knowledge_graph",
    "search_graph",
    "ensure_knowledge_graph",
    "literal_filter_hint",
]

_KG_DIRNAME = ".kg"
_KG_DBNAME = "graph.db"


def kg_db_path(context_dir: Path) -> Path:
    return Path(context_dir) / _KG_DIRNAME / _KG_DBNAME


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kg_nodes (
            name TEXT, source_file TEXT, source_type TEXT, row_count INTEGER,
            columns_json TEXT, samples_json TEXT
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            from_entity TEXT, from_column TEXT, to_entity TEXT, to_column TEXT,
            type TEXT, confidence REAL
        );
        CREATE TABLE IF NOT EXISTS kg_constraints (entity TEXT, field TEXT, rule TEXT);
        CREATE TABLE IF NOT EXISTS kg_metrics (name TEXT, formula TEXT, description TEXT);
        CREATE TABLE IF NOT EXISTS kg_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )


def persist_knowledge_graph(context_dir: Path, kg: KnowledgeGraph) -> Path | None:
    """Write the graph to SQLite under ``context_dir/.kg/graph.db``.

    Returns the DB path, or ``None`` if persistence failed (e.g. read-only dir) —
    persistence is best-effort and must never break a run.
    """
    db_path = kg_db_path(context_dir)
    conn = None
    try:
        conn = _connect(db_path)
        _create_schema(conn)
        conn.execute("DELETE FROM kg_nodes")
        conn.execute("DELETE FROM kg_edges")
        conn.execute("DELETE FROM kg_constraints")
        conn.execute("DELETE FROM kg_metrics")
        conn.executemany(
            "INSERT INTO kg_nodes VALUES (?,?,?,?,?,?)",
            [
                (
                    e.name, e.source_file, e.source_type, e.row_count,
                    json.dumps(e.columns, ensure_ascii=False),
                    json.dumps({k: v[:3] for k, v in e.sample_values.items()}, ensure_ascii=False),
                )
                for e in kg.entities
            ],
        )
        conn.executemany(
            "INSERT INTO kg_edges VALUES (?,?,?,?,?,?)",
            [
                (r.from_entity, r.from_column, r.to_entity, r.to_column,
                 r.relationship_type, r.confidence)
                for r in kg.relationships
            ],
        )
        conn.executemany(
            "INSERT INTO kg_constraints VALUES (?,?,?)",
            [(c.entity, c.field, c.rule) for c in kg.constraints],
        )
        conn.executemany(
            "INSERT INTO kg_metrics VALUES (?,?,?)",
            [(m.name, m.formula, m.description) for m in kg.metrics],
        )
        conn.execute("DELETE FROM kg_meta")
        conn.executemany(
            "INSERT INTO kg_meta VALUES (?,?)",
            [
                ("entities", str(len(kg.entities))),
                ("relationships", str(len(kg.relationships))),
                ("constraints", str(len(kg.constraints))),
                ("metrics", str(len(kg.metrics))),
                ("knowledge_summary", kg.knowledge_summary or ""),
            ],
        )
        conn.commit()
        return db_path
    except Exception:  # noqa: BLE001 - persistence is best-effort
        return None
    finally:
        if conn is not None:
            conn.close()


def _row_node(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "name": row[0],
        "source_file": row[1],
        "source_type": row[2],
        "row_count": row[3],
        "columns": json.loads(row[4]) if row[4] else [],
        "sample_values": json.loads(row[5]) if row[5] else {},
    }


def load_knowledge_graph(context_dir: Path) -> dict[str, Any] | None:
    """Read the persisted graph back into a plain dict, or ``None`` if absent."""
    db_path = kg_db_path(context_dir)
    if not db_path.exists():
        return None
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        nodes = [_row_node(r) for r in conn.execute(
            "SELECT name, source_file, source_type, row_count, columns_json, samples_json FROM kg_nodes"
        )]
        edges = [
            {"from": f"{r[0]}.{r[1]}", "to": f"{r[2]}.{r[3]}", "type": r[4],
             "confidence": round(r[5] or 0.0, 2)}
            for r in conn.execute(
                "SELECT from_entity, from_column, to_entity, to_column, type, confidence FROM kg_edges"
            )
        ]
        constraints = [
            {"entity": r[0], "field": r[1], "rule": r[2]}
            for r in conn.execute("SELECT entity, field, rule FROM kg_constraints")
        ]
        metrics = [
            {"name": r[0], "formula": r[1], "description": r[2]}
            for r in conn.execute("SELECT name, formula, description FROM kg_metrics")
        ]
        meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM kg_meta")}
        return {
            "entities": nodes,
            "relationships": edges,
            "constraints": constraints,
            "metrics": metrics,
            "knowledge_summary": meta.get("knowledge_summary", ""),
            "source": "persisted_sqlite",
        }
    except Exception:  # noqa: BLE001
        return None
    finally:
        if conn is not None:
            conn.close()


def ensure_knowledge_graph(context_dir: Path) -> dict[str, Any]:
    """Return the persisted graph, building + persisting it first if needed."""
    loaded = load_knowledge_graph(context_dir)
    if loaded is not None:
        return loaded
    # Build a transient PublicTask-less graph: build_knowledge_graph only needs
    # context_dir, so adapt via a tiny shim.
    kg = _build_for_dir(context_dir)
    persist_knowledge_graph(context_dir, kg)
    loaded = load_knowledge_graph(context_dir)
    if loaded is not None:
        return loaded
    # Persist failed (read-only dir) — return the in-memory graph directly.
    payload = kg.to_dict()
    payload.pop("compact_text", None)
    payload["source"] = "in_memory"
    return payload


def _build_for_dir(context_dir: Path) -> KnowledgeGraph:
    from types import SimpleNamespace

    task = SimpleNamespace(context_dir=Path(context_dir))
    return build_knowledge_graph(task)  # type: ignore[arg-type]


def _live_value_hits(
    context_dir: Path, ent: dict[str, Any], needle: str, *, max_cols: int = 40
) -> list[str]:
    """Scan the REAL source file for ``needle`` when the 3-row samples miss it.

    The persisted graph only keeps 3 sample values per column, so a value living
    in row 4+ is invisible to sample matching. This confirms (or denies) the term
    against the actual data → returns ``col=value`` hits, one example per column.
    """
    source_file = str(ent.get("source_file", ""))
    if not source_file:
        return []
    src = Path(context_dir) / source_file
    if not src.exists():
        return []
    cols = [c.get("name") for c in ent.get("columns", []) if c.get("name")][:max_cols]
    if not cols:
        return []
    hits: list[str] = []
    try:
        if ent.get("source_type") == "sqlite":
            conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                for col in cols:
                    try:
                        cur = conn.execute(
                            f'SELECT "{col}" FROM "{ent.get("name")}" '
                            f'WHERE instr(lower(CAST("{col}" AS TEXT)), ?) > 0 LIMIT 1',
                            (needle,),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            hits.append(f"{col}={row[0]}")
                    except Exception:  # noqa: BLE001 - skip unscannable column
                        continue
            finally:
                conn.close()
        elif ent.get("source_type") == "csv":
            matched_cols: set[str] = set()
            with open(src, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    for col in cols:
                        if col in matched_cols:
                            continue
                        val = row.get(col)
                        if val and needle in str(val).lower():
                            hits.append(f"{col}={val}")
                            matched_cols.add(col)
                    if len(matched_cols) >= len(cols):
                        break
    except Exception:  # noqa: BLE001 - live lookup is best-effort
        return hits
    return hits


_SQUOTE_RE = re.compile(r"'([^']{2,40})'")
_DQUOTE_RE = re.compile(r'"([^"]{2,40})"')
_NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
# Tokens that mark a captured literal as a SQL/code fragment, not a data value.
_SQLISH_TOKENS = ("select", "from ", "where", "join", " = ", "*", "(", ")", "%")


def _result_is_empty_or_zero(action: str, content: Any) -> bool:
    """True when an execute_* result is empty or a single zero/NULL aggregate."""
    if not isinstance(content, dict):
        return False
    if action in ("execute_context_sql", "execute_universal_sql"):
        rows = content.get("rows")
        if rows == []:
            return True
        return bool(isinstance(rows, list) and len(rows) == 1
                    and isinstance(rows[0], list) and len(rows[0]) == 1
                    and rows[0][0] in (0, 0.0, None))
    if action == "execute_python":
        if not content.get("success"):
            return False
        out = str(content.get("output", "")).strip().lower()
        if out in {"0", "0.0", "[]", "{}", "[[0]]", "[[0.0]]", "[[null]]", "[[none]]"}:
            return True
        return ('"rows": []' in out or "'rows': []" in out
                or '"rows": [[0]]' in out or "'rows': [[0]]" in out
                or '"rows": [[0.0]]' in out)
    return False


def _extract_string_literals(action_input: Any) -> list[str]:
    """Quoted, non-numeric *data-value* literals from a SQL/Python action_input.

    Single- and double-quoted spans are scanned separately so a value nested
    inside another quote (``"... status = 'unpaid'"``) is captured cleanly, and
    SQL/code fragments (containing SELECT/FROM/=/* …) are dropped.
    """
    text = ""
    if isinstance(action_input, dict):
        for key in ("sql", "code", "query"):
            value = action_input.get(key)
            if isinstance(value, str):
                text += "\n" + value
    out: list[str] = []
    seen: set[str] = set()
    for regex in (_SQUOTE_RE, _DQUOTE_RE):
        for match in regex.finditer(text):
            literal = match.group(1).strip()
            low = literal.lower()
            if not literal or _NUMERIC_LITERAL_RE.match(literal):
                continue
            if any(tok in low for tok in _SQLISH_TOKENS) or len(literal.split()) > 5:
                continue
            if low in seen:
                continue
            seen.add(low)
            out.append(literal)
    return out


def literal_filter_hint(
    action: str, action_input: Any, content: Any, context_dir: Path, *, model: Any | None = None
) -> str | None:
    """I2 — when an execute_* result is empty/zero, find which quoted filter value
    doesn't exist and suggest the REAL stored value(s) via fuzzy + concept-bridge.

    Turns the generic "verify your filter" nudge into a specific
    "'unpaid' isn't a value — the column has open/overdue" across SQL *and* python.
    """
    if action not in ("execute_context_sql", "execute_universal_sql", "execute_python"):
        return None
    if not _result_is_empty_or_zero(action, content):
        return None
    literals = _extract_string_literals(action_input)
    if not literals:
        return None

    suggestions: list[str] = []
    for literal in literals[:3]:
        if search_graph(context_dir, literal, mode="exact").get("match_count", 0) > 0:
            continue  # the literal exists somewhere → not the culprit
        bridged = search_graph(context_dir, literal, mode="hybrid", model=model)
        real_values: list[str] = []
        for ent in bridged.get("entities", []):
            for mo in ent.get("matched_on", []):
                real_values.append(f"{ent.get('entity')}.{mo}")
        if real_values:
            suggestions.append(f"'{literal}' → " + " | ".join(real_values[:4]))

    if not suggestions:
        return None
    return (
        "\n⚠️ FILTER VALUE NOT FOUND: a value you filtered by does not exist in the data; "
        "use the real stored value(s) instead and re-run (do NOT answer 0/empty):\n  "
        + "\n  ".join(suggestions)
    )


def _collect_column_values(
    context_dir: Path, ent: dict[str, Any], *, max_cols: int = 40, max_per_col: int = 2000
) -> dict[str, list[str]]:
    """Distinct values per column from the real source (sqlite/csv), bounded."""
    source_file = str(ent.get("source_file", ""))
    if not source_file:
        return {}
    src = Path(context_dir) / source_file
    if not src.exists():
        return {}
    cols = [c.get("name") for c in ent.get("columns", []) if c.get("name")][:max_cols]
    out: dict[str, list[str]] = {}
    try:
        if ent.get("source_type") == "sqlite":
            conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                for col in cols:
                    try:
                        cur = conn.execute(
                            f'SELECT DISTINCT "{col}" FROM "{ent.get("name")}" '
                            f'WHERE "{col}" IS NOT NULL LIMIT ?',
                            (max_per_col,),
                        )
                        out[col] = [str(r[0]) for r in cur.fetchall()]
                    except Exception:  # noqa: BLE001
                        continue
            finally:
                conn.close()
        elif ent.get("source_type") == "csv":
            seen: dict[str, set[str]] = {c: set() for c in cols}
            with open(src, newline="", encoding="utf-8", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    for col in cols:
                        val = row.get(col)
                        if val not in (None, "") and len(seen[col]) < max_per_col:
                            seen[col].add(str(val))
            out = {c: list(v) for c, v in seen.items() if v}
    except Exception:  # noqa: BLE001 - value collection is best-effort
        return out
    return out


def search_graph(
    context_dir: Path, term: str, *, mode: str = "exact", model: Any | None = None
) -> dict[str, Any]:
    """Find where a term lives in the graph.

    Matches the term against entity names, source files, column names and values,
    plus any join edge touching a matched entity. This is the "where do I even
    look?" query that replaces probing every file by hand.

    `mode` controls how values are matched (fix #3):
      • "exact"  — normalized substring (default; cheap, high precision).
      • "fuzzy"  — escalates to token/numeric/prefix matching when exact finds
                   nothing (handles word order, zero-pad, abbreviations).
      • "hybrid" — fuzzy, then a `model`-driven concept→value bridge if still empty.
      • "llm"    — concept→value bridge only (requires `model`).
    Escalation runs only when the exact pass matched no entity, so the precise
    fast path is never diluted.
    """
    graph = ensure_knowledge_graph(context_dir)
    needle = term.lower().strip()
    matched_entities: list[dict[str, Any]] = []
    for ent in graph.get("entities", []):
        hits: list[str] = []
        if needle in str(ent.get("name", "")).lower():
            hits.append("entity name")
        if needle in str(ent.get("source_file", "")).lower():
            hits.append("file name")
        col_hits = [c["name"] for c in ent.get("columns", []) if needle in str(c.get("name", "")).lower()]
        if col_hits:
            hits.append("columns: " + ", ".join(col_hits[:6]))
        val_hits = [
            f"{col}={v}"
            for col, vals in ent.get("sample_values", {}).items()
            for v in vals
            if needle in str(v).lower()
        ]
        if val_hits:
            hits.append("sample values: " + "; ".join(val_hits[:6]))
        else:
            # Samples only keep 3 values/column — confirm against the real source
            # before declaring "not found" (avoids false negatives that wrongly
            # send the agent off to read documents).
            live_hits = _live_value_hits(context_dir, ent, needle)
            if live_hits:
                hits.append("values: " + "; ".join(live_hits[:6]))
        if hits:
            matched_entities.append({
                "entity": ent.get("name"),
                "source_file": ent.get("source_file"),
                "source_type": ent.get("source_type"),
                "matched_on": hits,
            })

    matched_via = "exact"
    # Fix #3 — escalate beyond exact substring only when nothing matched, so the
    # precise path stays untouched and we pay the cost only on a real miss.
    if not matched_entities and mode != "exact":
        from data_agent_baseline.tools.semantic_match import match_values

        # Cost guard for the LLM concept-bridge: only categorical columns (few
        # distinct values) are worth bridging, and only up to a small budget of
        # columns may invoke the model. Everything else falls back to fuzzy.
        LLM_BRIDGE_MAX_CARD = 50
        LLM_BRIDGE_MAX_COLS = 8
        llm_budget = LLM_BRIDGE_MAX_COLS if (model is not None and mode in ("hybrid", "llm")) else 0

        for ent in graph.get("entities", []):
            col_values = _collect_column_values(context_dir, ent)
            sem_hits: list[str] = []
            for col, values in col_values.items():
                eff_mode, eff_model = mode, model
                if model is not None and mode in ("hybrid", "llm"):
                    if len(values) <= LLM_BRIDGE_MAX_CARD and llm_budget > 0:
                        llm_budget -= 1  # this categorical column may call the LLM
                    else:
                        eff_mode, eff_model = "fuzzy", None  # too large / out of budget
                matched = match_values(term, values, mode=eff_mode, model=eff_model)
                for v in matched[:3]:
                    sem_hits.append(f"{col}={v}")
            if sem_hits:
                matched_entities.append({
                    "entity": ent.get("name"),
                    "source_file": ent.get("source_file"),
                    "source_type": ent.get("source_type"),
                    "matched_on": [f"{mode} match: " + "; ".join(sem_hits[:6])],
                })
        if matched_entities:
            matched_via = mode

    matched_names = {m["entity"] for m in matched_entities}
    related_joins = [
        edge for edge in graph.get("relationships", [])
        if any(str(edge.get(side, "")).split(".")[0] in matched_names for side in ("from", "to"))
    ]
    return {
        "term": term,
        "match_count": len(matched_entities),
        "matched_via": matched_via,
        "entities": matched_entities,
        "related_joins": related_joins[:20],
        "note": (
            "No structured entity matched this term — it may live in a document "
            "(.pdf/.md/.txt). Try `map_sources` or `read_pdf`/`search_doc`."
            if not matched_entities else
            "Read/query the matched source(s) directly; use related_joins to connect tables."
        ),
    }
