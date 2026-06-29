"""Cross-file relationship map — including documents (PDF / Markdown / text).

The tabular knowledge graph only links structured files (csv/json/db) by shared
columns. But a workspace often answers a question from a *document* — e.g. "the
subsidiaries of Acme Corp" lives in a PDF review, not in any table. The agent
then wastes steps probing every database with `LIKE '%Acme%'` (0 rows) because
nothing ever told it the PDF is the relevant source.

`map_sources` closes that gap. It scans every file by type, pulls candidate
entities out of documents, and — the useful part — links documents to tables:
when a table's name, column, or *sample value* appears in a document's text, it
reports "this doc is about that table". With a `focus` term it ranks where that
term actually appears across all file types, structured or not.

Pure/deterministic (regex + pypdf), no LLM, so it is fast, free and testable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from data_agent_baseline.tools.kg_store import ensure_knowledge_graph

__all__ = ["read_any_text", "map_sources", "locate_terms"]

_DOC_SUFFIXES = {".pdf", ".md", ".txt", ".log", ".text"}
_STRUCT_SUFFIXES = {".csv", ".json", ".sqlite", ".db", ".sqlite3"}

# A proper-noun run: 1-5 capitalised words (Acme Corp, New York City, ACME Inc).
_PROPER_NOUN = re.compile(r"\b([A-Z][A-Za-z&.\-]+(?:\s+(?:[A-Z][A-Za-z&.\-]+|of|and|the|für))*)\b")
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s+(.+)|([A-Z][^\n]{2,60}))\s*$")
_STOP_PROPER = {
    "The", "This", "That", "These", "Those", "A", "An", "And", "Or", "But",
    "In", "On", "At", "For", "Of", "To", "From", "With", "By", "As", "It",
    "We", "Our", "They", "He", "She", "I", "If", "When", "While", "Page",
}


def _read_pdf_text(path: Path, *, max_pages: int = 50) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - pypdf missing → no PDF text
        return "", 0
    try:
        reader = PdfReader(str(path))
        total = len(reader.pages)
        parts = [(page.extract_text() or "") for page in reader.pages[:max_pages]]
        return "\n".join(parts), total
    except Exception:  # noqa: BLE001
        return "", 0


def read_any_text(path: Path, *, max_chars: int = 20000, max_pages: int = 50) -> dict[str, Any]:
    """Extract plain text from a PDF / Markdown / text file.

    PDFs go through pypdf (per page); everything else is read as text. Returns
    the (bounded) text plus metadata so callers know if it was truncated.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    pages = None
    if suffix == ".pdf":
        text, pages = _read_pdf_text(path, max_pages=max_pages)
    else:
        try:
            text = path.read_text(errors="replace")
        except Exception as exc:  # noqa: BLE001
            return {"path": path.name, "text": "", "error": str(exc), "total_chars": 0}
    total = len(text)
    return {
        "path": path.name,
        "type": suffix.lstrip("."),
        "pages": pages,
        "total_chars": total,
        "truncated": total > max_chars,
        "text": text[:max_chars],
    }


_CONNECTORS = {"of", "and", "the", "für", "or"}


def _trim_connectors(phrase: str) -> str:
    """Drop leading/trailing connector words so 'Acme Corp and the' → 'Acme Corp'."""
    words = phrase.split()
    while words and words[0].lower() in _CONNECTORS:
        words.pop(0)
    while words and words[-1].lower() in _CONNECTORS:
        words.pop()
    return " ".join(words)


def _candidate_entities(text: str, *, top_n: int = 25) -> list[str]:
    counts: dict[str, int] = {}
    for match in _PROPER_NOUN.finditer(text):
        phrase = _trim_connectors(match.group(1).strip(" .-&"))
        first = phrase.split()[0] if phrase else ""
        if not phrase or first in _STOP_PROPER or len(phrase) < 3:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [phrase for phrase, _ in ranked[:top_n]]


def _headings(text: str, *, top_n: int = 12) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            heading = (m.group(1) or m.group(2) or "").strip()
            if heading and heading not in out:
                out.append(heading)
        if len(out) >= top_n:
            break
    return out


