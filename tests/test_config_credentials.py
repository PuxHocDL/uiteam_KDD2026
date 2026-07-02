"""Tests for credential-override precedence: CLI flag > process env
(DABENCH_*/AZURE_OPENAI_*) > YAML config value."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_agent_baseline.config import CredentialOverrides, load_app_config

_BASE_YAML = """
dataset:
  root_path: data/public/input
agent:
  model: yaml-model
  api_base: https://yaml.example.com
  api_key: yaml-key
  api_version: ""
run:
  output_dir: artifacts/runs
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(_BASE_YAML, encoding="utf-8")
    return path


def test_no_overrides_uses_yaml_values(config_path: Path):
    app_config = load_app_config(config_path)
    assert app_config.agent.model == "yaml-model"
    assert app_config.agent.api_key == "yaml-key"


def test_cli_flag_overrides_yaml(config_path: Path):
    app_config = load_app_config(config_path, credentials=CredentialOverrides(api_key="from-cli-flag"))
    assert app_config.agent.api_key == "from-cli-flag"
    # Fields not overridden still fall back to YAML.
    assert app_config.agent.model == "yaml-model"


def test_cli_flag_overrides_process_env(config_path: Path, monkeypatch):
    monkeypatch.setenv("DABENCH_MODEL", "env-model")
    app_config = load_app_config(config_path, credentials=CredentialOverrides(model="from-cli-flag"))
    assert app_config.agent.model == "from-cli-flag"


def test_process_env_overrides_yaml_when_no_flag(config_path: Path, monkeypatch):
    monkeypatch.setenv("DABENCH_MODEL", "env-model")
    app_config = load_app_config(config_path)
    assert app_config.agent.model == "env-model"


def test_azure_env_alias_is_recognized(config_path: Path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "azure-alias-model")
    app_config = load_app_config(config_path)
    assert app_config.agent.model == "azure-alias-model"
