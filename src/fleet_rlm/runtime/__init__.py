"""Canonical runtime package surface with lazy exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "DaytonaInterpreter": (
        "fleet_rlm.integrations.daytona.interpreter",
        "DaytonaInterpreter",
    ),
    "configure_planner_from_env": (
        "fleet_rlm.runtime.config",
        "configure_planner_from_env",
    ),
    "get_planner_lm_from_env": ("fleet_rlm.runtime.config", "get_planner_lm_from_env"),
    "sandbox_driver": ("fleet_rlm.runtime.execution.core_driver", "sandbox_driver"),
    "tools": ("fleet_rlm.runtime.tools", ""),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:  # pragma: no cover - Python import protocol
        raise AttributeError(name) from exc

    module = import_module(module_name)
    value = module if not attr_name else getattr(module, attr_name)
    globals()[name] = value
    return value
