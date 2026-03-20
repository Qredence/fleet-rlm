"""Compatibility alias for the Daytona sandbox session module."""

from __future__ import annotations

import sys

from .sandbox import session as _session

sys.modules[__name__] = _session
