from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from openai import (
    APIError,
    APITimeoutError,
    AzureOpenAI,
    BadRequestError,
    OpenAI,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# USD per 1M tokens, (prompt_price, completion_price). Approximate public list
# prices — Azure billing may differ slightly by region/contract, so treat any
# derived cost as an estimate, not an invoice-accurate figure.
MODEL_PRICING_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Return an estimated USD cost for the given token counts, or None if the
    model isn't in the pricing table (unknown/local/self-hosted endpoints)."""
    pricing = MODEL_PRICING_PER_1M_TOKENS.get(model)
    if pricing is None:
        return None
    prompt_price, completion_price = pricing
    return (prompt_tokens / 1_000_000) * prompt_price + (completion_tokens / 1_000_000) * completion_price


@dataclass
class UsageTotals:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def diff(self, earlier: "UsageTotals") -> "UsageTotals":
        return UsageTotals(
            calls=self.calls - earlier.calls,
            prompt_tokens=self.prompt_tokens - earlier.prompt_tokens,
            completion_tokens=self.completion_tokens - earlier.completion_tokens,
            total_tokens=self.total_tokens - earlier.total_tokens,
        )


class _UsageTrackerMixin:
    """Thread-safe accumulator for token usage, shared by both adapters."""

    def _init_usage_tracking(self) -> None:
        self._usage_lock = threading.Lock()
        self._usage_totals = UsageTotals()

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        with self._usage_lock:
            self._usage_totals.calls += 1
            self._usage_totals.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self._usage_totals.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self._usage_totals.total_tokens += getattr(usage, "total_tokens", 0) or 0

    def usage_snapshot(self) -> UsageTotals:
        with self._usage_lock:
            return UsageTotals(**self._usage_totals.to_dict())

_MAX_RETRIES = 4
_INITIAL_BACKOFF = 1.5  # seconds
_BACKOFF_MULTIPLIER = 2.0
_REQUEST_TIMEOUT = 180.0  # seconds per API call


def _call_with_retry(fn, *, max_retries: int = _MAX_RETRIES) -> Any:
    """Call fn() with exponential backoff on transient API errors."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except (RateLimitError, APITimeoutError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _INITIAL_BACKOFF * (_BACKOFF_MULTIPLIER ** attempt)
                logger.warning(
                    "API transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries + 1, wait, exc,
                )
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"Model request failed after {max_retries + 1} attempts: {exc}"
                ) from exc
        except APIError as exc:
            if getattr(exc, "status_code", None) in (500, 502, 503, 529):
                last_exc = exc
                if attempt < max_retries:
                    wait = _INITIAL_BACKOFF * (_BACKOFF_MULTIPLIER ** attempt)
                    logger.warning(
                        "API server error %s (attempt %d/%d), retrying in %.1fs",
                        exc.status_code, attempt + 1, max_retries + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    raise RuntimeError(
                        f"Model request failed after {max_retries + 1} attempts: {exc}"
                    ) from exc
            else:
                raise RuntimeError(f"Model request failed: {exc}") from exc
    raise RuntimeError(f"Model request failed: {last_exc}") from last_exc


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelStep:
    thought: str
    action: str
    action_input: dict[str, Any]
    raw_response: str


class ModelAdapter(Protocol):
    def complete(self, messages: list[ModelMessage], *, json_object: bool = False) -> str:
        """Return the model's text reply.

        ``json_object=True`` asks the adapter to constrain the reply to a single
        valid JSON object (used by the reasoning engines, whose every step is a
        JSON action). Prose callers leave it False.
        """
        raise NotImplementedError


def _extract_content(response: Any) -> str:
    choices = response.choices or []
    if not choices:
        raise RuntimeError("Model response missing choices.")
    content = choices[0].message.content
    if not isinstance(content, str):
        raise RuntimeError("Model response missing text content.")
    return content


def _looks_like_json_mode_rejection(exc: BadRequestError) -> bool:
    """True when an endpoint rejected the request *because of* response_format.

    Local / OpenAI-compatible endpoints (vLLM, some proxies) may not support
    `response_format={"type":"json_object"}`. We detect that specific rejection
    so we can degrade to a plain request instead of failing the whole step.
    """
    text = str(getattr(exc, "message", "") or exc).lower()
    return "response_format" in text or "json_object" in text or "json mode" in text


class OpenAIModelAdapter(_UsageTrackerMixin):
    def __init__(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        temperature: float,
        json_mode: bool = True,
        seed: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        # Capability flags. json_mode is the operator switch; _json_object_ok is
        # flipped off automatically the first time an endpoint rejects it.
        self.json_mode = json_mode
        self.seed = seed
        self.max_tokens = max_tokens
        self._json_object_ok = json_mode
        self._client: OpenAI | None = None
        self._init_usage_tracking()

    def _get_client(self) -> OpenAI:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")
        if self._client is None:
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                timeout=_REQUEST_TIMEOUT,
            )
        return self._client

    def _request_kwargs(self, messages: list[ModelMessage], *, json_object: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
        }
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if json_object and self._json_object_ok:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def complete(self, messages: list[ModelMessage], *, json_object: bool = False) -> str:
        client = self._get_client()

        def _call() -> str:
            kwargs = self._request_kwargs(messages, json_object=json_object)
            try:
                response = client.chat.completions.create(**kwargs)
            except BadRequestError as exc:
                if "response_format" in kwargs and _looks_like_json_mode_rejection(exc):
                    # Endpoint can't do JSON mode — remember and retry without it.
                    self._json_object_ok = False
                    kwargs.pop("response_format", None)
                    response = client.chat.completions.create(**kwargs)
                else:
                    raise
            self._record_usage(response)
            return _extract_content(response)

        return _call_with_retry(_call)


class AzureOpenAIModelAdapter(_UsageTrackerMixin):
    def __init__(
        self,
        *,
        model: str,
        azure_endpoint: str,
        api_key: str,
        api_version: str,
        temperature: float,
        json_mode: bool = True,
        seed: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.azure_endpoint = azure_endpoint.rstrip("/")
        self.api_key = api_key
        self.api_version = api_version
        self.temperature = temperature
        self.json_mode = json_mode
        self.seed = seed
        self.max_tokens = max_tokens
        self._json_object_ok = json_mode
        self._client: AzureOpenAI | None = None
        self._init_usage_tracking()

    def _get_client(self) -> AzureOpenAI:
        if not self.api_key:
            raise RuntimeError("Missing model API key in config.agent.api_key.")
        if self._client is None:
            self._client = AzureOpenAI(
                api_key=self.api_key,
                azure_endpoint=self.azure_endpoint,
                api_version=self.api_version,
                timeout=_REQUEST_TIMEOUT,
            )
        return self._client

    def _request_kwargs(self, messages: list[ModelMessage], *, json_object: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
        }
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        if json_object and self._json_object_ok:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def complete(self, messages: list[ModelMessage], *, json_object: bool = False) -> str:
        client = self._get_client()

        def _call() -> str:
            kwargs = self._request_kwargs(messages, json_object=json_object)
            try:
                response = client.chat.completions.create(**kwargs)
            except BadRequestError as exc:
                if "response_format" in kwargs and _looks_like_json_mode_rejection(exc):
                    self._json_object_ok = False
                    kwargs.pop("response_format", None)
                    response = client.chat.completions.create(**kwargs)
                else:
                    raise
            self._record_usage(response)
            return _extract_content(response)

        return _call_with_retry(_call)


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def complete(self, messages: list[ModelMessage], *, json_object: bool = False) -> str:
        del messages, json_object
        if not self._responses:
            raise RuntimeError("No scripted model responses remaining.")
        return self._responses.pop(0)
