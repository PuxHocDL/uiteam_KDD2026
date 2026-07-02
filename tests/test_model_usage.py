"""Unit tests for token/cost tracking added to agents/model.py — no network calls."""

from __future__ import annotations

from types import SimpleNamespace

from data_agent_baseline.agents.model import (
    OpenAIModelAdapter,
    estimate_cost_usd,
)


def _fake_response(content: str, prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


class _FakeChatCompletions:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return self._responses.pop(0)


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeChatCompletions(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


def _adapter_with_fake_client(responses) -> OpenAIModelAdapter:
    adapter = OpenAIModelAdapter(
        model="gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        api_key="test-key",
        temperature=0.0,
    )
    adapter._client = _FakeClient(responses)
    return adapter


def test_usage_accumulates_across_calls():
    adapter = _adapter_with_fake_client(
        [
            _fake_response('{"a":1}', prompt_tokens=100, completion_tokens=10),
            _fake_response('{"a":2}', prompt_tokens=50, completion_tokens=5),
        ]
    )

    adapter.complete([], json_object=True)
    adapter.complete([], json_object=True)

    usage = adapter.usage_snapshot()
    assert usage.calls == 2
    assert usage.prompt_tokens == 150
    assert usage.completion_tokens == 15
    assert usage.total_tokens == 165


def test_usage_snapshot_diff_gives_per_task_delta():
    adapter = _adapter_with_fake_client(
        [
            _fake_response("x", prompt_tokens=100, completion_tokens=10),
            _fake_response("y", prompt_tokens=20, completion_tokens=2),
        ]
    )

    before = adapter.usage_snapshot()
    adapter.complete([])
    after_task_1 = adapter.usage_snapshot()
    delta_1 = after_task_1.diff(before)

    adapter.complete([])
    after_task_2 = adapter.usage_snapshot()
    delta_2 = after_task_2.diff(after_task_1)

    assert delta_1.total_tokens == 110
    assert delta_2.total_tokens == 22


def test_estimate_cost_usd_known_model():
    cost = estimate_cost_usd("gpt-4o", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == 2.50 + 10.00


def test_estimate_cost_usd_unknown_model_returns_none():
    assert estimate_cost_usd("some-local-llm", prompt_tokens=1000, completion_tokens=1000) is None
