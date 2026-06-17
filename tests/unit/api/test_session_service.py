from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fleet_rlm.api.runtime_services.session_service import SessionService
from fleet_rlm.integrations.database import ChatSessionStatus


def test_list_session_state_counts_exported_turns_schema() -> None:
    service = SessionService(persistence=None)
    identity = SimpleNamespace(tenant_claim="tenant", user_claim="user")
    session_cache = {
        "owner:abc:session-1": {
            "owner_tenant_claim": "tenant",
            "owner_user_claim": "user",
            "workspace_id": "default",
            "user_id": "anonymous",
            "session_id": "session-1",
            "session": {
                "state": {
                    "turns": [
                        {"user_message": "remember HISTORY_CHECK", "response": "ok"},
                        {"user_message": "what marker?", "response": "HISTORY_CHECK"},
                    ]
                }
            },
        }
    }

    response = service.list_session_state(session_cache=session_cache, identity=identity)

    assert response.ok is True
    assert len(response.sessions) == 1
    assert response.sessions[0].history_turns == 2


@pytest.mark.asyncio
async def test_list_sessions_tolerates_missing_first_turn_helper() -> None:
    session_id = uuid.uuid4()
    now = datetime.now(UTC)
    persistence = SimpleNamespace(
        list_chat_sessions=AsyncMock(return_value=(
            [
                SimpleNamespace(
                    id=session_id,
                    title="runtime-session",
                    status=ChatSessionStatus.ACTIVE,
                    model_name=None,
                    metadata_json={"external_session_id": "runtime-session"},
                    created_at=now,
                    updated_at=now,
                )
            ],
            1,
        ))
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    response = await SessionService(persistence).list_sessions(
        persisted_identity=identity,
        limit=5,
        offset=0,
    )

    assert response.total == 1
    assert response.items[0].external_session_id == "runtime-session"
