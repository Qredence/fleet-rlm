"""Core RLM engine components."""

from fleet_rlm.core.execution.core_driver import sandbox_driver

from . import tools
from .config import configure_planner_from_env, get_planner_lm_from_env


def __getattr__(name: str):
    """Lazy import to break circular dependency with core.tools."""
    if name == "ModalInterpreter":
        from .execution.interpreter import ModalInterpreter

        return ModalInterpreter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ModalInterpreter",
    "configure_planner_from_env",
    "get_planner_lm_from_env",
    "sandbox_driver",
    "tools",
]
