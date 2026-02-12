"""FastAPI dependency injection helpers."""

from __future__ import annotations

from typing import Any

from .config import ServerRuntimeConfig


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(self) -> None:
        self.config = ServerRuntimeConfig()
        self.planner_lm: Any | None = None

    @property
    def is_ready(self) -> bool:
        return self.planner_lm is not None


server_state = ServerState()


def get_config() -> ServerRuntimeConfig:
    return server_state.config


def get_planner_lm() -> Any:
    return server_state.planner_lm
