"""Compatibility package for legacy ``fleet_rlm.daytona_rlm`` imports."""

from __future__ import annotations

from importlib import import_module
import sys

from fleet_rlm.infrastructure.providers.daytona import *  # noqa: F403

_SUBMODULES = (
    "chat_agent",
    "config",
    "diagnostics",
    "dspy_modules",
    "results",
    "runner",
    "sandbox",
    "smoke",
    "types",
)

for _name in _SUBMODULES:
    _module = import_module(f"fleet_rlm.infrastructure.providers.daytona.{_name}")
    globals()[_name] = _module
    sys.modules[f"{__name__}.{_name}"] = _module
