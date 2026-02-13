"""Server runtime configuration."""

from __future__ import annotations

from pydantic import BaseModel


class ServerRuntimeConfig(BaseModel):
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
    ws_default_workspace_id: str = "default"
    ws_default_user_id: str = "anonymous"
    ws_enforce_react_interlocutor: bool = True
    ws_default_execution_profile: str = "ROOT_INTERLOCUTOR"
