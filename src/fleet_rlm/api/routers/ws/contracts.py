"""Typed contracts shared across websocket chat helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from fleet_rlm.core.models import StreamEvent

LocalPersistFn = Callable[..., Awaitable[None]]


class MaintenanceInterpreterProtocol(Protocol):
    """Interpreter capability needed for session manifest volume I/O."""

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object: ...


class ChatAgentProtocol(Protocol):
    """Subset of chat-agent behavior used by websocket runtime helpers."""

    interpreter: MaintenanceInterpreterProtocol | None

    async def __aenter__(self) -> "ChatAgentProtocol": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> bool: ...

    def history_turns(self) -> int: ...

    def set_execution_mode(self, execution_mode: str) -> None: ...

    def load_document(self, path: str, alias: str = "active") -> None: ...

    def export_session_state(self) -> dict[str, Any]: ...

    def import_session_state(self, state: dict[str, Any]) -> object: ...

    def reset(self, *, clear_sandbox_buffers: bool = True) -> object: ...

    async def execute_command(
        self, command: str, args: dict[str, Any]
    ) -> dict[str, Any] | object: ...

    def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check: Callable[[], bool] | None = None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[StreamEvent]: ...
