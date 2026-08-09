from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_cfg(**overrides):
    """Build a minimal AppConfig-like object for pool tests."""
    defaults = {
        "interpreter_pool_size": 2,
        "interpreter_pool_overflow_max": 4,
        "interpreter_pool_acquire_timeout": 1.0,
        "interpreter_pool_health_interval": 30.0,
        "volume_name": None,
        "timeout": 900,
        "rlm_max_llm_calls": 50,
        "rlm_max_iterations": 20,
        "rlm_child_isolation_mode": "auto",
        "rlm_child_fork_fallback": "clean",
        "delegate_max_calls_per_turn": 8,
        "delegate_result_truncation_chars": 8000,
        "delegate_execution_timeout": 300,
        "delegate_max_iterations": 8,
        "delegate_adapter": "json",
        "daytona_broker_health_timeout": 20.0,
        "daytona_broker_tool_call_timeout": 180.0,
        "daytona_broker_start_retries": 1,
        "interpreter_async_execute": True,
        "interpreter_pool_auto_size": False,
        "interpreter_pool_cpu_per_sandbox": 2,
        "daytona_runner_tags": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_interpreter():
    """Build a mock interpreter with the expected interface."""
    interp = MagicMock()
    interp.areset_for_pool = AsyncMock()
    interp.ashutdown = AsyncMock()
    interp._executor = MagicMock()
    interp._executor._bridge = None
    interp._host_repository = None
    interp._host_identity = None
    interp._host_run_id = None
    return interp


def test_pool_manifest_path_is_user_specific():
    from fleet_rlm.api.runtime_services.interpreter_pool import _POOL_MANIFEST_PATH, _pool_manifest_user_token

    token = _pool_manifest_user_token()

    assert token
    assert _POOL_MANIFEST_PATH.name == f"fleet-rlm-pool-manifest-{token}.json"
    assert _POOL_MANIFEST_PATH.name != "fleet-rlm-pool-manifest.json"


@pytest.mark.asyncio
async def test_pool_start_warms_interpreters():
    """start() creates pool_size interpreters, all available."""
    cfg = _mock_cfg(interpreter_pool_size=2)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        assert pool.size == 2
        assert pool.available == 2
        assert pool.in_use_count == 0

        # Cleanup
        await pool.drain()


@pytest.mark.asyncio
async def test_acquire_returns_warm_interpreter():
    """After start(), acquire() returns instantly without creating new."""
    cfg = _mock_cfg(interpreter_pool_size=2)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        interp = await pool.acquire(cfg)
        assert interp is not None
        assert pool.available == 1
        assert pool.in_use_count == 1

        # Release before drain so drain doesn't wait 60s for in-flight
        await pool.release(interp)
        await pool.drain()


@pytest.mark.asyncio
async def test_acquire_creates_overflow_when_pool_empty():
    """When all warm are in-use, acquire creates a new one (up to overflow_max)."""
    cfg = _mock_cfg(interpreter_pool_size=1, interpreter_pool_overflow_max=3)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        # Acquire the only warm interpreter
        interp1 = await pool.acquire(cfg)
        assert interp1 is not None
        assert pool.available == 0

        # Next acquire should create an overflow interpreter
        interp2 = await pool.acquire(cfg)
        assert interp2 is not None
        assert pool.size == 2  # 1 original + 1 overflow
        assert pool.in_use_count == 2

        # Release before drain
        await pool.release(interp1)
        await pool.release(interp2)
        await pool.drain()


@pytest.mark.asyncio
async def test_acquire_waits_then_gets_released():
    """When at overflow_max, acquire waits and gets one when another task releases."""
    cfg = _mock_cfg(
        interpreter_pool_size=1,
        interpreter_pool_overflow_max=1,
        interpreter_pool_acquire_timeout=5.0,
    )

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        # Take the only interpreter
        interp1 = await pool.acquire(cfg)
        assert interp1 is not None
        assert pool.available == 0

        # Start an acquire that will have to wait
        acquired = []

        async def waiting_acquire():
            result = await pool.acquire(cfg)
            acquired.append(result)

        task = asyncio.create_task(waiting_acquire())

        # Give the acquire task time to start waiting
        await asyncio.sleep(0.05)

        # Release the interpreter — the waiting acquire should get it
        await pool.release(interp1)
        await asyncio.sleep(0.05)

        # The task may have completed or we need to wait a bit more
        await asyncio.wait_for(task, timeout=2.0)

        assert len(acquired) == 1
        assert acquired[0] is not None

        # Release the acquired interpreter before drain
        await pool.release(acquired[0])
        await pool.drain()


@pytest.mark.asyncio
async def test_release_resets_and_returns_to_queue():
    """release calls areset_for_pool() and puts interpreter back in ready queue."""
    cfg = _mock_cfg(interpreter_pool_size=1)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        interp = await pool.acquire(cfg)
        assert pool.available == 0

        await pool.release(interp)

        interp.areset_for_pool.assert_awaited_once()
        assert pool.available == 1
        assert pool.in_use_count == 0

        await pool.drain()


@pytest.mark.asyncio
async def test_release_destroys_on_reset_failure():
    """If areset_for_pool() raises, interpreter is destroyed (not returned to pool)."""
    cfg = _mock_cfg(interpreter_pool_size=1)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        interp = await pool.acquire(cfg)
        # Make the reset fail
        interp.areset_for_pool.side_effect = RuntimeError("reset failed")

        await pool.release(interp)

        # Interpreter should NOT be back in the ready queue
        assert pool.available == 0
        # It should have been destroyed (unregistered)
        assert pool.size == 0
        interp.ashutdown.assert_awaited_once()

        await pool.drain()


@pytest.mark.asyncio
async def test_health_check_evicts_unhealthy():
    """Unhealthy interpreter removed from pool, triggers replenish."""
    cfg = _mock_cfg(interpreter_pool_size=1, interpreter_pool_health_interval=0.1)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        assert pool.available == 1

        # Make health check return False for the interpreter
        with patch.object(pool, "_is_healthy", new_callable=lambda: AsyncMock(return_value=False)):
            await pool._run_health_check()

        # The unhealthy interpreter should be evicted
        assert pool.available == 0

        await pool.drain()


@pytest.mark.asyncio
async def test_drain_shuts_down_all():
    """drain() destroys all interpreters."""
    cfg = _mock_cfg(interpreter_pool_size=2)
    created_interps = []

    def make_interp(**kw):
        interp = _mock_interpreter()
        created_interps.append(interp)
        return interp

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=make_interp,
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        assert pool.size == 2

        await pool.drain()

        assert pool.size == 0
        assert pool.available == 0
        assert pool.in_use_count == 0

        # All created interpreters should have been shut down
        for interp in created_interps:
            interp.ashutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_acquire_returns_none_when_draining():
    """After drain starts, acquire returns None."""
    cfg = _mock_cfg(interpreter_pool_size=1)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)
        await pool.start()

        await pool.drain()

        result = await pool.acquire(cfg)
        assert result is None


