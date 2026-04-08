from __future__ import annotations

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING

from .fleet_cli import app

if TYPE_CHECKING:
    from . import fleet_cli, runners

__all__ = ["app", "runners", "fleet_cli"]


def __getattr__(name: str) -> ModuleType:
    if name in {"fleet_cli", "runners"}:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
