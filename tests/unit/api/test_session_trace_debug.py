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
                        "start_time_unix_nano": "1000000000",
                        "end_time_unix_nano": "2500000000",
                        "inputs": {"code": "print('hi')"},
                        "outputs": {"stdout": "hi"},
                    },
                    {
                        "span_id": "s-2",
                        "name": "read_file",
                        "span_type": "TOOL",
                        "status": {"code": "STATUS_CODE_OK"},
                        "start_time_unix_nano": "3000000000",
                        "end_time_unix_nano": "4000000000",
                        "inputs": {"path": "README.md"},
                        "outputs": {"content": "hello"},
                    },
                    {
                        "span_id": "s-3",
                        "name": "rlm_available_tools",
                        "span_type": "LLM",
                        "status": {"code": "STATUS_CODE_OK"},
                    },
                    {
                        "span_id": "s-4",
                        "name": "fleet_rlm.chat_turn",
                        "span_type": "CHAIN",
                        "parent_span_id": None,
                        "status": {"code": "STATUS_CODE_OK"},
                        "start_time_unix_nano": "0",
                        "end_time_unix_nano": "10000000000",
                    },
                    {
                        "span_id": "s-5",
                        "name": "LM.__call__",
                        "span_type": "LLM",
                        "status": {"code": "STATUS_CODE_OK"},
                        "start_time_unix_nano": "5000000000",
                        "end_time_unix_nano": "9000000000",
                        "attributes": {
                            "mlflow.chat.tokenUsage": {
                                "input_tokens": 100,
                                "output_tokens": 25,
                                "total_tokens": 125,
                            },
                        },
                        "outputs": {"choices": [{"message": {"content": "large output"}}]},
                    },
                    {
                        "span_id": "s-6",
                        "name": "fleet_rlm.rlm_run",
                        "span_type": "CHAIN",
                        "status": {"code": "STATUS_CODE_OK"},
                        "attributes": {
                            "fleet_rlm.selected_skills": "long-context,rlm",
                            "fleet_rlm.rlm_action_max_tokens": "4096",
                            "fleet_rlm.rlm_max_output_chars": "5000",
                        },
                        "outputs": {"error": "AdapterParseError failed to parse the LM response; JSONAdapter fallback"},
                    },
                ]
            },
        }
    )

    response = build_session_trace_debug_response(trace=trace, resolved_from="trace_id")

    assert response.trace_id == "tr-test"
    assert response.renderable_span_count == 2
    assert response.non_rendered_span_count == 4
    assert [span.mapped_render_kind for span in response.spans[:3]] == [
        "sandbox",
        "tool",
        "non_rendered",
    ]
    assert response.spans[0].mapped_component_type == "tool-Bash"
    assert response.spans[1].mapped_component_type == "tool-Read"
    assert response.spans[0].duration_ms == 1500
    assert response.spans[4].total_tokens == 125
    assert response.performance_summary.total_duration_ms == 10000
    assert response.performance_summary.llm_duration_ms == 4000
    assert response.performance_summary.repl_duration_ms == 1500
    assert response.performance_summary.tool_duration_ms == 1000
    assert response.performance_summary.input_tokens == 100
    assert response.performance_summary.output_tokens == 25
    assert response.performance_summary.total_tokens == 125
    assert response.performance_summary.adapter_fallback_count == 1
    assert response.performance_summary.parse_error_count == 1
    assert response.performance_summary.selected_skills == ["long-context", "rlm"]
    assert response.performance_summary.rlm_action_max_tokens == 4096
    assert response.performance_summary.rlm_max_output_chars == 5000
    assert response.performance_summary.slowest_llm_span
    assert response.performance_summary.slowest_llm_span.name == "LM.__call__"


def test_trace_debug_adds_phase6_render_kinds_and_redacts_provider_values():
    trace = FakeTrace(
        {
            "info": {
                "trace_id": "tr-phase6",
                "request_preview": "Bearer top-secret /home/daytona/memory/private.txt",
            },
            "data": {
                "spans": [
                    {"span_id": "artifact", "name": "artifact", "span_type": "ARTIFACT"},
                    {"span_id": "task", "name": "task", "span_type": "TASK"},
                    {"span_id": "performance", "name": "performance", "span_type": "PERFORMANCE"},
                    {
                        "span_id": "mlflow",
                        "name": "direct_rlm.turn",
                        "attributes": {"event_kind": "mlflow_span"},
                        "inputs": {"api_key": "sk-live-secret", "path": "/private/trace.json"},
                    },
                ]
            },
        }
    )

    response = build_session_trace_debug_response(trace=trace, resolved_from="trace_id")

    assert [span.mapped_render_kind for span in response.spans] == [
        "artifact",
        "task",
        "performance",
        "mlflow_span",
    ]
    rendered = response.model_dump_json()
    assert "top-secret" not in rendered
    assert "sk-live-secret" not in rendered
    assert "/home/daytona/memory" not in rendered
    assert "/private/trace.json" not in rendered


def test_trace_debug_performance_summary_uses_sanitized_span_values():
    trace = FakeTrace(
        {
            "info": {"trace_id": "tr-safe-performance"},
            "data": {
                "spans": [
                    {
                        "span_id": "span-sensitive",
                        "name": "LM /etc/fleet-rlm/provider.conf",
                        "span_type": "LLM",
                        "start_time_unix_nano": "0",
                        "end_time_unix_nano": "5000000000",
                        "attributes": {
                            "fleet_rlm.selected_skills": "safe-skill,/etc/fleet-rlm/skill-secret",
                        },
                        "outputs": "Bearer top-secret-token",
                    }
                ]
            },
        }
    )

    response = build_session_trace_debug_response(trace=trace, resolved_from="trace_id")

    rendered = response.model_dump_json()
    assert "/etc/fleet-rlm/provider.conf" not in rendered
    assert "/etc/fleet-rlm/skill-secret" not in rendered
    assert "top-secret-token" not in rendered
    assert response.performance_summary.slowest_llm_span is not None
    assert response.performance_summary.slowest_llm_span.name != "LM /etc/fleet-rlm/provider.conf"


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
