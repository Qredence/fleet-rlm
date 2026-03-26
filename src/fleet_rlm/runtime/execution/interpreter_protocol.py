"""Shared interpreter protocols for ReAct/RLM backends."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Callable, Protocol

from dspy.primitives import FinalOutput

from .profiles import ExecutionProfile


class RLMInterpreterProtocol(Protocol):
    """Interpreter surface required by the shared ReAct + RLM runtime."""

    async_execute: bool
    volume_name: str | None
    volume_mount_path: str
    max_llm_calls: int
    _llm_call_count: int
    output_fields: list[dict[str, Any]] | None
    tools: dict[str, Callable[..., Any]]
    default_execution_profile: ExecutionProfile
    execution_event_callback: Any

    def start(self) -> None:
        pass

    async def astart(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    async def ashutdown(self) -> None:
        pass

    def execute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        pass

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        execution_profile: ExecutionProfile | None = None,
    ) -> str | FinalOutput:
        pass

    def execution_profile(
        self, profile: ExecutionProfile
    ) -> AbstractContextManager[Any]:
        pass

    def build_delegate_child(self, *, remaining_llm_budget: int) -> Any:
        pass


class StatefulWorkspaceInterpreterProtocol(RLMInterpreterProtocol, Protocol):
    """Optional workspace/session capabilities for backends like Daytona."""

    def configure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        pass

    def export_session_state(self) -> dict[str, Any]:
        pass

    def import_session_state(self, state: dict[str, Any]) -> None:
        pass

    async def aimport_session_state(self, state: dict[str, Any]) -> None:
        pass

    async def aconfigure_workspace(
        self,
        *,
        repo_url: str | None,
        repo_ref: str | None,
        context_paths: list[str] | None,
        volume_name: str | None,
        force_new_session: bool = False,
    ) -> None:
        pass
