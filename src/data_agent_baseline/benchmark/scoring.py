"""Scoring utilities for DABench predictions against gold answers."""

from __future__ import annotations

import csv
import json
import pandas as pd
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import timezone


@dataclass
class TaskScore:
    task_id: str
    recall: float
    score: float
    gold_rows: int
    gold_cols: int
    pred_rows: int
    pred_cols: int
    matched_cols: int
    difficulty: str = ""
    error: str | None = None
    e2e_elapsed_seconds: float | None = None
    num_steps: int | None = None
    run_error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    model_name: str | None = None

def _normalize(val: str) -> str:
    if val is None:
        return ""
    val = str(val).strip()
    
    val_lower = val.lower()
    if not val_lower or val_lower in ("null", "none", "nan", "nat", "<na>"):
        return ""
    
    # 2. Try Numeric
    try:
        d = Decimal(val)
        return str(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        pass
    
    # 3. Try DateTime
    # Filter quickly to avoid parsing regular strings or simple numbers as dates
    if len(val) >= 6 and any(c in val for c in ('-', '/', ':', ',')):
        try:
            dt = pd.to_datetime(val, errors='coerce')
            if pd.notna(dt):
                # Check for timezone
                if dt.tzinfo is not None:
                    dt = dt.tz_convert('UTC')
                    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    # Check if date only
                    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
                        return dt.strftime('%Y-%m-%d')
                    else:
                        return dt.strftime('%Y-%m-%dT%H:%M:%S')
        except Exception:
            pass

    # 4. String fallback
    # Remove leading/trailing whitespace and \r\n
    val = val.replace('\r', '').replace('\n', '')
    return val


def _read_csv_cols(path: Path) -> tuple[list[tuple[str, ...]], int]:
    """Returns a tuple of (list of column signatures, number of data rows)."""
    for enc in ("utf-8", "latin-1"):
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.reader(f)
                rows = list(reader)
                if not rows:
                    return [], 0
                if len(rows) <= 1:
                    return [tuple() for _ in rows[0]] if rows else [], 0
                
                num_cols = len(rows[0])
                num_rows = len(rows) - 1
                columns = [[] for _ in range(num_cols)]
                for row in rows[1:]:
                    for i in range(num_cols):
                        val = row[i] if i < len(row) else ""
                        columns[i].append(_normalize(val))
                
                # Column signature: tuple of sorted values
                return [tuple(sorted(col)) for col in columns], num_rows
        except UnicodeDecodeError:
            continue
            
    # last resort
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
        if not rows:
            return [], 0
        if len(rows) <= 1:
            return [tuple() for _ in rows[0]] if rows else [], 0
        
        num_cols = len(rows[0])
        num_rows = len(rows) - 1
        columns = [[] for _ in range(num_cols)]
        for row in rows[1:]:
            for i in range(num_cols):
                val = row[i] if i < len(row) else ""
                columns[i].append(_normalize(val))
        return [tuple(sorted(col)) for col in columns], num_rows


def score_task(
    gold_path: Path,
    pred_path: Path,
    lam: float = 0.1,
) -> TaskScore:
    """Score a single task prediction against gold using matching logic."""
    task_id = gold_path.parent.name

    if not pred_path.exists():
        return TaskScore(
            task_id=task_id, recall=0.0, score=0.0,
            gold_rows=0, gold_cols=0, pred_rows=0, pred_cols=0,
            matched_cols=0, error="prediction.csv not found",
        )

    try:
        gold_cols_sigs, gold_rows_cnt = _read_csv_cols(gold_path)
        pred_cols_sigs, pred_rows_cnt = _read_csv_cols(pred_path)
    except Exception as exc:
        return TaskScore(
            task_id=task_id, recall=0.0, score=0.0,
            gold_rows=0, gold_cols=0, pred_rows=0, pred_cols=0,
            matched_cols=0, error=str(exc),
        )

    gold_cols_cnt = len(gold_cols_sigs)
    pred_cols_cnt = len(pred_cols_sigs)

    if gold_cols_cnt == 0 or pred_cols_cnt == 0:
        return TaskScore(
            task_id=task_id, recall=0.0, score=0.0,
            gold_rows=gold_rows_cnt, gold_cols=gold_cols_cnt, 
            pred_rows=pred_rows_cnt, pred_cols=pred_cols_cnt,
            matched_cols=0, error="empty CSV",
        )

    # 1. Matches Column Signatures between Gold and Prediction
    gold_counts = Counter(gold_cols_sigs)
    pred_counts = Counter(pred_cols_sigs)

    matched_cols = 0
    for sig, count in gold_counts.items():
        matched_cols += min(count, pred_counts.get(sig, 0))

    # 2. Recall = Matched Columns / Gold Columns
    recall = matched_cols / gold_cols_cnt
    
    # 3. Extra Columns = Number of columns in prediction that do not match gold
    extra_cols = max(0, pred_cols_cnt - matched_cols)
    
    # 4. Final Score with light penalty
    score = recall - lam * (extra_cols / pred_cols_cnt)
    score = max(0.0, score) # Lower bound constraint

    return TaskScore(
        task_id=task_id, recall=recall, score=round(score, 4),
        gold_rows=gold_rows_cnt, gold_cols=gold_cols_cnt,
        pred_rows=pred_rows_cnt, pred_cols=pred_cols_cnt,
        matched_cols=matched_cols,
    )


def _read_task_difficulty(task_id: str, input_dir: Path | None) -> str:
    """Read difficulty from task.json in the input directory."""
    if input_dir is None:
        return ""
    task_json = input_dir / task_id / "task.json"
    if not task_json.exists():
        return ""
    try:
        data = json.loads(task_json.read_text(encoding="utf-8"))
        return data.get("difficulty", "")
    except Exception:
        return ""


def score_run(
    run_dir: Path,
    gold_dir: Path,
    lam: float = 0.1,
    input_dir: Path | None = None,
) -> list[TaskScore]:
    """Score all tasks in a run directory against gold."""
    results: list[TaskScore] = []
    task_dirs = sorted(run_dir.glob("task_*"))
    for task_path in task_dirs:
        task_id = task_path.name
        gold_path = gold_dir / task_id / "gold.csv"
        pred_path = task_path / "prediction.csv"
        if not gold_path.exists():
            continue
        ts = score_task(gold_path, pred_path, lam=lam)
        ts.difficulty = _read_task_difficulty(task_id, input_dir)
        
        trace_path = task_path / "trace.json"
        if trace_path.exists():
            try:
                with open(trace_path, encoding="utf-8") as f:
                    trace_data = json.load(f)
                    ts.e2e_elapsed_seconds = trace_data.get("e2e_elapsed_seconds")
                    steps = trace_data.get("steps")
                    ts.num_steps = len(steps) if isinstance(steps, list) else None
                    ts.run_error = trace_data.get("failure_reason")
                    token_usage = trace_data.get("token_usage")
                    if isinstance(token_usage, dict):
                        ts.prompt_tokens = token_usage.get("prompt_tokens")
                        ts.completion_tokens = token_usage.get("completion_tokens")
                        ts.total_tokens = token_usage.get("total_tokens")
                        ts.estimated_cost_usd = token_usage.get("estimated_cost_usd")
                        ts.model_name = token_usage.get("model")
            except Exception:
                pass
                
        results.append(ts)
    return results
