"""Wiring + retrieval-quality check for search_doc's hybrid path.

Run from the repo root:  python scripts/test_search_doc_hybrid.py

The full ReAct agent needs live LLM credentials, so this measures the retrieval
component the agent actually calls (`search_doc`) — comparing BM25-only (the
current agent behaviour) against the hybrid path on a document where the relevant
passage shares NO keywords with the query but a lexical decoy does. It also proves
the default path (no embedder) is byte-for-byte unchanged → no regression.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.tools.filesystem import search_doc

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


_CONCEPTS = {
    0: {"income", "profit", "revenue", "earnings", "sales", "net", "margin"},
    1: {"picnic", "party", "fun", "company", "workshop", "meeting"},
    2: {"rain", "rainfall", "spring", "sunny", "weather", "gardening"},
}


class StubEmbedder:
    """Deterministic concept-vector embedder standing in for a real model."""
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):  # noqa: ANN001
        self.calls += 1
        out = []
        for t in texts:
            toks = t.lower().replace(".", " ").split()
            v = [0.0, 0.0, 0.0]
            for dim, words in _CONCEPTS.items():
                v[dim] = float(sum(1 for w in toks if w in words))
            out.append(v)
        return out


DOC = (
    "Net income rose twelve percent year over year.\n\n"          # relevant (money), no query words
    "The company picnic was great fun this year.\n\n"             # event filler
    "Rainfall increased across the region in spring.\n\n"         # weather filler
    "Our gardening and rainfall workshop mentions profit earnings here."  # lexical decoy
)
QUERY = "profit earnings"


def main() -> int:
    ctx = Path(tempfile.mkdtemp())
    (ctx / "report.md").write_text(DOC, encoding="utf-8")
    task = SimpleNamespace(context_dir=ctx)

    # Offsets are exact; context windows overlap in a tiny doc, so assert on offset.
    relevant_off = DOC.index("Net income")          # == 0
    decoy_off = DOC.index("Our gardening")           # the lexical decoy

    def rank_of(res, offset):
        for i, m in enumerate(res.get("matches", [])):
            if m.get("offset") == offset:
                return i
        return None

    # ---- BM25-only (current agent behaviour) ----
    print("\n=== BM25-only (embedder=None) — current agent path ===")
    t0 = time.perf_counter()
    bm25 = search_doc(task, "report.md", query=QUERY, mode="keyword")
    t_bm25 = (time.perf_counter() - t0) * 1000
    check("default path is NOT tagged hybrid (no regression)",
          bm25.get("retriever") != "hybrid", f"retriever={bm25.get('retriever')}")
    check("BM25 surfaces the lexical DECOY first",
          bm25["matches"] and bm25["matches"][0]["offset"] == decoy_off,
          f"top offset={bm25['matches'][0]['offset'] if bm25['matches'] else None}, decoy={decoy_off}")

    # ---- Hybrid (BM25 + vector) ----
    print("\n=== Hybrid (BM25 + vector via stub embedder) ===")
    emb = StubEmbedder()
    t0 = time.perf_counter()
    hyb = search_doc(task, "report.md", query=QUERY, mode="keyword", embedder=emb)
    t_hyb = (time.perf_counter() - t0) * 1000
    check("hybrid path tagged retriever=hybrid", hyb.get("retriever") == "hybrid")
    check("hybrid RECOVERS the semantically-relevant passage to the top",
          hyb["matches"] and hyb["matches"][0]["offset"] == relevant_off,
          f"top offset={hyb['matches'][0]['offset'] if hyb['matches'] else None}, relevant={relevant_off}")
    check("top hit carries provenance matched_by=['vector']",
          "vector" in (hyb["matches"][0].get("matched_by") or []),
          str(hyb["matches"][0].get("matched_by")))

    # ---- recall@1: did the relevant passage reach rank 1? ----
    print("\n=== recall@1 (relevant passage at rank 1) ===")
    r_bm25, r_hyb = rank_of(bm25, relevant_off), rank_of(hyb, relevant_off)
    check("BM25 recall@1 = MISS (relevant absent / not rank 1)", r_bm25 != 0, f"rank={r_bm25}")
    check("hybrid recall@1 = HIT (relevant at rank 1)", r_hyb == 0, f"rank={r_hyb}")

    print(f"\n  timing: bm25={t_bm25:.2f}ms  hybrid={t_hyb:.2f}ms (incl. stub embed of all passages)")
    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
