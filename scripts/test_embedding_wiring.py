"""Tests for reusing the UI's (Azure) creds as the embedding endpoint.

Run from the repo root:  python scripts/test_embedding_wiring.py
No network: asserts the right provider is built from creds, that the request-scoped
embedder flows down to the search_doc TOOL (registry) via a contextvar, and that
everything degrades to BM25 when no embedding deployment is configured.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "src")
import data_agent_baseline.agents.react  # noqa: F401,E402  break a circular import

from data_agent_baseline.tools.hybrid_retriever import (
    AzureEmbeddingProvider, OpenAIEmbeddingProvider, build_embedder_from_creds,
    reset_request_embedder, resolve_embedder, set_request_embedder,
)
from data_agent_baseline.tools.registry import create_default_tool_registry

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    _PASS += 1 if ok else 0
    _FAIL += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


class StubEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):  # noqa: ANN001
        self.calls += 1
        # tiny money/other 2-d concept vector, enough to exercise the tool path
        money = {"income", "revenue", "profit", "earnings", "net", "sales"}
        return [[float(sum(w in t.lower().split() for w in money)), 1.0] for t in texts]


def main() -> int:
    # ===================== build_embedder_from_creds (no env) ===================
    print("\n=== provider selection from creds ===")
    os.environ.pop("DABENCH_EMBED_MODEL", None)
    # Default model is always text-embedding-3-large when nothing is specified.
    default_az = build_embedder_from_creds(api_key="k", api_base="https://x.azure.com", api_version="2024-02-01")
    check("defaults to text-embedding-3-large",
          isinstance(default_az, AzureEmbeddingProvider) and default_az.deployment == "text-embedding-3-large",
          getattr(default_az, "deployment", None))
    # Still None when creds themselves are missing.
    check("missing key/endpoint → None (BM25)",
          build_embedder_from_creds(api_key="", api_base="", api_version="2024-02-01") is None)

    # Azure selected when api_version present; deployment via explicit arg.
    az = build_embedder_from_creds(api_key="k", api_base="https://my.openai.azure.com",
                                   api_version="2024-02-01", deployment="text-embedding-ada-002")
    check("api_version present → AzureEmbeddingProvider", isinstance(az, AzureEmbeddingProvider),
          type(az).__name__)
    check("Azure reuses endpoint + version", az.azure_endpoint == "https://my.openai.azure.com"
          and az.api_version == "2024-02-01" and az.deployment == "text-embedding-ada-002")

    # OpenAI-compatible when no api_version.
    oai = build_embedder_from_creds(api_key="k", api_base="https://api.openai.com/v1",
                                    api_version="", deployment="text-embedding-3-small")
    check("no api_version → OpenAIEmbeddingProvider", isinstance(oai, OpenAIEmbeddingProvider),
          type(oai).__name__)

    # Deployment can come from env (the only piece the UI doesn't carry).
    os.environ["DABENCH_EMBED_MODEL"] = "ada-embed"
    env_az = build_embedder_from_creds(api_key="k", api_base="https://a.azure.com", api_version="2024-02-01")
    check("deployment from DABENCH_EMBED_MODEL", isinstance(env_az, AzureEmbeddingProvider)
          and env_az.deployment == "ada-embed")
    os.environ.pop("DABENCH_EMBED_MODEL", None)

    # ===================== contextvar resolution ===============================
    print("\n=== request-scoped embedder (contextvar) ===")
    check("resolve_embedder() is None by default", resolve_embedder() is None)
    stub = StubEmbedder()
    token = set_request_embedder(stub)
    try:
        check("resolve_embedder() returns the pinned stub", resolve_embedder() is stub)
    finally:
        reset_request_embedder(token)
    check("resolve_embedder() None again after reset", resolve_embedder() is None)

    # ===================== end-to-end through the search_doc TOOL ================
    print("\n=== search_doc tool picks up the request embedder ===")
    ctx = Path(tempfile.mkdtemp())
    (ctx / "r.md").write_text(
        "Net income rose twelve percent year over year.\n\n"
        "The company picnic was great fun this year.\n\n"
        "Our gardening workshop mentions profit earnings here.",
        encoding="utf-8",
    )
    task = SimpleNamespace(context_dir=ctx)
    reg = create_default_tool_registry()
    args = {"path": "r.md", "query": "profit earnings", "mode": "keyword"}

    # No embedder → BM25 (tool unchanged).
    res_bm25 = reg.execute(task, "search_doc", args).content
    check("tool BM25 when no embedder", res_bm25.get("retriever") != "hybrid",
          f"retriever={res_bm25.get('retriever')}")

    # Pin a stub embedder → tool goes hybrid, surfacing the semantic passage.
    stub2 = StubEmbedder()
    token = set_request_embedder(stub2)
    try:
        res_hyb = reg.execute(task, "search_doc", args).content
    finally:
        reset_request_embedder(token)
    relevant_off = 0  # "Net income…" is first in the doc
    check("tool goes hybrid with request embedder", res_hyb.get("retriever") == "hybrid")
    check("embedder actually called by the tool", stub2.calls > 0, f"calls={stub2.calls}")
    check("hybrid surfaces the semantic passage (offset 0)",
          res_hyb["matches"] and res_hyb["matches"][0]["offset"] == relevant_off,
          f"top offset={res_hyb['matches'][0]['offset'] if res_hyb['matches'] else None}")

    print(f"\n==== TOTAL: {_PASS} passed, {_FAIL} failed ====")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
