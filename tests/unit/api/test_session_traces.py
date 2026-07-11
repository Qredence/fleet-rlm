from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from fleet_rlm.api.runtime_services.session_service import SessionService
from fleet_rlm.api.runtime_services.session_trace_debug import get_owned_session_trace_debug
from fleet_rlm.api.schemas.sessions import SessionTraceExportRequest
from fleet_rlm.db.enums import ExternalTraceProvider
from fleet_rlm.integrations.observability.mlflow_traces import _trace_session_id
from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError


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


@pytest.mark.asyncio
async def test_get_session_traces_resolves_runtime_external_session_id() -> None:
    session_id = uuid.uuid4()
    trace_row = SimpleNamespace(
        trace_id="tr-runtime",
        client_request_id="chat-runtime",
        turn_id=None,
        provider=ExternalTraceProvider.MLFLOW,
        experiment_id="1",
        experiment_name="fleet-rlm",
        observed_at=datetime.now(UTC),
        metadata_json={},
    )
    persistence = SimpleNamespace(
        get_chat_session_by_external_id=AsyncMock(return_value=SimpleNamespace(id=session_id)),
        list_external_traces_for_session=AsyncMock(return_value=([trace_row], 1)),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    response = await SessionService(persistence).get_session_traces(
        persisted_identity=identity,
        session_id="runtime-session-123",
    )

    persistence.get_chat_session_by_external_id.assert_awaited_once_with(
        tenant_id=identity.tenant_id,
        external_session_id="runtime-session-123",
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    persistence.list_external_traces_for_session.assert_awaited_once_with(
        tenant_id=identity.tenant_id,
        session_id=session_id,
        workspace_id=identity.workspace_id,
        limit=50,
        offset=0,
    )
    assert response.items[0].trace_id == "tr-runtime"


@pytest.mark.asyncio
async def test_trace_debug_resolves_runtime_external_session_id(monkeypatch) -> None:
    session_id = uuid.uuid4()
    persistence = SimpleNamespace(
        get_chat_session_by_external_id=AsyncMock(
            return_value=SimpleNamespace(
                id=session_id,
                external_session_id="runtime-session-123",
                workspace_id="workspace-1",
                owner_user="user-1",
            )
        ),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    fake_trace = SimpleNamespace(
        to_dict=lambda: {
            "info": {"trace_id": "tr-runtime", "client_request_id": "chat-runtime"},
            "data": {
                "spans": [
                    {
                        "span_id": "span-1",
                        "name": "run_command",
                        "span_type": "TOOL",
                        "attributes": {"mlflow.spanFunctionName": "run_command"},
                        "inputs": {"command": "echo hi"},
                        "outputs": {"stdout": "hi"},
                    }
                ]
            },
        }
    )

    from fleet_rlm.integrations.observability import mlflow_traces

    monkeypatch.setattr(mlflow_traces, "resolve_trace", lambda **_kwargs: fake_trace)

    response = await get_owned_session_trace_debug(
        persistence=persistence,
        persisted_identity=identity,
        session_id="runtime-session-123",
        trace_id="tr-runtime",
    )

    persistence.get_chat_session_by_external_id.assert_awaited_once_with(
        tenant_id=identity.tenant_id,
        external_session_id="runtime-session-123",
        user_id=identity.user_id,
        workspace_id=identity.workspace_id,
    )
    assert response.trace_id == "tr-runtime"
    assert response.resolved_from == "trace_id"
    assert response.spans[0].mapped_render_kind == "sandbox"


@pytest.mark.asyncio
async def test_export_session_traces_writes_artifacts(tmp_path, monkeypatch) -> None:
    session_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    trace_row = SimpleNamespace(
        trace_id="tr-abc",
        client_request_id="chat-123",
        turn_id=turn_id,
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

    from fleet_rlm.integrations.observability import mlflow_traces

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "resolve_trace", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        mlflow_traces,
        "trace_to_full_payload",
        lambda trace, **kwargs: {
            "info": {"trace_id": "tr-abc", "client_request_id": "chat-123"},
            "session_id": kwargs["session_id"],
            "turn_id": kwargs["turn_id"],
            "metadata": kwargs["external_trace_metadata"],
            "spans": [],
            "assessments": [],
        },
    )

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=str(session_id),
        body=SessionTraceExportRequest(format="both"),
    )

    assert response.trace_count == 1
    assert response.skipped_trace_ids == []
    assert response.json_path is not None
    assert response.jsonl_path is not None
    assert response.distilled_bundle_path is not None
    assert response.summary["trace_count"] == 1


@pytest.mark.asyncio
async def test_export_session_traces_reports_missing_trace(tmp_path, monkeypatch) -> None:
    session_id = uuid.uuid4()
    trace_row = SimpleNamespace(
        trace_id="tr-missing",
        client_request_id="chat-missing",
        turn_id=None,
        provider=ExternalTraceProvider.MLFLOW,
        metadata_json={},
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

    from fleet_rlm.integrations.observability import mlflow_traces

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "resolve_trace", lambda **kwargs: None)

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=str(session_id),
        body=SessionTraceExportRequest(format="jsonl"),
    )

    assert response.trace_count == 0
    assert response.json_path is None
    assert response.jsonl_path is not None
    assert response.skipped_trace_ids == ["tr-missing"]


@pytest.mark.asyncio
async def test_export_session_traces_falls_back_to_mlflow_session_lookup(tmp_path, monkeypatch) -> None:
    session_id = uuid.UUID(int=42)
    session = SimpleNamespace(
        id=session_id,
        title="frontend-session-1",
        external_session_id="frontend-session-1",
        workspace_id="local-workspace",
        owner_user="local-user",
    )

    async def unsupported_external_traces(**kwargs):
        _ = kwargs
        raise UnsupportedLocalCapabilityError("list_external_traces_for_session")

    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=session),
        list_external_traces_for_session=unsupported_external_traces,
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    from fleet_rlm.integrations.observability import mlflow_traces

    searched_session_ids: list[str] = []

    workspace_scoped_session_id = "local-workspace:local-user:frontend-session-1"

    def fake_search_traces_by_session_id(session_id: str, **_kwargs):
        searched_session_ids.append(session_id)
        if session_id == workspace_scoped_session_id:
            return [SimpleNamespace()]
        return []

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "search_traces_by_session_id", fake_search_traces_by_session_id)
    monkeypatch.setattr(
        mlflow_traces,
        "trace_to_full_payload",
        lambda trace, **kwargs: {
            "info": {"trace_id": "tr-local", "client_request_id": "chat-local"},
            "session_id": kwargs["session_id"],
            "turn_id": kwargs["turn_id"],
            "metadata": kwargs["external_trace_metadata"],
            "spans": [],
            "assessments": [],
        },
    )

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=str(session_id),
        body=SessionTraceExportRequest(format="jsonl"),
    )

    assert response.trace_count == 1
    assert response.skipped_trace_ids == []
    assert searched_session_ids == [workspace_scoped_session_id]
    assert response.jsonl_path is not None
    assert response.distilled_bundle_path is not None


