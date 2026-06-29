"""Tests for the shared reasoning stack: JSON parsing, recovery hints, and the
model adapter's JSON-mode plumbing.

These are the pieces every engine (ReAct, DRAGIN, orchestrator) now leans on, so
a regression here weakens reasoning everywhere at once.
"""
from __future__ import annotations

import pytest

from data_agent_baseline.agents.model import OpenAIModelAdapter, ScriptedModelAdapter
from data_agent_baseline.agents.parsing import (
    extract_json_object,
    fix_brackets,
    load_single_json_object,
    parse_model_step,
    strip_json_fence,
)
from data_agent_baseline.agents.reasoning import (
    classify_tool_error,
    detect_repeat_loop,
    step_signature,
)


# --------------------------------------------------------------------------- #
# parsing                                                                     #
# --------------------------------------------------------------------------- #

def test_strip_json_fence_handles_json_fence():
    raw = "```json\n{\"action\": \"answer\"}\n```"
    assert strip_json_fence(raw) == '{"action": "answer"}'


def test_strip_json_fence_handles_generic_fence_and_prose():
    assert strip_json_fence("blah ```\n{\"a\":1}\n``` end") == '{"a":1}'
    # bare object embedded in prose
    assert strip_json_fence('I will do {"a": 1} now') == '{"a": 1}'


def test_strip_json_fence_drops_think_prelude():
    raw = "<think>let me reason</think>\n{\"action\": \"list_context\"}"
    assert strip_json_fence(raw) == '{"action": "list_context"}'


def test_fix_brackets_closes_missing_square_and_curly():
    # one missing ] and the trailing } repair
    broken = '{"rows": [[1, 2]}'
    repaired = fix_brackets(broken)
    assert load_single_json_object(repaired) == {"rows": [[1, 2]]}


def test_load_single_json_object_rejects_non_object():
    with pytest.raises(ValueError):
        load_single_json_object("[1, 2, 3]")


def test_extract_json_object_roundtrip_through_fence():
    assert extract_json_object("```json\n{\"x\": 5}\n```") == {"x": 5}


def test_parse_model_step_happy_path():
    step = parse_model_step('{"thought": "t", "action": "list_context", "action_input": {"max_depth": 4}}')
    assert step.action == "list_context"
    assert step.action_input == {"max_depth": 4}
    assert step.thought == "t"


def test_parse_model_step_wraps_string_action_input():
    # execute_python with a bare string body -> wrapped into {"code": ...}
    py = parse_model_step('{"action": "execute_python", "action_input": "print(1)"}')
    assert py.action_input == {"code": "print(1)"}
    # any other tool -> wrapped into {"path": ...}
    other = parse_model_step('{"action": "profile_csv", "action_input": "a.csv"}')
    assert other.action_input == {"path": "a.csv"}


def test_parse_model_step_tolerates_prose_and_missing_brace():
    # prose prefix + the final closing brace dropped — both recovered.
    raw = 'Sure! {"thought":"x","action":"answer","action_input":{"columns":["c"]}'
    step = parse_model_step(raw)
    assert step.action == "answer"
    assert step.action_input["columns"] == ["c"]


def test_parse_model_step_requires_action():
    with pytest.raises(ValueError):
        parse_model_step('{"thought": "no action here"}')


# --------------------------------------------------------------------------- #
# recovery hints                                                              #
# --------------------------------------------------------------------------- #

def test_classify_tool_error_hard_failures():
    assert "COLUMN NOT FOUND" in classify_tool_error("execute_context_sql", False, "no such column: foo")
    assert "TABLE NOT FOUND" in classify_tool_error("execute_universal_sql", False, "no such table: bar")
    assert "SYNTAX ERROR" in classify_tool_error("execute_python", False, "SyntaxError: bad")


def test_classify_tool_error_silent_empty_results():
    assert "EMPTY RESULT" in classify_tool_error("execute_context_sql", True, "query returned 0 rows")
    assert "EMPTY RESULT" in classify_tool_error("execute_python", True, "no matches found")


def test_classify_tool_error_returns_none_when_fine():
    assert classify_tool_error("list_context", True, "{'entries': [...]}") is None
    assert classify_tool_error("execute_python", True, "result printed ok") is None


