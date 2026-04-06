"""Shared helpers for runtime settings, diagnostics, and volume services."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

RUNTIME_TEST_TIMEOUT_SECONDS = 20
VOLUME_OPERATION_TIMEOUT_SECONDS = 30

_BlockingResultT = TypeVar("_BlockingResultT")


def utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def sanitize_error(exc: Exception) -> str:
    message = str(exc)
    sensitive_values = [
        os.environ.get("DSPY_LLM_API_KEY"),
        os.environ.get("DSPY_LM_API_KEY"),
        os.environ.get("DAYTONA_API_KEY"),
        os.environ.get("DAYTONA_API_KEY"),
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
    timeout: int | None,
) -> _BlockingResultT:
    task = asyncio.to_thread(fn, *args)
    if timeout is None:
        return await task
    return await asyncio.wait_for(task, timeout=timeout)
