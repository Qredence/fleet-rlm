"""Neutral Daytona sandbox code execution helpers."""

from __future__ import annotations

from typing import Any


def coerce_sandbox_result(raw: Any) -> dict[str, Any]:
    """Normalize Daytona interpreter execution results into a tool payload."""
    payload = getattr(raw, "output", raw)
    if isinstance(payload, dict):
        result = dict(payload)
        result.setdefault("status", "ok")
        return result
    if payload is None:
        return {"status": "ok"}
    return {"status": "ok", "output": str(payload)}


def execute_sandbox_tool(
    interpreter: Any,
    code: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute code through an interpreter and normalize the result."""
    raw = interpreter.execute(code, variables or {})
    return coerce_sandbox_result(raw)


__all__ = ["coerce_sandbox_result", "execute_sandbox_tool"]
