from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncio

from fleet_rlm.worker import (
    WorkspaceTaskRequest,
    run_workspace_task,
    stream_workspace_task,
)


@dataclass(slots=True)
class _FakeRuntimeEvent:
    kind: str
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class _FakeAgent:
    def __init__(self) -> None:
        self.execution_mode: str | None = None
        self.stream_calls: list[dict[str, object]] = []

    def set_execution_mode(self, execution_mode: str) -> None:
        self.execution_mode = execution_mode

    async def aiter_chat_turn_stream(
        self,
        message: str,
        trace: bool = True,
        cancel_check=None,
        *,
        docs_path: str | None = None,
        repo_url: str | None = None,
        repo_ref: str | None = None,
        context_paths: list[str] | None = None,
        batch_concurrency: int | None = None,
        volume_name: str | None = None,
    ) -> AsyncIterator[object]:
        self.stream_calls.append(
            {
                "message": message,
                "trace": trace,
                "docs_path": docs_path,
                "repo_url": repo_url,
                "repo_ref": repo_ref,
                "context_paths": context_paths,
                "batch_concurrency": batch_concurrency,
                "volume_name": volume_name,
            }
        )
        yield _FakeRuntimeEvent(kind="status", text="running")
        yield _FakeRuntimeEvent(
            kind="final",
            text="done",
            payload={"run_result": {"status": "completed"}},
        )


def test_worker_contracts_do_not_import_transport_types() -> None:
    contracts_source = __import__(
        "fleet_rlm.worker.contracts", fromlist=["__name__"]
    ).__file__
    assert contracts_source is not None
    with open(contracts_source, encoding="utf-8") as handle:
        content = handle.read()
    assert "fastapi" not in content
    assert "WebSocket" not in content


def test_run_workspace_task_executes_end_to_end() -> None:
    agent = _FakeAgent()
    prepared = False

    async def _prepare() -> None:
        nonlocal prepared
        prepared = True

    request = WorkspaceTaskRequest(
        agent=agent,
        message="Investigate the repository",
        execution_mode="tools_only",
        trace=True,
        docs_path="docs/readme.md",
        workspace_id="ws-1",
        repo_url="https://example.invalid/repo.git",
        repo_ref="main",
        context_paths=["src"],
        batch_concurrency=2,
        prepare=_prepare,
    )

    result = asyncio.run(run_workspace_task(request))

    assert prepared is True
    assert agent.execution_mode == "tools_only"
    assert result.status == "completed"
    assert result.output_text == "done"
    assert result.terminal_event.kind == "final"
    assert [event.kind for event in result.events] == ["status", "final"]
    assert agent.stream_calls[0]["volume_name"] == "ws-1"


def test_stream_workspace_task_yields_normalized_workspace_events() -> None:
    agent = _FakeAgent()
    request = WorkspaceTaskRequest(agent=agent, message="hello")

    async def _collect() -> list[Any]:
        return [event async for event in stream_workspace_task(request)]

    events = asyncio.run(_collect())

    assert [event.kind for event in events] == ["status", "final"]
    assert events[-1].terminal is True
    assert agent.stream_calls[0]["context_paths"] is None


def test_stream_workspace_task_preserves_unspecified_optional_overrides() -> None:
    agent = _FakeAgent()
    request = WorkspaceTaskRequest(agent=agent, message="hello")

    async def _collect() -> None:
        async for _event in stream_workspace_task(request):
            pass

    asyncio.run(_collect())

    assert agent.stream_calls[0] == {
        "message": "hello",
        "trace": True,
        "docs_path": None,
        "repo_url": None,
        "repo_ref": None,
        "context_paths": None,
        "batch_concurrency": None,
        "volume_name": None,
    }
