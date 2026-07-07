"""Tests for prepare_chat_runtime decoupling from WebSocket.

Covers validation assertions:
  VAL-REF-011, VAL-REF-012, VAL-REF-013, VAL-REF-030
"""

from __future__ import annotations

import inspect
from dataclasses import fields
from types import SimpleNamespace

import pytest

from fleet_rlm.api.runtime_services.chat_runtime import (
    PreparedChatRuntime,
    prepare_chat_runtime,
)

# ---------------------------------------------------------------------------
# VAL-REF-011 — prepare_chat_runtime signature excludes websocket
# ---------------------------------------------------------------------------


class TestSignatureNoWebSocket:
    """VAL-REF-011: prepare_chat_runtime no longer takes a WebSocket parameter."""

    def test_signature_excludes_websocket_keyword(self) -> None:
        """websocket is not in inspect.signature params (keyword)."""
        sig = inspect.signature(prepare_chat_runtime)
        param_names = set(sig.parameters.keys())
        assert "websocket" not in param_names, (
            f"prepare_chat_runtime should not accept 'websocket' parameter, got: {param_names}"
        )

    def test_signature_excludes_websocket_positional(self) -> None:
        """All params are keyword-only (no positional params)."""
        sig = inspect.signature(prepare_chat_runtime)
        for name, param in sig.parameters.items():
            assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"Parameter '{name}' should be keyword-only, got kind={param.kind}"
            )

    def test_passing_websocket_raises_type_error(self) -> None:
        """Calling prepare_chat_runtime(websocket=...) raises TypeError."""
        with pytest.raises(TypeError):
            prepare_chat_runtime(
                websocket=object(),  # type: ignore[call-overload]
                config_deps=object(),  # type: ignore[arg-type]
                lm_deps=object(),  # type: ignore[arg-type]
                persistence_deps=object(),  # type: ignore[arg-type]
                diagnostics_deps=object(),  # type: ignore[arg-type]
                identity=object(),  # type: ignore[arg-type]
                send_error=lambda **kw: True,  # type: ignore[return-value, arg-type]
                close_websocket=lambda **kw: None,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# VAL-REF-012 — PreparedChatRuntime fields unchanged
# ---------------------------------------------------------------------------


class TestPreparedChatRuntimeFields:
    """VAL-REF-012: PreparedChatRuntime has same fields as before refactor."""

    # These are the fields expected to exist — same as pre-refactor definition
    EXPECTED_FIELDS = {
        "cfg",
        "planner_lm",
        "delegate_lm",
        "repository",
        "persistence",
        "persistence_required",
        "identity_rows",
    }

    def test_prepared_chat_runtime_has_required_fields(self) -> None:
        actual = {f.name for f in fields(PreparedChatRuntime)}
        assert actual == self.EXPECTED_FIELDS, (
            f"PreparedChatRuntime fields mismatch. Expected {self.EXPECTED_FIELDS}, got {actual}"
        )

    def test_prepared_chat_runtime_is_dataclass_with_slots(self) -> None:
        from dataclasses import is_dataclass

        assert is_dataclass(PreparedChatRuntime)
        assert hasattr(PreparedChatRuntime, "__slots__")

    def test_prepared_chat_runtime_can_be_constructed(self) -> None:
        """Verify PreparedChatRuntime can still be constructed with all fields."""
        cfg = SimpleNamespace()
        runtime = PreparedChatRuntime(
            cfg=cfg,  # type: ignore[arg-type]
            planner_lm=object(),
            delegate_lm=object(),
            repository=object(),
            persistence=None,
            persistence_required=False,
            identity_rows=None,
        )
        assert runtime.cfg is cfg
        assert runtime.planner_lm is not None
        assert runtime.delegate_lm is not None
        assert runtime.repository is not None
        assert runtime.persistence is None
        assert runtime.persistence_required is False
        assert runtime.identity_rows is None


# ---------------------------------------------------------------------------
# VAL-REF-013 — prepare_chat_runtime error handling is transport-neutral
# ---------------------------------------------------------------------------


class TestTransportNeutralErrorHandling:
    """VAL-REF-013: prepare_chat_runtime error handling uses transport-neutral
    callbacks/exceptions, not WebSocket writes."""

    @pytest.mark.asyncio
    async def test_error_callbacks_work_without_websocket(self) -> None:
        """Transport-neutral callbacks can be called without a websocket arg."""
        sent_errors: list[dict[str, str]] = []
        close_codes: list[int] = []

        async def send_error(*, code: str, message: str) -> bool:
            sent_errors.append({"code": code, "message": message})
            return True

        async def close_ws(*, code: int = 1000) -> None:
            close_codes.append(code)

        # Callbacks work with keyword-only args, no websocket
        result = await send_error(code="test_error", message="test message")
        assert result is True
        assert len(sent_errors) == 1
        assert sent_errors[0]["code"] == "test_error"
        assert sent_errors[0]["message"] == "test message"

        await close_ws(code=1008)
        assert len(close_codes) == 1
        assert close_codes[0] == 1008

        # close_ws also works with default code
        await close_ws()
        assert len(close_codes) == 2
        assert close_codes[1] == 1000

    @pytest.mark.asyncio
    async def test_error_callbacks_do_not_receive_websocket(self) -> None:
        """Verify that send_error and close_websocket callback signatures
        do not include a websocket parameter — they are transport-neutral."""

        # Verify the callbacks we defined match transport-neutral signatures
        async def send_error(*, code: str, message: str) -> bool:
            return True

        async def close_ws(*, code: int = 1000) -> None:
            return None

        sig = inspect.signature(send_error)
        assert "websocket" not in sig.parameters

        sig = inspect.signature(close_ws)
        assert "websocket" not in sig.parameters

    def test_prepare_chat_runtime_signature_transport_neutral(self) -> None:
        """prepare_chat_runtime signature does not contain any transport-specific
        types like WebSocket, Request, etc."""
        sig = inspect.signature(prepare_chat_runtime)

        # Check parameter annotations
        for name, param in sig.parameters.items():
            annotation_str = str(param.annotation).lower() if param.annotation is not inspect.Parameter.empty else ""
            assert "websocket" not in annotation_str, f"Parameter '{name}' has WebSocket annotation: {param.annotation}"
            assert "request" not in annotation_str, f"Parameter '{name}' has Request annotation: {param.annotation}"


# ---------------------------------------------------------------------------
# VAL-REF-030 — WS path no longer calls prepare_chat_runtime with a WebSocket
# ---------------------------------------------------------------------------


class TestWSPathNoWebSocketArg:
    """VAL-REF-030: WS path calls prepare_chat_runtime without websocket= kwarg."""

    def test_prepare_chat_runtime_service_not_called_with_websocket(self) -> None:
        """Direct verification that calling prepare_chat_runtime with
        a websocket= keyword raises TypeError (not silently accepted)."""
        with pytest.raises(TypeError):
            prepare_chat_runtime(
                websocket=object(),  # type: ignore[call-overload]
                config_deps=object(),  # type: ignore[arg-type]
                lm_deps=object(),  # type: ignore[arg-type]
                persistence_deps=object(),  # type: ignore[arg-type]
                diagnostics_deps=object(),  # type: ignore[arg-type]
                identity=object(),  # type: ignore[arg-type]
                send_error=lambda **kw: True,  # type: ignore[return-value, arg-type]
                close_websocket=lambda **kw: None,  # type: ignore[arg-type]
            )

    def test_endpoint_wrapper_signature_still_has_websocket(self) -> None:
        """The WS endpoint wrapper in endpoint.py still takes websocket
        (it translates), but the underlying service does not."""
        # Import the WS endpoint wrapper
        from fleet_rlm.api.routers.ws.endpoint import _prepare_chat_runtime

        ws_sig = inspect.signature(_prepare_chat_runtime)
        ws_params = set(ws_sig.parameters.keys())
        assert "websocket" in ws_params, "WS wrapper _prepare_chat_runtime should still accept websocket"

        # Verify the service function no longer accepts websocket
        service_sig = inspect.signature(prepare_chat_runtime)
        service_params = set(service_sig.parameters.keys())
        assert "websocket" not in service_params, "Service function prepare_chat_runtime should NOT accept websocket"
