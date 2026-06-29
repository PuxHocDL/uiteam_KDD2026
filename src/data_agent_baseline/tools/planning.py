"""Two reasoning-primitive tools: classify a question and plan a task.

Both are deterministic (no LLM, no API key) and grounded in the real workspace:

- `classify_question` recommends which architecture fits — react / dragin / multi
  / hybrid_b — from the question wording and the data shape (how many structured
  families, whether documents are present). It mirrors the routing heuristics so
  the agent (or a human) can see *why* a path is suggested.

- `plan_task` produces a step plan grounded in `source_map`: it locates where the
  question's entities actually live (which table or which document) and lays out
  locate → join → compute → validate, naming the right tool for each source. That
  turns "I'll go probe files" into "subsidiaries are in the PDF → read_pdf".

Kept deliberately independent of the run/server layer (no imports upward) so the
tools layer stays self-contained.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from data_agent_baseline.tools.source_map import _candidate_entities, locate_terms

__all__ = ["classify_question", "plan_task"]

_DOC_SUFFIXES = {".pdf", ".md", ".txt", ".log", ".text"}

_SOLUTION_FIT = {
    "react": "Fast Reason→Act loop — best for direct lookups, filters and single-table aggregations.",
    "dragin": "Adaptive retrieval — pulls extra context when unsure; best for document- or knowledge-heavy questions.",
    "multi": "Planner drafts the steps, Analyst executes — best for complex, multi-step or cross-source work.",
    "hybrid_b": "Routes by difficulty — a light path for easy asks, deeper reasoning for hard ones.",
}

_MULTI_WORDS = (
    "for each", "compare", "across", "combine", " join", "relationship between",
    "correlat", "trend", "group by", "multiple", "and then", "breakdown", "per ",
)
_DOC_WORDS = (
    "why", "explain", "according to", "in the document", "in the report", "summar",
    "describe", "who is", "definition", "reason for", "mentioned", "states",
)
_STOPWORDS = {
    "what", "which", "who", "whom", "whose", "when", "where", "how", "many",
    "much", "the", "and", "for", "are", "was", "were", "that", "this", "with",
    "from", "into", "their", "there", "list", "show", "give", "find", "each",
    "all", "have", "has", "does", "did", "per", "between", "across", "name",
}
# Interrogatives that the proper-noun scanner may capture when they start a
# sentence ("What ...") — never worth locating.
_INTERROGATIVES = {"what", "which", "who", "whom", "whose", "when", "where", "how", "why"}


def _scan_families(context_dir: Path) -> dict[str, Any]:
    families: set[str] = set()
    docs: list[str] = []
    structured: list[str] = []
    for child in Path(context_dir).rglob("*"):
        if not child.is_file() or child.name.lower() == "gold.csv":
            continue
        if child.parent.name == ".kg":
            continue
        suffix = child.suffix.lower()
        rel = child.name
        if suffix == ".csv":
            families.add("csv"); structured.append(rel)
        elif suffix == ".json":
            families.add("json"); structured.append(rel)
        elif suffix in {".db", ".sqlite", ".sqlite3"}:
            families.add("db"); structured.append(rel)
        elif suffix in _DOC_SUFFIXES:
            docs.append(rel)
    return {
        "structured_families": sorted(families),
        "structured_count": len(families),
        "documents": docs,
        "structured_files": structured,
    }


def classify_question(context_dir: Path, question: str, difficulty: str | None = None) -> dict[str, Any]:
    shape = _scan_families(Path(context_dir))
    ql = question.lower()
    signals: list[str] = []

    has_docs = bool(shape["documents"])
    multi_family = shape["structured_count"] >= 2
    multi_words = [w.strip() for w in _MULTI_WORDS if w in ql]
    doc_words = [w for w in _DOC_WORDS if w in ql]

    if multi_words:
        signals.append("multi_step_wording: " + ", ".join(multi_words[:3]))
    if multi_family:
        signals.append(f"{shape['structured_count']} structured families")
    if has_docs:
        signals.append("documents present: " + ", ".join(shape["documents"][:3]))
    if doc_words:
        signals.append("document/explanatory wording: " + ", ".join(doc_words[:3]))

    diff = (difficulty or "").lower()
    if diff in {"hard", "extreme"}:
        signals.append(f"difficulty={diff}")

    # Decision: documents/explanatory → dragin; multi-step or multi-source → multi;
    # hard/extreme without a clear single path → hybrid_b; otherwise react.
    if (has_docs and doc_words) or (has_docs and not multi_family and len(ql.split()) > 12):
        recommended = "dragin"
        reasoning = "the answer likely depends on a document, which needs adaptive retrieval/reading"
    elif multi_words or multi_family:
        recommended = "multi"
        reasoning = "the question spans multiple steps or data sources, so plan-then-execute is safer"
    elif diff in {"hard", "extreme"}:
        recommended = "hybrid_b"
        reasoning = "a hard task with no single obvious path benefits from difficulty-based routing"
    else:
        recommended = "react"
        reasoning = "a direct lookup/aggregation over one source — the fast Reason→Act loop fits"

    alternatives = [
        {"id": sid, "fit": fit}
        for sid, fit in _SOLUTION_FIT.items()
        if sid != recommended
    ]
    return {
        "recommended": recommended,
        "reasoning": reasoning,
        "fit": _SOLUTION_FIT[recommended],
        "signals": signals,
        "data_shape": shape,
        "alternatives": alternatives,
    }


def _key_terms(question: str, *, max_terms: int = 6) -> list[str]:
    """Pull the entities/nouns worth locating: proper nouns first, then content words."""
    terms: list[str] = []
    for phrase in _candidate_entities(question, top_n=8):
        if phrase.lower() in _INTERROGATIVES:
            continue
        if phrase.lower() not in {t.lower() for t in terms}:
            terms.append(phrase)
    for word in re.findall(r"[A-Za-z][A-Za-z_]{3,}", question):
        wl = word.lower()
        if wl in _STOPWORDS:
            continue
        if wl not in {t.lower() for t in terms}:
            terms.append(word)
        if len(terms) >= max_terms:
            break
    return terms[:max_terms]


def plan_task(context_dir: Path, question: str) -> dict[str, Any]:
    terms = _key_terms(question)
    located = locate_terms(Path(context_dir), terms) if terms else {}

    in_tables: list[str] = []
    in_docs: list[str] = []
    nowhere: list[str] = []
    doc_paths: set[str] = set()
    table_entities: set[str] = set()
    for term, verdict in located.items():
        if verdict.get("structured"):
            in_tables.append(term)
            for hit in verdict["structured"]:
                table_entities.add(str(hit.get("entity")))
        elif verdict.get("documents"):
            in_docs.append(term)
            for hit in verdict["documents"]:
                doc_paths.add(str(hit.get("path")))
        else:
            nowhere.append(term)

    steps: list[str] = []
    n = 1
    steps.append(f"{n}. Restate the question and the exact output columns it asks for."); n += 1
    if in_tables:
        steps.append(
            f"{n}. Read structured sources for: {', '.join(in_tables)} "
            f"→ tables {', '.join(sorted(table_entities)) or '(see read_knowledge_graph)'}. "
            "Use execute_context_sql (DB) or execute_universal_sql/execute_python (CSV/JSON)."
        ); n += 1
    if in_docs:
        steps.append(
            f"{n}. Read the document(s) holding: {', '.join(in_docs)} "
            f"→ {', '.join(sorted(doc_paths))}. Use read_pdf (or search_doc to jump to the passage). "
            "Do NOT keep querying databases for these — they are not in any table."
        ); n += 1
    if nowhere:
        steps.append(
            f"{n}. Could not locate by literal match: {', '.join(nowhere)}. "
            "Try a synonym/root, map_sources, or read the most relevant document."
        ); n += 1
    if len(table_entities) >= 2:
        steps.append(
            f"{n}. Join the {len(table_entities)} tables using read_knowledge_graph join paths "
            "(copy the exact column pairs)."
        ); n += 1
    steps.append(f"{n}. Compute the result; apply every filter from the question."); n += 1
    steps.append(
        f"{n}. Validate: row count matches what the question implies, ONLY the asked columns, "
        "no NaN/None cells — then call answer."
    )

    plan_md = "\n".join(steps)
    return {
        "question": question,
        "key_terms": terms,
        "located": {
            "in_tables": in_tables,
            "in_documents": in_docs,
            "not_found": nowhere,
            "tables": sorted(table_entities),
            "documents": sorted(doc_paths),
        },
        "plan": plan_md,
        "recommended_tools": _recommended_tools(bool(table_entities), bool(doc_paths)),
        "note": "Deterministic plan grounded in the workspace. Follow it, but verify each result.",
    }


def _recommended_tools(has_tables: bool, has_docs: bool) -> list[str]:
    tools = ["read_knowledge_graph"]
    if has_docs:
        tools += ["read_pdf", "search_doc"]
    if has_tables:
        tools += ["execute_context_sql", "execute_universal_sql", "execute_python"]
    tools.append("answer")
    return tools
