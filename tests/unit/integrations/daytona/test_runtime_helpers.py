from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona.async_compat import _run_async_compat


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
