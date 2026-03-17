"""Compatibility shim for the historical ``fleet_rlm.core.interpreter`` path."""

from __future__ import annotations

from typing import Any

from .execution import interpreter as _impl

__all__ = getattr(
    _impl,
    "__all__",
    [name for name in vars(_impl) if not name.startswith("_") and name != "asyncio"],
)

globals().update({name: getattr(_impl, name) for name in __all__})

# Tests still patch ``fleet_rlm.core.interpreter.asyncio.*`` directly.
asyncio: Any = _impl.asyncio
