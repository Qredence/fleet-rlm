"""Shared helpers for runtime settings, diagnostics, and volume services."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, TypeVar

from fleet_rlm.integrations.database import SandboxProvider
from fleet_rlm.utils.time import (
    now_iso as utc_now_iso,  # noqa: F401  re-export for consumers
)

RUNTIME_TEST_TIMEOUT_SECONDS = 20
# LM smoke-test invoke deadline. Provider workspaces (e.g. Aliyun MAAS, vLLM) can
# take ~30s to cold-start, which exceeds the 20s generic test timeout. Bumped to
# accommodate a cold first request; subsequent (warm) requests return quickly.
LM_SMOKE_TEST_TIMEOUT_SECONDS = 60
VOLUME_OPERATION_TIMEOUT_SECONDS = 30

_BlockingResultT = TypeVar("_BlockingResultT")


def sanitize_error(exc: BaseException) -> str:
    message = str(exc)
    sensitive_values = [
        os.environ.get("DSPY_LLM_API_KEY"),
        os.environ.get("DSPY_LM_API_KEY"),
        os.environ.get("DAYTONA_API_KEY"),
    ]

    for value in sensitive_values:
        if value and len(value) >= 4:
            message = message.replace(value, "***")

    return message


def redact_secret(text: str | None, secret: str | None) -> str | None:
    """Replace any occurrence of ``secret`` in ``text`` with ``[REDACTED]``.

    Used to scrub a profile's decrypted api_key from provider error strings before
    they are returned to the client.
    """
    if text is None or not secret:
        return text
    return text.replace(secret, "[REDACTED]")


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


def parse_model_identity(raw_model: object) -> tuple[str | None, str | None]:
    """Extract (provider, model_name) from a raw model string.

    Returns (None, None) for non-string input.
    Returns (None, raw_model) for unqualified names.
    Returns (provider, name) for ``provider/name`` format.
    """
    if not isinstance(raw_model, str):
        return None, None
    if "/" in raw_model:
        provider, name = raw_model.split("/", 1)
        return provider, name
    return None, raw_model


def resolve_sandbox_provider(raw: str) -> SandboxProvider:
    """Map a config string to the SandboxProvider enum."""
    normalized = raw.strip().lower()
    try:
        return SandboxProvider(normalized)
    except ValueError:
        return SandboxProvider.DAYTONA
