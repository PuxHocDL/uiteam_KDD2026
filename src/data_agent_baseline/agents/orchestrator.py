from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from data_agent_baseline.agents.analyst_prompt import ANALYST_RESPONSE_EXAMPLES, ANALYST_SYSTEM_PROMPT
from data_agent_baseline.agents.model import ModelAdapter, ModelMessage
from data_agent_baseline.agents.planner_prompt import PLANNER_RESPONSE_EXAMPLES, PLANNER_SYSTEM_PROMPT
from data_agent_baseline.agents.parsing import parse_model_step
from data_agent_baseline.agents.reasoning import classify_tool_error
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig, _answer_preflight_hint
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MultiAgentConfig:
    planner_max_steps: int = 10
    analyst_max_steps: int = 20


def _build_planner_system_prompt(tool_descriptions: str) -> str:
    return (
        f"{PLANNER_SYSTEM_PROMPT}\n\n"
        f"Available tools:\n{tool_descriptions}\n\n"
        f"{PLANNER_RESPONSE_EXAMPLES}\n\n"
        "You must always return a single ```json fenced block containing one JSON object "
        "with keys `thought`, `action`, and `action_input`, and no extra text."
    )


def _build_analyst_system_prompt(tool_descriptions: str) -> str:
    return (
        f"{ANALYST_SYSTEM_PROMPT}\n\n"
        f"Available tools:\n{tool_descriptions}\n\n"
        f"{ANALYST_RESPONSE_EXAMPLES}\n\n"
        "You must always return a single ```json fenced block containing one JSON object "
        "with keys `thought`, `action`, and `action_input`, and no extra text."
    )


def _build_planner_task_prompt(task: PublicTask) -> str:
    return (
        f"Question: {task.question}\n"
        "Explore the data and produce a detailed execution plan. "
        "When ready, call `submit_plan` with your plan."
    )


def _build_analyst_task_prompt(task: PublicTask, plan: str, context_summary: str) -> str:
    return (
        f"Question: {task.question}\n\n"
        f"## Data Context\n{context_summary}\n\n"
        f"## Plan\n{plan}\n\n"
        "Execute this plan. File paths are relative to the task context directory.\n"
        "CHECKLIST before calling `answer`:\n"
        "- Columns: ONLY those in Output Schema (no IDs, no extras). Did you use SELECT * by mistake?\n"
        "- Rows: matches plan's expected count? If too many, you're missing a WHERE/GROUP BY/LIMIT.\n"
        "- All WHERE filters from the plan are in your query?\n"
        "- Column names match source data (not renamed/merged)?"
    )


def _build_observation_prompt(observation: dict[str, Any]) -> str:
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"


class _PlannerToolRegistry:
    """Wraps a ToolRegistry to add the submit_plan pseudo-tool for the planner."""

    def __init__(self, base_tools: ToolRegistry) -> None:
        self._base = base_tools
        # Tools the planner is allowed to use (read-only exploration)
        self._allowed = {
            "list_context", "read_csv", "read_json", "read_doc",
            "profile_csv", "profile_json", "profile_database",
            "profile_context",
            "inspect_sqlite_schema", "execute_context_sql",
        }

    def describe_for_prompt(self) -> str:
        lines = []
        for name in sorted(self._base.specs):
            if name in self._allowed:
                spec = self._base.specs[name]
                lines.append(f"- {spec.name}: {spec.description}")
                lines.append(f"  input_schema: {spec.input_schema}")
        # Add submit_plan
        lines.append("- submit_plan: Submit your execution plan. This is the only valid terminating action for the planner.")
        lines.append('  input_schema: {"plan": "detailed markdown plan...", "context_summary": "brief data summary"}')
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        if action == "submit_plan":
            return ToolExecutionResult(
                ok=True,
                content={"status": "plan_submitted"},
                is_terminal=True,
            )
        if action not in self._allowed:
            raise KeyError(f"Planner cannot use tool: {action}. Use only exploration tools.")
        return self._base.execute(task, action, action_input)


