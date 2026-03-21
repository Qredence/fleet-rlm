"""Compatibility alias for the split MLflow runtime module."""

from __future__ import annotations

import sys

from . import mlflow_runtime as _runtime

sys.modules[__name__] = _runtime
