"""Compatibility shim for legacy flat react tools module.

Canonical location:
- ``fleet_rlm.react.tools`` package (``__init__.py``)
"""

from importlib import import_module as _import_module

_impl = _import_module("fleet_rlm.react.tools")
for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

__all__ = [_name for _name in dir(_impl) if not _name.startswith("__")]
