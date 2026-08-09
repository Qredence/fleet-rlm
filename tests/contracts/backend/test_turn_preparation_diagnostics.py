"""Safe diagnostics for failures projected inside the Turn stream."""

from __future__ import annotations

import json
import logging
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.errors import install_error_handlers
from fleet_rlm.api.routes.turns import router
from fleet_rlm.chat.run_lifecycle import RunLifecycleUnavailableError
from fleet_rlm.chat.run_preparation import RunPreparationTimeoutError, RunPreparationUnavailableError
from fleet_rlm.composition.inventory import RuntimeInventory
from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import ProviderRequestError


class _FailingCoordinator:
    def __init__(self, cause: BaseException) -> None:
        self._cause = cause

    async def open(self, _command):
        if isinstance(self._cause, RunPreparationTimeoutError):
            raise self._cause
        if isinstance(self._cause, RunLifecycleUnavailableError):
            raise self._cause
        try:
            raise self._cause
        except BaseException as cause:
            raise RunPreparationUnavailableError("Turn environment is unavailable") from cause


def _client(cause: BaseException) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings()
    app.state.composition_ready = True
    app.state.runtime_inventory = RuntimeInventory(turn_coordinator=_FailingCoordinator(cause))
    install_error_handlers(app)
    app.include_router(router)
    return TestClient(app)


def _post(client: TestClient, *, headers: dict[str, str] | None = None):
    merged = {"Idempotency-Key": "diagnostics-test", **(headers or {})}
    return client.post(f"/api/sessions/{uuid4()}/turns", json={"text": "hello"}, headers=merged)


def _frames(response) -> list[str]:
    body = response.text
    return [line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")]


def _chunks(response) -> list[dict]:
    return [json.loads(value) for value in _frames(response) if value != "[DONE]"]


def _assert_streamed_failure(response, message: str) -> list[dict]:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response)
    assert frames[-1] == "[DONE]"
    prelude = _chunks(response)[0]
    assert prelude == {
        "type": "data-status",
        "data": {"phase": "preparation", "status": "running", "message": None},
        "transient": True,
    }
    chunks = _chunks(response)[1:]
    assert chunks == [
        {"type": "error", "errorText": message},
        {"type": "finish", "finishReason": "error"},
    ]
    return chunks


def test_preparation_unavailable_streams_typed_failure_and_logs_safe_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cause = ProviderRequestError(
        "provider failed for [redacted] at [redacted]",
        cause_type="ProviderResponseError",
        status_code=503,
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(_client(cause), headers={"X-Request-Id": "turn-corr-123"})

    _assert_streamed_failure(response, "Turn is unavailable")
    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert "correlation_id=turn-corr-123" in message
    assert "cause_type=provider_5xx" in message
    assert "provider_status_category=5xx" in message
    assert "message=provider failed for [redacted] at [redacted]" in message


def test_preparation_timeout_remains_typed_and_sanitized_inside_stream() -> None:
    private_detail = "private provider timeout api_key=never-return"

    response = _post(_client(RunPreparationTimeoutError(private_detail)))

    _assert_streamed_failure(response, "Turn preparation timed out")
    assert private_detail not in response.text
    assert "never-return" not in response.text


def test_lifecycle_unavailable_streams_typed_failure_with_correlation_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(
            _client(RunLifecycleUnavailableError("database session setup failed")),
            headers={"X-Request-Id": "lifecycle-corr"},
        )

    _assert_streamed_failure(response, "Turn is unavailable")
    assert "correlation_id=lifecycle-corr" in caplog.text


def test_preparation_unavailable_generates_uuid_correlation_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        _post(_client(ProviderRequestError("offline", cause_type="ConnectionError")))

    prefix = "correlation_id="
    start = caplog.text.index(prefix) + len(prefix)
    UUID(caplog.text[start:].split()[0])


def test_preparation_unavailable_prefers_request_id_over_correlation_id(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        _post(
            _client(ProviderRequestError("offline", cause_type="ConnectionError")),
            headers={"X-Request-Id": "request-wins", "X-Correlation-Id": "correlation-loses"},
        )

    assert "correlation_id=request-wins" in caplog.text


def test_unknown_preparation_failure_logs_exception_class_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "api_key=never-log-this /Users/zach/private.env"

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        response = _post(_client(RuntimeError(secret)), headers={"X-Correlation-Id": "unknown-corr"})

    _assert_streamed_failure(response, "Turn is unavailable")
    assert "correlation_id=unknown-corr" in caplog.text
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert "never-log-this" not in caplog.text
    assert "/Users/zach" not in caplog.text
    assert secret not in response.text


def test_unknown_adapter_failure_logs_cause_class_only(caplog: pytest.LogCaptureFixture) -> None:
    cause = ProviderRequestError(
        "api_key=never-log-this /Users/zach/private.env",
        cause_type="UnexpectedSDKError",
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.api.routes.turns"):
        _post(_client(cause), headers={"X-Request-Id": "adapter-unknown"})

    assert "message=UnexpectedSDKError" in caplog.text
    assert "never-log-this" not in caplog.text
    assert "/Users/zach" not in caplog.text
