"""Compatibility alias for the Daytona sandbox driver source module."""

from __future__ import annotations

import sys

from .sandbox import driver as _driver

sys.modules[__name__] = _driver
