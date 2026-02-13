"""FastAPI dependency injection helpers."""

from __future__ import annotations

from typing import Any

from .config import ServerRuntimeConfig


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(self) -> None:
        self.config = ServerRuntimeConfig()
        self.planner_lm: Any | None = None
        self.sessions: dict[str, dict[str, Any]] = {}

    @property
    def is_ready(self) -> bool:
        return self.planner_lm is not None


server_state = ServerState()


def get_config() -> ServerRuntimeConfig:
    return server_state.config


def get_planner_lm() -> Any:
    return server_state.planner_lm


def session_key(workspace_id: str, user_id: str) -> str:
    """Build a stable in-memory key for a stateful user/workspace session."""
    return f"{workspace_id}:{user_id}"
