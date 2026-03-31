"""Live credential-gated integration test for QRE-301 end-to-end tracing.

This test intentionally targets the real websocket chat + execution stream path.
It is skipped by default and only runs when all required live env vars are set.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import and_, func, select, text

from fleet_rlm.integrations.database import (
    Artifact,
    DatabaseManager,
    FleetRepository,
    Run,
    RunStatus,
    RunStep,
)
from fleet_rlm.api.main import create_app

pytestmark = [pytest.mark.live_llm, pytest.mark.db]


def _wait_for_execution_completed(
    execution_ws, *, timeout_seconds: float
) -> list[dict]:
    deadline = time.time() + timeout_seconds
    events: list[dict] = []
    while time.time() < deadline:
        event = execution_ws.receive_json()
        events.append(event)
        if event.get("type") == "execution_completed":
            return events
    raise AssertionError("Timed out waiting for execution_completed event")


def _wait_for_chat_terminal(chat_ws, *, timeout_seconds: float) -> list[dict]:
    deadline = time.time() + timeout_seconds
    messages: list[dict] = []
    while time.time() < deadline:
        msg = chat_ws.receive_json()
        messages.append(msg)
        if msg.get("type") == "error":
            raise AssertionError(f"Chat websocket returned error: {msg}")
        if msg.get("type") != "event":
            continue
        kind = msg.get("data", {}).get("kind")
        if kind in {"final", "error", "cancelled"}:
            return messages
    raise AssertionError("Timed out waiting for terminal chat event")


@pytest.mark.asyncio
async def test_qre301_live_trace_websocket_and_persistence_flow(
    require_qre301_live: str,
):
    workspace_id = "default"
    user_id = "alice"
    session_id = f"qre301-live-{uuid.uuid4().hex[:8]}"
    docs_path = str(Path(__file__).resolve().parents[2] / "AGENTS.md")
    prompt = (
        "Analyze this repository and provide a concise architecture summary with "
        "key execution flow checkpoints."
    )

    headers = {
        "X-Debug-Tenant-Id": workspace_id,
        "X-Debug-User-Id": user_id,
        "X-Debug-Email": f"{user_id}@example.com",
        "X-Debug-Name": "QRE 301 Test",
    }

    app = create_app()

    with TestClient(app) as client:
        ready_resp = client.get("/ready")
        assert ready_resp.status_code == 200
        ready_payload = ready_resp.json()
        assert ready_payload.get("planner") == "ready"
        assert ready_payload.get("database") == "ready"

        status_resp = client.get("/api/v1/runtime/status")
        assert status_resp.status_code == 200

        with client.websocket_connect(
            f"/api/v1/ws/execution?session_id={session_id}",
            headers=headers,
        ) as execution_ws:
            with client.websocket_connect(
                "/api/v1/ws/chat", headers=headers
            ) as chat_ws:
                chat_ws.send_json(
                    {
                        "type": "message",
                        "content": prompt,
                        "docs_path": docs_path,
                        "trace": True,
                        "session_id": session_id,
                    }
                )

                chat_messages = _wait_for_chat_terminal(chat_ws, timeout_seconds=240)
                execution_events = _wait_for_execution_completed(
                    execution_ws, timeout_seconds=240
                )

                chat_terminal = next(
                    (
                        msg
                        for msg in reversed(chat_messages)
                        if msg.get("type") == "event"
                        and msg.get("data", {}).get("kind")
                        in {"final", "error", "cancelled"}
                    ),
                    None,
                )
                assert chat_terminal is not None
                assert chat_terminal.get("data", {}).get("kind") == "final"

                # Persist a file artifact explicitly through command dispatch.
                chat_ws.send_json(
                    {
                        "type": "command",
                        "command": "write_to_file",
                        "args": {
                            "path": f"/data/workspaces/{workspace_id}/users/{user_id}/"
                            f"artifacts/{session_id}-qre301.txt",
                            "content": "QRE-301 artifact persistence probe",
                            "append": False,
                        },
                        "session_id": session_id,
                    }
                )
                command_result = chat_ws.receive_json()
                assert command_result.get("type") == "command_result"
                assert command_result.get("result", {}).get("status") == "ok"

                # Force local/session persistence pass after command side effects.
                chat_ws.send_json(
                    {
                        "type": "cancel",
                        "session_id": session_id,
                    }
                )
                cancel_ack = chat_ws.receive_json()
                assert cancel_ack.get("type") == "cancelled"

                run_started = next(
                    (
                        ev
                        for ev in execution_events
                        if ev.get("type") == "execution_started"
                    ),
                    None,
                )
                assert run_started is not None
                run_id = str(run_started["run_id"])

                assert execution_events[0].get("type") == "execution_started"
                assert execution_events[-1].get("type") == "execution_completed"
                assert any(
                    ev.get("type") == "execution_step" for ev in execution_events
                )
                assert all(ev.get("run_id") == run_id for ev in execution_events)
                assert all(
                    ev.get("workspace_id") == workspace_id for ev in execution_events
                )
                assert all(ev.get("user_id") == user_id for ev in execution_events)
                assert all(
                    ev.get("session_id") == session_id for ev in execution_events
                )

                step_events = [
                    ev for ev in execution_events if ev.get("type") == "execution_step"
                ]
                assert step_events
                for event in step_events:
                    step = event.get("step")
                    assert isinstance(step, dict)
                    assert isinstance(step.get("id"), str) and step["id"]
                    assert isinstance(step.get("label"), str) and step["label"]
                    assert step.get("type") in {
                        "llm",
                        "tool",
                        "repl",
                        "memory",
                        "output",
                    }
                    assert isinstance(step.get("timestamp"), (int, float))

        session_state_resp = client.get("/api/v1/sessions/state", headers=headers)
        assert session_state_resp.status_code == 200
        session_payload = session_state_resp.json()
        sessions = session_payload.get("sessions", [])
        matching = [
            s
            for s in sessions
            if s.get("workspace_id") == workspace_id
            and s.get("user_id") == user_id
            and s.get("session_id") == session_id
        ]
        assert matching, "Target session not found in /api/v1/sessions/state"
        assert int(matching[0].get("history_turns", 0)) >= 1

    db = DatabaseManager(require_qre301_live)
    repo = FleetRepository(db)
    identity = await repo.upsert_identity(
        entra_tenant_id=workspace_id,
        entra_user_id=user_id,
        email=f"{user_id}@example.com",
        full_name="QRE 301 Test",
    )

    try:
        async with db.session() as session:
            async with session.begin():
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(identity.tenant_id)},
                )

                run_row = (
                    await session.execute(
                        select(Run).where(
                            and_(
                                Run.tenant_id == identity.tenant_id,
                                Run.external_run_id == run_id,
                            )
                        )
                    )
                ).scalar_one_or_none()

                assert run_row is not None, (
                    f"Run not found for external_run_id={run_id}"
                )
                assert run_row.status == RunStatus.COMPLETED

                step_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(RunStep)
                        .where(
                            and_(
                                RunStep.tenant_id == identity.tenant_id,
                                RunStep.run_id == run_row.id,
                            )
                        )
                    )
                ).scalar_one()
                assert int(step_count or 0) > 0

                artifact_count = (
                    await session.execute(
                        select(func.count())
                        .select_from(Artifact)
                        .where(
                            and_(
                                Artifact.tenant_id == identity.tenant_id,
                                Artifact.run_id == run_row.id,
                            )
                        )
                    )
                ).scalar_one()
                assert int(artifact_count or 0) > 0
    finally:
        await db.dispose()