@pytest.mark.asyncio
async def test_export_session_traces_skips_foreign_mlflow_fallback_traces(tmp_path, monkeypatch) -> None:
    session_id = uuid.UUID(int=42)
    session = SimpleNamespace(
        id=session_id,
        title="frontend-session-1",
        external_session_id="frontend-session-1",
        workspace_id="local-workspace",
        owner_user="local-user",
    )

    async def unsupported_external_traces(**kwargs):
        _ = kwargs
        raise UnsupportedLocalCapabilityError("list_external_traces_for_session")

    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=session),
        list_external_traces_for_session=unsupported_external_traces,
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    from fleet_rlm.integrations.observability import mlflow_traces

    foreign_trace = SimpleNamespace(
        to_dict=lambda: {
            "info": {
                "trace_id": "tr-foreign",
                "trace_metadata": {
                    "mlflow.trace.user": "other-user",
                    "fleet_rlm.workspace_id": "other-workspace",
                },
            }
        }
    )

    def fake_search_traces_by_session_id(session_id: str, **_kwargs):
        if session_id == "local-workspace:local-user:frontend-session-1":
            return [foreign_trace]
        return []

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "search_traces_by_session_id", fake_search_traces_by_session_id)

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=str(session_id),
        body=SessionTraceExportRequest(format="jsonl"),
    )

    assert response.trace_count == 0
    assert response.skipped_trace_ids == ["tr-foreign"]
    assert any("ownership" in error for error in response.errors)


