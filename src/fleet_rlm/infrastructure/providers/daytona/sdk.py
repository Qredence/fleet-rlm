"""Compatibility alias for Daytona sandbox SDK helpers."""

from __future__ import annotations

import sys

from .sandbox import sdk as _sdk

sys.modules[__name__] = _sdk