def test_step_signature_keys_python_on_code():
    a = step_signature("execute_python", {"code": "print(1)"})
    b = step_signature("execute_python", {"code": "print(1)"})
    c = step_signature("execute_python", {"code": "print(2)"})
    assert a == b != c


def test_step_signature_is_order_independent():
    assert step_signature("x", {"a": 1, "b": 2}) == step_signature("x", {"b": 2, "a": 1})


def test_detect_repeat_loop_fires_on_repeats():
    sigs = ["execute_python:code"] * 3
    hint = detect_repeat_loop(sigs)
    assert hint is not None and "LOOP DETECTED" in hint


def test_detect_repeat_loop_quiet_when_varied():
    assert detect_repeat_loop(["a:1", "b:2", "c:3", "d:4"]) is None


def test_detect_repeat_loop_respects_threshold():
    # only twice in the window -> below default threshold of 3
    assert detect_repeat_loop(["a:1", "a:1", "b:2"]) is None


# --------------------------------------------------------------------------- #
# adapter JSON-mode plumbing (no network)                                     #
# --------------------------------------------------------------------------- #

def _adapter(**kw):
    base = dict(model="gpt-4o", api_base="https://x/v1", api_key="k", temperature=0.0)
    base.update(kw)
    return OpenAIModelAdapter(**base)


def test_request_kwargs_adds_response_format_only_when_asked():
    a = _adapter()
    assert _adapter()._request_kwargs([], json_object=True)["response_format"] == {"type": "json_object"}
    assert "response_format" not in a._request_kwargs([], json_object=False)


def test_request_kwargs_wires_seed_and_max_tokens():
    a = _adapter(seed=7, max_tokens=512)
    kw = a._request_kwargs([], json_object=True)
    assert kw["seed"] == 7 and kw["max_tokens"] == 512


def test_request_kwargs_drops_json_after_degrade():
    a = _adapter()
    a._json_object_ok = False  # simulate an endpoint that rejected JSON mode
    assert "response_format" not in a._request_kwargs([], json_object=True)


def test_json_mode_disabled_by_flag():
    a = _adapter(json_mode=False)
    assert "response_format" not in a._request_kwargs([], json_object=True)


def test_scripted_adapter_accepts_json_object_kwarg():
    s = ScriptedModelAdapter(['{"action": "answer"}'])
    assert s.complete([], json_object=True) == '{"action": "answer"}'


# --------------------------------------------------------------------------- #
# multi-agent forced final answer                                             #
# --------------------------------------------------------------------------- #

def test_orchestrator_forces_best_guess_answer():
    from types import SimpleNamespace

    from data_agent_baseline.agents.orchestrator import MultiAgentOrchestrator
    from data_agent_baseline.agents.runtime import AgentRuntimeState
    from data_agent_baseline.benchmark.schema import AnswerTable
    from data_agent_baseline.tools.registry import ToolExecutionResult

    class FakeTools:
        def describe_for_prompt(self):
            return ""

        def execute(self, task, action, ai):
            return ToolExecutionResult(
                ok=True, content={"status": "ok"}, is_terminal=True,
                answer=AnswerTable(columns=ai["columns"], rows=ai["rows"]),
            )

    reply = '{"thought":"best guess","action":"answer","action_input":{"columns":["c"],"rows":[[42]]}}'
    orch = MultiAgentOrchestrator(model=ScriptedModelAdapter([reply]), tools=FakeTools())
    state = AgentRuntimeState()
    orch._forced_final_answer(SimpleNamespace(question="How many?", task_id="t1"), state)

    assert state.answer is not None
    assert state.answer.rows == [[42]]
    assert state.steps[-1].observation.get("forced") is True


def test_orchestrator_forced_answer_noop_when_already_answered():
    from types import SimpleNamespace

    from data_agent_baseline.agents.orchestrator import MultiAgentOrchestrator
    from data_agent_baseline.agents.runtime import AgentRuntimeState
    from data_agent_baseline.benchmark.schema import AnswerTable

    orch = MultiAgentOrchestrator(model=ScriptedModelAdapter([]), tools=object())
    state = AgentRuntimeState()
    state.answer = AnswerTable(columns=["c"], rows=[[1]])
    # must not touch the model (empty script would raise if it did)
    orch._forced_final_answer(SimpleNamespace(question="q", task_id="t"), state)
    assert state.answer.rows == [[1]]
