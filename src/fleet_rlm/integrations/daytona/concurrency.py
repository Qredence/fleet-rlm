"""Global concurrency control for Daytona sandbox creation.

Provides a module-level asyncio.BoundedSemaphore to cap total active sandboxes
(root sessions + child RLMs) across the entire fleet-rlm runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import threading
import time
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


class _FleetSandboxSemaphore(asyncio.Semaphore):
    """Semaphore with a configurable release bound for reconciled state."""

    def __init__(self, *, value: int, bound: int) -> None:
        super().__init__(value)
        self._fleet_bound = bound

    def release(self) -> None:
        if self._value >= self._fleet_bound:
            raise ValueError("BoundedSemaphore released too many times")
        super().release()


_GLOBAL_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_LOCK = threading.Lock()
_INITIALIZED_CONFIG: ConcurrencyConfig | None = None


async def _get_global_semaphore() -> asyncio.Semaphore:
    """Get or initialize the global sandbox semaphore lazily."""
    global _GLOBAL_SEMAPHORE, _INITIALIZED_CONFIG
    if _GLOBAL_SEMAPHORE is None:
        with _SEMAPHORE_LOCK:
            if _GLOBAL_SEMAPHORE is None:
                config = ConcurrencyConfig.from_env()
                _GLOBAL_SEMAPHORE = _FleetSandboxSemaphore(
                    value=config.max_sandboxes,
                    bound=config.max_sandboxes,
                )
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


def reconcile_sandbox_slots(*, provider_active_count: int) -> SandboxUsageStats:
    """Reset local slot accounting from provider-visible Fleet sandbox count.

    This is intentionally a recovery tool, not the normal release path. It is
    used after slot acquisition times out and the Daytona provider reports fewer
    Fleet-managed sandboxes than the in-process semaphore believes are active.
    Waiting acquirers should retry after reconciliation because this replaces
    the process-local semaphore instead of mutating its internal counters.
    """
    global _GLOBAL_SEMAPHORE, _INITIALIZED_CONFIG
    with _SEMAPHORE_LOCK:
        if _INITIALIZED_CONFIG is None:
            _INITIALIZED_CONFIG = ConcurrencyConfig.from_env()
        limit = _INITIALIZED_CONFIG.max_sandboxes
        clamped_active = max(0, min(int(provider_active_count), limit))
        available = max(0, limit - clamped_active)
        _GLOBAL_SEMAPHORE = _FleetSandboxSemaphore(value=available, bound=limit)
        logger.warning(
            "Reconciled Fleet sandbox slots from provider state (provider_active=%d, limit=%d, available=%d)",
            clamped_active,
            limit,
            available,
        )
        return SandboxUsageStats(
            limit=limit,
            available_slots=available,
            active_count=clamped_active,
        )


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
    """Patch sandbox teardown methods to auto-release the slot.

    Wraps ``delete()``, ``stop()``, ``pause()``, and ``archive()`` so the
    global concurrency slot is released exactly once after any teardown call
    succeeds. ``pause``/``archive`` matter because paused/archived sandboxes
    no longer count against vCPU/RAM quota (only disk), so the slot must be
    freed for the next active sandbox. A ``_fleet_slot_released`` flag
    prevents double-release when multiple teardown methods are called.
    """
    _set_sandbox_attr(sandbox, "_fleet_slot_managed", True)
    _set_sandbox_attr(sandbox, "_fleet_slot_released", False)

    original_delete = getattr(sandbox, "delete", None)
    original_stop = getattr(sandbox, "stop", None)
    original_pause = getattr(sandbox, "pause", None)
    original_archive = getattr(sandbox, "archive", None)

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

    for original, attr_name in (
        (original_delete, "delete"),
        (original_stop, "stop"),
        (original_pause, "pause"),
        (original_archive, "archive"),
    ):
        if original is not None:
            _set_sandbox_attr(sandbox, attr_name, _make_release_wrapper(original))


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


# ---------------------------------------------------------------------------
# Phase 2.5: Pause lifecycle for root sessions
# ---------------------------------------------------------------------------

_PAUSED_REGISTRY: dict[str, float] = {}
_PAUSED_LOCK = threading.Lock()


class SessionLifecycleConfig(BaseModel):
    """Validated configuration for root-session teardown lifecycle."""

    model_config = ConfigDict(frozen=True)

    lifecycle: str = Field(default="delete")  # "pause" | "delete"
    max_paused_sandboxes: int = Field(default=3, ge=0, le=50)

    @field_validator("lifecycle", mode="before")
    @classmethod
    def _coerce_lifecycle(cls, value: Any) -> str:
        if value is None or value == "":
            return "delete"
        normalized = str(value).strip().lower()
        if normalized not in ("pause", "delete"):
            logger.warning("Invalid FLEET_SESSION_LIFECYCLE=%s, falling back to delete", value)
            return "delete"
        return normalized

    @field_validator("max_paused_sandboxes", mode="before")
    @classmethod
    def _coerce_max_paused(cls, value: Any) -> int:
        if value is None or value == "":
            return 3
        try:
            return max(0, min(int(value), 50))
        except (TypeError, ValueError):
            return 3

    @classmethod
    def from_env(cls) -> SessionLifecycleConfig:
        raw_max = os.environ.get("FLEET_MAX_PAUSED_SANDBOXES", "").strip()
        max_paused = 3
        if raw_max:
            try:
                max_paused = int(raw_max)
            except ValueError:
                logger.warning("Invalid FLEET_MAX_PAUSED_SANDBOXES: %s", raw_max)
        return cls(
            lifecycle=os.environ.get("FLEET_SESSION_LIFECYCLE", "").strip(),
            max_paused_sandboxes=max_paused,
        )


def should_pause_root_session() -> bool:
    """True if root sessions should be paused instead of deleted on shutdown."""
    return SessionLifecycleConfig.from_env().lifecycle == "pause"


def register_paused_sandbox(sandbox_id: str) -> None:
    """Record a paused root sandbox in the LRU registry."""
    if not sandbox_id:
        return
    with _PAUSED_LOCK:
        _PAUSED_REGISTRY[sandbox_id] = time.monotonic()


def unregister_paused_sandbox(sandbox_id: str) -> None:
    """Remove a paused sandbox from the registry (e.g. after deletion)."""
    with _PAUSED_LOCK:
        _PAUSED_REGISTRY.pop(sandbox_id, None)


def get_paused_sandbox_count() -> int:
    with _PAUSED_LOCK:
        return len(_PAUSED_REGISTRY)


async def reap_paused_sandboxes(*, runtime: Any) -> int:
    """Delete oldest paused root sandboxes until under the configured limit.

    Runs inline on every pause. Returns the number of sandboxes reaped.
    Uses the async client (native); falls back to sync client in a thread if
    the async client is unavailable.
    """
    config = SessionLifecycleConfig.from_env()
    limit = config.max_paused_sandboxes
    if limit == 0:
        # Pausing disabled when limit is 0: delete everything tracked.
        pass
    with _PAUSED_LOCK:
        items = sorted(_PAUSED_REGISTRY.items(), key=lambda kv: kv[1])
    reaped = 0
    # Keep only ``limit`` newest; delete the oldest excess.
    while len(items) > limit:
        sandbox_id, _ts = items.pop(0)
        try:
            await _delete_sandbox_by_id(runtime=runtime, sandbox_id=sandbox_id)
            reaped += 1
        except Exception:
            logger.warning("Failed to reap paused sandbox %s", sandbox_id, exc_info=True)
        finally:
            unregister_paused_sandbox(sandbox_id)
    return reaped


async def sweep_paused_sandboxes_on_startup(*, runtime: Any) -> int:
    """Best-effort startup sweep: delete provider-visible paused Fleet sandboxes.

    The in-process registry is lost on restart, so paused sandboxes from a
    previous run must be discovered via the provider list. This lists
    Fleet-managed sandboxes in a paused state and deletes them so the fleet
    does not leak paused sandboxes across restarts.
    """
    default_labels = getattr(runtime, "DEFAULT_LABELS", {"managed-by": "fleet-rlm"})
    try:
        client = runtime._get_async_client()
    except Exception:
        return await asyncio.to_thread(_sweep_sync, runtime=runtime, default_labels=default_labels)

    signature = inspect.signature(client.list)
    kwargs: dict[str, Any] = {}
    if "labels" in signature.parameters:
        kwargs["labels"] = default_labels
    result = client.list(**kwargs)
    swept = 0
    if hasattr(result, "__aiter__"):
        async for sandbox in result:
            raw_state = getattr(sandbox, "state", None)
            state = str(getattr(raw_state, "value", raw_state) or "").lower()
            if "paused" not in state and "paused" != state:
                continue
            try:
                await client.delete(sandbox)
                swept += 1
                unregister_paused_sandbox(getattr(sandbox, "id", "") or "")
            except Exception:
                logger.warning("Startup sweep: failed to delete paused sandbox", exc_info=True)
    return swept


def _sweep_sync(*, runtime: Any, default_labels: dict[str, str]) -> int:
    """Sync fallback for the startup sweep when the async client is unavailable."""
    client = runtime._get_client()
    signature = inspect.signature(client.list)
    kwargs: dict[str, Any] = {}
    if "labels" in signature.parameters:
        kwargs["labels"] = default_labels
    result = client.list(**kwargs)
    raw_items = getattr(result, "items", result) if result else []
    swept = 0
    for sandbox in raw_items:
        raw_state = getattr(sandbox, "state", None)
        state = str(getattr(raw_state, "value", raw_state) or "").lower()
        if "paused" not in state and "paused" != state:
            continue
        try:
            client.delete(sandbox)
            swept += 1
        except Exception:
            logger.warning("Startup sweep: failed to delete paused sandbox", exc_info=True)
    return swept


async def _delete_sandbox_by_id(*, runtime: Any, sandbox_id: str) -> None:
    """Delete a paused sandbox by ID via the async client (sync fallback)."""
    try:
        client = runtime._get_async_client()
    except Exception:
        await asyncio.to_thread(_delete_sandbox_by_id_sync, runtime=runtime, sandbox_id=sandbox_id)
        return
    sandbox = await client.get(sandbox_id)
    await client.delete(sandbox)


def _delete_sandbox_by_id_sync(*, runtime: Any, sandbox_id: str) -> None:
    client = runtime._get_client()
    sandbox = client.get(sandbox_id)
    client.delete(sandbox)
