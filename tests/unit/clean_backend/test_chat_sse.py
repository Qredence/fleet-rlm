"""impl-03: POST /api/chat typed SSE with injectable coordinator."""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import TurnCoordinator
from fleet_rlm_clean.rlm.events import EventRecorder, RuntimeEvent, RuntimeEventKind
from fleet_rlm_clean.rlm.outcome import TurnExecutionOutcome
from fleet_rlm_clean.rlm.runner import TurnEventStream


class _FakeRunner:
    def __init__(self, events: list[RuntimeEvent] | None = None, *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.seen: list[Any] = []
        self.closed = 0

    def stream(self, context: Any) -> TurnEventStream:
        self.seen.append(context)

        async def _agen() -> AsyncIterator[RuntimeEvent]:
            try:
                if self.fail:
                    return
                if self.events is not None:
                    for event in self.events:
                        if event.kind in {
                            RuntimeEventKind.RUN_COMPLETED,
                            RuntimeEventKind.ERROR,
                        }:
                            continue
                        yield event
                    return
                recorder = EventRecorder(run_id=context.run_id, session_id=context.session_id)
                yield recorder.emit(RuntimeEventKind.RUN_STARTED, {})
                yield recorder.emit(RuntimeEventKind.TEXT_DELTA, {"text": "hi"})
            finally:
                self.closed += 1

        if self.fail:
            outcome = TurnExecutionOutcome(
                terminal_status="failed",
                public_error_message="Turn failed",
            )
        elif self.events is not None:
            outcome = TurnExecutionOutcome(terminal_status="completed", assistant_text="hi")
            for event in self.events:
                if event.kind == RuntimeEventKind.RUN_COMPLETED:
                    outcome = TurnExecutionOutcome(
                        terminal_status="completed",
                        assistant_text=str(event.payload.get("assistant_text") or "hi"),
                    )
                elif event.kind == RuntimeEventKind.ERROR:
                    outcome = TurnExecutionOutcome(
                        terminal_status=str(event.payload.get("status") or "failed"),  # type: ignore[arg-type]
                        public_error_message=str(event.payload.get("message") or "failed"),
                    )
        else:
            outcome = TurnExecutionOutcome(terminal_status="completed", assistant_text="hi")
        return TurnEventStream(_agen(), outcome=outcome)


def _parse_sse_data_lines(body: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                raw = line[5:].lstrip()
                if raw:
                    payloads.append(json.loads(raw))
    return payloads


def test_chat_route_returns_typed_sse_events() -> None:
    from fleet_rlm_clean.app import create_app

    app = create_app()
    runner = _FakeRunner()
    app.state.turn_coordinator = TurnCoordinator(
        runner=runner,  # type: ignore[arg-type]
        context_builder=lambda command: _minimal_context(command),
    )

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "hello there"})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    payloads = _parse_sse_data_lines(response.text)
    assert payloads[0]["kind"] == "run.started"
    assert payloads[-1]["kind"] == "run.completed"
    assert [p["sequence"] for p in payloads] == list(range(1, len(payloads) + 1))
    assert runner.closed == 1


def test_chat_route_uses_synthetic_identity_headers() -> None:
    from fleet_rlm_clean.app import create_app
    from fleet_rlm_clean.rlm.context import RLMTurnContext

    captured: list[RLMTurnContext] = []

    def builder(command: ChatTurnCommand) -> RLMTurnContext:
        ctx = _minimal_context(command)
        captured.append(ctx)
        return ctx

    app = create_app()
    app.state.turn_coordinator = TurnCoordinator(
        runner=_FakeRunner(),  # type: ignore[arg-type]
        context_builder=builder,
    )
    user_id = uuid4()
    workspace_id = uuid4()
    session_id = uuid4()

    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "ping", "session_id": str(session_id)},
        headers={
            "X-Fleet-User-Id": str(user_id),
            "X-Fleet-Workspace-Id": str(workspace_id),
        },
    )
    assert response.status_code == 200
    assert captured[0].user_id == user_id
    assert captured[0].workspace_id == workspace_id
    assert captured[0].session_id == session_id


def test_chat_openapi_contains_request_contract() -> None:
    from fleet_rlm_clean.app import create_app

    app = create_app()
    schema = app.openapi()
    path = schema["paths"]["/api/chat"]["post"]
    body = path["requestBody"]["content"]["application/json"]["schema"]
    # Pydantic may inline or $ref
    if "$ref" in body:
        ref = body["$ref"].split("/")[-1]
        body = schema["components"]["schemas"][ref]
    props = body.get("properties", body)
    assert "message" in props
    assert "session_id" in props
    assert "attachment_ids" in props


def test_chat_rejects_invalid_attachment_before_stream(tmp_path: Path) -> None:
    """Invalid attachment_ids → HTTP 400; coordinator must not run."""
    from fleet_rlm_clean.app import create_app
    from fleet_rlm_clean.files.uploads import LocalAttachmentStore

    app = create_app()
    app.state.attachment_store = LocalAttachmentStore(tmp_path / "up", max_bytes=1024)
    runner = _FakeRunner()
    app.state.turn_coordinator = TurnCoordinator(
        runner=runner,  # type: ignore[arg-type]
        context_builder=_minimal_context,
    )
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        json={"message": "hello", "attachment_ids": [str(uuid4())]},
        headers={
            "X-Fleet-User-Id": str(uuid4()),
            "X-Fleet-Workspace-Id": str(uuid4()),
        },
    )
    assert response.status_code == 400
    assert "invalid attachment" in response.json()["detail"].lower()
    assert runner.closed == 0
    assert runner.seen == []


def test_chat_route_module_has_no_dspy_or_daytona_imports() -> None:
    route_path = Path(__file__).resolve().parents[3] / "src" / "fleet_rlm_clean" / "api" / "routes" / "chat.py"
    tree = ast.parse(route_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "dspy" not in imported
    assert "daytona" not in imported


def test_coordinator_rejects_empty_message() -> None:
    coordinator = TurnCoordinator(
        runner=_FakeRunner(),  # type: ignore[arg-type]
        context_builder=_minimal_context,
    )

    async def _run() -> None:
        command = ChatTurnCommand(
            user_id=uuid4(),
            workspace_id=uuid4(),
            message="   ",
        )
        with pytest.raises(ValueError, match="message"):
            async for _ in coordinator.stream(command):
                pass

    import asyncio

    asyncio.run(_run())


def _minimal_context(command: ChatTurnCommand) -> Any:
    from unittest.mock import MagicMock

    from fleet_rlm_clean.chat.turn_coordinator import ephemeral_lease
    from fleet_rlm_clean.rlm.budgets import RLMBudget
    from fleet_rlm_clean.rlm.context import RLMTurnContext
    from fleet_rlm_clean.rlm.model_bundle import RLMModelBundle

    return RLMTurnContext(
        run_id=uuid4(),
        session_id=command.session_id,
        user_id=command.user_id,
        workspace_id=command.workspace_id,
        request=command.message,
        models=RLMModelBundle(root_lm=MagicMock(), sub_lm=MagicMock()),
        budget=RLMBudget(),
        lease=ephemeral_lease(MagicMock()),
    )
