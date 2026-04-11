from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.api.dependencies import session_key
from fleet_rlm.agent_host import (
    OrchestrationSessionContext,
    build_orchestration_session_context,
    switch_orchestration_session,
)


class _AgentStub:
    def __init__(self) -> None:
        self.interpreter = None
        self.areset_calls = 0
        self.aimport_session_state_calls = 0

    async def areset(self, *, clear_sandbox_buffers: bool = True) -> object:
        _ = clear_sandbox_buffers
        self.areset_calls += 1
        return {"status": "ok"}

    async def aimport_session_state(self, state: dict[str, object]) -> object:
        _ = state
        self.aimport_session_state_calls += 1
        return {"status": "ok"}


def test_build_orchestration_session_context_reads_manifest_metadata() -> None:
    session_record = {
        "key": "owner:test:session-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "db_session_id": "db-session-a",
        "manifest": {
            "metadata": {
                "orchestration": {
                    "workflow_stage": "continued",
                    "continuation": {
                        "continuation_token": "token-123",
                        "message_id": "hitl-123",
                        "source": "clarification_questions",
                        "requested_at": "2026-04-10T15:00:00Z",
                        "updated_at": "2026-04-10T15:01:00Z",
                        "resolution": "Approve",
                    },
                }
            }
        },
    }

    context = build_orchestration_session_context(session_record=session_record)

    assert context.workflow_stage == "continued"
    assert context.continuation_token == "token-123"
    assert context.continuation is not None
    assert context.continuation.message_id == "hitl-123"
    assert context.session_record_link.key == "owner:test:session-a"
    assert context.session_record_link.db_session_id == "db-session-a"
    assert context.session_record_link.manifest_path == (
        "meta/workspaces/workspace-a/users/user-a/react-session-session-a.json"
    )


@pytest.mark.asyncio
async def test_switch_orchestration_session_restores_cached_state_and_context() -> None:
    key = session_key("tenant-a", "user-a", "session-a")
    state = SimpleNamespace(
        sessions={
            key: {
                "key": key,
                "workspace_id": "tenant-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "db_session_id": "db-session-a",
                "manifest": {
                    "metadata": {
                        "orchestration": {
                            "workflow_stage": "awaiting_hitl_resolution",
                            "continuation": {
                                "continuation_token": "token-123",
                                "message_id": "hitl-123",
                                "source": "clarification_questions",
                                "requested_at": "2026-04-10T15:00:00Z",
                                "updated_at": "2026-04-10T15:00:00Z",
                            },
                            "pending_approval": {
                                "message_id": "hitl-123",
                                "continuation_token": "token-123",
                                "workflow_stage": "awaiting_hitl_resolution",
                                "question": "Approve deployment?",
                                "source": "clarification_questions",
                                "action_labels": ["Approve", "Reject"],
                                "requested_at": "2026-04-10T15:00:00Z",
                            },
                        }
                    }
                },
                "session": {"state": {"history": [{"user_request": "hi"}]}},
            }
        }
    )
    agent = _AgentStub()

    outcome = await switch_orchestration_session(
        state=state,
        agent=agent,
        interpreter=None,
        workspace_id="tenant-a",
        user_id="user-a",
        sess_id="session-a",
        owner_tenant_claim="tenant-a",
        owner_user_claim="user-a",
        active_key=None,
        session_record=None,
        last_loaded_docs_path=None,
        local_persist=_noop_persist,
    )

    assert outcome.key == key
    assert outcome.manifest_path.endswith("react-session-session-a.json")
    assert outcome.session_record["session_id"] == "session-a"
    assert outcome.orchestration_session.workflow_stage == "awaiting_hitl_resolution"
    assert outcome.orchestration_session.continuation_token == "token-123"
    assert outcome.orchestration_session.pending_approval is not None
    assert outcome.orchestration_session.pending_approval.action_labels == [
        "Approve",
        "Reject",
    ]
    assert agent.aimport_session_state_calls == 1
    assert agent.areset_calls == 0


async def _noop_persist(*, include_volume_save: bool = True) -> None:
    _ = include_volume_save


def test_orchestration_session_context_builds_from_agent_host() -> None:
    session_record = {
        "session_id": "session-a",
        "manifest": {"metadata": {}},
    }
    context = build_orchestration_session_context(session_record=session_record)
    assert isinstance(context, OrchestrationSessionContext)
    assert context.session_record is session_record
    assert context.session_id == "session-a"
