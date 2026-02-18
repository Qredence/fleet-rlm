"""Session execution history tracking for sandbox driver.

Provides logging and retrieval of code execution history within a sandbox session.
"""

from __future__ import annotations

import time
from typing import Any


# Module-level session history storage
_session_history: list[dict[str, Any]] = []


def log_execution(
    code: str,
    result: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log code execution to session history for tracking and learning.

    Args:
        code: The code that was executed.
        result: Execution result with 'stdout', 'stderr', 'final' keys.
        metadata: Optional additional metadata about the execution.
    """
    entry = {
        "timestamp": time.time(),
        "code_preview": code[:200] + "..." if len(code) > 200 else code,
        "stdout_preview": result.get("stdout", "")[:200],
        "stderr_preview": result.get("stderr", "")[:200],
        "had_final": result.get("final") is not None,
        "metadata": metadata or {},
    }
    _session_history.append(entry)


def get_session_history() -> list[dict[str, Any]]:
    """Return all logged executions in this session.

    Returns:
        Copy of the session history as a list of execution entries.
    """
    return list(_session_history)


def get_last_execution() -> dict[str, Any] | None:
    """Return the most recent execution entry, or None if empty.

    Returns:
        The most recent execution entry dict, or None if no executions logged.
    """
    return _session_history[-1] if _session_history else None


def reset_session_history() -> None:
    """Reset session history.

    Called at the start of sandbox_driver() to ensure each session starts fresh.
    Also useful for testing.
    """
    _session_history.clear()
