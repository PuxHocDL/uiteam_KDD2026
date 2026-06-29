from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def inspect_sqlite_schema(path: Path) -> dict[str, object]:
    with _connect_read_only(path) as conn:
        rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables: list[dict[str, object]] = []
        for name, create_sql in rows:
            tables.append(
                {
                    "name": name,
                    "create_sql": create_sql,
                }
            )
    return {
        "path": str(path),
        "tables": tables,
    }


def profile_database(
    path: Path,
    *,
    max_tables: int = 50,
    sample_rows: int = 3,
    top_values: int = 5,
) -> dict[str, Any]:
    """Profile an entire SQLite database in one call: schema, stats, samples, foreign keys."""
    with _connect_read_only(path) as conn:
        # 1. Get all tables
        table_rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        tables_profile: list[dict[str, Any]] = []
        for table_name, create_sql in table_rows[:max_tables]:
            # Row count
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]

            # Column info via PRAGMA
            col_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            # col_info: (cid, name, type, notnull, dflt_value, pk)

            columns_profile: list[dict[str, Any]] = []
            for _cid, col_name, col_type, _notnull, _dflt, pk in col_info:
                profile: dict[str, Any] = {
                    "name": col_name,
                    "type": col_type or "TEXT",
                    "is_pk": bool(pk),
                }

                if row_count > 0:
                    # Null count
                    null_count = conn.execute(
                        f'SELECT COUNT(*) FROM "{table_name}" WHERE "{col_name}" IS NULL'
                    ).fetchone()[0]
                    profile["null_count"] = null_count

                    # Unique count (capped query for perf)
                    unique_count = conn.execute(
                        f'SELECT COUNT(DISTINCT "{col_name}") FROM "{table_name}"'
                    ).fetchone()[0]
                    profile["unique_count"] = unique_count

                    # Stats for numeric-like columns
                    upper_type = (col_type or "").upper()
                    is_numeric = any(
                        t in upper_type for t in ("INT", "REAL", "FLOAT", "DOUBLE", "NUM", "DECIMAL")
                    )
                    if is_numeric:
                        stats = conn.execute(
                            f'SELECT MIN("{col_name}"), MAX("{col_name}"), '
                            f'AVG("{col_name}") FROM "{table_name}" '
                            f'WHERE "{col_name}" IS NOT NULL'
                        ).fetchone()
                        if stats and stats[0] is not None:
                            profile["min"] = stats[0]
                            profile["max"] = stats[1]
                            profile["mean"] = round(float(stats[2]), 4) if stats[2] is not None else None
                    else:
                        # Top values for text columns
                        top_rows = conn.execute(
                            f'SELECT "{col_name}", COUNT(*) as cnt FROM "{table_name}" '
                            f'WHERE "{col_name}" IS NOT NULL '
                            f'GROUP BY "{col_name}" ORDER BY cnt DESC LIMIT {top_values}'
                        ).fetchall()
                        if top_rows:
                            profile["top_values"] = {str(r[0]): r[1] for r in top_rows}

                columns_profile.append(profile)

            # Sample rows
            sample = conn.execute(
                f'SELECT * FROM "{table_name}" LIMIT {sample_rows}'
            ).fetchall()
            sample_columns = [col_name for _, col_name, *_ in col_info]

            tables_profile.append({
                "name": table_name,
                "create_sql": create_sql,
                "row_count": row_count,
                "columns": columns_profile,
                "sample_columns": sample_columns,
                "sample_rows": [list(r) for r in sample],
            })

        # 2. Foreign keys for all tables
        foreign_keys: list[dict[str, str]] = []
        for table_name, _ in table_rows[:max_tables]:
            fk_rows = conn.execute(f'PRAGMA foreign_key_list("{table_name}")').fetchall()
            for fk in fk_rows:
                # fk: (id, seq, table, from, to, on_update, on_delete, match)
                foreign_keys.append({
                    "from_table": table_name,
                    "from_column": fk[3],
                    "to_table": fk[2],
                    "to_column": fk[4],
                })

    # Check if the DB filename suggests it's a sample
    db_name = path.stem.lower()
    is_sampled = any(tag in db_name for tag in ("_1k", "_sample", "_subset", "_small"))

    result: dict[str, Any] = {
        "path": str(path),
        "table_count": len(tables_profile),
        "tables": tables_profile,
        "foreign_keys": foreign_keys,
    }
    if is_sampled:
        result["WARNING"] = (
            "This database filename suggests it contains SAMPLED data (not the full dataset). "
            "Row counts and aggregation results may be inaccurate. "
            "Check if full data is available in CSV files and use execute_python with pandas instead."
        )
    return result


def execute_read_only_sql(path: Path, sql: str, *, limit: int = 200) -> dict[str, object]:
    normalized_sql = sql.lstrip().lower()
    if not normalized_sql.startswith(("select", "with", "pragma")):
        raise ValueError("Only read-only SQL statements are allowed.")

    with _connect_read_only(path) as conn:
        cursor = conn.execute(sql)
        column_names = [item[0] for item in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)

    truncated = len(rows) > limit
    limited_rows = rows[:limit]
    return {
        "path": str(path),
        "columns": column_names,
        "rows": [list(row) for row in limited_rows],
        "row_count": len(limited_rows),
        "truncated": truncated,
    }
