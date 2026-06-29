from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent_baseline.agents.dragin_prompt import DRAGIN_SYSTEM_PROMPT
from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.prompt import (
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.reasoning import classify_tool_error, empty_filter_hint
from data_agent_baseline.agents.react import (
    _DIFFICULTY_GUIDANCE,
    _answer_preflight_hint,
    parse_model_step,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class DRAGINAgentConfig:
    max_steps: int = 15
    rind_threshold: float = 0.28
    qfs_top_n: int = 12
    max_retrievals: int = 4
    retrieval_context_chars: int = 700


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    norm: str
    index: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _RINDDecision:
    trigger_token: str
    trigger_start: int
    trigger_end: int
    truncated_generation: str
    score: float
    entropy_proxy: float
    attention_proxy: float
    semantic: int
    query: str
    qfs_tokens: list[str]
    note: str


@dataclass(frozen=True, slots=True)
class _FileCandidate:
    path: str
    suffix: str
    size: int | None


_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")

_STOPWORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "did",
    "do",
    "does",
    "doing",
    "don",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
}

_INFO_NEED_CUES = {
    "check",
    "determine",
    "discover",
    "find",
    "identify",
    "inspect",
    "locate",
    "missing",
    "need",
    "needs",
    "relevant",
    "retrieve",
    "search",
    "unclear",
    "unknown",
    "verify",
}

_QUERY_NOISE = _INFO_NEED_CUES | {
    "action",
    "action_input",
    "answer",
    "call",
    "columns",
    "context",
    "data",
    "execute",
    "json",
    "list_context",
    "observation",
    "profile",
    "profile_context",
    "result",
    "rows",
    "step",
    "thought",
    "tool",
}

_RETRIEVAL_TOOLS = {
    "inspect_sqlite_schema",
    "list_context",
    "profile_context",
    "profile_csv",
    "profile_database",
    "profile_json",
    "read_csv",
    "read_doc",
    "read_doc_chunk",
    "read_json",
    "search_doc",
}


def _normalize_token(text: str) -> str:
    return text.strip("._:-").lower()


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN_RE.finditer(text):
        norm = _normalize_token(match.group(0))
        if not norm:
            continue
        tokens.append(
            _Token(
                text=match.group(0),
                norm=norm,
                index=len(tokens),
                start=match.start(),
                end=match.end(),
            )
        )
    return tokens


def _is_semantic_token(token: _Token) -> int:
    if token.norm in _STOPWORDS:
        return 0
    if len(token.norm) <= 1 and not token.norm.isdigit():
        return 0
    return 1


def _looks_entity(text: str) -> bool:
    return (
        "_" in text
        or text.isupper()
        or any(char.isdigit() for char in text)
        or (text[:1].isupper() and len(text) > 3)
    )


def _question_terms(question: str) -> set[str]:
    return {
        token.norm
        for token in _tokenize(question)
        if _is_semantic_token(token) and token.norm not in _QUERY_NOISE
    }


def _entropy_proxy(tokens: list[_Token], index: int) -> float:
    token = tokens[index]
    left = max(0, index - 5)
    right = min(len(tokens), index + 6)
    window = {item.norm for item in tokens[left:right]}

    score = 0.18
    if token.norm in _INFO_NEED_CUES:
        score += 0.50
    if window & _INFO_NEED_CUES:
        score += 0.24
    if _looks_entity(token.text) or len(token.norm) >= 8:
        score += 0.12
    if token.norm in {"maybe", "possibly", "likely", "guess", "infer"}:
        score += 0.20
    return min(score, 1.0)


def _attention_proxy(tokens: list[_Token], index: int, question_terms: set[str]) -> float:
    token = tokens[index]
    future = tokens[index + 1 :]
    future_mentions = sum(1 for item in future if item.norm == token.norm)

    score = 0.20
    if token.norm in question_terms:
        score += 0.42
    if future_mentions:
        score += min(0.25, 0.12 * future_mentions)
    if _looks_entity(token.text):
        score += 0.18
    if index < len(tokens) - 1:
        score += 0.06
    return min(score, 1.0)


def _top_qfs_tokens(
    tokens: list[_Token],
    *,
    trigger_index: int,
    question_terms: set[str],
    top_n: int,
) -> list[_Token]:
    scored: list[tuple[float, _Token]] = []
    for token in tokens[: trigger_index + 1]:
        if not _is_semantic_token(token) or token.norm in _QUERY_NOISE:
            continue
        score = _attention_proxy(tokens, token.index, question_terms)
        if token.norm in question_terms:
            score += 0.25
        if _looks_entity(token.text):
            score += 0.12
        scored.append((score, token))

    selected = sorted(scored, key=lambda item: (-item[0], item[1].index))[:top_n]
    return [token for _, token in sorted(selected, key=lambda item: item[1].index)]


def _dedupe_ordered_text(tokens: list[_Token]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token.norm in seen:
            continue
        seen.add(token.norm)
        result.append(token.text)
    return result


def _fallback_query_tokens(question: str, top_n: int) -> list[str]:
    tokens = [
        token
        for token in _tokenize(question)
        if _is_semantic_token(token) and token.norm not in _QUERY_NOISE
    ]
    return _dedupe_ordered_text(tokens[:top_n])


def _detect_information_need(
    *,
    generated_text: str,
    question: str,
    threshold: float,
    qfs_top_n: int,
) -> _RINDDecision | None:
    tokens = _tokenize(generated_text)
    if not tokens:
        return None

    q_terms = _question_terms(question)
    best: tuple[float, float, float, int, _Token] | None = None
    for token in tokens:
        semantic = _is_semantic_token(token)
        if not semantic:
            continue
        entropy = _entropy_proxy(tokens, token.index)
        attention = _attention_proxy(tokens, token.index, q_terms)
        score = entropy * attention * semantic
        if best is None or score > best[0]:
            best = (score, entropy, attention, semantic, token)

    if best is None:
        return None

    score, entropy, attention, semantic, trigger = best
    if score < threshold:
        return None

    qfs_tokens = _top_qfs_tokens(
        tokens,
        trigger_index=trigger.index,
        question_terms=q_terms,
        top_n=qfs_top_n,
    )
    query_tokens = _dedupe_ordered_text(qfs_tokens)
    if not query_tokens:
        query_tokens = _fallback_query_tokens(question, qfs_top_n)
    query = " ".join(query_tokens).strip() or question

    return _RINDDecision(
        trigger_token=trigger.text,
        trigger_start=trigger.start,
        trigger_end=trigger.end,
        truncated_generation=generated_text[: trigger.end].rstrip(),
        score=round(score, 4),
        entropy_proxy=round(entropy, 4),
        attention_proxy=round(attention, 4),
        semantic=semantic,
        query=query,
        qfs_tokens=query_tokens,
        note=(
            "Proxy RIND/QFS was used because the configured chat adapter does not expose "
            "token probabilities or transformer attention matrices."
        ),
    )


def _bootstrap_decision(task: PublicTask, top_n: int) -> _RINDDecision:
    query_tokens = _fallback_query_tokens(task.question, top_n)
    return _RINDDecision(
        trigger_token="<bootstrap>",
        trigger_start=0,
        trigger_end=0,
        truncated_generation="",
        score=1.0,
        entropy_proxy=1.0,
        attention_proxy=1.0,
        semantic=1,
        query=" ".join(query_tokens) or task.question,
        qfs_tokens=query_tokens,
        note="Bootstrap retrieval before first generation step.",
    )


def _json_action(action: str, action_input: dict[str, Any], thought: str) -> str:
    payload = {"thought": thought, "action": action, "action_input": action_input}
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _action_key(action: str, action_input: dict[str, Any]) -> str:
    return json.dumps([action, action_input], sort_keys=True, ensure_ascii=False)


def _content_dict(step: StepRecord) -> dict[str, Any]:
    content = step.observation.get("content")
    return content if isinstance(content, dict) else {}


def _suffix_for_path(path: str, fallback: str = "") -> str:
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    if fallback.startswith("."):
        return fallback.lower()
    return fallback.lower()


def _collect_file_candidates(state: AgentRuntimeState) -> dict[str, _FileCandidate]:
    files: dict[str, _FileCandidate] = {}
    for step in state.steps:
        content = _content_dict(step)

        entries = content.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("kind") != "file":
                    continue
                path = str(entry.get("path", ""))
                if not path:
                    continue
                size = entry.get("size")
                files[path] = _FileCandidate(
                    path=path,
                    suffix=_suffix_for_path(path),
                    size=size if isinstance(size, int) else None,
                )

        profiles = content.get("profiles")
        if isinstance(profiles, list):
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                path = str(profile.get("path", ""))
                if not path:
                    continue
                size = profile.get("size")
                files[path] = _FileCandidate(
                    path=path,
                    suffix=_suffix_for_path(path, str(profile.get("type", ""))),
                    size=size if isinstance(size, int) else None,
                )
    return files


def _profiled_paths(state: AgentRuntimeState) -> set[str]:
    profiled: set[str] = set()
    for step in state.steps:
        action_path = step.action_input.get("path")
        if isinstance(action_path, str) and step.action in {
            "inspect_sqlite_schema",
            "profile_csv",
            "profile_database",
            "profile_json",
            "read_csv",
            "read_doc",
            "read_doc_chunk",
            "read_json",
            "search_doc",
        }:
            profiled.add(action_path)

        content = _content_dict(step)
        profiles = content.get("profiles")
        if isinstance(profiles, list):
            for profile in profiles:
                if isinstance(profile, dict) and "profile" in profile:
                    path = str(profile.get("path", ""))
                    if path:
                        profiled.add(path)
    return profiled


def _has_action(state: AgentRuntimeState, action: str) -> bool:
    return any(step.action == action for step in state.steps)


def _path_terms(path: str) -> set[str]:
    return {
        _normalize_token(part)
        for part in re.split(r"[^A-Za-z0-9_]+", path)
        if _normalize_token(part)
    }


def _candidate_score(candidate: _FileCandidate, query_terms: set[str]) -> tuple[int, int, str]:
    path_terms = _path_terms(candidate.path)
    overlap = len(path_terms & query_terms)
    knowledge_bonus = 2 if Path(candidate.path).name.lower() == "knowledge.md" else 0
    size = candidate.size or 0
    return (overlap + knowledge_bonus, size, candidate.path)


class DRAGINAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: DRAGINAgentConfig | None = None,
        system_prompt: str | None = None,
        memory_context: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or DRAGINAgentConfig()
        self.system_prompt = system_prompt or DRAGIN_SYSTEM_PROMPT
        self.memory_context = memory_context or ""
        self._question_level: str | None = None

    def _tool_available(self, name: str) -> bool:
        return name in self.tools.handlers

    def _build_messages(
        self,
        task: PublicTask,
        state: AgentRuntimeState,
        step_index: int,
    ) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=self.system_prompt,
        )
        messages = [ModelMessage(role="system", content=system_content)]
        task_content = build_task_prompt(task)
        difficulty = task.difficulty.lower() if task.difficulty else ""
        if difficulty in _DIFFICULTY_GUIDANCE:
            task_content = _DIFFICULTY_GUIDANCE[difficulty] + "\n" + task_content
        if self.memory_context:
            task_content = self.memory_context + "\n\n" + task_content
        messages.append(ModelMessage(role="user", content=task_content))
        for step in state.steps:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(
                    role="user",
                    content=build_observation_prompt(step.observation, task_question=task.question),
                )
            )

        remaining = self.config.max_steps - step_index
        if 0 < remaining <= 3 and state.answer is None:
            messages.append(
                ModelMessage(
                    role="user",
                    content=(
                        f"URGENT: You have only {remaining} step(s) left before timeout. "
                        "Your NEXT action MUST be the `answer` tool with your best result so far. "
                        "Do not call more retrieval or exploration tools."
                    ),
                )
            )
        return messages

    def _choose_retrieval_action(
        self,
        *,
        task: PublicTask,
        state: AgentRuntimeState,
        decision: _RINDDecision,
        retrieval_keys: set[str],
    ) -> tuple[str, dict[str, Any]] | None:
        if self._tool_available("profile_context") and not _has_action(state, "profile_context"):
            action_input = {"max_depth": 4, "max_files": 16, "max_doc_chars": 3000}
            key = _action_key("profile_context", action_input)
            if key not in retrieval_keys:
                return "profile_context", action_input

        if self._tool_available("list_context") and not _has_action(state, "list_context"):
            action_input = {"max_depth": 4}
            key = _action_key("list_context", action_input)
            if key not in retrieval_keys:
                return "list_context", action_input

        files = _collect_file_candidates(state)
        if not files:
            return None

        profiled = _profiled_paths(state)
        query_terms = {
            token.norm
            for token in _tokenize(decision.query or task.question)
            if _is_semantic_token(token) and token.norm not in _QUERY_NOISE
        }

        knowledge_docs = [
            candidate
            for candidate in files.values()
            if Path(candidate.path).name.lower() == "knowledge.md" and candidate.path not in profiled
        ]
        if knowledge_docs and self._tool_available("read_doc"):
            action_input = {"path": knowledge_docs[0].path, "max_chars": 12000}
            key = _action_key("read_doc", action_input)
            if key not in retrieval_keys:
                return "read_doc", action_input

        text_docs = [
            candidate
            for candidate in files.values()
            if candidate.suffix in {".md", ".txt"}
        ]
        text_docs.sort(key=lambda candidate: _candidate_score(candidate, query_terms), reverse=True)
        if text_docs and self._tool_available("search_doc"):
            for candidate in text_docs:
                action_input = {
                    "path": candidate.path,
                    "query": decision.query or task.question,
                    "mode": "keyword",
                    "max_matches": 6,
                    "context_chars": self.config.retrieval_context_chars,
                }
                key = _action_key("search_doc", action_input)
                if key not in retrieval_keys:
                    return "search_doc", action_input

        structured = [
            candidate
            for candidate in files.values()
            if candidate.path not in profiled
            and candidate.suffix in {".csv", ".json", ".sqlite", ".db", ".sqlite3"}
        ]
        structured.sort(key=lambda candidate: _candidate_score(candidate, query_terms), reverse=True)
        for candidate in structured:
            if candidate.suffix == ".csv" and self._tool_available("profile_csv"):
                action_input = {"path": candidate.path}
                action = "profile_csv"
            elif candidate.suffix == ".json" and self._tool_available("profile_json"):
                action_input = {"path": candidate.path}
                action = "profile_json"
            elif candidate.suffix in {".sqlite", ".db", ".sqlite3"} and self._tool_available(
                "profile_database"
            ):
                action_input = {"path": candidate.path}
                action = "profile_database"
            else:
                continue
            key = _action_key(action, action_input)
            if key not in retrieval_keys:
                return action, action_input

        return None

    def _append_retrieval_step(
        self,
        *,
        task: PublicTask,
        state: AgentRuntimeState,
        step_index: int,
        action: str,
        action_input: dict[str, Any],
        decision: _RINDDecision,
        retrieval_keys: set[str],
        interrupted_action: str | None = None,
        interrupted_action_input: dict[str, Any] | None = None,
    ) -> bool:
        if decision.truncated_generation:
            thought = (
                "DRAGIN RIND preserved the interrupted generation prefix before retrieval. "
                f"Trigger token={decision.trigger_token!r}; QFS query={decision.query!r}."
            )
            raw_response = decision.truncated_generation
        else:
            thought = (
                "DRAGIN RIND triggered retrieval before continuing. "
                f"Trigger token={decision.trigger_token!r}; QFS query={decision.query!r}."
            )
            raw_response = _json_action(action, action_input, thought)
        retrieval_metadata = {
            "rind": {
                "trigger_token": decision.trigger_token,
                "trigger_start": decision.trigger_start,
                "trigger_end": decision.trigger_end,
                "score": decision.score,
                "threshold": self.config.rind_threshold,
                "entropy_proxy": decision.entropy_proxy,
                "attention_proxy": decision.attention_proxy,
                "semantic": decision.semantic,
            },
            "qfs": {
                "query": decision.query,
                "top_tokens": decision.qfs_tokens,
            },
            "note": decision.note,
        }
        if decision.truncated_generation:
            retrieval_metadata["interrupted_generation"] = {
                "preserved_prefix": True,
                "prefix_chars": len(decision.truncated_generation),
                "original_action": interrupted_action,
                "original_action_input": interrupted_action_input or {},
                "instruction": (
                    "Continue from the preserved assistant prefix after reading this retrieval "
                    "observation; revise the interrupted action if the evidence changes it."
                ),
            }
        try:
            tool_result = self.tools.execute(task, action, action_input)
            observation = {
                "ok": tool_result.ok,
                "tool": action,
                "content": tool_result.content,
                "dragin": retrieval_metadata,
            }
            state.steps.append(
                StepRecord(
                    step_index=step_index,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
            )
            retrieval_keys.add(_action_key(action, action_input))
            if tool_result.is_terminal:
                state.answer = tool_result.answer
                state.failure_reason = None
                return True
        except Exception as exc:  # noqa: BLE001 - record retrieval error in trace.
            observation = {
                "ok": False,
                "tool": action,
                "error": str(exc),
                "dragin": retrieval_metadata,
            }
            state.steps.append(
                StepRecord(
                    step_index=step_index,
                    thought=thought,
                    action=action,
                    action_input=action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                )
            )
            retrieval_keys.add(_action_key(action, action_input))
        return False

    def _forced_final_answer(self, task: PublicTask, state: AgentRuntimeState) -> None:
        if state.answer is not None:
            return
        forced_system = (
            "You ran out of steps. Based on the observations you already collected, "
            "produce your SINGLE best-guess answer now. Return EXACTLY one ```json fenced "
            "block with {\"thought\": \"...\", \"action\": \"answer\", \"action_input\": "
            "{\"columns\": [...], \"rows\": [[...]]}}. Do not call any other tool."
        )
        messages = [ModelMessage(role="system", content=forced_system)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        for step in state.steps[-6:]:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(
                ModelMessage(
                    role="user",
                    content=build_observation_prompt(step.observation, task_question=task.question),
                )
            )
        messages.append(ModelMessage(role="user", content="Submit the final answer now."))
        try:
            raw_response = self.model.complete(messages, json_object=True)
            model_step = parse_model_step(raw_response)
            if model_step.action != "answer":
                return
            tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
            state.steps.append(
                StepRecord(
                    step_index=len(state.steps) + 1,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation={
                        "ok": tool_result.ok,
                        "tool": "answer",
                        "content": tool_result.content,
                        "forced": True,
                    },
                    ok=tool_result.ok,
                )
            )
            if tool_result.is_terminal:
                state.answer = tool_result.answer
                state.failure_reason = None
        except Exception:
            pass

    def run(self, task: PublicTask) -> AgentRunResult:
        self._question_level = task.difficulty.lower() if task.difficulty else None

        state = AgentRuntimeState()
        retrieval_keys: set[str] = set()
        retrieval_count = 0
        max_error_retries = 2
        step_index = 0
        consecutive_errors = 0
        last_error_msg = ""
        last_action_key = ""
        repeat_count = 0
        max_repeats = 1
        hard_repeat_limit = 3
        fruitless_regex_count = 0

        while step_index < self.config.max_steps:
            step_index += 1

            if step_index == 1 and retrieval_count < self.config.max_retrievals:
                decision = _bootstrap_decision(task, self.config.qfs_top_n)
                retrieval = self._choose_retrieval_action(
                    task=task,
                    state=state,
                    decision=decision,
                    retrieval_keys=retrieval_keys,
                )
                if retrieval is not None:
                    action, action_input = retrieval
                    retrieval_count += 1
                    terminal = self._append_retrieval_step(
                        task=task,
                        state=state,
                        step_index=step_index,
                        action=action,
                        action_input=action_input,
                        decision=decision,
                        retrieval_keys=retrieval_keys,
                    )
                    if terminal:
                        break
                    continue

            raw_response = ""
            try:
                raw_response = self.model.complete(
                    self._build_messages(task, state, step_index), json_object=True
                )
                model_step = parse_model_step(raw_response)

                if (
                    model_step.action not in _RETRIEVAL_TOOLS
                    and retrieval_count < self.config.max_retrievals
                ):
                    decision = _detect_information_need(
                        generated_text=raw_response,
                        question=task.question,
                        threshold=self.config.rind_threshold,
                        qfs_top_n=self.config.qfs_top_n,
                    )
                    if decision is not None:
                        retrieval = self._choose_retrieval_action(
                            task=task,
                            state=state,
                            decision=decision,
                            retrieval_keys=retrieval_keys,
                        )
                        if retrieval is not None:
                            action, action_input = retrieval
                            retrieval_count += 1
                            terminal = self._append_retrieval_step(
                                task=task,
                                state=state,
                                step_index=step_index,
                                action=action,
                                action_input=action_input,
                                decision=decision,
                                retrieval_keys=retrieval_keys,
                                interrupted_action=model_step.action,
                                interrupted_action_input=model_step.action_input,
                            )
                            consecutive_errors = 0
                            last_error_msg = ""
                            if terminal:
                                break
                            continue

                if model_step.action == "answer" and (self.config.max_steps - step_index) >= 1:
                    preflight_hint = _answer_preflight_hint(task, model_step.action_input)
                    if preflight_hint is not None:
                        state.steps.append(
                            StepRecord(
                                step_index=step_index,
                                thought=model_step.thought,
                                action=model_step.action,
                                action_input=model_step.action_input,
                                raw_response=raw_response,
                                observation={
                                    "ok": False,
                                    "tool": "answer",
                                    "content": {"status": "preflight_rejected"},
                                    "hint": preflight_hint,
                                },
                                ok=False,
                            )
                        )
                        consecutive_errors = 0
                        last_error_msg = ""
                        continue

                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                step_record = StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                state.steps.append(step_record)
                consecutive_errors = 0
                last_error_msg = ""

                # Same targeted recovery hint ReAct gives on hard/silent failures
                # (no such column/table, syntax error, 0-row queries).
                tool_error_hint = classify_tool_error(
                    model_step.action, tool_result.ok, str(tool_result.content)
                )
                if tool_error_hint is None and tool_result.ok:
                    try:
                        from data_agent_baseline.tools.kg_store import literal_filter_hint
                        from data_agent_baseline.tools.semantic_match import resolve_model
                        tool_error_hint = literal_filter_hint(
                            model_step.action, model_step.action_input, tool_result.content,
                            task.context_dir, model=resolve_model(),
                        )
                    except Exception:  # noqa: BLE001
                        tool_error_hint = None
                if tool_error_hint is None and tool_result.ok:
                    tool_error_hint = empty_filter_hint(model_step.action, tool_result.content)
                if tool_error_hint is not None:
                    observation["hint"] = tool_error_hint.strip()

                action_key = _action_key(model_step.action, model_step.action_input)
                if action_key == last_action_key:
                    repeat_count += 1
                    if repeat_count >= max_repeats:
                        observation["hint"] = (
                            "You are repeating the same action. Stop and try a different approach "
                            "or submit your best answer now using the `answer` tool."
                        )
                    if repeat_count >= hard_repeat_limit:
                        state.failure_reason = (
                            f"Aborted: same action repeated {repeat_count + 1} times "
                            f"({model_step.action})."
                        )
                        break
                else:
                    repeat_count = 0
                last_action_key = action_key

                try:
                    content = tool_result.content if isinstance(tool_result.content, dict) else {}
                    is_search = model_step.action == "search_doc"
                    zero_matches = (
                        is_search
                        and (content.get("match_count") == 0 or content.get("error") is not None)
                    )
                    if zero_matches:
                        fruitless_regex_count += 1
                    else:
                        if is_search:
                            fruitless_regex_count = 0
                    if fruitless_regex_count >= 2:
                        observation["hint"] = (
                            "Your search_doc queries keep returning 0 matches or errors. "
                            "Switch to execute_python, inspect the raw file text, and parse it "
                            "with simple string operations."
                        )
                except Exception:
                    pass

                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
            except Exception as exc:
                error_msg = str(exc)
                consecutive_errors += 1
                observation = {
                    "ok": False,
                    "error": error_msg,
                    "hint": "Fix your JSON format or action. Model API error or invalid format.",
                }
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )
                same_error = error_msg == last_error_msg
                if not same_error and consecutive_errors <= max_error_retries:
                    step_index -= 1
                last_error_msg = error_msg
                if consecutive_errors >= 6:
                    state.failure_reason = (
                        f"Aborted: {consecutive_errors} consecutive errors. Last: {error_msg[:200]}"
                    )
                    break

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        if state.answer is None:
            self._forced_final_answer(task, state)

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
            question_level=self._question_level,
        )
