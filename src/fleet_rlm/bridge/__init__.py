"""Stdio bridge surface for Ink/terminal frontends."""

from __future__ import annotations

from typing import Any

__all__ = ["run_stdio_server"]


def run_stdio_server(*args: Any, **kwargs: Any) -> None:
    from .server import run_stdio_server as _run_stdio_server

    _run_stdio_server(*args, **kwargs)