def _root(word: str) -> str:
    """Crude singular root so 'subsidiary' matches 'subsidiaries' and vice versa."""
    w = word.lower()
    for suffix in ("ies", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    if w.endswith("y") and len(w) >= 5:
        return w[:-1]
    return w


def _focus_needles(term: str) -> list[str]:
    """Literal term first, then word-roots (≥4 chars) for plural-tolerant matching."""
    needles = [term.lower()]
    for word in re.findall(r"[A-Za-z0-9]+", term.lower()):
        root = _root(word)
        if len(root) >= 4 and root not in needles:
            needles.append(root)
    return needles


def _first_hit(text_low: str, needles: list[str]) -> tuple[int, str | None]:
    for needle in needles:
        idx = text_low.find(needle)
        if idx >= 0:
            return idx, needle
    return -1, None


def _structured_lookup(graph: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build {value/name/column (lower) → owning entity} maps from the KG."""
    names: dict[str, str] = {}
    columns: dict[str, str] = {}
    values: dict[str, str] = {}
    for ent in graph.get("entities", []):
        ename = str(ent.get("name", ""))
        if ename:
            names[ename.lower()] = ename
        for col in ent.get("columns", []):
            cname = str(col.get("name", ""))
            if len(cname) >= 3:
                columns.setdefault(cname.lower(), ename)
        for _col, vals in ent.get("sample_values", {}).items():
            for v in vals:
                sv = str(v).strip()
                if len(sv) >= 3:
                    values.setdefault(sv.lower(), ename)
    return names, columns, values


def _link_doc_to_tables(
    doc_text: str,
    names: dict[str, str],
    columns: dict[str, str],
    values: dict[str, str],
) -> list[dict[str, Any]]:
    low = doc_text.lower()
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Sample-value matches are the strongest signal (a real cell appears in prose).
    for value_l, entity in values.items():
        if value_l in low and entity not in seen:
            seen.add(entity)
            links.append({"entity": entity, "via": "value", "match": value_l[:60]})
    for name_l, entity in names.items():
        if name_l in low and entity not in seen:
            seen.add(entity)
            links.append({"entity": entity, "via": "table name", "match": name_l[:60]})
    for col_l, entity in columns.items():
        key = f"{entity}:{col_l}"
        if col_l in low and key not in seen and len(links) < 30:
            seen.add(key)
            links.append({"entity": entity, "via": "column", "match": col_l[:60]})
    return links[:30]


def map_sources(
    context_dir: Path,
    *,
    focus: str | None = None,
    max_doc_chars: int = 20000,
) -> dict[str, Any]:
    """Map every file and how they relate — structured joins + doc↔table links."""
    context_dir = Path(context_dir)
    graph = ensure_knowledge_graph(context_dir)
    names, columns, values = _structured_lookup(graph)

    files: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    doc_texts: dict[str, str] = {}

    for child in sorted(context_dir.rglob("*")):
        if not child.is_file() or child.name.lower() == "gold.csv":
            continue
        # skip our own cache db
        if child.parent.name == ".kg":
            continue
        rel = child.relative_to(context_dir).as_posix()
        suffix = child.suffix.lower()
        if suffix in _STRUCT_SUFFIXES:
            files.append({"path": rel, "type": suffix.lstrip("."), "role": "structured"})
        elif suffix in _DOC_SUFFIXES:
            extracted = read_any_text(child, max_chars=max_doc_chars)
            text = str(extracted.get("text", ""))
            doc_texts[rel] = text
            doc_entry = {
                "path": rel,
                "type": suffix.lstrip("."),
                "role": "document",
                "pages": extracted.get("pages"),
                "total_chars": extracted.get("total_chars"),
                "headings": _headings(text),
                "entities": _candidate_entities(text),
                "links_to_tables": _link_doc_to_tables(text, names, columns, values),
            }
            documents.append(doc_entry)
            files.append({"path": rel, "type": suffix.lstrip("."), "role": "document"})

    result: dict[str, Any] = {
        "file_count": len(files),
        "structured": {
            "entities": [
                {"entity": e.get("name"), "source_file": e.get("source_file"),
                 "type": e.get("source_type"), "row_count": e.get("row_count")}
                for e in graph.get("entities", [])
            ],
            "join_paths": graph.get("relationships", [])[:40],
        },
        "documents": documents,
        "note": (
            "Documents (pdf/md/txt) are NOT in the tabular join graph. Use "
            "`links_to_tables` to see which document describes which table, and "
            "`read_pdf`/`search_doc` to read the document the answer lives in."
        ),
    }

    if focus and focus.strip():
        result["focus"] = _focus_search(focus.strip(), graph, documents, doc_texts)
    return result


def _scan_documents(context_dir: Path, max_doc_chars: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    docs: list[dict[str, Any]] = []
    texts: dict[str, str] = {}
    for child in sorted(Path(context_dir).rglob("*")):
        if not child.is_file() or child.name.lower() == "gold.csv":
            continue
        if child.parent.name == ".kg":
            continue
        if child.suffix.lower() in _DOC_SUFFIXES:
            rel = child.relative_to(context_dir).as_posix()
            extracted = read_any_text(child, max_chars=max_doc_chars)
            texts[rel] = str(extracted.get("text", ""))
            docs.append({"path": rel, "type": child.suffix.lower().lstrip(".")})
    return docs, texts


def locate_terms(
    context_dir: Path,
    terms: list[str],
    *,
    max_doc_chars: int = 20000,
) -> dict[str, dict[str, Any]]:
    """Locate each term across structured data and documents in a single scan.

    Returns ``{term: focus_verdict}`` — the same per-term shape ``map_sources``
    produces under ``focus``. Used by ``plan_task`` to ground a plan in where the
    question's entities actually live.
    """
    graph = ensure_knowledge_graph(Path(context_dir))
    docs, texts = _scan_documents(Path(context_dir), max_doc_chars)
    return {term: _focus_search(term, graph, docs, texts) for term in terms if term.strip()}


def _focus_search(
    term: str,
    graph: dict[str, Any],
    documents: list[dict[str, Any]],
    doc_texts: dict[str, str],
) -> dict[str, Any]:
    needles = _focus_needles(term)

    def _hit(haystack: str) -> bool:
        return _first_hit(haystack.lower(), needles)[0] >= 0

    structured_hits: list[dict[str, Any]] = []
    for ent in graph.get("entities", []):
        where: list[str] = []
        if _hit(str(ent.get("name", ""))):
            where.append("table name")
        cols = [c["name"] for c in ent.get("columns", []) if _hit(str(c.get("name", "")))]
        if cols:
            where.append("columns: " + ", ".join(cols[:5]))
        vals = [
            f"{col}={v}"
            for col, col_vals in ent.get("sample_values", {}).items()
            for v in col_vals
            if _hit(str(v))
        ]
        if vals:
            where.append("values: " + "; ".join(vals[:5]))
        if where:
            structured_hits.append({"entity": ent.get("name"), "source_file": ent.get("source_file"), "where": where})

    doc_hits: list[dict[str, Any]] = []
    for doc in documents:
        rel = doc["path"]
        text = doc_texts.get(rel, "")
        idx, _needle = _first_hit(text.lower(), needles)
        if idx >= 0:
            start = max(0, idx - 120)
            snippet = text[start:idx + 160].replace("\n", " ").strip()
            doc_hits.append({"path": rel, "type": doc["type"], "snippet": "…" + snippet + "…"})

    if structured_hits:
        verdict = "Found in structured data — query the matched table(s) directly."
    elif doc_hits:
        verdict = (
            f"Found ONLY in document(s): {', '.join(d['path'] for d in doc_hits)}. "
            "Read that document (read_pdf / search_doc) — do NOT keep probing the databases."
        )
    else:
        verdict = "Not found anywhere by literal match. Try a synonym or read the most relevant document."
    return {"term": term, "structured": structured_hits, "documents": doc_hits, "verdict": verdict}
