"""Shared helpers for runtime settings, diagnostics, and volume services."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi.responses import JSONResponse

RUNTIME_TEST_TIMEOUT_SECONDS = 20
VOLUME_OPERATION_TIMEOUT_SECONDS = 30

_BlockingResultT = TypeVar("_BlockingResultT")


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def json_model_response(payload: Any) -> JSONResponse:
    """Serialize runtime API payloads eagerly before returning to FastAPI."""
    if hasattr(payload, "model_dump"):
        content = payload.model_dump(mode="json")
    else:
        content = payload
    return JSONResponse(content=content)


def sanitize_error(exc: Exception) -> str:
    message = str(exc)
    sensitive_values = [
        os.environ.get("DSPY_LLM_API_KEY"),
        os.environ.get("DSPY_LM_API_KEY"),
        os.environ.get("MODAL_TOKEN_SECRET"),
        os.environ.get("MODAL_TOKEN_ID"),
    ]

    for value in sensitive_values:
        if value and len(value) >= 4:
            message = message.replace(value, "***")

    return message


def extract_lm_text(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"]).strip()
        return str(first).strip()
    return str(response).strip()


def coerce_output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


async def run_blocking(
    fn: Callable[..., _BlockingResultT],
    *args: Any,
    timeout: int,
) -> _BlockingResultT:
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=timeout)
