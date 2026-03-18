"""CLI command modules for fleet-rlm.

This package contains extracted command implementations for the CLI.
Commands are organized by category and registered via registration functions.

Modules:
- init_cmd: Bootstrap Claude Code scaffold assets
- serve_cmds: FastAPI and FastMCP server commands
"""

from .init_cmd import register_init_command
from .serve_cmds import register_serve_commands

__all__ = ["register_init_command", "register_serve_commands"]
