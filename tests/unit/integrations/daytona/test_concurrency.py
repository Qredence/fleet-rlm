"""M7: attach_slot_release_handler wraps delete/stop/pause/archive."""

from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona import concurrency
from fleet_rlm.integrations.daytona.concurrency import (
    ConcurrencyConfig,
    attach_slot_release_handler,
)


class _FakeSandbox:
    """Minimal sandbox double with the teardown methods the handler patches."""

    def __init__(self) -> None:
        self.deleted = False
        self.stopped = False
        self.paused = False
        self.archived = False

    def delete(self) -> None:
        self.deleted = True

    def stop(self) -> None:
        self.stopped = True

    def pause(self) -> None:
        self.paused = True

    def archive(self) -> None:
        self.archived = True


@pytest.fixture(autouse=True)
def _reset_global_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level semaphore between tests so slot accounting is clean."""
    monkeypatch.setattr(concurrency, "_GLOBAL_SEMAPHORE", None)
    monkeypatch.setattr(concurrency, "_INITIALIZED_CONFIG", None)
    # Force a small known limit for the test.
    monkeypatch.setenv("FLEET_MAX_CONCURRENT_SANDBOXES", "5")


def _fresh_semaphore() -> None:
    """Initialize the global semaphore to a full state for the test.

    ``_get_global_semaphore`` is async; rather than await it here (which would
    make this helper async and force every test to be async), we let the first
    ``acquire_sandbox_slot`` call initialize it lazily. This helper exists only
    to document that intent; it is a no-op kept for readability.
    """
    return None


@pytest.mark.asyncio
async def test_pause_releases_slot() -> None:
    _fresh_semaphore()
    sandbox = _FakeSandbox()
    attach_slot_release_handler(sandbox)

    # Acquire one slot to simulate the sandbox being active.
    await concurrency.acquire_sandbox_slot(timeout=1.0)
    assert concurrency.get_current_sandbox_usage().active_count == 1

    sandbox.pause()

    assert sandbox.paused is True
    assert sandbox._fleet_slot_released is True
    # Slot returned to the pool.
    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_archive_releases_slot() -> None:
    _fresh_semaphore()
    sandbox = _FakeSandbox()
    attach_slot_release_handler(sandbox)

    await concurrency.acquire_sandbox_slot(timeout=1.0)
    assert concurrency.get_current_sandbox_usage().active_count == 1

    sandbox.archive()

    assert sandbox.archived is True
    assert sandbox._fleet_slot_released is True
    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_delete_releases_slot_once() -> None:
    _fresh_semaphore()
    sandbox = _FakeSandbox()
    attach_slot_release_handler(sandbox)

    await concurrency.acquire_sandbox_slot(timeout=1.0)
    sandbox.delete()
    sandbox.stop()  # second teardown must not over-release

    assert sandbox.deleted is True
    assert sandbox.stopped is True
    assert sandbox._fleet_slot_released is True
    # Only one slot returned despite two teardown calls.
    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_stop_releases_slot() -> None:
    _fresh_semaphore()
    sandbox = _FakeSandbox()
    attach_slot_release_handler(sandbox)

    await concurrency.acquire_sandbox_slot(timeout=1.0)
    sandbox.stop()

    assert sandbox.stopped is True
    assert concurrency.get_current_sandbox_usage().active_count == 0


def test_handler_marks_sandbox_managed() -> None:
    sandbox = _FakeSandbox()
    attach_slot_release_handler(sandbox)

    assert sandbox._fleet_slot_managed is True
    assert sandbox._fleet_slot_released is False


def test_release_for_unmanaged_sandbox_is_noop() -> None:
    sandbox = _FakeSandbox()  # no attach_slot_release_handler called

    # Should not raise and should not release anything.
    concurrency.release_sandbox_slot_for(sandbox)


def test_config_from_env_invalid_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_CONCURRENT_SANDBOXES", "not-a-number")
    cfg = ConcurrencyConfig.from_env()
    assert cfg.max_sandboxes == 5


def test_config_clamps_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_CONCURRENT_SANDBOXES", "999")
    cfg = ConcurrencyConfig.from_env()
    assert cfg.max_sandboxes == 50  # clamped to max

    monkeypatch.setenv("FLEET_MAX_CONCURRENT_SANDBOXES", "0")
    cfg = ConcurrencyConfig.from_env()
    assert cfg.max_sandboxes == 1  # clamped to min
