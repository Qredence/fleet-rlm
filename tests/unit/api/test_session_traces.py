from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fleet_rlm.api.runtime_services.session_service import SessionService
from fleet_rlm.integrations.database.models_enums import ExternalTraceProvider


@pytest.mark.asyncio
async def test_get_session_traces_maps_repository_rows() -> None:
    session_id = uuid.uuid4()
    trace_row = SimpleNamespace(
        trace_id="tr-abc",
        client_request_id="chat-123",
        turn_id=uuid.uuid4(),
        provider=ExternalTraceProvider.MLFLOW,
        experiment_id="1",
        experiment_name="fleet-rlm",
        observed_at=datetime.now(UTC),
        metadata_json={"fleet_rlm.routing_decision": "forced_rlm"},
    )
    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=SimpleNamespace(id=session_id)),
        list_external_traces_for_session=AsyncMock(return_value=([trace_row], 1)),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    response = await SessionService(persistence).get_session_traces(
        persisted_identity=identity,
        session_id=str(session_id),
    )

    assert response.total == 1
    assert response.items[0].trace_id == "tr-abc"
    assert response.items[0].client_request_id == "chat-123"
    assert response.items[0].metadata["fleet_rlm.routing_decision"] == "forced_rlm"
