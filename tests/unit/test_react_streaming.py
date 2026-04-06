"""Unit tests for ReAct chat agent streaming behaviour.

Tests cover chat_turn_stream, iter_chat_turn_stream, cancellation handling,
and fallback-to-non-streaming error resilience.
"""

from __future__ import annotations

import dspy
import pytest
from dspy.streaming.messages import StatusMessage, StreamResponse

from fleet_rlm.runtime.agent import RLMReActChatAgent
from fleet_rlm.runtime.execution.streaming import (
    _build_final_payload,
    _normalize_trajectory,
    build_cancelled_stream_event,
    build_final_stream_event,
    prepare_streaming_turn,
)
from tests.unit.fixtures_react import FakeInterpreter

pytestmark = pytest.mark.usefixtures("react_records")


# ---------------------------------------------------------------------------
# chat_turn_stream tests
# ---------------------------------------------------------------------------


def test_chat_turn_stream_collects_chunks_and_status(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            assert "user_request" in stream_kwargs
            assert "history" in stream_kwargs
            assert "core_memory" in stream_kwargs
            assert stream_kwargs["core_memory"]
            yield StatusMessage(message="reasoning")
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="hello ",
                is_last_chunk=False,
            )
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="world",
                is_last_chunk=True,
            )
            yield dspy.Prediction(
                assistant_response="hello world",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    result = agent.chat_turn_stream(message="say hi", trace=False)

    assert result["assistant_response"] == "hello world"
    assert result["status_messages"] == ["reasoning"]
    assert result["stream_chunks"] == ["hello ", "world"]
    assert result["trajectory"] == {"tool_name_0": "finish"}
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_passes_effective_max_iters(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            captured.update(stream_kwargs)
            yield dspy.Prediction(
                assistant_response="done",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
    )
    list(agent.iter_chat_turn_stream("full codebase deep analysis", trace=False))
    assert captured["max_iters"] == 35


def test_chat_turn_stream_falls_back_to_non_streaming_on_error(monkeypatch):
    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken stream")

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _bad_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    result = agent.chat_turn_stream(message="test fallback", trace=False)

    assert result["assistant_response"] == "echo:test fallback"
    assert any("stream error" in m for m in result.get("status_messages", []))


# ---------------------------------------------------------------------------
# iter_chat_turn_stream tests
# ---------------------------------------------------------------------------


def test_iter_chat_turn_stream_emits_ordered_events(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield StatusMessage(message="thinking")
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="hello ",
                is_last_chunk=False,
            )
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="world",
                is_last_chunk=True,
            )
            yield dspy.Prediction(
                assistant_response="hello world",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("say hi", trace=False))

    kinds = [e.kind for e in events]
    assert kinds[0] == "status"
    assert "assistant_token" in kinds
    assert kinds[-1] == "final"

    final_event = events[-1]
    assert final_event.text == "hello world"
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_enriches_tool_payloads(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield StatusMessage(message="Calling tool: memory_write(path='/tmp/x')")
            yield StatusMessage(message="Tool finished.")
            yield dspy.Prediction(
                assistant_response="done",
                trajectory={"tool_name_0": "memory_write"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("store memory", trace=False))

    tool_call_event = next(
        event for event in events if event.kind in ("tool_call", "memory_update")
    )
    assert tool_call_event.payload["tool_name"] == "memory_write"
    assert tool_call_event.payload["raw_status"].startswith("Calling tool:")
    assert "path='/tmp/x'" in tool_call_event.payload["tool_args"]

    tool_result_event = next(event for event in events if event.kind == "tool_result")
    assert tool_result_event.payload["tool_name"] == "memory_write"
    assert tool_result_event.payload["raw_status"] == "Tool finished."


@pytest.mark.asyncio
async def test_aiter_chat_turn_stream_passes_core_memory(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        assert kwargs.get("async_streaming") is True

        async def _stream(**stream_kwargs):
            assert "user_request" in stream_kwargs
            assert "history" in stream_kwargs
            assert "core_memory" in stream_kwargs
            assert stream_kwargs["core_memory"]
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="hello",
                is_last_chunk=True,
            )
            yield dspy.Prediction(
                assistant_response="hello",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = [
        event
        async for event in agent.aiter_chat_turn_stream(
            "say hi",
            trace=False,
        )
    ]

    assert events[-1].kind == "final"
    assert events[-1].text == "hello"
    assert len(agent.history.messages) == 1


@pytest.mark.asyncio
async def test_aiter_chat_turn_stream_passes_effective_max_iters(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_streamify(*args, **kwargs):
        assert kwargs.get("async_streaming") is True

        async def _stream(**stream_kwargs):
            captured.update(stream_kwargs)
            yield dspy.Prediction(
                assistant_response="hello",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        react_max_iters=15,
        deep_react_max_iters=35,
    )
    events = [
        event
        async for event in agent.aiter_chat_turn_stream(
            "full codebase deep analysis",
            trace=False,
        )
    ]

    assert events[-1].kind == "final"
    assert captured["max_iters"] == 35


def test_iter_chat_turn_stream_cancelled_emits_partial_and_marks_history(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield StatusMessage(message="reasoning")
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="partial ",
                is_last_chunk=False,
            )
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="content",
                is_last_chunk=False,
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    call_count = 0

    def _cancel_check():
        nonlocal call_count
        call_count += 1
        return call_count > 2

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = list(
        agent.iter_chat_turn_stream(
            "please",
            trace=False,
            cancel_check=_cancel_check,
        )
    )

    kinds = [e.kind for e in events]
    assert "cancelled" in kinds
    assert len(agent.history.messages) == 1
    assert agent.history.messages[0]["assistant_response"].endswith("[cancelled]")


def test_build_cancelled_stream_event_logs_local_store_failure(
    monkeypatch, caplog: pytest.LogCaptureFixture
):
    from fleet_rlm.integrations.database import local_store

    def _boom(*args, **kwargs):
        raise RuntimeError("db offline")

    monkeypatch.setattr(local_store, "add_turn", _boom)

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    prepared = prepare_streaming_turn(agent, message="please", trace=False)
    agent._db_session_id = 123

    with caplog.at_level("ERROR"):
        event = build_cancelled_stream_event(
            agent=agent,
            message="please",
            assistant_chunks=["partial"],
            ctx=prepared.ctx,
        )

    assert event.kind == "cancelled"
    assert "Failed to persist cancelled streaming turn to local_store" in caplog.text


def test_build_final_stream_event_logs_local_store_failure(
    monkeypatch, caplog: pytest.LogCaptureFixture
):
    from fleet_rlm.integrations.database import local_store

    def _boom(*args, **kwargs):
        raise RuntimeError("db offline")

    monkeypatch.setattr(local_store, "add_turn", _boom)

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    prepared = prepare_streaming_turn(agent, message="hello", trace=False)
    agent._db_session_id = 123

    with caplog.at_level("ERROR"):
        event = build_final_stream_event(
            agent=agent,
            message="hello",
            final_prediction=dspy.Prediction(
                assistant_response="world",
                trajectory={"tool_name_0": "finish"},
            ),
            assistant_chunks=["world"],
            ctx=prepared.ctx,
        )

    assert event.kind == "final"
    assert event.text == "world"
    assert "Failed to persist final streaming turn to local_store" in caplog.text


def test_iter_chat_turn_stream_fallback_on_stream_exception(monkeypatch):
    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken stream")

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _bad_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("fallback now", trace=False))
    assert events[0].kind == "status"
    assert events[-1].kind == "final"
    assert events[-1].text == "echo:fallback now"
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_includes_guardrail_warnings(monkeypatch):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="ok",
                is_last_chunk=True,
            )
            yield dspy.Prediction(
                assistant_response="ok",
                trajectory={"tool_name_0": "finish"},
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(
        interpreter=FakeInterpreter(),
        guardrail_mode="warn",
        min_substantive_chars=20,
    )
    events = list(agent.iter_chat_turn_stream("say hi", trace=False))

    final_event = events[-1]
    assert final_event.kind == "final"
    warnings = list(final_event.payload.get("guardrail_warnings", []) or [])
    assert warnings
    assert any("brief" in warning for warning in warnings)


def test_iter_chat_turn_stream_enriches_final_payload_with_sources_and_citations(
    monkeypatch,
):
    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield dspy.Prediction(
                assistant_response="done",
                trajectory={
                    "thought_0": "collect evidence",
                    "output_0": {
                        "citations": [
                            {
                                "title": "DSPy docs",
                                "url": "https://dspy.ai",
                                "quote": "Reasoning framework",
                            }
                        ]
                    },
                },
            )

        return _stream

    monkeypatch.setattr(
        "fleet_rlm.runtime.agent.chat_agent.dspy.streamify", _fake_streamify
    )

    agent = RLMReActChatAgent(interpreter=FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("cite", trace=False))
    final_event = events[-1]

    assert final_event.kind == "final"
    assert final_event.payload["schema_version"] == 2
    assert len(final_event.payload["citations"]) == 1
    assert len(final_event.payload["sources"]) == 1
    assert len(final_event.payload["citation_anchors"]) == 1


def test_build_final_payload_filters_unsafe_external_urls():
    payload = _build_final_payload(
        final_prediction=dspy.Prediction(
            citations=[
                {
                    "source_id": "src-unsafe",
                    "anchor_id": "anc-unsafe",
                    "title": "Unsafe source",
                    "url": "javascript:alert(1)",
                },
                {
                    "source_id": "src-safe",
                    "anchor_id": "anc-safe",
                    "title": "Safe source",
                    "url": "https://example.com/safe",
                },
                {
                    "source_id": "src-file",
                    "anchor_id": "anc-file",
                    "title": "File source",
                    "url": "file:///tmp/local.txt",
                },
            ],
            attachments=[
                {
                    "attachment_id": "att-unsafe",
                    "name": "Unsafe attachment",
                    "url": "javascript:alert(1)",
                    "preview_url": "data:text/plain,hello",
                },
                {
                    "attachment_id": "att-safe",
                    "name": "Safe attachment",
                    "url": "https://example.com/file.txt",
                },
                {
                    "attachment_id": "att-file",
                    "name": "File attachment",
                    "url": "file:///tmp/local.txt",
                },
            ],
        ),
        trajectory={},
        history_turns=0,
        guardrail_warnings=[],
        turn_metrics={},
        fallback=False,
    )

    citations = payload["citations"]
    assert len(citations) == 1
    assert citations[0]["source_id"] == "src-safe"
    assert citations[0]["url"] == "https://example.com/safe"

    sources = payload["sources"]
    assert len(sources) == 1
    assert sources[0]["source_id"] == "src-safe"
    assert sources[0]["canonical_url"] == "https://example.com/safe"

    attachments = payload["attachments"]
    assert len(attachments) == 3
    unsafe = next(att for att in attachments if att["attachment_id"] == "att-unsafe")
    assert unsafe["url"] is None
    assert unsafe["preview_url"] is None
    safe = next(att for att in attachments if att["attachment_id"] == "att-safe")
    assert safe["url"] == "https://example.com/file.txt"
    file_attachment = next(
        att for att in attachments if att["attachment_id"] == "att-file"
    )
    assert file_attachment["url"] is None


def test_build_final_payload_omits_mlflow_metadata():
    payload = _build_final_payload(
        final_prediction=dspy.Prediction(assistant_response="Done"),
        trajectory={},
        history_turns=1,
        guardrail_warnings=[],
        turn_metrics={},
        fallback=False,
    )

    assert "mlflow_trace_id" not in payload
    assert "mlflow_client_request_id" not in payload


# ---------------------------------------------------------------------------
# _normalize_trajectory tests
# ---------------------------------------------------------------------------


def test_normalize_trajectory_handles_dict():
    """Test _normalize_trajectory with plain dict trajectory."""
    trajectory = {"tool_name_0": "finish", "tool_name_1": "search"}
    result = _normalize_trajectory(trajectory)
    # Should extract steps with index
    assert len(result) == 2
    assert result[0]["index"] == 0
    assert result[0]["tool_name"] == "finish"
    assert result[1]["index"] == 1
    assert result[1]["tool_name"] == "search"


def test_normalize_trajectory_handles_multiple_fields():
    """Test _normalize_trajectory with multiple fields per step."""
    trajectory = {
        "thought_0": "First thought",
        "tool_name_0": "tool1",
        "input_0": "arg1",
        "output_0": "result1",
        "thought_1": "Second thought",
        "tool_name_1": "tool2",
    }
    result = _normalize_trajectory(trajectory)
    assert len(result) == 2
    assert result[0]["index"] == 0
    assert result[0]["thought"] == "First thought"
    assert result[0]["tool_name"] == "tool1"
    assert result[0]["input"] == "arg1"
    assert result[0]["output"] == "result1"
    assert result[1]["index"] == 1
    assert result[1]["thought"] == "Second thought"
    assert result[1]["tool_name"] == "tool2"


def test_normalize_trajectory_handles_tool_args_and_observation_aliases():
    """Test _normalize_trajectory alias handling for tool args/observation keys."""
    trajectory = {
        "tool_name_0": "glob",
        "tool_args_0": {"path": ".", "pattern": "**/*"},
        "observation_0": {"count": 3},
    }
    result = _normalize_trajectory(trajectory)
    assert len(result) == 1
    assert result[0]["index"] == 0
    assert result[0]["tool_name"] == "glob"
    assert result[0]["tool_args"] == {"path": ".", "pattern": "**/*"}
    assert result[0]["input"] == {"path": ".", "pattern": "**/*"}
    assert result[0]["observation"] == {"count": 3}
    assert result[0]["output"] == {"count": 3}


def test_normalize_trajectory_handles_input_and_output_alias_backfill():
    """Test _normalize_trajectory backfills alias keys from canonical fields."""
    trajectory = {
        "tool_name_0": "read_file",
        "input_0": {"path": "README.md"},
        "output_0": {"content": "ok"},
    }
    result = _normalize_trajectory(trajectory)
    assert len(result) == 1
    assert result[0]["index"] == 0
    assert result[0]["tool_name"] == "read_file"
    assert result[0]["input"] == {"path": "README.md"}
    assert result[0]["tool_args"] == {"path": "README.md"}
    assert result[0]["output"] == {"content": "ok"}
    assert result[0]["observation"] == {"content": "ok"}


def test_normalize_trajectory_handles_structured_dict():
    """Test _normalize_trajectory with already-structured dict (future DSPy)."""
    steps = [{"index": 0, "tool_name": "finish"}]
    trajectory = {"steps": steps}
    result = _normalize_trajectory(trajectory)
    # Should pass through
    assert result == steps


def test_normalize_trajectory_handles_legacy_trajectory_list():
    """Test _normalize_trajectory with legacy 'trajectory' list shape."""
    steps = [{"index": 0, "tool_name": "finish"}]
    trajectory = {"trajectory": steps}
    result = _normalize_trajectory(trajectory)
    assert result == steps


def test_normalize_trajectory_handles_none():
    """Test _normalize_trajectory with None input."""
    result = _normalize_trajectory(None)
    assert result == []


def test_normalize_trajectory_handles_empty_dict():
    """Test _normalize_trajectory with empty dict input."""
    result = _normalize_trajectory({})
    assert result == []
