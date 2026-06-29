"""Hybrid document retriever — BM25 (lexical) + optional vector (semantic), fused
with Reciprocal Rank Fusion (RRF).

This is the "borrow the idea, not the dependency" answer to kotaemon's hybrid RAG:
the same lexical+vector+fusion recipe, but behind a tiny interface, with NO vector
DB / LlamaIndex / Gradio. It degrades to pure BM25 when no embedder is supplied, so
it stays offline-testable and matches the repo's plug-and-play principle:

  • lexical only  — what `search_doc` already does (keyword/BM25).
  • + embedder    — adds a semantic ranker that catches passages with no keyword
                    overlap (e.g. query "company profit" → "Net income rose 12%").
  • RRF           — rank-based fusion, robust to the two scorers being on different
                    scales; each passage's score = Σ 1/(k + rank_in_ranker).

The embedding model sits behind `EmbeddingProvider`, so a real OpenAI-compatible
endpoint and a deterministic test stub are interchangeable.
"""
from __future__ import annotations

import contextvars
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "EmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "AzureEmbeddingProvider",
    "HybridDocRetriever",
    "split_passages",
    "reciprocal_rank_fusion",
    "embedder_from_env",
    "build_embedder_from_creds",
    "DEFAULT_EMBED_MODEL",
    "set_request_embedder",
    "reset_request_embedder",
    "resolve_embedder",
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""
        ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def split_passages(text: str) -> list[str]:
    """Split a document into passages on blank lines (fallback: per line)."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return parts or [line.strip() for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Lexical (BM25) ranking
# ---------------------------------------------------------------------------

def _bm25_scores(query: str, passages: list[str], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [_tokenize(p) for p in passages]
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    q_terms = set(_tokenize(query))
    scores: list[float] = []
    for d in docs:
        dl = len(d) or 1
        score = 0.0
        for t in q_terms:
            f = d.count(t)
            if f == 0:
                continue
            idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Vector ranking
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(rank_lists: list[list[int]], *, k: int = 60) -> dict[int, float]:
    """RRF over several ranked lists of item indices (rank 0 = best).

    score(item) = Σ 1 / (k + rank_of_item_in_list). Items missing from a list
    simply contribute nothing from it.
    """
    fused: dict[int, float] = {}
    for ranking in rank_lists:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + rank)
    return fused


def _ranking_from_scores(scores: list[float]) -> list[int]:
    """Indices ordered by descending score (stable on ties)."""
    return sorted(range(len(scores)), key=lambda i: (-scores[i], i))


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

@dataclass
class RetrievedPassage:
    index: int
    passage: str
    score: float
    matched_by: list[str]
    lexical_rank: int | None
    vector_rank: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "passage": self.passage,
            "score": round(self.score, 6),
            "matched_by": self.matched_by,
            "lexical_rank": self.lexical_rank,
            "vector_rank": self.vector_rank,
        }


class HybridDocRetriever:
    """Rank passages by BM25, optionally fused with vector similarity via RRF."""

    def __init__(
        self,
        passages: list[str],
        *,
        embedder: EmbeddingProvider | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.passages = list(passages)
        self.embedder = embedder
        self.rrf_k = rrf_k
        self._doc_embeddings: list[list[float]] | None = None
        if embedder is not None and self.passages:
            try:
                self._doc_embeddings = embedder.embed(self.passages)
            except Exception:  # noqa: BLE001 - degrade to lexical-only on embed failure
                self._doc_embeddings = None

    @property
    def is_hybrid(self) -> bool:
        return self._doc_embeddings is not None

    def retrieve(self, query: str, *, top_k: int = 5) -> list[dict[str, Any]]:
        if not self.passages:
            return []
        lex_scores = _bm25_scores(query, self.passages)
        lex_ranking = _ranking_from_scores(lex_scores)
        lex_pos = {idx: rank for rank, idx in enumerate(lex_ranking)}

        rank_lists = [lex_ranking]
        vec_pos: dict[int, int] = {}
        if self._doc_embeddings is not None:
            try:
                q_emb = self.embedder.embed([query])[0]  # type: ignore[union-attr]
                vec_scores = [_cosine(q_emb, d) for d in self._doc_embeddings]
                vec_ranking = _ranking_from_scores(vec_scores)
                vec_pos = {idx: rank for rank, idx in enumerate(vec_ranking)}
                rank_lists.append(vec_ranking)
            except Exception:  # noqa: BLE001 - degrade to lexical-only at query time
                vec_pos = {}

        fused = reciprocal_rank_fusion(rank_lists, k=self.rrf_k)
        order = sorted(fused, key=lambda i: (-fused[i], i))[:top_k]

        results: list[RetrievedPassage] = []
        for idx in order:
            matched_by: list[str] = []
            # "hit" = this scorer actually ranked the passage meaningfully (lexical
            # score > 0, or the passage is in the top half of the vector ranking).
            if lex_scores[idx] > 0:
                matched_by.append("lexical")
            if vec_pos and vec_pos.get(idx, len(self.passages)) < max(1, len(self.passages) // 2 + 1):
                matched_by.append("vector")
            results.append(RetrievedPassage(
                index=idx,
                passage=self.passages[idx],
                score=fused[idx],
                matched_by=matched_by or ["fusion"],
                lexical_rank=lex_pos.get(idx),
                vector_rank=vec_pos.get(idx) if vec_pos else None,
            ))
        return [r.to_dict() for r in results]


class OpenAIEmbeddingProvider:
    """Production embedder over an OpenAI-compatible /embeddings endpoint.

    Optional: only used when the operator configures an embedding model. Offline
    tests use a deterministic stub instead, so this path is intentionally thin.
    """

    def __init__(self, *, model: str, api_base: str, api_key: str) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self._client = None

    def _get_client(self):
        if not self.api_key:
            raise RuntimeError("Missing embedding API key.")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.api_base)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._get_client().embeddings.create(model=self.model, input=list(texts))
        return [item.embedding for item in resp.data]


class AzureEmbeddingProvider:
    """Production embedder over an Azure OpenAI embeddings deployment.

    Mirrors `AzureOpenAIModelAdapter`: same key / endpoint / api_version as the
    chat model, but a SEPARATE embeddings *deployment* name (Azure requires one).
    """

    def __init__(self, *, deployment: str, azure_endpoint: str, api_key: str, api_version: str) -> None:
        self.deployment = deployment
        self.azure_endpoint = azure_endpoint.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version
        self._client = None

    def _get_client(self):
        if not self.api_key:
            raise RuntimeError("Missing Azure embedding API key.")
        if self._client is None:
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.azure_endpoint,
                api_version=self.api_version,
            )
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._get_client().embeddings.create(model=self.deployment, input=list(texts))
        return [item.embedding for item in resp.data]


DEFAULT_EMBED_MODEL = "text-embedding-3-large"


def build_embedder_from_creds(
    *, api_key: str, api_base: str, api_version: str = "", deployment: str = ""
) -> EmbeddingProvider | None:
    """Build an embedder reusing the run's chat credentials (key + endpoint + version).

    The embedding model/deployment NAME defaults to `text-embedding-3-large`
    (override with the `deployment` arg or the `DABENCH_EMBED_MODEL` env var). For
    Azure this is the embeddings *deployment* name — make sure a deployment with
    this name exists. Returns None (→ BM25-only) only when key/endpoint are missing.
    Azure is selected exactly like the chat adapter: by the presence of `api_version`.
    """
    import os

    deployment = (deployment or os.environ.get("DABENCH_EMBED_MODEL", "") or DEFAULT_EMBED_MODEL).strip()
    api_key, api_base = api_key.strip(), api_base.strip()
    if not (api_key and api_base and deployment):
        return None
    if api_version.strip():
        return AzureEmbeddingProvider(
            deployment=deployment, azure_endpoint=api_base,
            api_key=api_key, api_version=api_version.strip(),
        )
    return OpenAIEmbeddingProvider(model=deployment, api_base=api_base, api_key=api_key)


def embedder_from_env() -> EmbeddingProvider | None:
    """Build an embedder from DABENCH_EMBED_* env vars, or None (→ BM25-only).

    Keeps credentials out of code and the hybrid path strictly opt-in: with no
    embedding endpoint configured, `search_doc` behaves exactly as before.
    """
    import os

    api_key = os.environ.get("DABENCH_EMBED_API_KEY", "").strip()
    api_base = os.environ.get("DABENCH_EMBED_API_BASE", "").strip()
    model = os.environ.get("DABENCH_EMBED_MODEL", "").strip()
    api_version = os.environ.get("DABENCH_EMBED_API_VERSION", "").strip()
    if not (api_key and api_base and model):
        return None
    if api_version:
        return AzureEmbeddingProvider(
            deployment=model, azure_endpoint=api_base, api_key=api_key, api_version=api_version,
        )
    return OpenAIEmbeddingProvider(model=model, api_base=api_base, api_key=api_key)


# Request-scoped embedder: the server sets this (built from the UI's Azure creds)
# before running the agent, so `search_doc` reuses the same key/endpoint without
# any env vars or UI changes. Falls back to env config when not set.
_REQUEST_EMBEDDER: contextvars.ContextVar[EmbeddingProvider | None] = contextvars.ContextVar(
    "request_embedder", default=None
)


def set_request_embedder(provider: EmbeddingProvider | None):
    """Pin an embedder for the current run/thread; returns a token to reset with."""
    return _REQUEST_EMBEDDER.set(provider)


def reset_request_embedder(token) -> None:
    _REQUEST_EMBEDDER.reset(token)


def resolve_embedder() -> EmbeddingProvider | None:
    """The active embedder: request-scoped first, then env config, else None."""
    return _REQUEST_EMBEDDER.get() or embedder_from_env()
