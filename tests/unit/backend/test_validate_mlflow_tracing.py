"""Unit contracts for the standalone MLflow tracing verifier."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def verifier() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / "validate_mlflow_tracing.py"
    spec = importlib.util.spec_from_file_location("validate_mlflow_tracing", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_option_overrides_process_environment(verifier: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "from-environment")

    assert verifier._resolve_option("from-cli", "FLEET_MLFLOW_TRACE_SCHEMA") == "from-cli"


def test_environment_option_is_used_when_cli_option_is_missing(
    verifier: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "from-environment")

    assert verifier._resolve_option(None, "FLEET_MLFLOW_TRACE_SCHEMA") == "from-environment"


def test_missing_required_option_fails_closed(verifier: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_MLFLOW_TRACE_SCHEMA", raising=False)

    with pytest.raises(RuntimeError, match="FLEET_MLFLOW_TRACE_SCHEMA is required"):
        verifier._resolve_option(None, "FLEET_MLFLOW_TRACE_SCHEMA")


def test_tables_uses_configured_cli_profile(verifier: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"full_name": "cat.sch.tbl"}]')
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed

    monkeypatch.setattr(verifier.subprocess, "run", run)

    assert verifier._tables("profile", "cat.sch") == {"cat.sch.tbl"}
    assert calls == [["databricks", "tables", "list", "cat", "sch", "--profile", "profile", "-o", "json"]]


def test_tables_uses_default_auth_when_profile_is_not_configured(
    verifier: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]")
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return completed

    monkeypatch.setattr(verifier.subprocess, "run", run)

    assert verifier._tables(None, "cat.sch") == set()
    assert calls == [["databricks", "tables", "list", "cat", "sch", "-o", "json"]]
