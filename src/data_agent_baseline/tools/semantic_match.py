"""Semantic-ish value matching for knowledge-graph lookup (fix #3).

`search_graph` (fixes #1/#2) matches a query term against data values by exact
substring. That misses three classes the agent really hits:

  • surface variants  — "Corp Acme" vs "Acme Corporation" (word order),
                        "Customer 40" vs "Customer 040" (zero-pad), case/punct.
  • concept → value   — question says "unpaid", the column value is "overdue".
  • concept → column  — question says "revenue", the column is named "total".

This module offers three escalating strategies, all behind one entry point so the
caller picks a `mode`:

  • "exact"  — normalized substring (cheap, high precision).
  • "fuzzy"  — token-set + numeric + prefix matching. Deterministic, offline,
               solves the surface-variant class. Cannot bridge concepts.
  • "llm"    — ask a ModelAdapter which of the real values a concept maps to,
               then VALIDATE the answer is a subset of the real values (no
               hallucinated values leak through). Needs a model.
  • "hybrid" — fuzzy first; escalate to llm only when fuzzy finds nothing and a
               model is available; degrades to fuzzy when it isn't.

Everything except the llm call is a pure function of strings, so the bulk is
unit-testable with no IO and the llm path is testable with a scripted stub.
"""
from __future__ import annotations

import contextvars
import json
import re
from typing import Any, Protocol

__all__ = [
    "normalize", "tokens", "fuzzy_match", "llm_expand_values", "match_values",
    "set_request_model", "reset_request_model", "resolve_model",
]

_PUNCT_RE = re.compile(r"[^a-z0-9]+")


class _Model(Protocol):
    def complete(self, messages: list[Any], *, json_object: bool = False) -> str: ...


def normalize(text: str) -> str:
    """Lowercase, turn punctuation into spaces, collapse whitespace."""
    return _PUNCT_RE.sub(" ", str(text).lower()).strip()


def tokens(text: str) -> set[str]:
    """Normalized tokens; all-digit tokens lose leading zeros so 040 == 40."""
    out: set[str] = set()
    for tok in normalize(text).split():
        out.add(tok.lstrip("0") or "0" if tok.isdigit() else tok)
    return out


def _token_matches(q_tok: str, v_tok: str) -> bool:
    """A query token matches a value token if equal, or one is a prefix of the
    other (length ≥ 4) — so 'corp' ↔ 'corporation', 'order' ↔ 'orders'."""
    if q_tok == v_tok:
        return True
    if len(q_tok) >= 4 and len(v_tok) >= 4 and (q_tok.startswith(v_tok) or v_tok.startswith(q_tok)):
        return True
    return False


def fuzzy_match(query: str, value: str) -> bool:
    """True if `query` matches `value` up to surface variation (not concepts)."""
    nq, nv = normalize(query), normalize(value)
    if not nq:
        return False
    if nq in nv:  # exact substring (fast, also covers most cases)
        return True
    q_tokens, v_tokens = tokens(query), tokens(value)
    if not q_tokens:
        return False
    # Every query token must find a match among value tokens (order-independent).
    return all(any(_token_matches(qt, vt) for vt in v_tokens) for qt in q_tokens)


def llm_expand_values(query: str, candidates: list[str], model: _Model | None,
                      *, max_candidates: int = 200) -> list[str]:
    """Ask the model which real values the concept `query` maps to, then keep only
    answers that are genuinely in `candidates` (guards against hallucination)."""
    if model is None or not candidates:
        return []
    shortlist = candidates[:max_candidates]
    valid = {str(c) for c in shortlist}
    prompt = (
        "You map a search CONCEPT to the actual values present in a data column.\n"
        f"CONCEPT: {query!r}\n"
        f"COLUMN VALUES (choose only from these, copy them verbatim): {json.dumps(shortlist, ensure_ascii=False)}\n"
        'Reply with ONLY a JSON array of the values that match the concept, e.g. ["x","y"]. '
        "Return [] if none match."
    )
    try:
        from data_agent_baseline.agents.model import ModelMessage
        raw = model.complete([ModelMessage(role="user", content=prompt)], json_object=True)
        picked = _parse_json_array(raw)
        # Keep only validated, real values — preserve column order, dedupe.
        seen: set[str] = set()
        result: list[str] = []
        for item in picked:
            s = str(item)
            if s in valid and s not in seen:
                seen.add(s)
                result.append(s)
        return result
    except Exception:  # noqa: BLE001 - LLM path is best-effort; degrade to nothing
        return []


def _parse_json_array(raw: str) -> list[Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        data = json.loads(m.group(0))
    if isinstance(data, dict):  # tolerate {"values": [...]} / {"matches": [...]}
        for key in ("values", "matches", "result", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def match_values(query: str, values: list[str], *, mode: str = "fuzzy",
                 model: _Model | None = None) -> list[str]:
    """Return the values matching `query` under the chosen strategy."""
    uniq = list(dict.fromkeys(str(v) for v in values))  # dedupe, keep order
    if mode == "exact":
        nq = normalize(query)
        return [v for v in uniq if nq and nq in normalize(v)]
    if mode == "fuzzy":
        return [v for v in uniq if fuzzy_match(query, v)]
    if mode == "llm":
        return llm_expand_values(query, uniq, model)
    if mode == "hybrid":
        hits = [v for v in uniq if fuzzy_match(query, v)]
        if not hits and model is not None:
            hits = llm_expand_values(query, uniq, model)
        return hits
    raise ValueError(f"Unknown match mode: {mode!r}")


# Request-scoped chat model for the LLM concept-bridge: the server pins the run's
# model (the same creds entered in the UI) so `read_knowledge_graph` can bridge a
# concept to real column values without any extra UI fields. None → fuzzy-only.
_REQUEST_MODEL: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "request_model", default=None
)


def set_request_model(model: Any | None):
    """Pin a chat model for the current run/thread; returns a token to reset with."""
    return _REQUEST_MODEL.set(model)


def reset_request_model(token) -> None:
    _REQUEST_MODEL.reset(token)


def resolve_model() -> Any | None:
    """The active chat model for the concept-bridge, or None (→ fuzzy-only)."""
    return _REQUEST_MODEL.get()
