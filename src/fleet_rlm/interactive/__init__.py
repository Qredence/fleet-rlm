"""Interactive coding CLI runtime for ReAct + RLM sessions."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DependencyCheck:
    """Result of optional dependency checks for interactive runtime."""

    ok: bool
    missing: list[str]


def check_interactive_dependencies(
    *, mode: Literal["textual", "legacy", "all"] = "textual"
) -> DependencyCheck:
    """Check whether interactive extras dependencies are importable."""
    common = [
        "rich",
        "loguru",
        "tenacity",
        "aiofiles",
        "tomlkit",
        "ripgrepy",
    ]
    if mode == "legacy":
        required = [*common, "prompt_toolkit"]
    elif mode == "all":
        required = [*common, "prompt_toolkit", "textual"]
    else:
        required = [*common, "textual"]
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    return DependencyCheck(ok=not missing, missing=missing)


def install_hint(*, include_server: bool = False, include_mcp: bool = False) -> str:
    """Return the canonical `uv sync` command to enable optional extras."""
    extras = ["--extra dev", "--extra interactive"]
    if include_server:
        extras.append("--extra server")
    if include_mcp:
        extras.append("--extra mcp")
    return "uv sync " + " ".join(extras)
