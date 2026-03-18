"""Compatibility wrapper for the historical ``fleet_rlm.runners`` module."""

from __future__ import annotations

from .cli import runners as _runners

__all__ = getattr(
    _runners, "__all__", [name for name in dir(_runners) if not name.startswith("_")]
)

globals().update({name: getattr(_runners, name) for name in __all__})
