from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.api.bootstrap_observability import (
    initialize_mlflow_runtime_service,
    resolve_mlflow_auto_start_enabled,
)
from fleet_rlm.api.dependencies import ServerState


@pytest.mark.parametrize(
    ("app_env", "tracking_uri", "auto_start_env", "expected"),
    [
        ("local", "http://127.0.0.1:5001", None, True),
        ("local", "http://localhost:5001", None, True),
        ("local", "http://127.0.0.1:5001", "false", False),
        ("local", "http://127.0.0.1:5001", "true", True),
        ("local", "https://mlflow.example.com", None, False),
        ("staging", "http://127.0.0.1:5001", None, False),
    ],
)
def test_resolve_mlflow_auto_start_enabled(
    app_env: str,
    tracking_uri: str,
    auto_start_env: str | None,
    expected: bool,
) -> None:
    assert (
        resolve_mlflow_auto_start_enabled(
            app_env=app_env,
            mlflow_enabled=True,
            tracking_uri=tracking_uri,
            auto_start_env=auto_start_env,
        )
        is expected
    )


@pytest.mark.asyncio
async def test_initialize_mlflow_runtime_service_infers_local_auto_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = ServerState()
    started: list[tuple[str, str]] = []

    async def _fake_start_mlflow_server(
        *,
        app_env: str,
        tracking_uri: str,
    ) -> SimpleNamespace:
        started.append((app_env, tracking_uri))
        return SimpleNamespace(pid=1234)

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001")
    monkeypatch.delenv("MLFLOW_AUTO_START", raising=False)
    monkeypatch.setattr(
        "fleet_rlm.api.bootstrap_observability.start_mlflow_server",
        _fake_start_mlflow_server,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
        lambda config: True,
    )

    await initialize_mlflow_runtime_service(state, app_env="local")

    assert started == [("local", "http://127.0.0.1:5001")]
    assert state.mlflow_server_process is not None
    assert state.optional_service_status["mlflow"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("app_env", "tracking_uri", "auto_start_env"),
    [
        ("local", "http://127.0.0.1:5001", "false"),
        ("local", "https://mlflow.example.com", None),
        ("staging", "http://127.0.0.1:5001", None),
    ],
)
async def test_initialize_mlflow_runtime_service_skips_auto_start_when_disabled_or_non_local(
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    tracking_uri: str,
    auto_start_env: str | None,
) -> None:
    state = ServerState()
    started: list[tuple[str, str]] = []

    async def _fake_start_mlflow_server(
        *,
        app_env: str,
        tracking_uri: str,
    ) -> SimpleNamespace:
        started.append((app_env, tracking_uri))
        return SimpleNamespace(pid=1234)

    monkeypatch.setenv("MLFLOW_ENABLED", "true")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", tracking_uri)
    if auto_start_env is None:
        monkeypatch.delenv("MLFLOW_AUTO_START", raising=False)
    else:
        monkeypatch.setenv("MLFLOW_AUTO_START", auto_start_env)
    monkeypatch.setattr(
        "fleet_rlm.api.bootstrap_observability.start_mlflow_server",
        _fake_start_mlflow_server,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.observability.mlflow_runtime.initialize_mlflow",
        lambda config: True,
    )

    await initialize_mlflow_runtime_service(state, app_env=app_env)

    assert started == []
    assert state.mlflow_server_process is None
    assert state.optional_service_status["mlflow"] == "ready"
