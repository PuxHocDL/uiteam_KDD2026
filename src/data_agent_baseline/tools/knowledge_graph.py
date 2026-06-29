"""Knowledge Graph builder for task context data.

Builds a compact knowledge graph from the task's data files:
- Parses knowledge.md for entity definitions, metrics, constraints
- Scans CSV/JSON/SQLite schemas for actual column names and types
- Detects shared columns across tables → join paths (FK candidates)
- Samples values to confirm join relationships
- Returns a structured KG summary the agent can use for query planning
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class KGEntity:
    """An entity (table/file) in the knowledge graph."""
    name: str
    source_file: str
    source_type: str  # csv, json, sqlite, doc
    columns: list[dict[str, str]]  # [{name, dtype, description}]
    row_count: int | None = None
    sample_values: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class KGRelationship:
    """A detected relationship between two entities."""
    from_entity: str
    from_column: str
    to_entity: str
    to_column: str
    relationship_type: str  # "shared_column", "fk_candidate", "confirmed_fk"
    confidence: float = 0.0  # 0-1


@dataclass
class KGConstraint:
    """A constraint or convention from knowledge.md."""
    entity: str
    field: str
    rule: str  # e.g. "Thrombosis=1 means most severe"


@dataclass
class KGMetric:
    """A metric/KPI definition from knowledge.md."""
    name: str
    formula: str
    description: str


@dataclass
class KnowledgeGraph:
    """Complete knowledge graph for a task context."""
    entities: list[KGEntity] = field(default_factory=list)
    relationships: list[KGRelationship] = field(default_factory=list)
    constraints: list[KGConstraint] = field(default_factory=list)
    metrics: list[KGMetric] = field(default_factory=list)
    knowledge_summary: str = ""

    def to_compact_text(self) -> str:
        """Render KG as compact text for LLM context injection."""
        lines: list[str] = []

        # Entities
        lines.append("## DATA GRAPH")
        for ent in self.entities:
            col_desc = ", ".join(
                f"{c['name']}({c.get('dtype', '?')})" for c in ent.columns[:30]
            )
            row_info = f" [{ent.row_count} rows]" if ent.row_count is not None else ""
            lines.append(f"\n### {ent.name} ({ent.source_type}: {ent.source_file}){row_info}")
            lines.append(f"  Columns: {col_desc}")

            # Sample values for key columns (max 3 cols, 3 values each)
            if ent.sample_values:
                samples = []
                for col, vals in list(ent.sample_values.items())[:4]:
                    samples.append(f"{col}=[{', '.join(str(v) for v in vals[:3])}]")
                lines.append(f"  Samples: {'; '.join(samples)}")

        # Relationships / Join paths
        if self.relationships:
            lines.append("\n## JOIN PATHS")
            for rel in self.relationships:
                conf = f" (confidence={rel.confidence:.0%})" if rel.confidence < 1.0 else ""
                lines.append(
                    f"  {rel.from_entity}.{rel.from_column} ──{rel.relationship_type}── "
                    f"{rel.to_entity}.{rel.to_column}{conf}"
                )

        # Constraints
        if self.constraints:
            lines.append("\n## CONSTRAINTS")
            for c in self.constraints:
                lines.append(f"  {c.entity}.{c.field}: {c.rule}")

        # Metrics
        if self.metrics:
            lines.append("\n## METRICS / KPIs")
            for m in self.metrics:
                lines.append(f"  {m.name}: {m.formula}")

        # Knowledge summary
        if self.knowledge_summary:
            lines.append(f"\n## KNOWLEDGE NOTES\n{self.knowledge_summary}")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [
                {
                    "name": e.name,
                    "source_file": e.source_file,
                    "source_type": e.source_type,
                    "columns": e.columns,
                    "row_count": e.row_count,
                    "sample_values": {k: v[:3] for k, v in e.sample_values.items()},
                }
                for e in self.entities
            ],
            "relationships": [
                {
                    "from": f"{r.from_entity}.{r.from_column}",
                    "to": f"{r.to_entity}.{r.to_column}",
                    "type": r.relationship_type,
                    "confidence": round(r.confidence, 2),
                }
                for r in self.relationships
            ],
            "constraints": [
                {"entity": c.entity, "field": c.field, "rule": c.rule}
                for c in self.constraints
            ],
            "metrics": [
                {"name": m.name, "formula": m.formula, "description": m.description}
                for m in self.metrics
            ],
            "compact_text": self.to_compact_text(),
        }


# ---------------------------------------------------------------------------
# Knowledge.md parser
# ---------------------------------------------------------------------------

def _parse_knowledge_md(text: str) -> tuple[
    list[KGConstraint], list[KGMetric], str, dict[str, list[dict[str, str]]]
]:
    """Extract entities, constraints, metrics from knowledge.md."""
    constraints: list[KGConstraint] = []
    metrics: list[KGMetric] = []
    entity_fields: dict[str, list[dict[str, str]]] = {}

    # Extract entity sections: ### EntityName
    current_entity = ""
    lines = text.split("\n")
    summary_parts: list[str] = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Detect entity headers: ### EntityName or ### Entity Name
        entity_match = re.match(r"^###\s+(.+?)(?:\s*$)", line)
        if entity_match:
            entity_name = entity_match.group(1).strip()
            # Skip non-entity headers like "Key Performance Indicators"
            if not any(kw in entity_name.lower() for kw in (
                "kpi", "indicator", "metric", "constraint", "convention",
                "filter", "temporal", "unit", "example", "use case",
                "business", "boundary",
            )):
                current_entity = entity_name

        # Detect field definitions: - **FieldName (type):** Description
        field_match = re.match(
            r"^-\s+\*\*(.+?)\s*(?:\(([^)]+)\))?\s*(?::?\*\*)\s*:?\s*(.*)",
            line,
        )
        if field_match and current_entity:
            field_name = field_match.group(1).strip().rstrip("*")
            field_type = (field_match.group(2) or "").strip()
            field_desc = (field_match.group(3) or "").strip()

            if current_entity not in entity_fields:
                entity_fields[current_entity] = []
            entity_fields[current_entity].append({
                "name": field_name,
                "dtype": field_type,
                "description": field_desc,
            })

            # Extract constraints from field descriptions
            desc_lower = field_desc.lower()
            if any(kw in desc_lower for kw in (
                "above", "below", "greater", "less", "normal range",
                "indicates", "denoted as", "with '", "filter",
            )):
                constraints.append(KGConstraint(
                    entity=current_entity,
                    field=field_name,
                    rule=field_desc[:200],
                ))

        # Detect metric definitions: - **MetricName:**
        metric_match = re.match(r"^-\s+\*\*(.+?)\*\*\s*:?\s*(.*)", line)
        if metric_match and "formula" not in line.lower():
            # Look for formula in next lines
            formula = ""
            description = metric_match.group(2).strip()
            j = i + 1
            while j < len(lines) and j < i + 5:
                next_line = lines[j].strip()
                if "Formula" in next_line or "formula" in next_line:
                    formula_match = re.search(r"[`$](.+?)[`$]|:\s*(.+)", next_line)
                    if formula_match:
                        formula = (formula_match.group(1) or formula_match.group(2) or "").strip()
                if next_line.startswith("- **Description") or "Description" in next_line:
                    desc_match = re.search(r":\s*(.+)", next_line)
                    if desc_match:
                        description = desc_match.group(1).strip()
                j += 1

            if formula and not any(
                kw in metric_match.group(1).lower() for kw in (
                    "id", "name", "date", "code", "type", "sex", "gender",
                )
            ):
                metrics.append(KGMetric(
                    name=metric_match.group(1).strip(),
                    formula=formula[:300],
                    description=description[:200],
                ))

        # Collect filtering criteria and conventions for summary
        if any(kw in line.lower() for kw in (
            "filter", "convention", "temporal", "boundary", "unit",
        )):
            summary_parts.append(line)

        i += 1

    knowledge_summary = "\n".join(summary_parts[:10])
    return constraints, metrics, knowledge_summary, entity_fields


# ---------------------------------------------------------------------------
# Schema extractors
# ---------------------------------------------------------------------------

def _extract_csv_entity(rel_path: str, full_path: Path) -> KGEntity | None:
    """Extract entity from a CSV file."""
    try:
        with full_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return None

            # Count rows (up to 100k for perf)
            row_count = 0
            sample_values: dict[str, list[str]] = {col: [] for col in header[:20]}
            for row in reader:
                row_count += 1
                if row_count <= 5:
                    for idx, col in enumerate(header[:20]):
                        if idx < len(row) and row[idx].strip():
                            sample_values[col].append(row[idx].strip()[:100])
                if row_count >= 100_000:
                    break

            entity_name = Path(rel_path).stem
            columns = [{"name": col, "dtype": "text"} for col in header]

            return KGEntity(
                name=entity_name,
                source_file=rel_path,
                source_type="csv",
                columns=columns,
                row_count=row_count,
                sample_values={k: v for k, v in sample_values.items() if v},
            )
    except Exception:
        return None


def _extract_json_entity(rel_path: str, full_path: Path) -> KGEntity | None:
    """Extract entity from a JSON file."""
    try:
        text = full_path.read_text(errors="replace")
        data = json.loads(text)

        # Handle both array and object with nested arrays
        records: list[dict] = []
        if isinstance(data, list):
            records = [r for r in data[:100] if isinstance(r, dict)]
        elif isinstance(data, dict):
            # Find the first list-of-dicts value
            for key, val in data.items():
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    records = val[:100]
                    break
            if not records:
                records = [data]

        if not records:
            return None

        # Extract columns from first few records
        all_keys: dict[str, str] = {}
        for rec in records[:20]:
            for k, v in rec.items():
                if k not in all_keys:
                    if isinstance(v, (int, float)):
                        all_keys[k] = "number"
                    elif isinstance(v, bool):
                        all_keys[k] = "boolean"
                    else:
                        all_keys[k] = "text"

        # Sample values
        sample_values: dict[str, list[str]] = {}
        for rec in records[:5]:
            for k, v in rec.items():
                if k not in sample_values:
                    sample_values[k] = []
                if v is not None and len(sample_values[k]) < 3:
                    sample_values[k].append(str(v)[:100])

        entity_name = Path(rel_path).stem
        total = len(data) if isinstance(data, list) else len(records)

        return KGEntity(
            name=entity_name,
            source_file=rel_path,
            source_type="json",
            columns=[{"name": k, "dtype": v} for k, v in all_keys.items()],
            row_count=total,
            sample_values={k: v for k, v in sample_values.items() if v},
        )
    except Exception:
        return None


def _extract_sqlite_entities(rel_path: str, full_path: Path) -> list[KGEntity]:
    """Extract entities from all tables in a SQLite database."""
    entities: list[KGEntity] = []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{full_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        for table_name in tables[:25]:
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            cols = [
                {"name": row[1], "dtype": row[2] or "text"}
                for row in cursor.fetchall()
            ]

            cursor.execute(f"SELECT COUNT(*) FROM '{table_name}'")
            row_count = cursor.fetchone()[0]

            # Sample values
            sample_values: dict[str, list[str]] = {}
            col_names = [c["name"] for c in cols[:20]]
            try:
                cursor.execute(f"SELECT * FROM '{table_name}' LIMIT 5")
                for row in cursor.fetchall():
                    for idx, col in enumerate(col_names):
                        if idx < len(row) and row[idx] is not None:
                            if col not in sample_values:
                                sample_values[col] = []
                            if len(sample_values[col]) < 3:
                                sample_values[col].append(str(row[idx])[:100])
            except Exception:
                pass

            entities.append(KGEntity(
                name=table_name,
                source_file=rel_path,
                source_type="sqlite",
                columns=cols,
                row_count=row_count,
                sample_values=sample_values,
            ))

    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()
    return entities


# ---------------------------------------------------------------------------
# Relationship detection
# ---------------------------------------------------------------------------

_BARE_PK_NAMES = {"id", "rowid", "pk", "_rowid_"}


def _is_bare_pk(col_name: str) -> bool:
    """A standalone primary-key column ('id'), not a reference to another table."""
    return col_name.lower().strip() in _BARE_PK_NAMES


def _resolve_source(context_root: Path | None, source_file: str) -> Path | None:
    if context_root is None or not source_file:
        return None
    path = Path(context_root) / source_file
    return path if path.exists() else None


def _sqlite_declared_fks(full_path: Path, table: str) -> list[tuple[str, str, str]]:
    """Declared foreign keys for a table → [(from_col, to_table, to_col)]."""
    out: list[tuple[str, str, str]] = []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{full_path}?mode=ro", uri=True)
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")'):
            # row = (id, seq, table, from, to, on_update, on_delete, match)
            out.append((str(row[3]), str(row[2]), str(row[4] or "id")))
    except Exception:  # noqa: BLE001 - FK introspection is best-effort
        pass
    finally:
        if conn is not None:
            conn.close()
    return out


def _column_values(
    context_root: Path | None, ent: KGEntity, col: str, *, limit: int = 20000
) -> set[str] | None:
    """Distinct string values of a column from the REAL source (sqlite/csv), bounded.

    Returns ``None`` when the source is unreachable (so callers fall back to the
    name-based heuristic instead of treating "no data" as "no overlap").
    """
    src = _resolve_source(context_root, ent.source_file)
    if src is None:
        return None
    try:
        if ent.source_type == "sqlite":
            conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                cur = conn.execute(
                    f'SELECT DISTINCT "{col}" FROM "{ent.name}" '
                    f'WHERE "{col}" IS NOT NULL LIMIT ?',
                    (limit,),
                )
                return {str(r[0]) for r in cur.fetchall()}
            finally:
                conn.close()
        if ent.source_type == "csv":
            values: set[str] = set()
            with open(src, newline="", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                for i, row in enumerate(reader):
                    if i >= limit:
                        break
                    val = row.get(col)
                    if val not in (None, ""):
                        values.add(str(val))
            return values
    except Exception:  # noqa: BLE001 - value lookup is best-effort
        return None
    return None


def _detect_relationships(
    entities: list[KGEntity], context_root: Path | None = None
) -> list[KGRelationship]:
    """Detect join paths between entities.

    Signal priority (strongest first):
      1. Declared foreign keys (PRAGMA foreign_key_list) → confirmed_fk 1.0.
      2. ``<entity>_id`` → ``<entity>.id`` fuzzy match, verified by real value
         containment when the source is reachable.
      3. Shared column names → weak join-key hints.

    Two bare primary keys ('id'/'id') are NEVER linked: their values coincidentally
    start 1,2,3 and the old sample-overlap rule mislabelled every id↔id pair as a
    confirmed_fk 1.0, burying the real foreign key.
    """
    relationships: list[KGRelationship] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(fe: str, fc: str, te: str, tc: str, rel_type: str, confidence: float) -> None:
        key = (fe, fc, te, tc)
        rev_key = (te, tc, fe, fc)
        if key in seen or rev_key in seen:
            return
        seen.add(key)
        relationships.append(KGRelationship(
            from_entity=fe, from_column=fc, to_entity=te, to_column=tc,
            relationship_type=rel_type, confidence=confidence,
        ))

    ent_by_name: dict[str, KGEntity] = {e.name.lower(): e for e in entities}

    # 1. Declared foreign keys — the ground truth when the DB defines them.
    for ent in entities:
        if ent.source_type != "sqlite":
            continue
        src = _resolve_source(context_root, ent.source_file)
        if src is None:
            continue
        for from_col, to_table, to_col in _sqlite_declared_fks(src, ent.name):
            target = ent_by_name.get(to_table.lower())
            if target is not None:
                add(ent.name, from_col, target.name, to_col, "confirmed_fk", 1.0)

    # 2. Fuzzy '<entity>_id' → '<entity>.id', verified against real values.
    id_pattern = re.compile(r"^(.+?)(?:_id|id|_key|_code)$", re.IGNORECASE)
    for ent in entities:
        for col in ent.columns:
            m = id_pattern.match(col["name"])
            if not m:
                continue
            ref_name = m.group(1).lower().replace("_", "")
            if not ref_name:
                continue
            for other_name, other_ent in ent_by_name.items():
                if other_ent.name == ent.name:
                    continue
                other_clean = other_name.replace("_", "")
                if not (ref_name == other_clean
                        or ref_name + "s" == other_clean
                        or ref_name == other_clean + "s"):
                    continue
                target_col = "id"
                for oc in other_ent.columns:
                    if oc["name"].lower() in ("id", f"{other_clean}_id", f"{ref_name}_id"):
                        target_col = oc["name"]
                        break

                rel_type, confidence = "fk_candidate", 0.7
                child = _column_values(context_root, ent, col["name"], limit=500)
                parent = _column_values(context_root, other_ent, target_col, limit=20000)
                if child and parent:
                    frac = len(child & parent) / len(child)
                    if frac >= 0.8:
                        rel_type, confidence = "confirmed_fk", round(0.9 + 0.1 * frac, 2)
                    elif frac >= 0.3:
                        rel_type, confidence = "fk_candidate", round(0.5 + 0.3 * frac, 2)
                    else:
                        rel_type, confidence = "shared_column", 0.2
                add(ent.name, col["name"], other_ent.name, target_col, rel_type, confidence)

    # 3. Shared exact column names → weak join-key hints (never confirmed_fk).
    col_to_entities: dict[str, list[tuple[KGEntity, str]]] = {}
    for ent in entities:
        for col in ent.columns:
            col_to_entities.setdefault(col["name"].lower().strip(), []).append((ent, col["name"]))

    for col_lower, ent_col_pairs in col_to_entities.items():
        if len(ent_col_pairs) < 2:
            continue
        fk_like = (
            any(col_lower.endswith(suffix) for suffix in ("_id", "_code", "_key"))
            and not _is_bare_pk(col_lower)
        )
        for i, (ent_a, col_a) in enumerate(ent_col_pairs):
            for ent_b, col_b in ent_col_pairs[i + 1:]:
                if ent_a.name == ent_b.name:
                    continue
                # Two standalone primary keys are not a foreign key of each other.
                if _is_bare_pk(col_a) and _is_bare_pk(col_b):
                    continue
                add(ent_a.name, col_a, ent_b.name, col_b,
                    "shared_column", 0.5 if fk_like else 0.3)

    # Sort by confidence descending
    relationships.sort(key=lambda r: -r.confidence)
    return relationships


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_knowledge_graph(task: PublicTask) -> KnowledgeGraph:
    """Build a knowledge graph from all data files in the task context."""
    context_root = task.context_dir.resolve()
    kg = KnowledgeGraph()

    # 1. Parse knowledge.md if it exists
    knowledge_path = context_root / "knowledge.md"
    entity_fields_from_knowledge: dict[str, list[dict[str, str]]] = {}
    if knowledge_path.exists():
        try:
            text = knowledge_path.read_text(errors="replace")
            constraints, metrics, summary, entity_fields_from_knowledge = _parse_knowledge_md(text)
            kg.constraints = constraints
            kg.metrics = metrics
            kg.knowledge_summary = summary
        except Exception:
            pass

    # 2. Scan all data files
    for child in sorted(context_root.rglob("*")):
        if not child.is_file():
            continue
        if child.name.lower() == "gold.csv":
            continue
        # Skip hidden/cache dirs — notably our own .kg/graph.db cache, which would
        # otherwise be re-ingested as entities (kg_nodes, kg_edges, …) on a rebuild.
        rel_parts = child.relative_to(context_root).parts
        if any(part.startswith(".") for part in rel_parts[:-1]):
            continue

        rel = child.relative_to(context_root).as_posix()
        suffix = child.suffix.lower()

        if suffix == ".csv":
            ent = _extract_csv_entity(rel, child)
            if ent:
                kg.entities.append(ent)
        elif suffix == ".json":
            ent = _extract_json_entity(rel, child)
            if ent:
                kg.entities.append(ent)
        elif suffix in {".sqlite", ".db", ".sqlite3"}:
            sqlite_ents = _extract_sqlite_entities(rel, child)
            kg.entities.extend(sqlite_ents)

    # 3. Enrich entities with knowledge.md field descriptions
    for ent in kg.entities:
        # Try to match entity to knowledge.md section
        ent_name_lower = ent.name.lower().replace("_", "")
        for kg_entity_name, kg_fields in entity_fields_from_knowledge.items():
            kg_name_lower = kg_entity_name.lower().replace(" ", "").replace("_", "")
            if ent_name_lower == kg_name_lower or ent_name_lower in kg_name_lower or kg_name_lower in ent_name_lower:
                # Merge descriptions from knowledge.md
                kg_field_map = {
                    f["name"].lower().replace(" ", "_"): f
                    for f in kg_fields
                }
                for col in ent.columns:
                    col_key = col["name"].lower().replace(" ", "_")
                    if col_key in kg_field_map:
                        kf = kg_field_map[col_key]
                        if kf.get("description"):
                            col["description"] = kf["description"][:150]
                        if kf.get("dtype") and col.get("dtype") in ("text", "?"):
                            col["dtype"] = kf["dtype"]

    # 4. Detect relationships (pass context_root so FKs can be verified against real data)
    kg.relationships = _detect_relationships(kg.entities, context_root)

    return kg
