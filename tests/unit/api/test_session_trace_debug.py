from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from fleet_rlm.api.runtime_services.session_trace_debug import (
    build_session_trace_debug_response,
    get_owned_session_trace_debug,
)


class FakeTrace:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return self._payload


def test_build_session_trace_debug_response_maps_tool_and_observability_spans():
    trace = FakeTrace(
        {
            "info": {
                "trace_id": "tr-test",
                "client_request_id": "chat-test",
                "state": "OK",
                "request_preview": "hello",
                "response_preview": "world",
            },
            "data": {
                "spans": [
                    {
                        "span_id": "s-1",
                        "name": "repl_execute",
                        "span_type": "TOOL",
                        "status": {"code": "STATUS_CODE_OK"},
                        "inputs": {"code": "print('hi')"},
                        "outputs": {"stdout": "hi"},
                    },
                    {
                        "span_id": "s-2",
                        "name": "read_file",
                        "span_type": "TOOL",
                        "status": {"code": "STATUS_CODE_OK"},
                        "inputs": {"path": "README.md"},
                        "outputs": {"content": "hello"},
                    },
                    {
                        "span_id": "s-3",
                        "name": "rlm_available_tools",
                        "span_type": "LLM",
                        "status": {"code": "STATUS_CODE_OK"},
                    },
                ]
            },
        }
    )

    response = build_session_trace_debug_response(trace=trace, resolved_from="trace_id")

    assert response.trace_id == "tr-test"
    assert response.renderable_span_count == 2
    assert response.non_rendered_span_count == 1
    assert [span.mapped_render_kind for span in response.spans] == [
        "sandbox",
        "tool",
        "non_rendered",
    ]
    assert response.spans[0].mapped_component_type == "tool-Bash"
    assert response.spans[1].mapped_component_type == "tool-Read"


@pytest.mark.asyncio
async def test_get_owned_session_trace_debug_resolves_explicit_trace_id(monkeypatch: pytest.MonkeyPatch):
    from fleet_rlm.integrations.observability import mlflow_traces

    persisted_identity = SimpleNamespace(
        tenant_id=uuid4(),
        user_id=uuid4(),
        workspace_id=uuid4(),
    )
    session = SimpleNamespace(
        id=uuid4(),
        workspace_id=str(persisted_identity.workspace_id),
        owner_user=str(persisted_identity.user_id),
        metadata_json={"external_session_id": "external-session"},
        external_session_id="external-session",
    )

    trace = FakeTrace(
        {
            "info": {
                "trace_id": "tr-explicit",
                "client_request_id": "chat-explicit",
                "trace_metadata": {
                    "mlflow.trace.user": str(persisted_identity.user_id),
                    "fleet_rlm.workspace_id": str(persisted_identity.workspace_id),
                },
            },
            "data": {"spans": []},
        }
    )

    class FakePersistence:
        async def get_chat_session(self, **_: object):
            return session

    monkeypatch.setattr(mlflow_traces, "resolve_trace", lambda **_: trace)

    response = await get_owned_session_trace_debug(
        persistence=FakePersistence(),
        persisted_identity=persisted_identity,
        session_id=str(session.id),
        trace_id="tr-explicit",
    )

    assert response.trace_id == "tr-explicit"
    assert response.client_request_id == "chat-explicit"
    assert response.resolved_from == "trace_id"
