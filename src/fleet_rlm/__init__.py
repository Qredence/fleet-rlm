"""Top-level package exports for fleet_rlm."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any


_PYPROJECT_VERSION_PATTERN = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _load_version_from_pyproject() -> str:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    match = _PYPROJECT_VERSION_PATTERN.search(
        pyproject_path.read_text(encoding="utf-8")
    )
    if match is None:
        msg = "Could not locate [project].version in pyproject.toml"
        raise RuntimeError(msg)
    return match.group(1)


def _resolve_version() -> str:
    try:
        return package_version("fleet-rlm")
    except PackageNotFoundError:
        return _load_version_from_pyproject()


__version__ = _resolve_version()

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
