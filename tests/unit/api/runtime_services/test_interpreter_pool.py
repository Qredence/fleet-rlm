from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool


def _runtime_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        volume_name="test-volume",
        timeout=123,
        rlm_max_llm_calls=17,
        rlm_max_depth=4,
        rlm_max_iterations=11,
        rlm_child_isolation_mode="auto",
        rlm_child_fork_fallback="clean",
        delegate_max_calls_per_turn=3,
        delegate_result_truncation_chars=500,
        interpreter_async_execute=True,
    )


@pytest.mark.anyio
async def test_acquire_returns_interpreter_when_daytona_available(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _FakeDaytonaInterpreter:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _FakeDaytonaInterpreter,
    )

    pool = InterpreterPool()
    result = await pool.acquire(_runtime_cfg())

    assert result is not None
    assert calls == [
        {
            "volume_name": "test-volume",
            "timeout": 123,
            "max_llm_calls": 17,
            "max_recursion_depth": 4,
            "rlm_max_iterations": 11,
            "child_isolation_mode": "auto",
            "child_fork_fallback": "clean",
            "delegate_max_calls_per_turn": 3,
            "delegate_result_truncation_chars": 500,
            "async_execute": True,
        }
    ]


@pytest.mark.anyio
async def test_acquire_returns_none_on_import_error(monkeypatch) -> None:
    def _raise_import(*_args: object, **_kwargs: object) -> None:
        raise ImportError("no daytona")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _raise_import,
    )

    pool = InterpreterPool()
    result = await pool.acquire(_runtime_cfg())
    assert result is None


@pytest.mark.anyio
async def test_acquire_returns_none_on_config_error(monkeypatch) -> None:
    class _FakeDaytonaConfigError(Exception):
        pass

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.config.DaytonaConfigError",
        _FakeDaytonaConfigError,
    )

    class _FakeInterpreter:
        def __init__(self, **_kwargs: Any) -> None:
            raise _FakeDaytonaConfigError("missing credentials")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _FakeInterpreter,
    )

    pool = InterpreterPool()
    result = await pool.acquire(_runtime_cfg())
    assert result is None


@pytest.mark.anyio
async def test_release_calls_ashutdown_when_available() -> None:
    shutdown_calls: list[Any] = []

    class _FakeInterpreter:
        async def ashutdown(self) -> None:
            shutdown_calls.append("ashutdown")

    pool = InterpreterPool()
    await pool.release(_FakeInterpreter())
    assert shutdown_calls == ["ashutdown"]


@pytest.mark.anyio
async def test_release_calls_shutdown_when_ashutdown_unavailable() -> None:
    shutdown_calls: list[Any] = []

    class _FakeInterpreter:
        def shutdown(self) -> None:
            shutdown_calls.append("shutdown")

    pool = InterpreterPool()
    await pool.release(_FakeInterpreter())
    assert shutdown_calls == ["shutdown"]


@pytest.mark.anyio
async def test_release_swallows_exceptions() -> None:
    class _FailingInterpreter:
        async def ashutdown(self) -> None:
            raise RuntimeError("boom")

    pool = InterpreterPool()
    # Should not raise
    await pool.release(_FailingInterpreter())


@pytest.mark.anyio
async def test_release_noop_for_none() -> None:
    pool = InterpreterPool()
    # Should not raise
    await pool.release(None)
