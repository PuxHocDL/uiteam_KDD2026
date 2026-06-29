from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _is_eval_mode() -> bool:
    """Detect if running inside the evaluation Docker environment."""
    return "MODEL_API_URL" in os.environ


def _default_dataset_root() -> Path:
    if _is_eval_mode():
        return Path("/input")
    return PROJECT_ROOT / "data" / "public" / "input"


def _default_run_output_dir() -> Path:
    if _is_eval_mode():
        return Path("/output")
    return PROJECT_ROOT / "artifacts" / "runs"


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    root_path: Path = field(default_factory=_default_dataset_root)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = "gpt-4.1-mini"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_version: str = ""
    agent_mode: str = "single"  # "single" = original ReAct, "multi" = Planner→Analyst
    max_steps: int = 30
    temperature: float = 0.0


@dataclass(frozen=True, slots=True)
class RunConfig:
    output_dir: Path = field(default_factory=_default_run_output_dir)
    run_id: str | None = None
    max_workers: int = 24
    task_timeout_seconds: int = 1350
    eval_mode: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunConfig = field(default_factory=RunConfig)


def _path_value(raw_value: str | None, default_value: Path) -> Path:
    if not raw_value:
        return default_value
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def load_app_config(config_path: Path) -> AppConfig:
    payload = yaml.safe_load(config_path.read_text()) or {}
    dataset_defaults = DatasetConfig()
    agent_defaults = AgentConfig()
    run_defaults = RunConfig()

    dataset_payload = payload.get("dataset", {})
    agent_payload = payload.get("agent", {})
    run_payload = payload.get("run", {})

    dataset_config = DatasetConfig(
        root_path=_path_value(dataset_payload.get("root_path"), dataset_defaults.root_path),
    )
    agent_config = AgentConfig(
        model=str(agent_payload.get("model", agent_defaults.model)),
        api_base=str(agent_payload.get("api_base", agent_defaults.api_base)),
        api_key=str(agent_payload.get("api_key", agent_defaults.api_key)),
        api_version=str(agent_payload.get("api_version", agent_defaults.api_version)),
        agent_mode=str(agent_payload.get("agent_mode", agent_defaults.agent_mode)),
        max_steps=int(agent_payload.get("max_steps", agent_defaults.max_steps)),
        temperature=float(agent_payload.get("temperature", agent_defaults.temperature)),
    )
    raw_run_id = run_payload.get("run_id")
    run_id = run_defaults.run_id
    if raw_run_id is not None:
        normalized_run_id = str(raw_run_id).strip()
        run_id = normalized_run_id or None

    run_config = RunConfig(
        output_dir=_path_value(run_payload.get("output_dir"), run_defaults.output_dir),
        run_id=run_id,
        max_workers=int(run_payload.get("max_workers", run_defaults.max_workers)),
        task_timeout_seconds=int(run_payload.get("task_timeout_seconds", run_defaults.task_timeout_seconds)),
        eval_mode=_is_eval_mode(),
    )

    # Eval-mode: env vars are the only legitimate source for model endpoint/key
    # (KDD Cup 2026 rule 5.2 — hardcoding is strictly prohibited). MODEL_API_URL
    # is used verbatim (organizer's vLLM endpoint already includes /v1).
    if _is_eval_mode():
        agent_config = AgentConfig(
            model=os.environ.get("MODEL_NAME", agent_config.model),
            api_base=os.environ["MODEL_API_URL"],
            api_key=os.environ.get("MODEL_API_KEY", "EMPTY"),
            api_version=agent_config.api_version,
            agent_mode=agent_config.agent_mode,
            max_steps=agent_config.max_steps,
            temperature=agent_config.temperature,
        )

    return AppConfig(dataset=dataset_config, agent=agent_config, run=run_config)


class _TeeStream:
    """Write to multiple streams simultaneously."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def setup_eval_logging() -> None:
    """Redirect stdout/stderr to /logs/runtime.log when in eval mode."""
    logs_dir = Path("/logs")
    if not logs_dir.is_dir():
        return
    log_file = open(logs_dir / "runtime.log", "w", encoding="utf-8")  # noqa: SIM115
    sys.stdout = _TeeStream(sys.__stdout__, log_file)
    sys.stderr = _TeeStream(sys.__stderr__, log_file)
