from __future__ import annotations

import threading

import pytest

from fleet_rlm.integrations.daytona import async_compat as async_compat_module
from fleet_rlm.integrations.daytona.async_compat import _run_async_compat
from fleet_rlm.integrations.daytona.config import (
    classify_daytona_sdk_error,
    format_daytona_sdk_error,
)


@pytest.mark.asyncio
async def test_run_async_compat_returns_value_inside_running_loop() -> None:
    async def _returns_value() -> str:
        return "ok"

    assert _run_async_compat(_returns_value) == "ok"


@pytest.mark.asyncio
async def test_run_async_compat_reraises_exception_inside_running_loop() -> None:
    async def _raises_error() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        _run_async_compat(_raises_error)


@pytest.mark.asyncio
async def test_run_async_compat_reuses_persistent_background_runner() -> None:
    async def _thread_id() -> int:
        return threading.get_ident()

    first = _run_async_compat(_thread_id)
    runner_thread = async_compat_module._BACKGROUND_ASYNC_RUNNER._thread

    assert runner_thread is not None
    assert runner_thread.is_alive() is True

    second = _run_async_compat(_thread_id)

    assert async_compat_module._BACKGROUND_ASYNC_RUNNER._thread is runner_thread
    assert first == second == runner_thread.ident


class _FakeDaytonaApiError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = message


def test_classify_daytona_sdk_error_treats_400_quota_as_resource_error() -> None:
    error = _FakeDaytonaApiError("Quota limit exceeded for sandbox resources", status=400)

    classification = classify_daytona_sdk_error(error)

    assert classification.status_code == 400
    assert classification.kind == "resource_or_quota"
    assert classification.is_resource_or_quota_error is True


def test_classify_daytona_sdk_error_treats_429_as_resource_error() -> None:
    error = _FakeDaytonaApiError("Too many sandbox create requests", status=429)

    classification = classify_daytona_sdk_error(error)

    assert classification.status_code == 429
    assert classification.kind == "resource_or_quota"


def test_format_daytona_sdk_error_includes_status_and_provider_message() -> None:
    error = _FakeDaytonaApiError("precondition failed: resource unavailable", status=400)

    message = format_daytona_sdk_error(error)

    assert "resource/quota/precondition failure" in message
    assert "HTTP 400" in message
    assert "resource unavailable" in message
