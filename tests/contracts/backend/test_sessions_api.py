"""Canonical Session Catalog HTTP surface."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app_services import install_runtime_inventory
from fleet_rlm.rlm.result import RLMOutcome
from fleet_rlm.sessions.lifecycle import SessionLifecycle
from fleet_rlm.sessions.models import TurnAccess, TurnInput
from fleet_rlm.sessions.run_state import (
    ClaimedRun,
    RunClaim,
)
from tests.support.testing_app import create_testing_app


def test_sessions_route_does_not_discover_provider_retirement() -> None:
    source = Path("src/fleet_rlm/api/routes/sessions.py").read_text(encoding="utf-8")
    assert "close_root_session" not in source
    assert "run_environment_resources" not in source


def _headers(user_id=None, workspace_id=None):
    return {
        "X-Fleet-User-Id": str(user_id or uuid4()),
        "X-Fleet-Workspace-Id": str(workspace_id or uuid4()),
    }


def test_sessions_crud_happy_path() -> None:
    app = create_testing_app()
    user, workspace = uuid4(), uuid4()
    headers = _headers(user, workspace)
    with TestClient(app) as client:
        created = client.post("/api/sessions", json={"title": "My chat"}, headers=headers)
        assert created.status_code == 201
        body = created.json()
        session_id = body["id"]
        assert body["title"] == "My chat"
        assert body["status"] == "active"
        assert body["checkpoint_version"] == 0

        listed = client.get("/api/sessions", headers=headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == session_id

        patched = client.patch(
            f"/api/sessions/{session_id}",
            json={"title": "Renamed"},
            headers=headers,
        )
        assert patched.status_code == 200
        assert patched.json()["title"] == "Renamed"

        archived = client.patch(
            f"/api/sessions/{session_id}",
            json={"status": "archived"},
            headers=headers,
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"


def test_archive_returns_pending_when_provider_retirement_fails() -> None:
    class _FailingRetirement:
        async def close_root_session(self, workspace_id, session_id, *, deadline=None) -> None:
            del workspace_id, session_id, deadline
            raise RuntimeError("provider unavailable")

    app = create_testing_app()

    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        assert inventory is not None
        assert inventory.session_catalog is not None
        install_runtime_inventory(
            app,
            replace(
                inventory,
                session_lifecycle=SessionLifecycle(inventory.session_catalog, _FailingRetirement()),
            ),
        )
        created = client.post("/api/sessions", json={"title": "retire-me"})
        assert created.status_code == 201
        session_id = created.json()["id"]

        archived = client.patch(f"/api/sessions/{session_id}", json={"status": "archived"})
        assert archived.status_code == 503
        assert archived.json() == {
            "code": "session_retirement_pending",
            "message": "Session retirement is pending",
        }

        persisted = client.get(f"/api/sessions/{session_id}")
        assert persisted.status_code == 200
        assert persisted.json()["status"] == "archived"


def test_caller_supplied_identity_headers_do_not_change_local_scope() -> None:
    app = create_testing_app()
    user, workspace_a, workspace_b = uuid4(), uuid4(), uuid4()
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={"title": "private"},
            headers=_headers(user, workspace_a),
        )
        session_id = created.json()["id"]

        same_local_scope = client.get(f"/api/sessions/{session_id}", headers=_headers(user, workspace_b))
        assert same_local_scope.status_code == 200
        assert client.get("/api/sessions", headers=_headers(user, workspace_b)).json()["total"] == 1


@pytest.mark.asyncio
async def test_session_turns_are_canonical_ui_messages() -> None:
    from fleet_rlm.rlm.result import PredictionResult

    app = create_testing_app()
    scope = LocalScope()
    access = TurnAccess(scope.user_id, scope.workspace_id)
    headers = {}
    with TestClient(app) as client:
        session_id = UUID(client.post("/api/sessions", json={}, headers=headers).json()["id"])
        lifecycle = app.state.runtime_inventory.run_lifecycle
        assert lifecycle is not None
        started = await lifecycle.begin(RunClaim(access, session_id, TurnInput("question"), "turn-key", uuid4()))
        assert isinstance(started, ClaimedRun)
        await lifecycle.finish(
            started,
            RLMOutcome(
                "completed",
                prediction=PredictionResult("answer", {"answer": "answer"}, "fleet.default", "1"),
                usage={
                    "iterations": 1,
                    "observed_lm_usage": {"root": {"total_tokens": 3}},
                    "duration_ms": 4,
                },
            ),
        )

        response = client.get(f"/api/sessions/{session_id}/turns", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["next_after_sequence"] is None
        assert [message["role"] for message in payload["items"]] == ["user", "assistant"]
        assert payload["items"][0]["parts"][0]["text"] == "question"
        assert payload["items"][1]["parts"][-1]["text"] == "answer"


def test_session_turn_ui_projection_thaws_nested_json_values() -> None:
    from fleet_rlm.api.ui_message import assistant_turn_to_ui_message
    from fleet_rlm.sessions.committed_turn import (
        CommittedTurn,
        StructuredResultPart,
        TextPart,
        ToolCallPart,
        UsagePart,
    )
    from fleet_rlm.sessions.models import AssistantTurnRecord

    committed = CommittedTurn(
        schema_version=1,
        parts=(
            ToolCallPart(
                "call-1",
                "verify_semantic_work",
                "completed",
                {"batch_results": ["ALPHA", "BETA", "GAMMA"]},
                {"ok": True, "nested": {"checksum": "abc"}},
            ),
            UsagePart(
                {
                    "iterations": 1,
                    "observed_lm_usage": {},
                    "duration_ms": 1,
                }
            ),
            StructuredResultPart("contract", "1", {"findings": [{"status": "ok"}]}),
            TextPart("answer"),
        ),
    )

    message = assistant_turn_to_ui_message(AssistantTurnRecord(uuid4(), uuid4(), 1, committed, uuid4()))
    tool_part = message["parts"][0]
    result_part = message["parts"][2]
    assert tool_part["input"] == {"batch_results": ["ALPHA", "BETA", "GAMMA"]}
    assert tool_part["output"] == {"ok": True, "nested": {"checksum": "abc"}}
    assert result_part["data"]["value"] == {"findings": [{"status": "ok"}]}