@pytest.mark.asyncio
async def test_export_session_traces_supports_validated_mlflow_session_hint(
    tmp_path,
    monkeypatch,
) -> None:
    durable_session_id = uuid.uuid4()
    mlflow_session_id = "default:anonymous:frontend-session-1"
    session = SimpleNamespace(
        id=durable_session_id,
        title="frontend-session-1",
        metadata_json={"external_session_id": "frontend-session-1"},
        workspace_id="local-workspace",
        owner_user="local-user",
    )

    async def unsupported_external_traces(**kwargs):
        _ = kwargs
        raise UnsupportedLocalCapabilityError("list_external_traces_for_session")

    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=session),
        get_chat_session_by_external_id=AsyncMock(return_value=None),
        list_external_traces_for_session=unsupported_external_traces,
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    from fleet_rlm.integrations.observability import mlflow_traces

    searched_session_ids: list[str] = []

    def fake_search_traces_by_session_id(session_id: str, **_kwargs):
        searched_session_ids.append(session_id)
        if session_id == mlflow_session_id:
            return [SimpleNamespace()]
        return []

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "search_traces_by_session_id", fake_search_traces_by_session_id)
    monkeypatch.setattr(
        mlflow_traces,
        "trace_to_full_payload",
        lambda trace, **kwargs: {
            "info": {"trace_id": "tr-direct", "client_request_id": "chat-direct"},
            "session_id": kwargs["session_id"],
            "turn_id": kwargs["turn_id"],
            "metadata": kwargs["external_trace_metadata"],
            "spans": [],
            "assessments": [],
        },
    )

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=str(durable_session_id),
        body=SessionTraceExportRequest(format="both", mlflow_session_id=mlflow_session_id),
    )

    assert response.session_id == str(durable_session_id)
    assert response.trace_count == 1
    assert response.summary["trace_count"] == 1
    assert searched_session_ids == [mlflow_session_id]
    assert response.jsonl_path is not None
    assert response.distilled_bundle_path is not None


def test_trace_session_id_reads_modern_mlflow_session_metadata() -> None:
    trace = SimpleNamespace(
        to_dict=lambda: {
            "info": {
                "trace_metadata": {
                    "mlflow.trace.session": "default:anonymous:frontend-session-1",
                }
            }
        }
    )

    assert _trace_session_id(trace) == "default:anonymous:frontend-session-1"


@pytest.mark.asyncio
async def test_export_session_traces_rejects_unlinked_runtime_session() -> None:
    runtime_session_id = str(uuid.uuid4())
    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=None),
        get_chat_session_by_external_id=AsyncMock(return_value=None),
        list_external_traces_for_session=AsyncMock(
            side_effect=AssertionError("external trace lookup should be skipped")
        ),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        await SessionService(persistence).export_session_traces(
            persisted_identity=identity,
            session_id=runtime_session_id,
            body=SessionTraceExportRequest(format="jsonl"),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_export_session_traces_rejects_spoofed_mlflow_session_hint(
    tmp_path,
    monkeypatch,
) -> None:
    session_id = uuid.uuid4()
    session = SimpleNamespace(
        id=session_id,
        title="owned-session",
        metadata_json={"external_session_id": "owned-session"},
        workspace_id="workspace-a",
        owner_user="user-a",
    )
    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=session),
        get_chat_session_by_external_id=AsyncMock(return_value=None),
        list_external_traces_for_session=AsyncMock(return_value=([], 0)),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        await SessionService(persistence).export_session_traces(
            persisted_identity=identity,
            session_id=str(session_id),
            body=SessionTraceExportRequest(
                format="jsonl",
                mlflow_session_id="default:anonymous:someone-elses-session",
            ),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_export_session_traces_resolves_external_session_id(
    tmp_path,
    monkeypatch,
) -> None:
    runtime_session_id = str(uuid.uuid4())
    durable_session_id = uuid.uuid4()
    session = SimpleNamespace(
        id=durable_session_id,
        title="runtime chat",
        metadata_json={"external_session_id": runtime_session_id},
        workspace_id="workspace-a",
        owner_user="user-a",
    )
    trace_row = SimpleNamespace(
        trace_id="tr-ext",
        client_request_id="chat-ext",
        turn_id=None,
        provider=ExternalTraceProvider.MLFLOW,
        metadata_json={},
    )
    persistence = SimpleNamespace(
        get_chat_session=AsyncMock(return_value=None),
        get_chat_session_by_external_id=AsyncMock(return_value=session),
        list_external_traces_for_session=AsyncMock(return_value=([trace_row], 1)),
    )
    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    from fleet_rlm.integrations.observability import mlflow_traces

    monkeypatch.setenv("FLEET_RLM_OPTIMIZATION_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(mlflow_traces, "resolve_trace", lambda **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        mlflow_traces,
        "trace_to_full_payload",
        lambda trace, **kwargs: {
            "info": {"trace_id": "tr-ext", "client_request_id": "chat-ext"},
            "session_id": kwargs["session_id"],
            "turn_id": kwargs["turn_id"],
            "metadata": kwargs["external_trace_metadata"],
            "spans": [],
            "assessments": [],
        },
    )

    response = await SessionService(persistence).export_session_traces(
        persisted_identity=identity,
        session_id=runtime_session_id,
        body=SessionTraceExportRequest(format="jsonl"),
    )

    assert response.trace_count == 1
    persistence.get_chat_session_by_external_id.assert_awaited_once()
