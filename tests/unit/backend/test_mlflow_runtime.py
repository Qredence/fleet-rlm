"""Lifespan-owned MLflow runtime contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config import FleetConfigurationError, Settings
from fleet_rlm.observability.mlflow_runtime import MLflowRuntime, MLflowRuntimeState


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mlflow_tracing_enabled": True,
        "mlflow_experiment_name": "fleet-test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_runtime_tracks_inactive_starting_active_and_explicit_flush() -> None:
    calls: list[str] = []
    runtime = MLflowRuntime(_settings())

    def configure(_settings: Settings) -> bool:
        assert runtime.state is MLflowRuntimeState.STARTING
        calls.append("configure")
        return True

    def flush() -> None:
        calls.append("flush")

    runtime._configure = configure
    runtime._flush = flush

    assert runtime.state is MLflowRuntimeState.INACTIVE
    await runtime.start()
    assert runtime.state is MLflowRuntimeState.ACTIVE
    assert runtime.active is True
    await runtime.close()

    assert calls == ["configure", "flush"]
    assert runtime.state is MLflowRuntimeState.CLOSED
    assert runtime.active is False


@pytest.mark.asyncio
async def test_runtime_unavailable_is_fail_soft_and_never_flushes() -> None:
    calls: list[str] = []
    runtime = MLflowRuntime(_settings())
    runtime._configure = lambda _settings: calls.append("configure") or False
    runtime._flush = lambda: calls.append("flush")

    await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE
    await runtime.close()

    assert calls == ["configure"]
    assert runtime.state is MLflowRuntimeState.CLOSED


@pytest.mark.asyncio
async def test_runtime_setup_error_is_fail_soft_by_default() -> None:
    calls: list[str] = []

    def fail(_settings: Settings) -> bool:
        raise RuntimeError("mlflow offline")

    runtime = MLflowRuntime(_settings())
    runtime._configure = fail
    runtime._flush = lambda: calls.append("flush")

    await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE
    await runtime.close()
    assert calls == []


@pytest.mark.asyncio
async def test_intentional_trace_configuration_error_still_surfaces_and_marks_unavailable() -> None:
    def reject(_settings: Settings) -> bool:
        raise FleetConfigurationError("destination conflict")

    runtime = MLflowRuntime(_settings())
    runtime._configure = reject

    with pytest.raises(FleetConfigurationError, match="destination conflict"):
        await runtime.start()
    assert runtime.state is MLflowRuntimeState.UNAVAILABLE


@pytest.mark.asyncio
async def test_closed_lifespan_retry_can_configure_again_without_sticky_failure() -> None:
    results = [False, True]
    calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        calls.append("configure")
        return results[len(calls) - 1]

    first = MLflowRuntime(_settings())
    first._configure = configure
    await first.start()
    await first.close()
    assert first.state is MLflowRuntimeState.CLOSED

    second = MLflowRuntime(_settings())
    second._configure = configure
    second._flush = lambda: calls.append("flush")
    await second.start()
    assert second.active is True
    await second.close()

    assert calls == ["configure", "configure", "flush"]


def test_create_app_constructs_mlflow_runtime_without_contacting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        calls.append("configure")
        return False

    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", configure)
    app = create_app(settings=_settings(mlflow_tracing_enabled=False))

    assert calls == []
    assert app.state.mlflow_runtime.state is MLflowRuntimeState.INACTIVE


def test_app_lifespan_starts_tracing_and_closes_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        calls.append("configure")
        return True

    def flush() -> None:
        calls.append("flush")

    def install(app, settings, *, database):
        del app, settings
        return SimpleNamespace(
            run_state_store=SimpleNamespace(),
            run_cleanup_supervisor=None,
            database=database,
        )

    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", configure)
    monkeypatch.setattr("fleet_rlm.observability.tracing.flush_tracing", flush)
    app = create_app(settings=_settings(), _composition_installer=install)

    assert calls == []
    with TestClient(app) as client:
        assert calls == ["configure"]
        assert client.get("/openapi.json").status_code == 200
    assert calls == ["configure", "flush"]
    assert app.state.mlflow_runtime.state is MLflowRuntimeState.CLOSED
