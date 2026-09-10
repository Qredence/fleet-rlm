"""Owned cleanup shared by Session, recursive, and batch live canaries."""

import asyncio
from typing import Any

_CLEANUP_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)


async def _retry_cleanup(operation: Any) -> bool:
    """Retry an asynchronous cleanup operation until it succeeds or all configured attempts fail.

    Parameters:
        operation (Any): Asynchronous cleanup operation to execute.

    Returns:
        bool: `True` if the operation succeeds, `False` after all attempts fail.
    """
    for delay in (*_CLEANUP_RETRY_DELAYS, None):
        try:
            await operation()
            return True
        except Exception:
            if delay is None:
                return False
            await asyncio.sleep(delay)
    return False


async def _strict_cleanup(resources: Any, volume_name: str) -> tuple[str, ...]:
    """
    Delete tracked sandboxes and the owned volume, returning labels for cleanup failures.

    Parameters:
        resources (Any): Resource manager containing tracked sandboxes and cleanup clients.
        volume_name (str): Name of the volume to delete.

    Returns:
        tuple[str, ...]: Cleanup failure labels, including "sandbox", "tracking", or "volume".
    """
    failures: list[str] = []
    for sandbox_id in sorted(set(resources._sandbox_ids)):

        async def delete_sandbox(sandbox_id: str = sandbox_id) -> None:
            """Delete the specified Daytona sandbox if it exists.

            Parameters:
                sandbox_id (str): Identifier of the sandbox to delete.
            """
            sandbox = await resources.platform.get(sandbox_id)
            if sandbox is not None:
                await resources.platform.delete(sandbox)

        if not await _retry_cleanup(delete_sandbox):
            failures.append("sandbox")
    try:
        resources._sandbox_ids.clear()
    except Exception:
        failures.append("tracking")

    async def delete_volume() -> None:
        """Delete the configured volume if it exists."""
        volume = await resources.client.volume.get(volume_name, create=False)
        if volume is not None:
            await resources.client.volume.delete(volume)

    if not await _retry_cleanup(delete_volume):
        failures.append("volume")
    return tuple(failures)
