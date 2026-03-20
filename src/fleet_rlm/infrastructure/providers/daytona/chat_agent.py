"""Compatibility alias for the renamed Daytona workbench agent module."""

from __future__ import annotations

import sys

from . import agent as _agent

sys.modules[__name__] = _agent
