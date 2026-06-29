"""Data Doctor (§12.1) — factual profiler + LLM-driven, code-generated fixes.

Three functions:
  • profile_quality(path|df)         → per-column factual report (no hard-coded rules)
  • llm_suggest_fixes(report, model) → LLM returns suggestion cards, each carrying a
                                       short *pandas snippet* that implements the fix
  • apply_pandas_fix(df, code)       → run that snippet against `df` in a sandbox,
                                       diff before↔after, return (new_df, result)

The LLM owns *both* the diagnosis and the fix logic — there is no fixed action menu.
The sandbox (`_validate_code` + restricted exec namespace + thread watchdog) bounds
what the code can do: no imports, no I/O, no dunder access, no `open/exec/eval/...`,
only the `df`, `pd`, `np`, `re` variables, with a safe subset of builtins. The UI
renders whatever the LLM returns: title, rationale, the code (editable), and a
before→after diff after a dry-run.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import json
import re
import sqlite3
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_agent_baseline.agents.model import ModelMessage

SEVERITY_RANK = {"info": 0, "warn": 1, "error": 2}

_NA_VALUES = ["", "NA", "N/A", "n/a", "null", "NULL", "NaN", "nan", "None", "-"]
_NUM_STRIP = re.compile(r"[,$€£%\s]")
_UNIT_SUFFIX = re.compile(r"(?i)(kg|km|cm|mm|usd|eur|pcs|g|m)$")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _py(value: Any) -> Any:
    """Coerce numpy/pandas scalars to JSON-friendly Python types."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        return None if np.isnan(f) else f
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value) if np.ndim(value) == 0 else False:
        return None
    return value


