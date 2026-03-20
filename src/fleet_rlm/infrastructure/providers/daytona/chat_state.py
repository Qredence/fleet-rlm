"""Compatibility alias for the renamed Daytona session-state helpers."""

from __future__ import annotations

import sys

from . import state as _state

sys.modules[__name__] = _state
