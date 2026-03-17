"""Compatibility package for legacy ``fleet_rlm.analytics`` imports."""

from __future__ import annotations

from importlib import import_module
import sys

from fleet_rlm.features.analytics import *  # noqa: F403

_SUBMODULES = (
    "client",
    "config",
    "mlflow_evaluation",
    "mlflow_integration",
    "mlflow_optimization",
    "posthog_callback",
    "sanitization",
    "scorers",
    "trace_context",
)

for _name in _SUBMODULES:
    _module = import_module(f"fleet_rlm.features.analytics.{_name}")
    globals()[_name] = _module
    sys.modules[f"{__name__}.{_name}"] = _module
