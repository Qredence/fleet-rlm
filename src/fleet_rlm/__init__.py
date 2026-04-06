"""Top-level package exports for fleet_rlm."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.4.99"

__all__ = [
    "DaytonaInterpreter",
    "__version__",
    "configure_planner_from_env",
    "get_planner_lm_from_env",
]

if TYPE_CHECKING:
    from .runtime import (
        DaytonaInterpreter,
        configure_planner_from_env,
        get_planner_lm_from_env,
    )

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "configure_planner_from_env": ("fleet_rlm.runtime", "configure_planner_from_env"),
    "get_planner_lm_from_env": ("fleet_rlm.runtime", "get_planner_lm_from_env"),
    "DaytonaInterpreter": ("fleet_rlm.runtime", "DaytonaInterpreter"),
}


def __getattr__(name: str) -> Any:
    """Load exported symbols lazily to reduce top-level import cost."""
    attr_spec = _LAZY_ATTRS.get(name)
    if attr_spec is not None:
        module_name, attr_name = attr_spec
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_ATTRS))