class MultiAgentOrchestrator:
    """Orchestrates Planner → Analyst pipeline."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: MultiAgentConfig | None = None,
        memory_context: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or MultiAgentConfig()
        self.memory_context = memory_context or ""

    def _run_planner(self, task: PublicTask) -> tuple[str, str, list[StepRecord]]:
        """Run the planner agent. Returns (plan, context_summary, steps)."""
        planner_tools = _PlannerToolRegistry(self.tools)
        system_content = _build_planner_system_prompt(planner_tools.describe_for_prompt())

        state = AgentRuntimeState()
        plan = ""
        context_summary = ""
        step_index = 0
        consecutive_errors = 0
        last_error_msg = ""

        while step_index < self.config.planner_max_steps:
            step_index += 1

            messages = [ModelMessage(role="system", content=system_content)]
            task_prompt = _build_planner_task_prompt(task)
            if self.memory_context:
                task_prompt = self.memory_context + "\n\n" + task_prompt
            messages.append(ModelMessage(role="user", content=task_prompt))
            for step in state.steps:
                messages.append(ModelMessage(role="assistant", content=step.raw_response))
                messages.append(ModelMessage(role="user", content=_build_observation_prompt(step.observation)))

            raw_response = self.model.complete(messages, json_object=True)
            try:
                model_step = parse_model_step(raw_response)
                tool_result = planner_tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                state.steps.append(StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                ))
                consecutive_errors = 0
                last_error_msg = ""

                if tool_result.is_terminal:
                    plan = str(model_step.action_input.get("plan", ""))
                    context_summary = str(model_step.action_input.get("context_summary", ""))
                    break
            except Exception as exc:
                error_msg = str(exc)
                consecutive_errors += 1
                observation = {
                    "ok": False,
                    "error": error_msg,
                    "hint": "Fix your JSON format or use a valid exploration tool.",
                }
                state.steps.append(StepRecord(
                    step_index=step_index,
                    thought="",
                    action="__error__",
                    action_input={},
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                ))
                if error_msg != last_error_msg and consecutive_errors <= 2:
                    step_index -= 1
                last_error_msg = error_msg

        if not plan:
            plan = "No plan was produced. Explore the data yourself and answer the question."
            context_summary = "Planner did not complete exploration."

        return plan, context_summary, list(state.steps)

    def _run_analyst(self, task: PublicTask, plan: str, context_summary: str) -> tuple[AgentRuntimeState, list[StepRecord]]:
        """Run the analyst agent with the planner's output."""
        tool_descriptions = self.tools.describe_for_prompt()
        system_content = _build_analyst_system_prompt(tool_descriptions)

        state = AgentRuntimeState()
        step_index = 0
        consecutive_errors = 0
        last_error_msg = ""
        last_action_key = ""
        repeat_count = 0

        while step_index < self.config.analyst_max_steps:
            step_index += 1

            messages = [ModelMessage(role="system", content=system_content)]
            messages.append(ModelMessage(
                role="user",
                content=_build_analyst_task_prompt(task, plan, context_summary),
            ))
            for step in state.steps:
                messages.append(ModelMessage(role="assistant", content=step.raw_response))
                messages.append(ModelMessage(role="user", content=_build_observation_prompt(step.observation)))

            raw_response = self.model.complete(messages, json_object=True)
            try:
                model_step = parse_model_step(raw_response)

                # Answer preflight: catch obvious answer-shape mistakes (extra
                # columns, wrong row count) before the analyst commits — the
                # same guard ReAct/DRAGIN use, which the analyst previously lacked.
                if model_step.action == "answer" and (self.config.analyst_max_steps - step_index) >= 1:
                    preflight_hint = _answer_preflight_hint(task, model_step.action_input)
                    if preflight_hint is not None:
                        state.steps.append(StepRecord(
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
                        ))
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

                tool_error_hint = classify_tool_error(
                    model_step.action, tool_result.ok, str(tool_result.content)
                )
                if tool_error_hint is not None:
                    observation["hint"] = tool_error_hint.strip()

                action_key = json.dumps([model_step.action, model_step.action_input], sort_keys=True)
                if action_key == last_action_key:
                    repeat_count += 1
                    if repeat_count >= 2:
                        observation["hint"] = (
                            "You are repeating the same action. Try a DIFFERENT approach "
                            "or submit your best answer now."
                        )
                else:
                    repeat_count = 0
                last_action_key = action_key

                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
            except Exception as exc:
                error_msg = str(exc)
                consecutive_errors += 1
                observation = {
                    "ok": False,
                    "error": error_msg,
                    "hint": "Fix your JSON format. action_input must be a dict.",
                }
                state.steps.append(StepRecord(
                    step_index=step_index,
                    thought="",
                    action="__error__",
                    action_input={},
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                ))
                if error_msg != last_error_msg and consecutive_errors <= 2:
                    step_index -= 1
                last_error_msg = error_msg

        return state, list(state.steps)

    def _forced_final_answer(self, task: PublicTask, state: AgentRuntimeState) -> None:
        """Last-chance best-guess answer when the analyst exhausts its steps.

        Mirrors ReAct/DRAGIN: a run that ends with no answer scores 0, so we make
        one constrained attempt from the observations already collected rather
        than returning nothing.
        """
        if state.answer is not None:
            return
        forced_system = (
            "You ran out of steps. Based on the observations you already collected, "
            "produce your SINGLE best-guess answer now. Return EXACTLY one JSON object "
            "with {\"thought\": \"...\", \"action\": \"answer\", \"action_input\": "
            "{\"columns\": [...], \"rows\": [[...]]}}. Do not call any other tool. "
            "Return ONLY the columns the question asks for."
        )
        messages = [ModelMessage(role="system", content=forced_system)]
        messages.append(ModelMessage(role="user", content=f"Question: {task.question}"))
        for step in state.steps[-6:]:
            messages.append(ModelMessage(role="assistant", content=step.raw_response))
            messages.append(ModelMessage(role="user", content=_build_observation_prompt(step.observation)))
        messages.append(ModelMessage(role="user", content="Submit the final answer now."))
        try:
            raw_response = self.model.complete(messages, json_object=True)
            model_step = parse_model_step(raw_response)
            if model_step.action != "answer":
                return
            tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
            state.steps.append(StepRecord(
                step_index=len(state.steps) + 1,
                thought=model_step.thought,
                action=model_step.action,
                action_input=model_step.action_input,
                raw_response=raw_response,
                observation={"ok": tool_result.ok, "tool": "answer",
                             "content": tool_result.content, "forced": True},
                ok=tool_result.ok,
            ))
            if tool_result.is_terminal:
                state.answer = tool_result.answer
        except Exception:  # noqa: BLE001 - forced answer is best-effort.
            pass

    def run(self, task: PublicTask) -> AgentRunResult:
        """Full pipeline: Planner → Analyst."""
        logger.info("=== PLANNER phase for %s ===", task.task_id)
        plan, context_summary, planner_steps = self._run_planner(task)
        logger.info("Planner completed in %d steps. Plan length: %d chars", len(planner_steps), len(plan))

        logger.info("=== ANALYST phase for %s ===", task.task_id)
        analyst_state, _ = self._run_analyst(task, plan, context_summary)
        if analyst_state.answer is None:
            self._forced_final_answer(task, analyst_state)
        analyst_steps = list(analyst_state.steps)
        logger.info("Analyst completed in %d steps", len(analyst_steps))

        # Combine all steps for trace
        all_steps: list[StepRecord] = []
        for step in planner_steps:
            all_steps.append(StepRecord(
                step_index=step.step_index,
                thought=f"[PLANNER] {step.thought}",
                action=step.action,
                action_input=step.action_input,
                raw_response=step.raw_response,
                observation=step.observation,
                ok=step.ok,
            ))
        for i, step in enumerate(analyst_steps):
            all_steps.append(StepRecord(
                step_index=len(planner_steps) + i + 1,
                thought=f"[ANALYST] {step.thought}",
                action=step.action,
                action_input=step.action_input,
                raw_response=step.raw_response,
                observation=step.observation,
                ok=step.ok,
            ))

        failure_reason = None
        if analyst_state.answer is None:
            failure_reason = "Analyst did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=analyst_state.answer,
            steps=all_steps,
            failure_reason=failure_reason,
        )