@pytest.mark.asyncio
async def test_pool_size_and_available_properties():
    """Verify .size, .available, .in_use_count track state correctly."""
    cfg = _mock_cfg(interpreter_pool_size=3, interpreter_pool_overflow_max=5)

    with patch(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        side_effect=lambda **kw: _mock_interpreter(),
    ):
        from fleet_rlm.api.runtime_services.interpreter_pool import InterpreterPool

        pool = InterpreterPool(cfg)

        # Before start
        assert pool.size == 0
        assert pool.available == 0
        assert pool.in_use_count == 0

        await pool.start()

        # After start
        assert pool.size == 3
        assert pool.available == 3
        assert pool.in_use_count == 0

        # Acquire one
        interp1 = await pool.acquire(cfg)
        assert pool.size == 3
        assert pool.available == 2
        assert pool.in_use_count == 1

        # Acquire another
        interp2 = await pool.acquire(cfg)
        assert pool.size == 3
        assert pool.available == 1
        assert pool.in_use_count == 2

        # Release one
        await pool.release(interp1)
        assert pool.size == 3
        assert pool.available == 2
        assert pool.in_use_count == 1

        # Release the other
        await pool.release(interp2)
        assert pool.size == 3
        assert pool.available == 3
        assert pool.in_use_count == 0

        await pool.drain()
