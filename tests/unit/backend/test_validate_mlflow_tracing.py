"""Unit contracts for the standalone MLflow tracing verifier."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def verifier() -> ModuleType:
    path = Path(__file__).parents[3] / "scripts" / "validate_mlflow_tracing.py"
    spec = importlib.util.spec_from_file_location("validate_mlflow_tracing", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_settings_are_resolved_from_fleet_policy(verifier: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        mlflow_tracing_enabled=True,
        mlflow_tracking_uri="databricks",
        mlflow_experiment_name="/Users/zachary@qredence.ai/fleet-rlm-traces",
        mlflow_trace_catalog="uscentral",
        mlflow_trace_schema="default",
        mlflow_trace_table_prefix="fleet_rlm",
        mlflow_tracing_sql_warehouse_id="4d07bd43a3ddfff2",
    )
    monkeypatch.setattr(verifier, "load_runtime_settings", lambda: settings)
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "stale-environment-value")

    assert verifier._tracing_settings() is settings


def test_local_settings_are_resolved_without_managed_fields(
    verifier: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        mlflow_tracing_enabled=True,
        mlflow_tracking_uri="http://127.0.0.1:5001",
        mlflow_experiment_name="fleet-rlm",
        mlflow_trace_catalog=None,
        mlflow_trace_schema=None,
        mlflow_trace_table_prefix=None,
        mlflow_tracing_sql_warehouse_id=None,
    )
    monkeypatch.setattr(verifier, "load_runtime_settings", lambda: settings)

    assert verifier._tracing_settings() is settings


def test_tracing_settings_reject_incomplete_managed_policy(
    verifier: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verifier,
        "load_runtime_settings",
        lambda: SimpleNamespace(
            mlflow_tracing_enabled=True,
            mlflow_tracking_uri="databricks",
            mlflow_experiment_name="fleet-managed",
            mlflow_trace_catalog=None,
            mlflow_trace_schema=None,
            mlflow_trace_table_prefix=None,
            mlflow_tracing_sql_warehouse_id=None,
        ),
    )

    with pytest.raises(RuntimeError, match="incomplete Managed Databricks MLflow"):
        verifier._tracing_settings()


def test_main_emits_and_retrieves_local_trace(
    verifier: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        mlflow_tracing_enabled=True,
        mlflow_tracking_uri="http://127.0.0.1:5001",
        mlflow_experiment_name="fleet-rlm",
        mlflow_trace_catalog=None,
        mlflow_trace_schema=None,
        mlflow_trace_table_prefix=None,
        mlflow_tracing_sql_warehouse_id=None,
    )
    calls = SimpleNamespace(tracking_uri=None, experiment_name=None)
    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda uri: setattr(calls, "tracking_uri", uri)  # type: ignore[attr-defined]

    def set_experiment(*, experiment_name: str) -> SimpleNamespace:
        calls.experiment_name = experiment_name
        return SimpleNamespace(experiment_id="1")

    def trace(*, name: str):
        assert name == "fleet_mlflow_smoke"

        def decorate(function):
            return function

        return decorate

    mlflow.set_experiment = set_experiment  # type: ignore[attr-defined]
    mlflow.trace = trace  # type: ignore[attr-defined]
    mlflow.get_last_active_trace_id = lambda: "trace-1"  # type: ignore[attr-defined]

    def get_trace(trace_id: str, *, flush: bool) -> SimpleNamespace:
        assert trace_id == "trace-1"
        assert flush is True
        return SimpleNamespace(
            info=SimpleNamespace(
                state="OK",
                trace_id="trace-1",
                request_preview="request",
                response_preview="response",
            ),
            data=SimpleNamespace(spans=[SimpleNamespace(parent_span_id=None)]),
        )

    mlflow.get_trace = get_trace  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setattr(verifier, "_load_repository_env", lambda: None)
    monkeypatch.setattr(verifier, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["validate_mlflow_tracing.py"])

    assert verifier.main() == 0
    assert calls.tracking_uri == "http://127.0.0.1:5001"
    assert calls.experiment_name == "fleet-rlm"
    output = capsys.readouterr().out
    assert "trace_id=trace-1" in output
    assert "tracking_uri=http://127.0.0.1:5001" in output
    assert "status=PASS" in output


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
