"""Compatibility alias for Daytona SDK loading and async helpers."""

from __future__ import annotations

import sys

from .sandbox import sdk as _sdk

sys.modules[__name__] = _sdk
