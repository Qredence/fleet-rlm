"""Compatibility facade for execution payload sanitizer utilities."""

from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("fleet_rlm.server.execution.sanitizer")
