from __future__ import annotations

from typing import Any

CHAT_RUNTIME_PREPARE_FAILED_CODE = "chat_runtime_prepare_failed"
CHAT_RUNTIME_PREPARE_FAILED_MESSAGE = "Failed to prepare chat runtime."

# Curated error codes that are safe to expose to clients
CURATED_SAFE_CODES = {"tenant_forbidden", "auth_failed", "durable_state_unavailable"}


def public_prepare_error_detail(*, code: str | None = None, message: str | None = None) -> dict[str, str]:
    """Return a sanitized error detail dictionary for client-facing HTTP exceptions."""
    if code in CURATED_SAFE_CODES and message:
        return {"code": code, "message": message}
    return {
        "code": CHAT_RUNTIME_PREPARE_FAILED_CODE,
        "message": CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
    }


def public_prepare_error_envelope(
    *,
    code: str | None = None,
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a sanitized error envelope dictionary for client-facing websocket payloads."""
    payload: dict[str, Any] = {
        "type": "error",
        "code": CHAT_RUNTIME_PREPARE_FAILED_CODE,
        "message": CHAT_RUNTIME_PREPARE_FAILED_MESSAGE,
    }
    if code in CURATED_SAFE_CODES and message:
        payload["code"] = code
        payload["message"] = message

    if details:
        payload["details"] = details
    return payload
