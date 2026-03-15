"""RLM with Modal package for sandboxed code execution."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__version__ = "0.4.97"

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------
# Only symbols that are actually consumed by external code, entry-points,
# tests, or the MCP server are listed here.  Internal code should import
# from the specific sub-package (e.g. ``fleet_rlm.core``,
# ``fleet_rlm.cli.runners``) instead of going through this façade.
# ---------------------------------------------------------------------------

__all__ = [
    "__version__",
    # Core planner / interpreter
    "configure_planner_from_env",
    "get_planner_lm_from_env",
    "ModalInterpreter",
    # Lazy sub-modules (accessed as fleet_rlm.runners, fleet_rlm.fleet_cli)
    "runners",
    "fleet_cli",
    "scaffold",
    "daytona_rlm",
]

if TYPE_CHECKING:
    from .core import (
        ModalInterpreter,
        configure_planner_from_env,
        get_planner_lm_from_env,
    )

# ---------------------------------------------------------------------------
# Lazy loading
# ---------------------------------------------------------------------------

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "configure_planner_from_env": ("fleet_rlm.core", "configure_planner_from_env"),
    "get_planner_lm_from_env": ("fleet_rlm.core", "get_planner_lm_from_env"),
    "ModalInterpreter": ("fleet_rlm.core", "ModalInterpreter"),
}

_LAZY_MODULES: dict[str, str] = {
    "scaffold": "fleet_rlm.utils.scaffold",
    "runners": "fleet_rlm.cli.runners",
    "fleet_cli": "fleet_rlm.cli.fleet_cli",
    "daytona_rlm": "fleet_rlm.infrastructure.providers.daytona",
}


def __getattr__(name: str) -> Any:
    """Load exported symbols lazily to reduce top-level import cost."""
    attr_spec = _LAZY_ATTRS.get(name)
    if attr_spec is not None:
        module_name, attr_name = attr_spec
        value = getattr(import_module(module_name), attr_name)
        globals()[name] = value
        return value

    module_name = _LAZY_MODULES.get(name)
    if module_name is not None:
        module = import_module(module_name)
        globals()[name] = module
        return module

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_LAZY_MODULES))
