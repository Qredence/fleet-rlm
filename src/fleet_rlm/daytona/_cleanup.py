"""Private cleanup invocation helpers for the Daytona runtime facade."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


async def await_cleanup(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoke sync or async cleanup without changing its exception identity."""
    result = callback(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = ["await_cleanup"]
