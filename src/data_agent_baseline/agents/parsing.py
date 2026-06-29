"""Robust parsing of LLM responses into structured agent steps.

Every engine (ReAct, DRAGIN, multi-agent orchestrator) and the consensus
selector turn a free-text model reply into JSON. Historically each one had its
own parser with slightly different robustness, so a reply that one engine could
recover the others would drop. This module is the single, hardened
implementation they all share, so output-shape reliability is identical across
the whole reasoning stack (P3 — one source of truth).

It tolerates the failure modes GPT-4o / GPT-4.1 actually produce in practice:
prose before/after the JSON, ```json fences, `<think>...</think>` preludes, and
unbalanced trailing brackets.
"""

from __future__ import annotations

import json
import re

from data_agent_baseline.agents.model import ModelStep

__all__ = [
    "strip_json_fence",
    "fix_brackets",
    "load_single_json_object",
    "extract_json_object",
    "parse_model_step",
]


def strip_json_fence(raw_response: str) -> str:
    """Reduce a raw model reply down to the JSON payload it contains.

    Handles, in order: ```json fenced blocks, generic ``` fences, and a bare
    ``{...}`` object embedded in surrounding prose. ``<think>...</think>``
    reasoning preludes (emitted by some reasoning models) are stripped first.
    """
    text = raw_response.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    json_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if json_match is not None:
        return json_match.group(0).strip()
    return text


def fix_brackets(text: str) -> str:
    """Best-effort repair of unbalanced brackets in near-valid JSON text.

    Models occasionally emit ``[[...]}`` instead of ``[[...]]}`` or drop a
    closing brace. We close the missing brackets in the right order so the
    payload becomes decodable instead of failing the whole step.
    """
    open_sq = text.count("[")
    close_sq = text.count("]")
    open_cu = text.count("{")
    close_cu = text.count("}")
    if close_sq < open_sq:
        diff = open_sq - close_sq
        last_curly = text.rfind("}")
        if last_curly > 0:
            text = text[:last_curly] + "]" * diff + text[last_curly:]
    if close_cu < open_cu:
        text += "}" * (open_cu - close_cu)
    return text


def load_single_json_object(text: str) -> dict[str, object]:
    """Decode the first JSON object in ``text``, repairing brackets if needed."""
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        fixed = fix_brackets(text)
        payload, _ = json.JSONDecoder().raw_decode(fixed)
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def extract_json_object(text: str) -> dict[str, object]:
    """Fence-strip then decode a JSON object from a raw reply.

    Convenience wrapper used by callers (e.g. the consensus selector) that only
    need the dict, not a full :class:`ModelStep`.
    """
    return load_single_json_object(strip_json_fence(text))


def parse_model_step(raw_response: str) -> ModelStep:
    """Parse a raw model reply into a validated :class:`ModelStep`.

    Normalises common deviations: string ``action_input`` is auto-wrapped into
    the dict shape the matching tool expects (``code`` for ``execute_python``,
    ``path`` otherwise) so a minor format slip does not cost the agent a step.
    """
    normalized = strip_json_fence(raw_response)
    payload = load_single_json_object(normalized)

    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if isinstance(action_input, str):
        if action == "execute_python":
            action_input = {"code": action_input}
        else:
            action_input = {"path": action_input}
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )
