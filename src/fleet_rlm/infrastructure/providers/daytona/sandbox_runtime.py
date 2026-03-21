"""Compatibility alias for the Daytona sandbox runtime module."""

from __future__ import annotations

import sys

from .sandbox import runtime as _runtime

sys.modules[__name__] = _runtime
