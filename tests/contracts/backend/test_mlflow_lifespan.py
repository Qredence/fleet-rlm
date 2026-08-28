"""End-to-end MLflow lifecycle certification through the public Turn API."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fleet_rlm.composition.testing import create_testing_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.mlflow import MLflowRuntimeState


def _post_canned_turn(client: TestClient) -> None:
    session = client.post("/api/sessions", json={})
    assert session.status_code == 201
    response = client.post(
        f"/api/sessions/{session.json()['id']}/turns",
        json={"text": "hello"},
        headers={"Idempotency-Key": "mlflow-lifecycle-proof"},
    )
    assert response.status_code == 200
    frames = response.content.decode().split("data: ")
    assert frames[-1].strip() == "[DONE]"
    chunks = [json.loads(frame.splitlines()[0]) for frame in frames[1:-1]]
    assert any(chunk["type"] == "start" for chunk in chunks)
    assert chunks[-1]["type"] == "finish"


def test_public_turn_succeeds_when_tracing_is_disabled_by_policy() -> None:
    app = create_testing_app(settings=Settings(mlflow_tracing_enabled=False))

    with TestClient(app) as client:
        _post_canned_turn(client)
        assert app.state.mlflow_runtime.state is MLflowRuntimeState.UNAVAILABLE
    assert app.state.mlflow_runtime.state is MLflowRuntimeState.CLOSED


def test_public_turn_succeeds_when_tracing_setup_is_unavailable(monkeypatch) -> None:
    configure_calls: list[Settings] = []

    def unavailable(settings: Settings) -> bool:
        configure_calls.append(settings)
        return False

    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", unavailable)
    app = create_testing_app(
        settings=Settings(
            mlflow_tracing_enabled=True,
            mlflow_experiment_name="fleet-test",
            mlflow_tracking_uri="http://127.0.0.1:5001",
        )
    )

    with TestClient(app) as client:
        _post_canned_turn(client)
        assert app.state.mlflow_runtime.state is MLflowRuntimeState.UNAVAILABLE
    assert len(configure_calls) == 1
    assert app.state.mlflow_runtime.state is MLflowRuntimeState.CLOSED


def test_two_fresh_app_lifespans_can_reconfig_after_a_failed_attempt(monkeypatch) -> None:
    outcomes = [False, True]
    configure_calls: list[str] = []

    def configure(_settings: Settings) -> bool:
        configure_calls.append("configure")
        return outcomes[len(configure_calls) - 1]

    monkeypatch.setattr("fleet_rlm.observability.tracing.configure_tracing", configure)
    settings = Settings(
        mlflow_tracing_enabled=True,
        mlflow_experiment_name="fleet-test",
        mlflow_tracking_uri="http://127.0.0.1:5001",
    )
    first = create_testing_app(settings=settings)
    second = create_testing_app(settings=settings)

    with TestClient(first):
        assert first.state.mlflow_runtime.state is MLflowRuntimeState.UNAVAILABLE
    with TestClient(second):
        assert second.state.mlflow_runtime.state is MLflowRuntimeState.ACTIVE

    assert configure_calls == ["configure", "configure"]