def _short(text: Any, limit: int = 60) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def read_table(path: Path, sheet: str | int | None = None, table: str | None = None) -> pd.DataFrame:
    """Read a CSV/TSV/Excel/SQLite file as all-strings so we can detect issues ourselves.
    For Excel, `sheet` selects a sheet (default: first). For SQLite, `table` selects a
    table (default: the first user table). Each row in the resulting DataFrame is a row
    in the chosen table — perfect for Data Doctor / Explore."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        sep = "\t" if suffix == ".tsv" else ","
        return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=True, na_values=_NA_VALUES)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=str, sheet_name=sheet if sheet is not None else 0)
    if suffix in {".sqlite", ".db", ".sqlite3"}:
        tables = sqlite_tables(path)
        if not tables:
            raise ValueError(f"SQLite file '{path.name}' has no user tables to read.")
        chosen = table or tables[0]
        if chosen not in tables:
            raise ValueError(f"table '{chosen}' not found in {path.name}; available: {tables}")
        # Read as text so the Data Doctor / Explore pipelines see the same shape they do
        # for CSV (mixed-type tolerance, no premature coercion). Use object dtype + NumPy
        # NaN so JSON serializers downstream don't trip over pandas.NA.
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            df = pd.read_sql_query(f'SELECT * FROM "{chosen}"', conn)
        for col in df.columns:
            df[col] = df[col].astype("string").astype(object)
        return df.where(df.notna(), np.nan)
    raise ValueError(f"Data Doctor supports CSV/TSV/Excel/SQLite files, not '{suffix or path.name}'.")


def excel_sheets(path: Path) -> list[str]:
    """List the sheet names of an Excel file ([] for non-Excel)."""
    path = Path(path)
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return []
    return [str(name) for name in pd.ExcelFile(path).sheet_names]


def sqlite_tables(path: Path) -> list[str]:
    """List user tables (no sqlite_* internals, no views) in a SQLite file."""
    path = Path(path)
    if path.suffix.lower() not in {".sqlite", ".db", ".sqlite3"}:
        return []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [r[0] for r in rows]


def inspect_sqlite_schema(path: Path) -> dict[str, Any]:
    """Full schema dump for ER-graph rendering: tables, columns (with PK flag),
    foreign keys, and a row count per table. Read-only, no LLM."""
    path = Path(path)
    if path.suffix.lower() not in {".sqlite", ".db", ".sqlite3"}:
        raise ValueError(f"inspect_sqlite_schema only supports SQLite files (.db/.sqlite), not '{path.suffix}'.")
    tables: list[dict[str, Any]] = []
    fks: list[dict[str, Any]] = []
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        for name in names:
            cols: list[dict[str, Any]] = []
            for cid, col_name, col_type, notnull, _dflt, pk in conn.execute(f'PRAGMA table_info("{name}")').fetchall():
                cols.append({
                    "name": col_name,
                    "type": (col_type or "").upper() or "ANY",
                    "pk": bool(pk),
                    "notnull": bool(notnull),
                })
            try:
                rows = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.DatabaseError:
                rows = None
            for _id, _seq, ref_table, from_col, to_col, *_rest in conn.execute(f'PRAGMA foreign_key_list("{name}")').fetchall():
                fks.append({
                    "from_table": name, "from_column": from_col,
                    "to_table": ref_table, "to_column": to_col or from_col,
                })
            # Flag any column that links to another table as a foreign-key column.
            fk_cols = {f["from_column"] for f in fks if f["from_table"] == name}
            for c in cols:
                c["fk"] = c["name"] in fk_cols
            tables.append({"name": name, "columns": cols, "rows": rows})
    return {"file": path.name, "tables": tables, "foreign_keys": fks}


def preview_table(path: Path, rows: int = 50, sheet: str | int | None = None,
                  table: str | None = None) -> dict[str, Any]:
    """First `rows` of a CSV/Excel/SQLite file as columns + row lists (NaN → None)."""
    df = read_table(path, sheet=sheet, table=table)
    head = df.head(max(1, rows))
    head = head.where(head.notna(), None)
    return {
        "columns": [str(c) for c in df.columns],
        "rows": head.astype(object).values.tolist(),
        "total_rows": int(len(df)),
    }


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_numeric_series(series: pd.Series) -> pd.Series:
    """Parse a (possibly messy) string column into numbers: strip separators/units."""
    cleaned = series.astype("string").str.replace(_NUM_STRIP, "", regex=True)
    cleaned = cleaned.str.replace(_UNIT_SUFFIX, "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce")


# --------------------------------------------------------------------------- #
# Sandbox for LLM-generated pandas fix snippets
# --------------------------------------------------------------------------- #
# Builtins exposed to the snippet. Anything that gives access to the filesystem,
# arbitrary code execution, or attribute escape hatches is OMITTED on purpose.
_SAFE_BUILTINS = {
    "abs", "all", "any", "bin", "bool", "bytes", "chr", "complex", "dict",
    "divmod", "enumerate", "filter", "float", "format", "frozenset", "hash",
    "hex", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
    "max", "min", "next", "oct", "ord", "pow", "print", "range", "repr",
    "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple",
    "zip", "True", "False", "None",
}

# Names that, even if not in builtins, must not appear anywhere in the AST.
_FORBIDDEN_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "input",
    "globals", "locals", "vars", "breakpoint", "exit", "quit",
    "getattr", "setattr", "delattr", "hasattr", "help", "dir",
    "memoryview", "object", "type", "super", "classmethod", "staticmethod",
    "__builtins__", "__class__", "__bases__", "__subclasses__",
}

_FIX_TIMEOUT_SECONDS = 8.0


def _validate_code(code: str) -> None:
    """Reject obviously dangerous snippets BEFORE we exec them. Belt-and-braces:
    the restricted globals already block most of this — the AST check just gives
    a clear error message and stops trivial escape attempts (imports, dunders)."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"invalid Python syntax: {exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("imports are not allowed in fix code")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("global/nonlocal are not allowed in fix code")
        if isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith, ast.Await)):
            raise ValueError("async constructs are not allowed in fix code")
        if isinstance(node, ast.With):
            raise ValueError("`with` blocks (file/resource handles) are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"private attribute '{node.attr}' is not allowed in fix code")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"name '{node.id}' is not allowed in fix code")


def _exec_with_timeout(code: str, g: dict[str, Any], l: dict[str, Any], timeout: float) -> None:
    """Run `code` with a watchdog thread so a runaway snippet doesn't hang the request.
    On Windows we can't truly kill the worker thread, but we return promptly to the
    caller (the daemon thread will die with the process)."""
    err: dict[str, BaseException] = {}

    def target() -> None:
        try:
            exec(compile(code, "<fix>", "exec"), g, l)  # noqa: S102 - sandboxed by design
        except BaseException as e:  # noqa: BLE001 - surface every failure to the caller
            err["e"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"fix code exceeded the {timeout:.0f}s time budget")
    if "e" in err:
        raise err["e"]


def _diff_df(before: pd.DataFrame, after: pd.DataFrame, limit: int = 8) -> dict[str, Any]:
    """Describe what changed between two dataframes (shape + per-cell deltas).
    The result has the same shape regardless of which fix produced it, so the UI
    can render any LLM-generated change without knowing the action."""
    cols_before = [str(c) for c in before.columns]
    cols_after = [str(c) for c in after.columns]
    added = [c for c in cols_after if c not in cols_before]
    removed = [c for c in cols_before if c not in cols_after]
    shared = [c for c in cols_after if c in cols_before]

    nb = int(before[shared].isna().sum().sum()) if shared else 0
    na = int(after[shared].isna().sum().sum()) if shared else 0

    changed: list[dict[str, Any]] = []
    if len(before) == len(after) and shared:
        for col in shared:
            a = before[col].reset_index(drop=True)
            b = after[col].reset_index(drop=True)
            for i in range(len(a)):
                ov, nv = a.iloc[i], b.iloc[i]
                if (pd.isna(ov) != pd.isna(nv)) or (not pd.isna(ov) and str(ov) != str(nv)):
                    changed.append({"row": int(i), "column": str(col),
                                    "before": _cell(ov), "after": _cell(nv)})
                    if len(changed) >= limit:
                        break
            if len(changed) >= limit:
                break

    parts: list[str] = []
    if len(before) != len(after):
        parts.append(f"{len(before)} → {len(after)} rows")
    if added:
        parts.append(f"added column(s): {', '.join(added)}")
    if removed:
        parts.append(f"dropped column(s): {', '.join(removed)}")
    if nb != na:
        parts.append(f"nulls {nb} → {na}")
    if not parts and changed:
        parts.append(f"{len(changed)} cell(s) changed")
    return {
        "rows_before": int(len(before)),
        "rows_after": int(len(after)),
        "nulls_before": nb,
        "nulls_after": na,
        "columns_added": added,
        "columns_removed": removed,
        "changed": changed,
        "message": "; ".join(parts) if parts else "no observable change",
    }


# --------------------------------------------------------------------------- #
# 1) profile_quality
# --------------------------------------------------------------------------- #
def profile_quality_df(df: pd.DataFrame, filename: str = "data") -> dict[str, Any]:
    """Describe the data FACTUALLY — types, nulls, uniques, sample values, basic stats.
    Deciding what is *wrong* and how to fix it is the LLM's job (llm_suggest_fixes), so
    this stays a pure profiler with no hard-coded issue rules."""
    n_total = int(len(df))
    duplicate_rows = int(df.duplicated().sum()) if n_total else 0
    column_reports: list[dict[str, Any]] = []

    for col in df.columns:
        non_null = df[col].dropna()
        n = int(len(non_null))
        nulls = n_total - n
        num = to_numeric_series(non_null) if n else pd.Series([], dtype="float64")
        num_ratio = (int(num.notna().sum()) / n) if n else 0.0
        kind = "empty" if n == 0 else ("numeric" if num_ratio >= 0.9 else "text")

        report: dict[str, Any] = {
            "column": str(col),
            "kind": kind,
            "nulls": nulls,
            "null_pct": round(nulls / n_total, 4) if n_total else 0.0,
            "unique": int(non_null.nunique()) if n else 0,
            "samples": [_short(v, 40) for v in list(non_null.unique())[:5]],
        }
        if kind == "numeric":
            report["min"] = _py(num.min())
            report["max"] = _py(num.max())
            report["mean"] = round(float(num.mean()), 4)
        column_reports.append(report)

    return {
        "file": filename,
        "rows": n_total,
        "columns": int(df.shape[1]),
        "duplicate_rows": duplicate_rows,
        "column_reports": column_reports,
    }


def profile_quality(path: Path, table: str | None = None) -> dict[str, Any]:
    path = Path(path)
    df = read_table(path, table=table)
    label = f"{path.name}#{table}" if table else path.name
    return profile_quality_df(df, filename=label)


# --------------------------------------------------------------------------- #
# 2) Multi-agent diagnose-and-fix pipeline
# --------------------------------------------------------------------------- #
# A *single* generic prompt forces the model to do everything at once and often
# produces shallow, ungrounded suggestions. We instead split the problem into
# 5 specialists, each with a narrow remit + its own examples + its own anti-
# scope ("don't propose X — another specialist owns it"). Each agent gets the
# same factual profile but a different system prompt. Their outputs go through:
#   1) sandbox AST validation (_validate_code)
#   2) ONE repair pass if validation fails (model rewrites the snippet)
#   3) per-agent dedupe → cross-agent dedupe
# All runs happen in parallel via a small thread pool — the LLM HTTP calls
# dominate latency, so parallelism cuts wall time ~5× without changing logic.
# --------------------------------------------------------------------------- #
_BASE_RULES = (
    "RULES for `pandas_code` (sandbox):\n"
    " • Only `df` (pandas.DataFrame), `pd`, `np`, `re` and safe builtins are available.\n"
    " • NO imports. NO file I/O. NO `with` blocks. NO underscored attributes (`df.__class__`).\n"
    " • NO `open/exec/eval/compile/getattr/setattr/__import__/...`.\n"
    " • Either mutate `df` in place or rebind it.\n"
    " • Use column names EXACTLY as given; never invent columns.\n"
    " • Keep snippets short (1–4 lines). Prefer the least destructive fix.\n"
    " • `title`, `rationale`, and `expected_effect` MUST describe what the code actually does."
)
_OUTPUT_SCHEMA = (
    "OUTPUT — STRICT JSON only (no markdown fences, no prose):\n"
    "{\"suggestions\":[{\"column\":<name|null>,\"issue\":<short label>,"
    "\"severity\":\"error\"|\"warn\"|\"info\",\"title\":<short imperative>,"
    "\"rationale\":<one sentence>,\"pandas_code\":<snippet>,"
    "\"expected_effect\":<one sentence>}]}\n"
    "If your specialty has nothing to flag, return {\"suggestions\":[]}."
)


@dataclass(frozen=True)
class QualityAgent:
    """A specialist data-quality agent. Stateless — just a name + prompt."""
    name: str         # short id used in the API and UI ("missing", "outliers"…)
    label: str        # short human label for chips
    role: str         # one-line description
    system: str       # full system prompt


DOMAIN_AGENTS: tuple[QualityAgent, ...] = (
    QualityAgent(
        name="missing",
        label="Missing values",
        role="Nulls, blanks, and missing-value sentinels.",
        system=(
            "You are the MISSING VALUES specialist in a multi-agent data-quality pipeline.\n"
            "Look ONLY for problems in your domain:\n"
            " • Real nulls (NaN / empty string) — propose imputation (median, mean, mode, constant)"
            " or row-drop with a clear cutoff.\n"
            " • Sentinel values masquerading as data: -1, 0, 9999, '?', 'unknown', 'N/A', 'none',"
            " 'missing' in fields where they make no semantic sense. Convert them to NaN first.\n"
            " • Columns where >50% of values are null (consider drop_column).\n"
            "Do NOT propose: type conversions, deduplication, casing/whitespace fixes, outlier"
            " handling — other specialists own those.\n"
            "Pick the LEAST DESTRUCTIVE fix that works. When in doubt, coerce to NaN rather"
            " than delete rows.\n"
            "Example snippets:\n"
            "  df['age'] = df['age'].fillna(df['age'].median())\n"
            "  df.loc[df['country'].isin(['?', 'unknown']), 'country'] = pd.NA\n"
            "  df = df.dropna(subset=['email']).reset_index(drop=True)\n\n"
            + _BASE_RULES + "\n\n" + _OUTPUT_SCHEMA
        ),
    ),
    QualityAgent(
        name="duplicates",
        label="Duplicates",
        role="Exact and near-duplicate rows.",
        system=(
            "You are the DUPLICATES specialist in a multi-agent data-quality pipeline.\n"
            "Look ONLY for problems in your domain:\n"
            " • Exact duplicate rows (`report.duplicate_rows > 0`) → propose `df.drop_duplicates()`.\n"
            " • Near-duplicates: same business key but trivially different (case/whitespace,"
            " trailing punctuation). Suggest normalising the key column first, then dedup.\n"
            " • Columns whose `unique ≈ rows` look like primary keys; flag collisions when there"
            " should be none.\n"
            "Use `df.drop_duplicates(subset=[...])` with a sensible subset when the table has a"
            " clear key.\n"
            "Do NOT propose: null imputation, type conversions, outlier handling, formatting fixes"
            " outside the dedupe key.\n"
            "Example snippets:\n"
            "  df = df.drop_duplicates().reset_index(drop=True)\n"
            "  df = df.drop_duplicates(subset=['order_id']).reset_index(drop=True)\n\n"
            + _BASE_RULES + "\n\n" + _OUTPUT_SCHEMA
        ),
    ),
    QualityAgent(
        name="types",
        label="Types & parsing",
        role="Coerce wrongly-typed columns to the correct dtype.",
        system=(
            "You are the TYPES & PARSING specialist in a multi-agent data-quality pipeline.\n"
            "Look ONLY for problems in your domain:\n"
            " • Numbers stored as text: samples like '$1,200', '12kg', '15%', '  3.14 '. Strip"
            " currency/units/whitespace then use `pd.to_numeric(..., errors='coerce')`.\n"
            " • Dates/datetimes stored as text or in mixed formats. Use"
            " `pd.to_datetime(..., errors='coerce')` (prefer `format='mixed'` for variety).\n"
            " • Booleans stored as 'yes'/'no'/'Y'/'N'/'true'/'false' strings. Convert with a map.\n"
            " • Numeric columns silently held in object dtype because of one or two bad values.\n"
            "Always prefer `errors='coerce'` so bad cells become NaN — the missing-values agent"
            " handles imputation downstream.\n"
            "Do NOT propose: imputation of resulting NaNs, deduplication, casing fixes.\n"
            "Example snippets:\n"
            "  df['price'] = pd.to_numeric(df['price'].astype(str).str.replace(r'[$,]', '', regex=True), errors='coerce')\n"
            "  df['date'] = pd.to_datetime(df['date'], errors='coerce', format='mixed')\n"
            "  df['active'] = df['active'].map({'yes': True, 'no': False, 'Y': True, 'N': False})\n\n"
            + _BASE_RULES + "\n\n" + _OUTPUT_SCHEMA
        ),
    ),
    QualityAgent(
        name="formatting",
        label="Formatting & normalisation",
        role="Whitespace, casing, label unification.",
        system=(
            "You are the FORMATTING & NORMALISATION specialist in a multi-agent data-quality pipeline.\n"
            "Look ONLY for problems in your domain:\n"
            " • Leading/trailing whitespace in text columns → `.str.strip()`.\n"
            " • Inconsistent casing where samples show the same logical value in different forms"
            " ('USA' vs 'usa' vs 'Usa') → `.str.lower()` or `.str.title()`.\n"
            " • Spelling/encoding variants that should be unified via a small `replace`/mapping.\n"
            " • Stray punctuation, multiple inner spaces (`re.sub(r'\\s+', ' ', x)`).\n"
            " • Mixed unit suffixes that should be stripped or unified.\n"
            "Do NOT propose: type conversions to numeric/date, imputation, deduplication.\n"
            "Example snippets:\n"
            "  df['country'] = df['country'].astype('string').str.strip().str.title()\n"
            "  df['email'] = df['email'].astype('string').str.lower()\n"
            "  df['name'] = df['name'].astype('string').str.replace(r'\\s+', ' ', regex=True).str.strip()\n\n"
            + _BASE_RULES + "\n\n" + _OUTPUT_SCHEMA
        ),
    ),
    QualityAgent(
        name="outliers",
        label="Outliers & distribution",
        role="Outlier handling and structural anomalies.",
        system=(
            "You are the OUTLIERS & DISTRIBUTION specialist in a multi-agent data-quality pipeline.\n"
            "Look ONLY for problems in your domain:\n"
            " • Numeric outliers: values outside `Q1 − 1.5·IQR` / `Q3 + 1.5·IQR`, or ≥ 1000× the"
            " median, or impossible domain values (negative age, latitude > 90, percent > 100).\n"
            " • Suspiciously constant columns (`unique == 1`) → propose dropping them.\n"
            " • Near-id columns (`unique ≈ rows`) on what should be a category → flag, don't auto-fix.\n"
            "Prefer WINSORISING (cap at the IQR fence) or setting outliers to NaN over deleting"
            " rows. Only drop rows for clearly invalid values (negative age, year 1900).\n"
            "ALWAYS coerce the target column with `pd.to_numeric(..., errors='coerce')` BEFORE"
            " calling `.quantile()`, `.clip()`, `.mean()` etc — the file may have been loaded as"
            " strings (e.g. `'$1,200'`) and Arrow-backed pandas will raise"
            " `ArrowNotImplementedError` if you skip the coercion.\n"
            "If the target column is integer-typed (Int64/Int32/int), CAST IT TO FLOAT FIRST"
            " (`df['c'] = df['c'].astype('Float64')`) before `.clip()` / `.fillna(median)` /"
            " assigning any non-integer result — otherwise pandas raises"
            " `TypeError: Invalid value '<x.5>' for dtype 'Int64'`. If you must keep the column"
            " as integer, `.round().astype('Int64')` the result after clipping.\n"
            "Do NOT propose: imputation of regular nulls, casing/whitespace fixes, deduplication.\n"
            "Example snippets:\n"
            "  s = pd.to_numeric(df['amount'], errors='coerce').astype('Float64')\n"
            "  q1, q3 = s.quantile([0.25, 0.75]); iqr = q3 - q1\n"
            "  df['amount'] = s.clip(q1 - 1.5*iqr, q3 + 1.5*iqr)\n"
            "  df['age'] = pd.to_numeric(df['age'], errors='coerce').astype('Float64')\n"
            "  q1, q3 = df['age'].quantile([0.25, 0.75]); iqr = q3 - q1\n"
            "  df['age'] = df['age'].clip(q1 - 1.5*iqr, q3 + 1.5*iqr)\n"
            "  df.loc[pd.to_numeric(df['age'], errors='coerce') < 0, 'age'] = pd.NA\n"
            "  df = df.drop(columns=['constant_col'])\n\n"
            + _BASE_RULES + "\n\n" + _OUTPUT_SCHEMA
        ),
    ),
)

AGENT_BY_NAME: dict[str, QualityAgent] = {a.name: a for a in DOMAIN_AGENTS}


def _extract_json(text: str) -> dict[str, Any]:
    s = str(text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", s).strip()
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return {}


_REPAIR_SYSTEM = (
    "The pandas snippet you wrote was REJECTED. Rewrite ONLY the snippet so it"
    " passes both the sandbox AND actually runs on the real dataframe, while keeping"
    " the SAME goal.\n"
    " • Sandbox: no imports, no `with`, no underscored attributes (e.g. `df.__class__`),"
    " no `open / exec / eval / compile / getattr / setattr / __import__ / globals / locals`.\n"
    " • Only `df`, `pd`, `np`, `re` and safe builtins are available.\n"
    " • If the failure was a runtime error (TypeError, ValueError, KeyError, …),"
    " coerce/cast the involved columns BEFORE operating on them. Common fixes:\n"
    "   – `pd.to_numeric(df['c'], errors='coerce')` before quantile/clip/mean.\n"
    "   – Cast Int64/Int32 columns to Float64 BEFORE assigning a float result"
    " (`df['c'] = df['c'].astype('Float64')`) — assigning a float to an Int64 column raises"
    " `TypeError: Invalid value '...' for dtype 'Int64'`.\n"
    "   – Use `df.loc[mask, 'c']` instead of `df['c'][mask]` when assigning.\n"
    "Reply with STRICT JSON only: {\"pandas_code\":\"<new snippet>\"}"
)


def _repair_code(item: dict[str, Any], error: str, model: Any, agent: QualityAgent) -> str:
    """One-shot retry: ask the model to rewrite a rejected snippet. Returns "" on
    any failure — the caller treats that as "drop this card with the original
    rejection reason"."""
    user = (
        f"Specialist: {agent.label}\n"
        f"Goal: {item.get('title') or item.get('issue') or 'apply fix'}\n"
        f"Column: {item.get('column') or '(table-level)'}\n"
        f"Original snippet:\n{item.get('pandas_code') or ''}\n"
        f"Rejection reason: {error}"
    )
    try:
        raw = model.complete([
            ModelMessage(role="system", content=_REPAIR_SYSTEM),
            ModelMessage(role="user", content=user),
        ])
    except Exception:  # noqa: BLE001 - any model failure → just drop the card
        return ""
    parsed = _extract_json(raw) or {}
    return str(parsed.get("pandas_code") or "").strip()


def _dry_run_snippet(code: str, df_sample: pd.DataFrame) -> str:
    """Actually execute the snippet against a copy of `df_sample` in the sandbox.
    Returns "" on success, or a short error message on failure.

    Why: AST validation catches forbidden constructs but cannot tell that e.g.
    `df['age'] = df['age'].clip(...)` will raise `TypeError: Invalid value '61.5'
    for dtype 'Int64'`. A real dry-run on the same data the user will fix catches
    those before the card ever reaches the UI."""
    try:
        apply_pandas_fix(df_sample, code)
        return ""
    except ValueError as exc:
        # apply_pandas_fix already wraps runtime exceptions as ValueError("Type: msg").
        return str(exc)


def _empty_drop_bucket() -> dict[str, int]:
    return {"no_code": 0, "unsafe_code": 0, "unknown_column": 0,
            "duplicate": 0, "not_dict": 0, "repair_failed": 0, "runtime_error": 0}


_DRY_RUN_SAMPLE_ROWS = 2000


def _sample_for_dry_run(df: pd.DataFrame) -> pd.DataFrame:
    """Cap the dry-run dataframe so the validation pass stays cheap even on large
    uploads. Keeps the first N rows — outliers and dtype issues reproduce in the
    head almost always, and the original `apply_pandas_fix` always runs on full df."""
    if len(df) <= _DRY_RUN_SAMPLE_ROWS:
        return df
    return df.head(_DRY_RUN_SAMPLE_ROWS).copy()


def _process_items(
    items: Any, columns: set[str], model: Any, agent: QualityAgent, *,
    allow_repair: bool, df_sample: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate / repair / dry-run / dedup a single agent's items."""
    bag: dict[str, Any] = {
        "raw": len(items) if isinstance(items, list) else 0,
        "kept": 0, "repaired": 0,
        "dropped": _empty_drop_bucket(),
        "dropped_examples": [],
    }

    def _note(reason: str, item: Any) -> None:
        if len(bag["dropped_examples"]) >= 3:
            return
        title = ""
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("issue") or item.get("column") or "")[:60]
        bag["dropped_examples"].append({"reason": reason, "title": title, "agent": agent.name})

    def _try_repair(item: dict[str, Any], reason: str) -> str | None:
        """Repair a rejected snippet (AST or runtime). Returns the new code, or
        None if repair is disabled / the model declined / the rewrite still fails."""
        if not allow_repair:
            return None
        new_code = _repair_code(item, reason, model, agent)
        if not new_code:
            return None
        try:
            _validate_code(new_code)
        except ValueError as exc:
            bag["dropped"]["repair_failed"] += 1
            _note(f"repair still unsafe: {exc}", item)
            return None
        if df_sample is not None:
            err = _dry_run_snippet(new_code, df_sample)
            if err:
                bag["dropped"]["repair_failed"] += 1
                _note(f"repair still crashes: {err}", item)
                return None
        return new_code

    kept: list[dict[str, Any]] = []
    seen_local: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            bag["dropped"]["not_dict"] += 1
            _note("not a dict", item)
            continue
        code = str(item.get("pandas_code") or "").strip()
        if not code:
            bag["dropped"]["no_code"] += 1
            _note("no pandas_code", item)
            continue
        # 1) AST-level sandbox check.
        try:
            _validate_code(code)
        except ValueError as exc:
            new_code = _try_repair(item, str(exc))
            if new_code is None:
                bag["dropped"]["unsafe_code"] += 1
                _note(str(exc), item)
                continue
            code = new_code
            bag["repaired"] += 1
        # 2) Runtime dry-run on the actual data — catches dtype mismatches,
        #    KeyError on missing columns post-coercion, etc.
        if df_sample is not None:
            err = _dry_run_snippet(code, df_sample)
            if err:
                new_code = _try_repair(item, err)
                if new_code is None:
                    bag["dropped"]["runtime_error"] += 1
                    _note(err, item)
                    continue
                code = new_code
                bag["repaired"] += 1
        col = item.get("column")
        col = None if col in (None, "", "null") else str(col)
        if col and col not in columns:
            bag["dropped"]["unknown_column"] += 1
            _note(f"unknown column '{col}'", item)
            continue
        sev = str(item.get("severity", "warn")).lower()
        if sev not in SEVERITY_RANK:
            sev = "warn"
        digest = hashlib.md5(code.encode("utf-8")).hexdigest()[:8]
        sid = f"{agent.name}:{col or 'table'}:{digest}"
        if sid in seen_local:
            bag["dropped"]["duplicate"] += 1
            _note("duplicate within agent", item)
            continue
        seen_local.add(sid)
        kept.append({
            "id": sid,
            "agent": agent.name,
            "agent_label": agent.label,
            "column": col,
            "issue": _short(item.get("issue") or "fix", 40),
            "severity": sev,
            "title": _short(item.get("title") or "Apply fix", 90),
            "rationale": _short(item.get("rationale") or "", 200),
            "pandas_code": code,
            "expected_effect": _short(item.get("expected_effect") or "", 200),
        })
    bag["kept"] = len(kept)
    return kept, bag


def _run_agent(
    agent: QualityAgent, profile_user: str, columns: set[str], model: Any, *,
    allow_repair: bool, df_sample: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Call one specialist and process its output. Never raises — any failure is
    captured in the returned diag so the UI can show it."""
    a_diag: dict[str, Any] = {
        "name": agent.name,
        "label": agent.label,
        "role": agent.role,
        "raw": 0, "kept": 0, "repaired": 0,
        "dropped": _empty_drop_bucket(),
        "dropped_examples": [],
        "parse_ok": True,
        "error": None,
    }
    try:
        raw = model.complete([
            ModelMessage(role="system", content=agent.system),
            ModelMessage(role="user", content=profile_user),
        ])
    except Exception as exc:  # noqa: BLE001 - LLM/HTTP failure
        a_diag["error"] = str(exc)
        return [], a_diag
    parsed = _extract_json(raw)
    if not parsed:
        a_diag["parse_ok"] = False
        return [], a_diag
    items = parsed.get("suggestions", []) if isinstance(parsed, dict) else []
    kept, bag = _process_items(items, columns, model, agent,
                               allow_repair=allow_repair, df_sample=df_sample)
    a_diag.update({"raw": bag["raw"], "kept": bag["kept"], "repaired": bag["repaired"],
                   "dropped": bag["dropped"], "dropped_examples": bag["dropped_examples"]})
    return kept, a_diag


def llm_suggest_fixes_diag(
    report: dict[str, Any],
    model: Any,
    *,
    parallel: bool = True,
    allow_repair: bool = True,
    agents: tuple[QualityAgent, ...] | None = None,
    df: pd.DataFrame | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Multi-agent diagnose-and-fix pipeline. Returns `(suggestions, diag)`.

    Five specialists (missing / duplicates / types / formatting / outliers) each
    receive the SAME factual profile but a NARROW system prompt. Their snippets
    go through:
      1) AST sandbox check (`_validate_code`)
      2) **runtime dry-run** against `df` (when supplied) — catches dtype mismatches
         and other errors that AST cannot see (e.g. `TypeError: Invalid value '61.5'
         for dtype 'Int64'`)
      3) ONE repair pass per failure (model rewrites the snippet)
      4) per-agent dedupe → cross-agent dedupe
    `diag.agents` is a per-specialist breakdown (raw, kept, repaired, dropped
    reasons + examples) — the UI uses it for chips and the "why was this dropped"
    expander.
    """
    columns = {c["column"] for c in report.get("column_reports", [])}
    profile_user = "Profile:\n" + json.dumps({
        "file": report.get("file"),
        "rows": report.get("rows"),
        "duplicate_rows": report.get("duplicate_rows"),
        "columns": report.get("column_reports", []),
    }, ensure_ascii=False, default=str)

    use_agents = agents if agents is not None else DOMAIN_AGENTS
    df_sample = _sample_for_dry_run(df) if df is not None else None

    results: list[tuple[list[dict[str, Any]], dict[str, Any]]]
    if parallel and len(use_agents) > 1:
        results = [([], {}) for _ in use_agents]
        with ThreadPoolExecutor(max_workers=min(8, len(use_agents))) as pool:
            futures = {
                pool.submit(_run_agent, a, profile_user, columns, model,
                            allow_repair=allow_repair, df_sample=df_sample): i
                for i, a in enumerate(use_agents)
            }
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
    else:
        results = [_run_agent(a, profile_user, columns, model,
                              allow_repair=allow_repair, df_sample=df_sample)
                   for a in use_agents]

    # Cross-agent dedup — same (column, pandas_code) wins by higher severity, then by agent order.
    chosen: dict[str, dict[str, Any]] = {}
    cross_drop = 0
    for kept, _ in results:
        for sug in kept:
            key = f"{sug.get('column')}::{sug['pandas_code']}"
            cur = chosen.get(key)
            if cur is None:
                chosen[key] = sug
                continue
            cross_drop += 1
            if SEVERITY_RANK.get(sug["severity"], 0) > SEVERITY_RANK.get(cur["severity"], 0):
                chosen[key] = sug

    suggestions = sorted(chosen.values(), key=lambda s: -SEVERITY_RANK.get(s["severity"], 0))

    agent_diags = [d for _, d in results]
    raw_total = sum(d.get("raw", 0) for d in agent_diags)
    dropped_total = _empty_drop_bucket()
    dropped_total["duplicate"] = cross_drop
    examples_total: list[dict[str, Any]] = []
    for d in agent_diags:
        for k, v in (d.get("dropped") or {}).items():
            dropped_total[k] = dropped_total.get(k, 0) + v
        for ex in d.get("dropped_examples", []) or []:
            if len(examples_total) < 6:
                examples_total.append(ex)
    overall_parse_ok = any((d.get("parse_ok") is not False) and d.get("error") is None for d in agent_diags)

    diag: dict[str, Any] = {
        "raw_count": raw_total,
        "kept": len(suggestions),
        "parse_ok": overall_parse_ok,
        "dropped": dropped_total,
        "dropped_examples": examples_total,
        "agents": agent_diags,
    }
    return suggestions, diag


def llm_suggest_fixes(report: dict[str, Any], model: Any) -> list[dict[str, Any]]:
    """Thin wrapper that discards diagnostics — kept for tests / callers that
    only need the cards."""
    return llm_suggest_fixes_diag(report, model)[0]


# --------------------------------------------------------------------------- #
# 3) apply_pandas_fix — runs the LLM snippet in the sandbox + diffs the result
# --------------------------------------------------------------------------- #
def _cell(value: Any) -> Any:
    return None if (value is None or (np.ndim(value) == 0 and pd.isna(value))) else _py(value)


def _coerce_inferred_numeric_columns(df: pd.DataFrame, threshold: float = 0.9) -> pd.DataFrame:
    """Best-effort: convert string/object columns where ≥`threshold` of values parse
    as numbers (after stripping `$ , % kg …`) into real numeric dtype.

    Why this matters: `read_table` deliberately loads everything as strings so the
    profiler can see raw mess. But once an LLM snippet wants to call `.quantile()` /
    `.clip()` / `.mean()` on a 'looks-numeric' column, pandas (now Arrow-backed) blows
    up with `ArrowNotImplementedError: Function 'quantile' has no kernel matching
    input types (large_string)`. Coercing here is harmless for downstream snippets —
    formatting snippets that want strings can still do `.astype('string')`.
    """
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if s.dtype.kind in ("i", "u", "f", "b", "M"):
            continue
        non_null = s.dropna()
        if non_null.empty:
            continue
        num = to_numeric_series(non_null)
        if (num.notna().sum() / len(non_null)) >= threshold:
            out[col] = to_numeric_series(s)
    return out


def apply_pandas_fix(df: pd.DataFrame, code: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Execute an LLM-generated pandas snippet against a copy of `df` and diff the
    result. Returns `(new_df, result)` where `result` always has the same shape so
    the UI can render any change uniformly. Raises `ValueError` if the snippet
    fails validation or execution."""
    code = str(code or "").strip()
    if not code:
        raise ValueError("pandas_code is empty")
    _validate_code(code)

    # Coerce both sides so the diff doesn't drown in "1200" → 1200.0 noise.
    df = _coerce_inferred_numeric_columns(df)
    original = df.copy()
    work = df.copy()
    safe_builtins = {name: getattr(builtins, name) for name in _SAFE_BUILTINS
                     if hasattr(builtins, name)}
    g: dict[str, Any] = {"__builtins__": safe_builtins, "pd": pd, "np": np, "re": re}
    l: dict[str, Any] = {"df": work}

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _exec_with_timeout(code, g, l, timeout=_FIX_TIMEOUT_SECONDS)
    except ValueError:
        raise
    except BaseException as exc:  # noqa: BLE001 - surfaced as a 400 to the UI
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc

    new_df = l.get("df")
    if not isinstance(new_df, pd.DataFrame):
        raise ValueError("after running the snippet, `df` is no longer a pandas DataFrame")

    result = _diff_df(original, new_df)
    result["pandas_code"] = code
    return new_df, result

