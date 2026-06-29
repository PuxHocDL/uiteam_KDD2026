"""Text → Knowledge Graph (§12.2a).

Reads a text document (PDF, Markdown, plain text) and uses the LLM to extract a
knowledge graph: entities (people, organisations, projects, places, concepts) and
the relations between them, every node and edge carrying a *source quote* and the
page/section it came from so the UI can show the user *where* a claim is grounded.

Two public functions:
  • read_text_document(path)                       → {kind, pages:[{page,text}], n_chars}
  • build_text_knowledge_graph(doc, model)         → {nodes:[…], edges:[…], note}

The extractor is intentionally simple — one LLM call per page chunk (≤ ~3500 chars),
then a merge step that normalises duplicates (same canonical label → one node) and
keeps every mention as an evidence entry. That keeps the prompt small enough to
work on local LLMs and avoids long-context drift on big PDFs.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent_baseline.agents.model import ModelMessage

logger = logging.getLogger(__name__)

# Page chunk size for the LLM. Big enough to keep ~1 page in one call, small
# enough that even a 4k-context local model can handle it with headroom for the
# response.
PAGE_CHARS = 3500
MAX_PAGES = 25            # hard cap so the user can't burn a $5 call by accident
ENTITY_TYPES = ("Person", "Organisation", "Project", "Place", "Concept", "Event", "Date", "Other")


# --------------------------------------------------------------------------- #
# document loader
# --------------------------------------------------------------------------- #
def read_text_document(path: Path) -> dict[str, Any]:
    """Load a text-bearing file as `{kind, pages:[{page,text}], n_chars}`.

    PDF      → one entry per page (using pypdf).
    md / txt → one entry per ~PAGE_CHARS slice so long docs still chunk evenly.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix in {".md", ".markdown", ".txt", ".rst"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        pages = _split_chunks(text, PAGE_CHARS)
        return {"kind": "text", "pages": pages, "n_chars": len(text)}
    raise ValueError(
        f"Knowledge graph builder supports PDF / Markdown / text, not '{suffix or path.name}'."
    )


def _read_pdf(path: Path) -> dict[str, Any]:
    from pypdf import PdfReader  # local import keeps the rest of the module importable without pypdf
    reader = PdfReader(str(path))
    pages: list[dict[str, Any]] = []
    total = 0
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 — corrupt PDFs throw all kinds of errors
            text = ""
        text = _normalise(text)
        if text:
            pages.append({"page": i + 1, "text": text})
            total += len(text)
    return {"kind": "pdf", "pages": pages, "n_chars": total}


def _split_chunks(text: str, size: int) -> list[dict[str, Any]]:
    """Split `text` into ~`size`-char chunks on paragraph/line boundaries. Each
    chunk's `page` is a 1-based index — for the UI it's still a useful citation."""
    text = _normalise(text)
    if not text:
        return []
    paras = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if not p:
            continue
        if len(cur) + len(p) + 2 <= size or not cur:
            cur = (cur + "\n\n" + p) if cur else p
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return [{"page": i + 1, "text": c} for i, c in enumerate(chunks)]


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# LLM extractor
# --------------------------------------------------------------------------- #
_SYSTEM = """You are a precise information-extraction engine. From a passage of
text you return ONLY a JSON object describing the entities and relations in that
passage. Schema:

{
  "nodes": [
    {"label": "<canonical name>", "type": "<one of: Person, Organisation, Project, Place, Concept, Event, Date, Other>",
     "summary": "<at most one short sentence describing this entity>"}
  ],
  "edges": [
    {"source": "<node label>", "target": "<node label>",
     "relation": "<short verb phrase, lowercase, ≤ 4 words>",
     "quote": "<an exact, short substring from the passage that supports this relation>"}
  ]
}

Rules:
- Use ONLY entities that actually appear in the passage; never invent or infer
  facts that aren't textually supported.
- Every edge MUST cite a quote copied verbatim from the passage.
- `source` and `target` must each match a `label` in `nodes`.
- Skip pronouns, generic nouns, headings ("Chapter 1"), and dates that aren't
  themselves discussed as entities.
- Return at most 25 nodes and 40 edges per passage.
- Return ONLY the JSON, no prose, no markdown fence."""


def _legacy_extract(
    doc: dict[str, Any],
    model: Any,
    *,
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    """Builtin per-page extractor (fallback). One LLM call per page chunk, merged.

    Returns `{nodes, edges, pages_used, pages_total, note}`. On any model error
    the offending page is skipped and the failure is reported via `note`.
    """
    pages = doc.get("pages", []) or []
    if not pages:
        return {"nodes": [], "edges": [], "pages_used": 0, "pages_total": 0,
                "note": "The document has no extractable text — knowledge graph is empty."}

    used = pages[:max_pages]
    skipped: list[int] = []
    nodes_acc: dict[str, dict[str, Any]] = {}
    edges_acc: list[dict[str, Any]] = []

    for page in used:
        try:
            raw = model.complete([
                ModelMessage(role="system", content=_SYSTEM),
                ModelMessage(role="user", content=f"PASSAGE (page {page['page']}):\n\n{page['text']}"),
            ])
        except Exception:  # noqa: BLE001 — surface partial graph on bad page
            skipped.append(page["page"])
            continue
        parsed = _extract_json(raw)
        if not isinstance(parsed, dict):
            skipped.append(page["page"])
            continue
        _merge_page(parsed, page["page"], page["text"], nodes_acc, edges_acc)

    nodes = list(nodes_acc.values())
    clusters, hierarchy = _cluster_graph(nodes, edges_acc)
    note = None
    if skipped:
        note = f"Skipped {len(skipped)} page(s) the model could not parse: {skipped[:6]}{'…' if len(skipped) > 6 else ''}."
    if len(pages) > max_pages:
        extra = f" Only the first {max_pages} of {len(pages)} pages were processed."
        note = (note or "") + extra
    return {
        "nodes": nodes, "edges": edges_acc,
        "clusters": clusters, "hierarchy": hierarchy,
        "pages_used": len(used) - len(skipped),
        "pages_total": len(pages),
        "note": note,
    }


def _merge_page(
    parsed: dict[str, Any], page_no: int, page_text: str,
    nodes_acc: dict[str, dict[str, Any]], edges_acc: list[dict[str, Any]],
) -> None:
    raw_nodes = parsed.get("nodes")
    raw_edges = parsed.get("edges")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        return

    # Build a per-page label → canonical label map, so edges from this page
    # resolve to the same merged node even when the LLM repeats minor spelling
    # variations ("Acme Inc." vs "Acme Inc").
    page_alias: dict[str, str] = {}
    for n in raw_nodes:
        if not isinstance(n, dict):
            continue
        label = _clean(n.get("label"))
        if not label:
            continue
        ntype = _norm_type(n.get("type"))
        summary = _clean(n.get("summary"))
        key = label.lower()
        page_alias[key] = label
        # Merge into accumulator — keep the first canonical casing/type/summary
        # and append the page as a mention.
        if key in nodes_acc:
            node = nodes_acc[key]
            if not node["summary"] and summary:
                node["summary"] = summary
            if node["type"] in {"Other", ""} and ntype != "Other":
                node["type"] = ntype
            if page_no not in node["pages"]:
                node["pages"].append(page_no)
        else:
            nodes_acc[key] = {
                "id": key, "label": label, "type": ntype, "summary": summary,
                "pages": [page_no],
            }

    for e in raw_edges:
        if not isinstance(e, dict):
            continue
        src = _clean(e.get("source"))
        tgt = _clean(e.get("target"))
        relation = _clean(e.get("relation"))
        quote = _clean(e.get("quote"))
        if not (src and tgt and relation):
            continue
        # Resolve to canonical labels we accepted into nodes_acc above. Skip the
        # edge if either side wasn't declared as a node (model hallucination).
        s_key = src.lower()
        t_key = tgt.lower()
        if s_key not in nodes_acc or t_key not in nodes_acc or s_key == t_key:
            continue
        # Only keep the quote if it actually appears in the page text — guards
        # against the LLM paraphrasing instead of quoting verbatim.
        if quote and quote.lower() not in page_text.lower():
            quote = ""
        edges_acc.append({
            "source": s_key, "target": t_key,
            "relation": relation.lower(), "quote": quote, "page": page_no,
        })


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


def _extract_json(raw: str) -> Any:
    if not raw:
        return None
    s = raw.strip()
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    # Some models add a leading "Here is the JSON:" line.
    brace = s.find("{")
    if brace > 0:
        s = s[brace:]
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        # Trim trailing prose after the JSON object.
        depth = 0
        end = -1
        for i, ch in enumerate(s):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            try:
                return json.loads(s[:end])
            except (json.JSONDecodeError, ValueError):
                return None
    return None


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _norm_type(value: Any) -> str:
    v = _clean(value).title()
    if v in ENTITY_TYPES:
        return v
    # Common synonyms the LLM tends to invent.
    syn = {"Company": "Organisation", "Corporation": "Organisation", "Org": "Organisation",
           "Team": "Organisation", "Location": "Place", "City": "Place", "Country": "Place",
           "Topic": "Concept", "Idea": "Concept", "Product": "Project"}
    return syn.get(v, "Other")


# --------------------------------------------------------------------------- #
# public orchestrator — LlamaIndex PropertyGraph first, builtin fallback
# --------------------------------------------------------------------------- #
def build_text_knowledge_graph(
    doc: dict[str, Any],
    creds: dict[str, str],
    *,
    max_pages: int = MAX_PAGES,
    num_workers: int = 4,
) -> dict[str, Any]:
    """Extract a knowledge graph from a loaded document.

    Primary path uses LlamaIndex's PropertyGraph extractor (typed entities +
    relations, cross-chunk consistency, parallel calls). If LlamaIndex is not
    installed, the endpoint is Azure, or extraction raises for any reason, we
    fall back to the builtin per-page extractor so the feature never hard-breaks.
    The result always carries an `engine` field naming the path that produced it.
    """
    pages = doc.get("pages", []) or []
    if not pages:
        return {"nodes": [], "edges": [], "pages_used": 0, "pages_total": 0,
                "note": "The document has no extractable text — knowledge graph is empty.",
                "engine": "none"}
    try:
        return _extract_with_llamaindex(doc, creds, max_pages=max_pages, num_workers=num_workers)
    except Exception as exc:  # noqa: BLE001 — any failure degrades to the builtin path
        logger.warning("LlamaIndex extraction unavailable (%s); using builtin extractor.", exc)
    out = _legacy_extract(doc, _legacy_adapter(creds), max_pages=max_pages)
    out["engine"] = "builtin"
    return out


def _legacy_adapter(creds: dict[str, str]) -> Any:
    """Build the project's own OpenAI/Azure model adapter for the fallback path."""
    from data_agent_baseline.agents.model import AzureOpenAIModelAdapter, OpenAIModelAdapter
    if creds.get("api_version"):
        return AzureOpenAIModelAdapter(
            model=creds.get("model", ""), azure_endpoint=creds.get("api_base", ""),
            api_key=creds.get("api_key", ""), api_version=creds["api_version"], temperature=0.0,
        )
    return OpenAIModelAdapter(
        model=creds.get("model", ""), api_base=creds.get("api_base", ""),
        api_key=creds.get("api_key", ""), temperature=0.0,
    )


# --------------------------------------------------------------------------- #
# LlamaIndex PropertyGraph extraction
# --------------------------------------------------------------------------- #
def _extract_with_llamaindex(
    doc: dict[str, Any], creds: dict[str, str], *, max_pages: int, num_workers: int,
) -> dict[str, Any]:
    """Run a LlamaIndex PropertyGraph extractor over each page node, then merge.

    The extractor writes EntityNode / Relation objects into each TextNode's
    metadata; we read them back, keep the page number and source text for
    grounding, and hand everything to `_merge_triplets` for cross-chunk dedup
    and best-effort quote recovery. Imports are local so the module stays
    importable (and the builtin path keeps working) without llama-index.
    """
    from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
    from llama_index.core.schema import TextNode

    llm = _build_li_llm(creds)
    extractor, kind = _build_li_extractor(llm, num_workers)

    pages = (doc.get("pages") or [])[:max_pages]
    tnodes = [TextNode(text=p["text"], metadata={"page": p["page"]}) for p in pages]
    processed = extractor(tnodes)

    records: list[dict[str, Any]] = []
    for tn in processed:
        page = tn.metadata.get("page")
        text = getattr(tn, "text", "") or ""
        kg_nodes = tn.metadata.get(KG_NODES_KEY) or []
        kg_rels = tn.metadata.get(KG_RELATIONS_KEY) or []
        type_by_name: dict[str, str] = {}
        for en in kg_nodes:
            name = _clean(getattr(en, "name", "") or "")
            if name:
                type_by_name[name] = _norm_type(getattr(en, "label", "") or "")
        # When the extractor emitted an explicit entity set, trust it and drop any
        # relation whose endpoint isn't a real entity (hallucination guard). If no
        # entities were emitted, fall back to treating relation endpoints as nodes.
        strict = bool(type_by_name)
        for rel in kg_rels:
            s = _clean(getattr(rel, "source_id", "") or "")
            t = _clean(getattr(rel, "target_id", "") or "")
            relation = _clean(getattr(rel, "label", "") or "")
            if not (s and t and relation):
                continue
            if strict and (s not in type_by_name or t not in type_by_name):
                continue
            records.append({
                "s_name": s, "s_type": type_by_name.get(s, "Other"),
                "t_name": t, "t_type": type_by_name.get(t, "Other"),
                "relation": relation, "page": page, "text": text,
            })

    result = _merge_triplets(
        records, pages_total=len(doc.get("pages") or []), pages_used=len(pages), max_pages=max_pages,
    )
    result["engine"] = f"llamaindex:{kind}"
    return result


def _build_li_llm(creds: dict[str, str]) -> Any:
    """A LlamaIndex LLM bound to the user's endpoint (OpenAI-compatible or Azure)."""
    if creds.get("api_version"):
        from llama_index.llms.azure_openai import AzureOpenAI
        deployment = creds.get("model", "")
        common = dict(
            deployment_name=deployment,
            api_key=creds.get("api_key", ""),
            azure_endpoint=creds.get("api_base", ""),
            api_version=creds["api_version"],
            temperature=0.0, max_tokens=2048, timeout=180.0,
        )
        llm = AzureOpenAI(model=deployment, **common)
        # Azure deployments are user-named, so the deployment often isn't a known
        # OpenAI model — that only breaks tokenizer/context inference, not the call.
        # Validate it, and if unknown fall back to a known model *for metadata only*
        # (requests still target `deployment_name`).
        try:
            _ = llm.metadata
        except ValueError:
            llm = AzureOpenAI(model="gpt-4o", **common)
        return llm
    from llama_index.llms.openai_like import OpenAILike
    return OpenAILike(
        model=creds.get("model", ""),
        api_base=creds.get("api_base", ""),
        api_key=creds.get("api_key", ""),
        is_chat_model=True,
        is_function_calling_model=False,
        context_window=128000,
        max_tokens=2048,
        temperature=0.0,
        timeout=180.0,
    )


def _build_li_extractor(llm: Any, num_workers: int) -> tuple[Any, str]:
    """Prefer the typed, flexible Dynamic extractor; fall back to the Simple one."""
    from llama_index.core.indices.property_graph import SimpleLLMPathExtractor
    try:
        from llama_index.core.indices.property_graph import DynamicLLMPathExtractor
        return (
            DynamicLLMPathExtractor(
                llm=llm,
                max_triplets_per_chunk=20,
                num_workers=max(1, num_workers),
                allowed_entity_types=list(ENTITY_TYPES),
            ),
            "dynamic",
        )
    except Exception:  # noqa: BLE001 — older llama-index or signature drift → Simple
        return (
            SimpleLLMPathExtractor(llm=llm, max_paths_per_chunk=20, num_workers=max(1, num_workers)),
            "simple",
        )


# --------------------------------------------------------------------------- #
# merge: cross-chunk dedup + quote grounding (shared shape with the legacy path)
# --------------------------------------------------------------------------- #
def _merge_triplets(
    records: list[dict[str, Any]], *, pages_total: int, pages_used: int, max_pages: int,
) -> dict[str, Any]:
    """Fold per-chunk triples into a deduplicated `{nodes, edges, …}` graph.

    Nodes merge on a punctuation/space-insensitive key so spelling variants
    collapse; each node keeps every page it was mentioned on. Edges dedupe on
    (source, target, relation) and carry a verbatim quote when one can be found.
    """
    nodes_acc: dict[str, dict[str, Any]] = {}

    def _touch(name: str, ntype: str, page: Any) -> str | None:
        key = _canon(name)
        if not key:
            return None
        node = nodes_acc.get(key)
        if node is None:
            nodes_acc[key] = {
                "id": key, "label": name, "type": ntype or "Other",
                "summary": "", "pages": ([page] if page else []),
            }
        else:
            if node["type"] in ("Other", "") and ntype not in ("Other", ""):
                node["type"] = ntype
            if page and page not in node["pages"]:
                node["pages"].append(page)
        return key

    seen: set[tuple[str, str, str]] = set()
    edges: list[dict[str, Any]] = []
    for r in records:
        s_key = _touch(r["s_name"], r.get("s_type", "Other"), r.get("page"))
        t_key = _touch(r["t_name"], r.get("t_type", "Other"), r.get("page"))
        if not s_key or not t_key or s_key == t_key:
            continue
        relation = _clean(r.get("relation", "")).lower()
        if not relation:
            continue
        sig = (s_key, t_key, relation)
        if sig in seen:
            continue
        seen.add(sig)
        edges.append({
            "source": s_key, "target": t_key, "relation": relation,
            "quote": _best_quote(r.get("text", ""), r["s_name"], r["t_name"]),
            "page": r.get("page"),
        })

    nodes = list(nodes_acc.values())
    for n in nodes:
        n["pages"].sort()
    clusters, hierarchy = _cluster_graph(nodes, edges)
    note = None
    if pages_total > max_pages:
        note = f"Only the first {max_pages} of {pages_total} pages were processed."
    return {"nodes": nodes, "edges": edges, "clusters": clusters, "hierarchy": hierarchy,
            "pages_used": pages_used, "pages_total": pages_total, "note": note}


def _canon(name: str) -> str:
    """Punctuation/space-insensitive key so 'Acme Inc.' and 'Acme Inc' merge."""
    s = _clean(name).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _best_quote(text: str, a: str, b: str, limit: int = 240) -> str:
    """Return a verbatim sentence from `text` mentioning both endpoints, else ''."""
    if not text or not a or not b:
        return ""
    a_l, b_l = a.lower(), b.lower()
    for sent in re.split(r"(?<=[.!?])\s+", text):
        sl = sent.lower()
        if a_l in sl and b_l in sl:
            sent = sent.strip()
            return sent if len(sent) <= limit else sent[: limit - 1].rstrip() + "…"
    return ""


# --------------------------------------------------------------------------- #
# clustering: community detection + 2-level hierarchy for the UI
# --------------------------------------------------------------------------- #
# Detects communities in the merged graph so the UI can render a hierarchical view
# (super-cluster by entity type → community → entities) and collapse big graphs
# into a few super-nodes. Uses label propagation — deterministic, no extra deps,
# O(iter * |E|) — which is good enough for the few-hundred-node graphs we get
# from a single document.
LP_MAX_ITER = 30


def _cluster_graph(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stamp each node with a `cluster_id` and return `(clusters, hierarchy)`.

    Communities come from weighted label propagation over the undirected graph.
    Each cluster is then summarised (size, dominant entity type, hub node).
    Hierarchy is a coarse second level grouping clusters by their dominant type
    so the UI can show a tree: type → community → entities.
    """
    if not nodes:
        return [], []

    by_id = {n["id"]: n for n in nodes}
    adj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if not s or not t or s == t or s not in by_id or t not in by_id:
            continue
        adj[s][t] += 1
        adj[t][s] += 1

    labels = _label_propagation(by_id, adj)

    # Group node ids by community label.
    groups: dict[str, list[str]] = defaultdict(list)
    for nid, lbl in labels.items():
        groups[lbl].append(nid)

    # Order clusters by size (desc) then label so ids are stable across runs.
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    cid_map: dict[str, str] = {}
    clusters: list[dict[str, Any]] = []
    for i, (lbl, members) in enumerate(ordered):
        members.sort()
        cid = f"c{i + 1}"
        cid_map[lbl] = cid
        type_counts: Counter[str] = Counter(by_id[m].get("type", "Other") for m in members)
        dominant = type_counts.most_common(1)[0][0]
        # Hub = highest in-cluster degree, tiebreak by shorter label then alpha
        # (a short canonical name reads better as the cluster's headline).
        def _deg(m: str) -> int:
            return sum(1 for nb in adj.get(m, {}) if labels.get(nb) == lbl)
        hub_id = max(
            members,
            key=lambda m: (_deg(m), -len(by_id[m]["label"]), by_id[m]["label"]),
        )
        # External edges = edges crossing into other clusters (for "is this an
        # isolated cluster or a hub?" hints in the UI).
        external = sum(
            1 for m in members for nb in adj.get(m, {}) if labels.get(nb) != lbl
        )
        internal = sum(
            _deg(m) for m in members
        ) // 2
        clusters.append({
            "id": cid,
            "label": by_id[hub_id]["label"],
            "size": len(members),
            "dominant_type": dominant,
            "type_counts": dict(type_counts),
            "members": members,
            "hub_id": hub_id,
            "internal_edges": internal,
            "external_edges": external,
        })

    for n in nodes:
        n["cluster_id"] = cid_map[labels[n["id"]]]

    # Level-2 hierarchy: super-clusters keyed by dominant entity type.
    super_groups: dict[str, list[str]] = defaultdict(list)
    for c in clusters:
        super_groups[c["dominant_type"]].append(c["id"])
    hierarchy = [
        {
            "id": f"t:{t}",
            "label": t,
            "type": t,
            "children": cids,
            "size": sum(next(c["size"] for c in clusters if c["id"] == cid) for cid in cids),
        }
        for t, cids in sorted(super_groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return clusters, hierarchy


def _label_propagation(
    by_id: dict[str, dict[str, Any]], adj: dict[str, dict[str, int]],
) -> dict[str, str]:
    """Hybrid community detection: connected components are the base partition
    (disjoint topics in the document naturally don't share entities), then
    asynchronous label propagation splits any component bigger than the
    threshold into tighter sub-communities. Isolates stay as singletons. Sweep
    order is sorted for determinism across Python hash seeds."""
    # ---- pass 1: connected components ---------------------------------------
    visited: set[str] = set()
    comp_of: dict[str, str] = {}
    for nid in sorted(by_id.keys()):
        if nid in visited:
            continue
        # Use the smallest member id as the component anchor — stable across runs.
        stack = [nid]
        members: list[str] = []
        while stack:
            x = stack.pop()
            if x in visited:
                continue
            visited.add(x)
            members.append(x)
            for nb in adj.get(x, {}):
                if nb not in visited:
                    stack.append(nb)
        anchor = min(members)
        for m in members:
            comp_of[m] = anchor

    labels = dict(comp_of)

    # ---- pass 2: refine large components with async LP ----------------------
    SPLIT_THRESHOLD = 8
    comps: dict[str, list[str]] = defaultdict(list)
    for x, c in comp_of.items():
        comps[c].append(x)
    for members in comps.values():
        if len(members) <= SPLIT_THRESHOLD:
            continue
        member_set = set(members)
        sub: dict[str, str] = {m: m for m in members}
        order = sorted(members)
        for _ in range(LP_MAX_ITER):
            changed = False
            for nid in order:
                nbrs = {k: v for k, v in adj.get(nid, {}).items() if k in member_set}
                if not nbrs:
                    continue
                counts: dict[str, float] = defaultdict(float)
                for nb, w in nbrs.items():
                    counts[sub[nb]] += w
                cur = sub[nid]
                # Stable tiebreak: highest weight, then prefer keeping the
                # current label (anti-oscillation), then alphabetic on label.
                best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == cur, kv[0]))[0]
                if best != cur:
                    sub[nid] = best
                    changed = True
            if not changed:
                break
        for m in members:
            labels[m] = sub[m]
    return labels

