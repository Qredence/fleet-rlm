"""Compatibility wrapper for the historical ``fleet_rlm.scaffold`` module."""

from __future__ import annotations

from .utils import scaffold as _scaffold

__all__ = getattr(
    _scaffold,
    "__all__",
    [name for name in dir(_scaffold) if not name.startswith("_")],
)

globals().update({name: getattr(_scaffold, name) for name in __all__})
