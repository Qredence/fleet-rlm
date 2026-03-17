"""Compatibility shim for the historical ``fleet_rlm.core.interpreter`` path."""

from __future__ import annotations

from .execution import interpreter as _impl
from .execution.interpreter import *  # noqa: F403

# Tests still patch ``fleet_rlm.core.interpreter.asyncio.*`` directly.
asyncio = _impl.asyncio
