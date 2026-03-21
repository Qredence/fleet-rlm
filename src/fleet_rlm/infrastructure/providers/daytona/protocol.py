"""Compatibility alias for Daytona sandbox protocol types."""

from __future__ import annotations

import sys

from .sandbox import protocol as _protocol

sys.modules[__name__] = _protocol
