"""Typed contracts and Daytona request normalization for websocket execution."""

from __future__ import annotations

from contextlib import AbstractContextManager
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fleet_rlm.runtime.models import StreamEvent

from ...schemas import WSMessage

LocalPersistFn = Callable[..., Awaitable[None]]
PreStreamSetupFn = Callable[[], Awaitable[None]]


class MaintenanceInterpreterProtocol(Protocol):
    """Interpreter capability needed for session manifest volume I/O."""

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object: ...

    def execution_profile(self, profile: object) -> AbstractContextManager[object]: ...


class ChatAgentProtocol(Protocol):
    """Subset of chat-agent behavior used by websocket runtime helpers."""

    interpreter: MaintenanceInterpreterProtocol | None

    async def __aenter__(self) -> ChatAgentProtocol: ...

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

    async def aimport_session_state(self, state: dict[str, Any]) -> object: ...

    def reset(self, *, clear_sandbox_buffers: bool = True) -> object: ...

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> object: ...

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


@dataclass(slots=True)
class DaytonaChatRequestOptions:
    """Normalized Daytona websocket options after schema validation."""

    repo_url: str | None
    repo_ref: str | None
    context_paths: list[str]
    batch_concurrency: int | None
    workspace_id: str


def normalize_daytona_chat_request(
    msg: WSMessage,
    workspace_id: str,
) -> DaytonaChatRequestOptions | None:
    """Return a typed Daytona request payload for the canonical runtime."""

    repo_url = str(msg.repo_url or "").strip() or None
    repo_ref = str(msg.repo_ref or "").strip() or None
    context_paths = [
        str(item).strip() for item in (msg.context_paths or []) if str(item).strip()
    ]
    return DaytonaChatRequestOptions(
        repo_url=repo_url,
        repo_ref=repo_ref,
        context_paths=context_paths,
        batch_concurrency=msg.batch_concurrency,
        workspace_id=workspace_id,
    )


def _normalize_context_paths(*groups: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
    return normalized


async def prepare_daytona_workspace_for_turn(
    *,
    agent: ChatAgentProtocol,
    request: DaytonaChatRequestOptions,
    docs_path: str | None,
) -> None:
    """Apply Daytona workspace settings via the interpreter's native session API."""

    interpreter = getattr(agent, "interpreter", None)
    if interpreter is None:
        return

    configure_workspace = getattr(interpreter, "aconfigure_workspace", None)
    if not callable(configure_workspace):
        return

    raw_loaded_paths = getattr(agent, "loaded_document_paths", ())
    loaded_document_paths = (
        [str(item) for item in raw_loaded_paths]
        if isinstance(raw_loaded_paths, (list, tuple))
        else []
    )
    docs_paths = [str(docs_path)] if docs_path is not None else []
    context_paths = _normalize_context_paths(
        loaded_document_paths,
        list(request.context_paths),
        docs_paths,
    )

    normalized_batch_concurrency = (
        max(1, int(request.batch_concurrency))
        if isinstance(request.batch_concurrency, int) and request.batch_concurrency > 0
        else None
    )
    setattr(agent, "batch_concurrency", normalized_batch_concurrency)

    await configure_workspace(
        repo_url=request.repo_url,
        repo_ref=request.repo_ref,
        context_paths=context_paths,
        volume_name=request.workspace_id,
    )
