"""Explore (§12.2c) — deterministic "data-scientist" statistics for CSV/Excel.

One pure function, no LLM required (fast, free, unit-testable):
  • profile_statistics(path|df) → per-column distributions (histogram / top categories),
    a numeric correlation matrix, missingness, and suggested scatter pairs.

All values are plain Python types so the result serialises straight to JSON; the UI
("Explore" view) renders the histograms, the correlation heatmap and the missingness bars.
"""
from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from data_agent_baseline.tools.data_quality import excel_sheets, read_table, sqlite_tables, to_numeric_series

MAX_COLUMNS = 80          # cap columns profiled (wide files)
MAX_CORR_COLUMNS = 25     # cap the correlation matrix size
TOP_CATEGORIES = 10
MAX_HIST_BINS = 20
NUMERIC_RATIO = 0.9       # a column is "numeric" when ≥90% of values parse as numbers


def _f(value: Any) -> float | None:
    """Round to a JSON-friendly float, or None for NaN/inf/non-numeric."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else round(v, 6)


def _short(text: Any, limit: int = 60) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _classify(non_null: pd.Series) -> tuple[str, pd.Series | None]:
    """Decide a column kind and return the coerced (non-null) values where relevant."""
    if len(non_null) == 0:
        return "empty", None
    num = to_numeric_series(non_null)
    if num.notna().mean() >= NUMERIC_RATIO:
        return "numeric", num.dropna()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dt = pd.to_datetime(non_null, errors="coerce", format="mixed")
    if dt.notna().mean() >= NUMERIC_RATIO:
        return "datetime", dt.dropna()
    return "categorical", None


def _numeric_stats(values: pd.Series) -> dict[str, Any]:
    v = values.astype(float)
    q1, q3 = float(v.quantile(0.25)), float(v.quantile(0.75))
    iqr = q3 - q1
    outliers = int(((v < q1 - 1.5 * iqr) | (v > q3 + 1.5 * iqr)).sum()) if iqr > 0 else 0
    n_bins = min(MAX_HIST_BINS, max(5, int(math.sqrt(len(v))) or 5))
    counts, edges = np.histogram(v.to_numpy(), bins=n_bins)
    return {
        "min": _f(v.min()), "max": _f(v.max()), "mean": _f(v.mean()),
        "median": _f(v.median()), "std": _f(v.std()), "q1": _f(q1), "q3": _f(q3),
        "outliers": outliers,
        "histogram": {"edges": [_f(e) for e in edges], "counts": [int(c) for c in counts]},
    }


def profile_statistics_df(df: pd.DataFrame, filename: str = "data",
                          sheet: str | None = None) -> dict[str, Any]:
    n_total = int(len(df))
    columns = list(df.columns)[:MAX_COLUMNS]

    column_stats: list[dict[str, Any]] = []
    missingness: list[dict[str, Any]] = []
    numeric_frames: dict[str, pd.Series] = {}  # full-length numeric series for correlation

    for col in columns:
        series = df[col]
        non_null = series.dropna()
        miss = n_total - int(len(non_null))
        miss_pct = (miss / n_total) if n_total else 0.0
        missingness.append({"column": str(col), "missing": miss, "missing_pct": round(miss_pct, 4)})

        kind, coerced = _classify(non_null)
        stat: dict[str, Any] = {
            "column": str(col), "kind": kind, "missing": miss,
            "missing_pct": round(miss_pct, 4),
            "unique": int(non_null.nunique()) if len(non_null) else 0,
        }
        if kind == "numeric" and coerced is not None and len(coerced):
            stat.update(_numeric_stats(coerced))
            numeric_frames[str(col)] = to_numeric_series(series)  # aligned, NaN-padded
        elif kind == "datetime" and coerced is not None and len(coerced):
            stat["min"] = str(coerced.min())
            stat["max"] = str(coerced.max())
        else:
            vc = non_null.astype(str).value_counts().head(TOP_CATEGORIES)
            stat["top"] = [{"value": _short(k), "count": int(c)} for k, c in vc.items()]
        column_stats.append(stat)

    # numeric correlation matrix + scatter-pair suggestions
    correlation: dict[str, Any] | None = None
    scatter: list[dict[str, Any]] = []
    num_cols = list(numeric_frames)[:MAX_CORR_COLUMNS]
    if len(num_cols) >= 2:
        matrix = pd.DataFrame({c: numeric_frames[c] for c in num_cols}).corr(method="pearson")
        correlation = {
            "columns": num_cols,
            "matrix": [[_f(matrix.iloc[i, j]) for j in range(len(num_cols))] for i in range(len(num_cols))],
        }
        for i in range(len(num_cols)):
            for j in range(i + 1, len(num_cols)):
                r = matrix.iloc[i, j]
                if pd.notna(r) and abs(r) >= 0.3:
                    scatter.append({"x": num_cols[i], "y": num_cols[j], "r": _f(r)})
        scatter.sort(key=lambda d: -abs(d["r"] or 0.0))
        scatter = scatter[:6]

    return {
        "file": filename,
        "sheet": sheet,
        "rows": n_total,
        "columns": int(df.shape[1]),
        "columns_truncated": len(df.columns) > len(columns),
        "numeric_columns": len(numeric_frames),
        "column_stats": column_stats,
        "correlation": correlation,
        "scatter_suggestions": scatter,
        "missingness": missingness,
    }


def profile_statistics(path: Path, sheet: str | None = None,
                       table: str | None = None) -> dict[str, Any]:
    path = Path(path)
    df = read_table(path, sheet=sheet, table=table)
    label = f"{path.name}#{table}" if table else path.name
    result = profile_statistics_df(df, filename=label, sheet=sheet)
    sheets = excel_sheets(path)
    if sheets:
        result["sheets"] = sheets
    tables = sqlite_tables(path)
    if tables:
        # The UI uses this to render a table picker for .db files (like sheet picker for Excel).
        result["tables"] = tables
        result["table"] = table or tables[0]
    return result
