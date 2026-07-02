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
    agent_mode: str = "single"  # "single"/"react", "multi", "dragin", or "hybrid_b"
    max_steps: int = 30
    temperature: float = 0.0
    # Constrain every reasoning step to a single JSON object via the provider's
    # JSON mode. Auto-degrades if the endpoint rejects it. Set false for
    # endpoints that don't support response_format=json_object at all.
    force_json: bool = True
    # Optional determinism / budget knobs passed straight to the chat API.
    seed: int | None = None
    request_max_tokens: int | None = None
    dragin_rind_threshold: float = 0.28
    dragin_qfs_top_n: int = 12
    dragin_max_retrievals: int = 4
    hybrid_hard_min_signals: int = 2


@dataclass(frozen=True, slots=True)
class RunConfig:
    output_dir: Path = field(default_factory=_default_run_output_dir)
    run_id: str | None = None
    max_workers: int = 24
    task_timeout_seconds: int = 1350
    difficulties: tuple[str, ...] = ()
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


def _optional_int(raw_value: object, default_value: int | None) -> int | None:
    """Parse an optional int config value; blank/None falls back to default."""
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        return default_value
    return int(raw_value)


# Two accepted spellings per credential, checked in this order: an explicit
# CLI flag, then the process env under either name. The AZURE_OPENAI_* names
# match the standard Azure SDK convention (so `docker run -e
# AZURE_OPENAI_API_KEY=...` works with zero renaming); DABENCH_* is the
# provider-agnostic fallback for non-Azure OpenAI-compatible endpoints.
_MODEL_ENV_KEYS = ("DABENCH_MODEL", "AZURE_OPENAI_DEPLOYMENT")
_API_BASE_ENV_KEYS = ("DABENCH_API_BASE", "AZURE_OPENAI_ENDPOINT")
_API_KEY_ENV_KEYS = ("DABENCH_API_KEY", "AZURE_OPENAI_API_KEY")
_API_VERSION_ENV_KEYS = ("DABENCH_API_VERSION", "AZURE_OPENAI_API_VERSION")


def _resolve_credential(
    cli_value: str | None,
    env_keys: tuple[str, ...],
    yaml_value: str,
) -> str:
    """Precedence: explicit CLI flag > process env (DABENCH_*/AZURE_OPENAI_*) > YAML config."""
    if cli_value:
        return cli_value
    for key in env_keys:
        if os.environ.get(key):
            return os.environ[key]
    return yaml_value


@dataclass(frozen=True, slots=True)
class CredentialOverrides:
    """Optional overrides for agent.{model,api_base,api_key,api_version},
    layered on top of whatever the YAML config file has. Lets `dabench` run
    with a config that has no secret baked in — pass credentials via
    `--model`/`--api-base`/`--api-key`/`--api-version` flags, or process env
    vars (DABENCH_* or AZURE_OPENAI_*) instead."""

    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    api_version: str | None = None


def _normalize_difficulties(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()

    if isinstance(raw_value, str):
        candidates = [part.strip() for part in raw_value.split(",")]
    elif isinstance(raw_value, list):
        candidates = [str(part).strip() for part in raw_value]
    else:
        raise ValueError("run.difficulties must be a list like [easy, hard] or a comma-separated string.")

    normalized: list[str] = []
    allowed = {"easy", "medium", "hard", "extreme"}
    for candidate in candidates:
        if not candidate:
            continue
        lowered = candidate.lower()
        if lowered not in allowed:
            raise ValueError(
                f"Unsupported difficulty {candidate!r} in run.difficulties. "
                "Expected only: easy, medium, hard, extreme."
            )
        if lowered not in normalized:
            normalized.append(lowered)
    return tuple(normalized)


def load_app_config(
    config_path: Path,
    *,
    credentials: CredentialOverrides | None = None,
) -> AppConfig:
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

    credentials = credentials or CredentialOverrides()

    resolved_model = _resolve_credential(
        credentials.model, _MODEL_ENV_KEYS,
        str(agent_payload.get("model", agent_defaults.model)),
    )
    resolved_api_base = _resolve_credential(
        credentials.api_base, _API_BASE_ENV_KEYS,
        str(agent_payload.get("api_base", agent_defaults.api_base)),
    )
    resolved_api_key = _resolve_credential(
        credentials.api_key, _API_KEY_ENV_KEYS,
        str(agent_payload.get("api_key", agent_defaults.api_key)),
    )
    resolved_api_version = _resolve_credential(
        credentials.api_version, _API_VERSION_ENV_KEYS,
        str(agent_payload.get("api_version", agent_defaults.api_version)),
    )

    agent_config = AgentConfig(
        model=resolved_model,
        api_base=resolved_api_base,
        api_key=resolved_api_key,
        api_version=resolved_api_version,
        agent_mode=str(agent_payload.get("agent_mode", agent_defaults.agent_mode)).lower(),
        max_steps=int(agent_payload.get("max_steps", agent_defaults.max_steps)),
        temperature=float(agent_payload.get("temperature", agent_defaults.temperature)),
        force_json=bool(agent_payload.get("force_json", agent_defaults.force_json)),
        seed=_optional_int(agent_payload.get("seed"), agent_defaults.seed),
        request_max_tokens=_optional_int(
            agent_payload.get("request_max_tokens"), agent_defaults.request_max_tokens
        ),
        dragin_rind_threshold=float(
            agent_payload.get("dragin_rind_threshold", agent_defaults.dragin_rind_threshold)
        ),
        dragin_qfs_top_n=int(
            agent_payload.get("dragin_qfs_top_n", agent_defaults.dragin_qfs_top_n)
        ),
        dragin_max_retrievals=int(
            agent_payload.get("dragin_max_retrievals", agent_defaults.dragin_max_retrievals)
        ),
        hybrid_hard_min_signals=int(
            agent_payload.get("hybrid_hard_min_signals", agent_defaults.hybrid_hard_min_signals)
        ),
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
        difficulties=_normalize_difficulties(run_payload.get("difficulties")),
        eval_mode=_is_eval_mode(),
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
