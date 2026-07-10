"""Daytona interpreter facade."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter


def __getattr__(name: str) -> Any:
    """Resolve the compatibility facade lazily from its implementation module.

    Test and application dependency injection sometimes replace the legacy
    class temporarily. Keeping no cached alias here prevents a transient
    replacement from becoming a stale public facade after it is restored.
    """
    if name == "DaytonaInterpreter":
        from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

        return DaytonaInterpreter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["DaytonaInterpreter"]
