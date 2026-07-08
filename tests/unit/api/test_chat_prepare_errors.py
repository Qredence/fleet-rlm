from __future__ import annotations

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.dependencies import ConfigDeps, DiagnosticsDeps, LmDeps, PersistenceDeps
from fleet_rlm.api.routers.ws.transport import chat_startup_error_payload
from fleet_rlm.api.runtime_services.chat_prepare_errors import (
    CHAT_RUNTIME_PREPARE_FAILED_CODE,
    CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
    public_prepare_error_detail,
    public_prepare_error_envelope,
)
from fleet_rlm.api.runtime_services.chat_runtime import prepare_chat_runtime


def test_public_prepare_error_detail() -> None:
    """Test public_prepare_error_detail sanitization."""
    # Curated safe codes should be preserved
    res = public_prepare_error_detail(code="tenant_forbidden", message="Forbidden")
    assert res == {"code": "tenant_forbidden", "message": "Forbidden"}

    # Uncurated codes should be sanitized
    res = public_prepare_error_detail(code="planner_initialization_failed", message="Secret stuff")
    assert res == {
        "code": CHAT_RUNTIME_PREPARE_FAILED_CODE,
        "message": CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
    }

    # None arguments should return sanitized default
    res = public_prepare_error_detail()
    assert res == {
        "code": CHAT_RUNTIME_PREPARE_FAILED_CODE,
        "message": CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
    }


def test_public_prepare_error_envelope() -> None:
    """Test public_prepare_error_envelope sanitization."""
    # Curated safe codes should be preserved
    res = public_prepare_error_envelope(
        code="auth_failed",
        message="Invalid token",
        details={"foo": "bar"},
    )
    assert res == {
        "type": "error",
        "code": "auth_failed",
        "message": "Invalid token",
        "details": {"foo": "bar"},
    }

    # Uncurated codes should be sanitized
    res = public_prepare_error_envelope(
        code="planner_missing",
        message="Secret env vars",
        details={"foo": "bar"},
    )
    assert res == {
        "type": "error",
        "code": CHAT_RUNTIME_PREPARE_FAILED_CODE,
        "message": CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
        "details": {"foo": "bar"},
    }


def test_chat_startup_error_payload_does_not_leak_exception_detail() -> None:
    """chat_startup_error_payload sanitizes exception messages."""
    exc = RuntimeError("SECRET-DATABASE-PASSWORD-XYZ")
    payload = chat_startup_error_payload(exc)

    assert payload["type"] == "error"
    assert payload["code"] == CHAT_RUNTIME_PREPARE_FAILED_CODE
    assert payload["message"] == CHAT_RUNTIME_PREPARE_FAILED_MESSAGE
    assert "SECRET-DATABASE-PASSWORD-XYZ" not in str(payload)


@pytest.mark.asyncio
async def test_prepare_chat_runtime_planner_failure_sends_sanitized_message(
    monkeypatch,
) -> None:
    """prepare_chat_runtime sanitizes planner_initialization_failed message."""

    import fleet_rlm.api.runtime_services.chat_runtime as chat_runtime_mod

    async def _mock_ensure_models(*args, **kwargs):
        raise RuntimeError("SENTINEL-PLANNER-INIT-XYZ")

    monkeypatch.setattr(chat_runtime_mod, "_ensure_runtime_models", _mock_ensure_models)

    config_deps = ConfigDeps()
    lm_deps = LmDeps()
    persistence_deps = PersistenceDeps()
    diagnostics_deps = DiagnosticsDeps()
    identity = NormalizedIdentity(
        tenant_claim="tenant-1",
        user_claim="user-1",
        email="test@example.com",
        name="Test User",
    )

    captured_errors = []

    async def _send_error(*, code: str, message: str) -> bool:
        captured_errors.append((code, message))
        return True

    async def _close_websocket(*args, **kwargs) -> None:
        pass

    res = await prepare_chat_runtime(
        config_deps=config_deps,
        lm_deps=lm_deps,
        persistence_deps=persistence_deps,
        diagnostics_deps=diagnostics_deps,
        identity=identity,
        send_error=_send_error,
        close_websocket=_close_websocket,
    )

    assert res is None
    assert len(captured_errors) == 1
    code, message = captured_errors[0]
    assert code == "planner_initialization_failed"
    assert message == CHAT_RUNTIME_PREPARE_FAILED_MESSAGE
    assert "SENTINEL-PLANNER-INIT-XYZ" not in message
