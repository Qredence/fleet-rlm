"""Safe diagnostics for failures before the Turn stream starts."""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes.turns import router
from fleet_rlm.chat.turn_preparation import TurnPreparationUnavailable
from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import ProviderRequestError


class _UnavailableCoordinator:
    def __init__(self, cause: BaseException) -> None:
        self._cause = cause

    async def open(self, _command):
        try:
            raise self._cause
        except BaseException as cause:
            raise TurnPreparationUnavailable("Turn environment is unavailable") from cause


def _client(cause: BaseException) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.composition_ready = True
    app.state.turn_coordinator = _UnavailableCoordinator(cause)
    install_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


def _post(client: TestClient, *, headers: dict[str, str] | None = None):
    merged = {"Idempotency-Key": "diagnostics-test", **(headers or {})}
    return client.post(f"/api/sessions/{uuid4()}/turns", json={"text": "hello"}, headers=merged)


def test_preparation_503_returns_incoming_request_id_and_logs_safe_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cause = ProviderRequestError(
        "provider failed for [redacted] at [redacted]",
        cause_type="ProviderResponseError",
        status_code=503,
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(_client(cause), headers={"X-Request-Id": "turn-corr-123"})

    assert response.status_code == 503
    assert response.json() == {"code": "turn_unavailable", "message": "Turn is unavailable"}
    assert response.headers["x-request-id"] == "turn-corr-123"
    assert response.headers["x-correlation-id"] == "turn-corr-123"
    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert "correlation_id=turn-corr-123" in message
    assert "cause_type=provider_5xx" in message
    assert "provider_status_category=5xx" in message
    assert "message=provider failed for [redacted] at [redacted]" in message


def test_preparation_503_generates_uuid_correlation_id() -> None:
    response = _post(_client(ProviderRequestError("offline", cause_type="ConnectionError")))

    assert response.headers["x-request-id"] == response.headers["x-correlation-id"]
    assert UUID(response.headers["x-correlation-id"])


def test_preparation_503_prefers_request_id_over_correlation_id() -> None:
    response = _post(
        _client(ProviderRequestError("offline", cause_type="ConnectionError")),
        headers={"X-Request-Id": "request-wins", "X-Correlation-Id": "correlation-loses"},
    )

    assert response.headers["x-request-id"] == "request-wins"
    assert response.headers["x-correlation-id"] == "request-wins"


def test_unknown_preparation_failure_logs_exception_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "api_key=never-log-this /Users/zach/private.env"

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(_client(RuntimeError(secret)), headers={"X-Correlation-Id": "unknown-corr"})

    assert response.status_code == 503
    assert response.headers["x-correlation-id"] == "unknown-corr"
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert "never-log-this" not in caplog.text
    assert "/Users/zach" not in caplog.text


def test_unknown_adapter_failure_logs_cause_class_only(caplog: pytest.LogCaptureFixture) -> None:
    cause = ProviderRequestError(
        "api_key=never-log-this /Users/zach/private.env",
        cause_type="UnexpectedSDKError",
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(_client(cause), headers={"X-Request-Id": "adapter-unknown"})

    assert response.status_code == 503
    assert "message=UnexpectedSDKError" in caplog.text
    assert "never-log-this" not in caplog.text
    assert "/Users/zach" not in caplog.text
