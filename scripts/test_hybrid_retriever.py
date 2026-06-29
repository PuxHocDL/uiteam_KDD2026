"""Tests for the HybridDocRetriever POC (BM25 + optional vector, RRF fusion).

Run from the repo root:  python scripts/test_hybrid_retriever.py
Fully offline: the embedding model is a deterministic concept-vector stub standing
in for a real OpenAI-compatible embedder.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.tools.hybrid_retriever import (
    HybridDocRetriever, reciprocal_rank_fusion, split_passages,
)
from data_agent_baseline.tools.hybrid_retriever import _cosine  # noqa: PLC2701 - unit test

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


# Deterministic stand-in for a real embedding model: map each passage to a
# 3-concept vector [money, event, weather] by keyword counting.
_CONCEPTS = {
    0: {"income", "profit", "revenue", "earnings", "sales", "net", "margin", "margins"},
    1: {"picnic", "party", "fun", "company", "workshop", "meeting"},
    2: {"rain", "rainfall", "spring", "sunny", "weather", "gardening"},
}


class StubEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):  # noqa: ANN001
        self.calls += 1
        vecs = []
        for t in texts:
            toks = t.lower().replace(".", " ").split()
            v = [0.0, 0.0, 0.0]
            for dim, words in _CONCEPTS.items():
                v[dim] = float(sum(1 for w in toks if w in words))
            vecs.append(v)
        return vecs


PASSAGES = [
    "Net income rose twelve percent year over year.",            # 0 money (semantic match)
    "The company picnic was great fun this year.",               # 1 event
    "Rainfall increased across the region in spring.",           # 2 weather
    "Our gardening and rainfall workshop mentions profit earnings here.",  # 3 lexical decoy
]
QUERY = "profit earnings"  # lexically only present in the decoy (3); semantically about money (0)


def main() -> int:
    # ============================ unit: RRF & cosine ============================
    print("\n=== unit: fusion & cosine ===")
    fused = reciprocal_rank_fusion([[0, 1, 2], [2, 0, 1]])
    order = sorted(fused, key=lambda i: (-fused[i], i))
    check("RRF fuses two rankings", order == [0, 2, 1], str(order))
    check("cosine identical == 1", abs(_cosine([1, 0, 1], [1, 0, 1]) - 1.0) < 1e-9)
    check("cosine orthogonal == 0", abs(_cosine([1, 0], [0, 1])) < 1e-9)
    check("split_passages splits on blank lines",
          split_passages("a para.\n\nsecond para.\n\nthird.") == ["a para.", "second para.", "third."])

    # ============================ lexical-only (BM25) ===========================
    print("\n=== lexical-only (no embedder) ===")
    lex = HybridDocRetriever(PASSAGES)  # embedder=None
    check("not hybrid without embedder", lex.is_hybrid is False)
    lex_res = lex.retrieve(QUERY, top_k=4)
    check("BM25 ranks the lexical DECOY (idx 3) first",
          lex_res[0]["index"] == 3, f"top={lex_res[0]['index']} ({lex_res[0]['matched_by']})")
    # the semantically-correct passage (0) is NOT surfaced first by lexical
    check("BM25 does NOT surface correct passage (idx 0) first",
          lex_res[0]["index"] != 0)

    # ============================ hybrid (BM25 + vector) ========================
    print("\n=== hybrid (BM25 + vector, RRF) ===")
    emb = StubEmbedder()
    hyb = HybridDocRetriever(PASSAGES, embedder=emb)
    check("is hybrid with embedder", hyb.is_hybrid is True)
    hyb_res = hyb.retrieve(QUERY, top_k=4)
    top = hyb_res[0]
    check("hybrid RECOVERS correct passage (idx 0) to the top",
          top["index"] == 0, f"top={top['index']} matched_by={top['matched_by']}")
    check("correct passage matched via vector (no keyword overlap)",
          "vector" in top["matched_by"], str(top["matched_by"]))
    decoy = next((r for r in hyb_res if r["index"] == 3), None)
    check("decoy still present but demoted below correct passage",
          decoy is not None and hyb_res.index(decoy) > 0,
          f"decoy at pos {hyb_res.index(decoy) if decoy else '-'}")

    # ============================ graceful degrade =============================
    print("\n=== graceful degradation ===")
    none_order = [r["index"] for r in HybridDocRetriever(PASSAGES, embedder=None).retrieve(QUERY, top_k=4)]
    check("embedder=None == lexical order",
          none_order == [r["index"] for r in lex_res])

    class BrokenEmbedder:
        def embed(self, texts):  # noqa: ANN001
            raise RuntimeError("embedding endpoint down")

    broken = HybridDocRetriever(PASSAGES, embedder=BrokenEmbedder())
    check("broken embedder degrades to lexical (no crash)", broken.is_hybrid is False)
    check("broken embedder still returns lexical ranking",
          broken.retrieve(QUERY, top_k=1)[0]["index"] == 3)

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
