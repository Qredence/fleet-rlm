"""Global concurrency control for Daytona sandbox creation.

Provides a module-level asyncio.BoundedSemaphore to cap total active sandboxes
(root sessions + child RLMs) across the entire fleet-rlm runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration model
# ---------------------------------------------------------------------------


class ConcurrencyConfig(BaseModel):
    """Validated concurrency configuration for sandbox slot management."""

    model_config = ConfigDict(frozen=True)

    max_sandboxes: int = Field(default=5, ge=1, le=50)
    slot_timeout_seconds: float = Field(default=60.0, gt=0)

    @field_validator("max_sandboxes", mode="before")
    @classmethod
    def _coerce_max_sandboxes(cls, value: Any) -> int:
        if value is None or value == "":
            return 5
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 5
        return max(1, min(parsed, 50))

    @classmethod
    def from_env(cls) -> ConcurrencyConfig:
        """Load configuration from environment variables with defaults."""
        raw = os.environ.get("FLEET_MAX_CONCURRENT_SANDBOXES", "").strip()
        limit = 5
        if raw:
            try:
                limit = int(raw)
            except ValueError:
                logger.warning("Invalid FLEET_MAX_CONCURRENT_SANDBOXES: %s", raw)
        return cls(max_sandboxes=limit, slot_timeout_seconds=60.0)


# ---------------------------------------------------------------------------
# Diagnostics model
# ---------------------------------------------------------------------------


class SandboxUsageStats(BaseModel):
    """Current semaphore state snapshot for diagnostics."""

    model_config = ConfigDict(frozen=True)

    limit: int = Field(ge=0)
    available_slots: int = Field(ge=0)
    active_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Module-level semaphore state
# ---------------------------------------------------------------------------

_GLOBAL_SEMAPHORE: asyncio.BoundedSemaphore | None = None
_SEMAPHORE_LOCK = threading.Lock()
_INITIALIZED_CONFIG: ConcurrencyConfig | None = None


async def _get_global_semaphore() -> asyncio.BoundedSemaphore:
    """Get or initialize the global sandbox semaphore lazily."""
    global _GLOBAL_SEMAPHORE, _INITIALIZED_CONFIG
    if _GLOBAL_SEMAPHORE is None:
        with _SEMAPHORE_LOCK:
            if _GLOBAL_SEMAPHORE is None:
                config = ConcurrencyConfig.from_env()
                _GLOBAL_SEMAPHORE = asyncio.BoundedSemaphore(config.max_sandboxes)
                _INITIALIZED_CONFIG = config
                logger.info(
                    "Initialized global sandbox semaphore with limit=%d",
                    config.max_sandboxes,
                )
    return _GLOBAL_SEMAPHORE


def _get_initialized_limit() -> int:
    """Return the limit the semaphore was initialized with."""
    if _INITIALIZED_CONFIG is not None:
        return _INITIALIZED_CONFIG.max_sandboxes
    return ConcurrencyConfig.from_env().max_sandboxes


# ---------------------------------------------------------------------------
# Slot acquisition and release
# ---------------------------------------------------------------------------


async def acquire_sandbox_slot(timeout: float | None = None) -> bool:
    """Acquire a slot for sandbox creation.

    Args:
        timeout: Maximum seconds to wait. Defaults to config value (60s).

    Returns:
        True if slot acquired.

    Raises:
        asyncio.TimeoutError: If timeout is reached.
    """
    if timeout is None:
        config = _INITIALIZED_CONFIG or ConcurrencyConfig.from_env()
        timeout = config.slot_timeout_seconds
    sem = await _get_global_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=timeout)
        logger.debug("Acquired sandbox slot (available=%d)", sem._value)
        return True
    except asyncio.TimeoutError:
        logger.warning("Timeout waiting for sandbox slot (limit=%d)", _get_initialized_limit())
        raise


def release_sandbox_slot() -> None:
    """Release a sandbox slot back to the pool.

    Call this when a sandbox is destroyed or stopped.

    Note:
        Over-releasing (calling without a prior acquire) is prevented by the
        bounded semaphore and logged as a warning. It does NOT raise.
    """
    if _GLOBAL_SEMAPHORE is not None:
        try:
            _GLOBAL_SEMAPHORE.release()
            logger.debug("Released sandbox slot (available=%d)", _GLOBAL_SEMAPHORE._value)
        except ValueError:
            logger.warning("Attempted to release unheld sandbox slot (over-release)")


def release_sandbox_slot_for(sandbox: Any) -> None:
    """Release the Fleet slot associated with an SDK sandbox exactly once."""
    if not bool(getattr(sandbox, "_fleet_slot_managed", False)):
        return
    if bool(getattr(sandbox, "_fleet_slot_released", False)):
        return
    release_sandbox_slot()
    _set_sandbox_attr(sandbox, "_fleet_slot_released", True)


def _set_sandbox_attr(sandbox: Any, name: str, value: Any) -> None:
    """Set SDK object attributes, bypassing validated assignment when needed."""
    try:
        setattr(sandbox, name, value)
    except Exception:
        object.__setattr__(sandbox, name, value)


# ---------------------------------------------------------------------------
# Slot release handler (attaches to sandbox lifecycle)
# ---------------------------------------------------------------------------


def attach_slot_release_handler(sandbox: Any) -> None:
    """Patch sandbox.delete() and sandbox.stop() to auto-release the slot.

    The Daytona Python SDK exposes ``sandbox.delete()`` and ``sandbox.stop()``
    as the only teardown methods. This function monkey-patches both to release
    the global concurrency slot exactly once after a teardown call succeeds.

    A ``_fleet_slot_released`` flag prevents double-release.
    """
    _set_sandbox_attr(sandbox, "_fleet_slot_managed", True)
    _set_sandbox_attr(sandbox, "_fleet_slot_released", False)

    original_delete = getattr(sandbox, "delete", None)
    original_stop = getattr(sandbox, "stop", None)

    def _make_release_wrapper(original: Any) -> Any:
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            if not getattr(sandbox, "_fleet_slot_released", False):
                result = None
                if original is not None:
                    result = original(*args, **kwargs)
                release_sandbox_slot_for(sandbox)
                return result
            if original is not None:
                return original(*args, **kwargs)
            return None

        return _wrapper

    if original_delete is not None:
        _set_sandbox_attr(sandbox, "delete", _make_release_wrapper(original_delete))
    if original_stop is not None:
        _set_sandbox_attr(sandbox, "stop", _make_release_wrapper(original_stop))


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def get_current_sandbox_usage() -> SandboxUsageStats:
    """Get current semaphore state for diagnostics."""
    limit = _get_initialized_limit()
    if _GLOBAL_SEMAPHORE is None:
        return SandboxUsageStats(limit=limit, available_slots=limit, active_count=0)
    available = getattr(_GLOBAL_SEMAPHORE, "_value", 0)
    active = max(0, limit - available)
    return SandboxUsageStats(limit=limit, available_slots=available, active_count=active)
