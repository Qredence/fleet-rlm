"""Compatibility shim for legacy flat tools_rlm_delegate module."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("fleet_rlm.react.tools.delegate")
