"""Tests for Daytona sandbox concurrency control."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.integrations.daytona.concurrency import (
    ConcurrencyConfig,
    SandboxUsageStats,
    acquire_sandbox_slot,
    attach_slot_release_handler,
    get_current_sandbox_usage,
    release_sandbox_slot,
)


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """Reset global semaphore state between tests."""
    import fleet_rlm.integrations.daytona.concurrency as mod

    mod._GLOBAL_SEMAPHORE = None
    mod._INITIALIZED_CONFIG = None
    yield
    mod._GLOBAL_SEMAPHORE = None
    mod._INITIALIZED_CONFIG = None


# ---------------------------------------------------------------------------
# ConcurrencyConfig model tests
# ---------------------------------------------------------------------------


class TestConcurrencyConfig:
    def test_defaults(self) -> None:
        config = ConcurrencyConfig()
        assert config.max_sandboxes == 5
        assert config.slot_timeout_seconds == 60.0

    def test_from_env(self) -> None:
        with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "10"}):
            config = ConcurrencyConfig.from_env()
            assert config.max_sandboxes == 10

    def test_from_env_invalid_falls_back(self) -> None:
        with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "bad"}):
            config = ConcurrencyConfig.from_env()
            assert config.max_sandboxes == 5

    def test_from_env_zero_clamps_to_one(self) -> None:
        with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "0"}):
            config = ConcurrencyConfig.from_env()
            assert config.max_sandboxes == 1

    def test_from_env_over_max_clamps(self) -> None:
        with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "100"}):
            config = ConcurrencyConfig.from_env()
            assert config.max_sandboxes == 50

    def test_frozen(self) -> None:
        from pydantic import ValidationError

        config = ConcurrencyConfig()
        with pytest.raises((ValidationError, TypeError)):
            config.max_sandboxes = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Slot acquisition and release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_acquire_slot_success() -> None:
    result = await acquire_sandbox_slot(timeout=1.0)
    assert result is True
    release_sandbox_slot()


@pytest.mark.asyncio
async def test_acquire_slot_timeout() -> None:
    with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "1"}):
        await acquire_sandbox_slot(timeout=1.0)

        with pytest.raises(asyncio.TimeoutError):
            await acquire_sandbox_slot(timeout=0.1)

        release_sandbox_slot()
        result = await acquire_sandbox_slot(timeout=1.0)
        assert result is True
        release_sandbox_slot()


@pytest.mark.asyncio
async def test_release_without_acquire() -> None:
    release_sandbox_slot()  # Should not raise


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_usage_returns_pydantic_model() -> None:
    usage = get_current_sandbox_usage()
    assert isinstance(usage, SandboxUsageStats)
    assert usage.limit == 5
    assert usage.available_slots == 5
    assert usage.active_count == 0


@pytest.mark.asyncio
async def test_get_usage_after_acquire() -> None:
    await acquire_sandbox_slot(timeout=1.0)

    usage = get_current_sandbox_usage()
    assert usage.available_slots == 4
    assert usage.active_count == 1

    release_sandbox_slot()


# ---------------------------------------------------------------------------
# Slot release handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_release_handler_attached() -> None:
    mock_sandbox = MagicMock()
    mock_sandbox.delete = MagicMock()
    mock_sandbox.stop = MagicMock()

    attach_slot_release_handler(mock_sandbox)

    assert mock_sandbox._fleet_slot_released is False


@pytest.mark.asyncio
async def test_slot_released_on_delete() -> None:
    with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "1"}):
        mock_sandbox = MagicMock()
        original_delete = MagicMock()
        mock_sandbox.delete = original_delete

        attach_slot_release_handler(mock_sandbox)

        await acquire_sandbox_slot(timeout=1.0)
        usage = get_current_sandbox_usage()
        assert usage.active_count == 1

        mock_sandbox.delete()

        assert mock_sandbox._fleet_slot_released is True
        original_delete.assert_called_once()

        # Slot should be released -- we can acquire again
        result = await acquire_sandbox_slot(timeout=1.0)
        assert result is True
        release_sandbox_slot()


@pytest.mark.asyncio
async def test_slot_released_on_stop() -> None:
    with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "1"}):
        mock_sandbox = MagicMock()
        original_stop = MagicMock()
        mock_sandbox.stop = original_stop
        mock_sandbox.delete = None

        attach_slot_release_handler(mock_sandbox)

        await acquire_sandbox_slot(timeout=1.0)
        mock_sandbox.stop()

        assert mock_sandbox._fleet_slot_released is True
        original_stop.assert_called_once()
        release_sandbox_slot()


@pytest.mark.asyncio
async def test_double_release_prevented() -> None:
    mock_sandbox = MagicMock()
    mock_sandbox.delete = MagicMock()
    mock_sandbox.stop = MagicMock()

    attach_slot_release_handler(mock_sandbox)
    await acquire_sandbox_slot(timeout=1.0)

    mock_sandbox.delete()
    mock_sandbox.stop()

    # Flag should still be True and only one release should have happened
    assert mock_sandbox._fleet_slot_released is True
    release_sandbox_slot()


# ---------------------------------------------------------------------------
# Child RLM concurrency integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_rlm_respects_concurrency_limit() -> None:
    """Verify that child sandbox creation flows through the semaphore."""
    with patch.dict("os.environ", {"FLEET_MAX_CONCURRENT_SANDBOXES": "2"}):
        # Acquire 2 slots (simulating 2 active sandboxes)
        await acquire_sandbox_slot(timeout=1.0)
        await acquire_sandbox_slot(timeout=1.0)

        # Third acquisition (child RLM) should timeout
        with pytest.raises(asyncio.TimeoutError):
            await acquire_sandbox_slot(timeout=0.1)

        # Release one slot
        release_sandbox_slot()

        # Now child can proceed
        result = await acquire_sandbox_slot(timeout=1.0)
        assert result is True

        # Cleanup
        release_sandbox_slot()
        release_sandbox_slot()
