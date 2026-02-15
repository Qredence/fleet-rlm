"""Unit tests for ReAct chat agent streaming behaviour.

Tests cover chat_turn_stream, iter_chat_turn_stream, cancellation handling,
and fallback-to-non-streaming error resilience.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput
from dspy.streaming.messages import StatusMessage, StreamResponse

from fleet_rlm.react import RLMReActChatAgent


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeInterpreter:
    def __init__(self):
        self.start_calls = 0
        self.shutdown_calls = 0
        self.execute_calls: list[tuple[str, dict]] = []
        self.default_execution_profile = "RLM_DELEGATE"

    def start(self):
        self.start_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1

    @contextmanager
    def execution_profile(self, profile):
        previous = self.default_execution_profile
        self.default_execution_profile = profile
        try:
            yield self
        finally:
            self.default_execution_profile = previous

    def execute(self, code, variables=None, **kwargs):
        self.execute_calls.append((code, variables or {}))
        return FinalOutput(
            {
                "status": "ok",
                "chunk_count": len((variables or {}).get("prompts", [])),
                "findings_count": len((variables or {}).get("prompts", [])),
                "buffer_name": (variables or {}).get("buffer_name", "findings"),
            }
        )


def _make_fake_react(records):
    class _FakeReAct:
        def __init__(self, *, signature, tools, max_iters):
            records.append(
                {
                    "signature": signature,
                    "tools": tools,
                    "max_iters": max_iters,
                }
            )

        def __call__(self, **kwargs):
            request = kwargs.get("user_request", "")
            return SimpleNamespace(
                assistant_response=f"echo:{request}",
                trajectory={"tool_name_0": "finish"},
            )

    return _FakeReAct


# ---------------------------------------------------------------------------
# chat_turn_stream tests
# ---------------------------------------------------------------------------


def test_chat_turn_stream_collects_chunks_and_status(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn_stream(message="say hi", trace=False)

    assert result["assistant_response"] == "hello world"
    assert result["status_messages"] == ["reasoning"]
    assert result["stream_chunks"] == ["hello ", "world"]
    assert result["trajectory"] == {"tool_name_0": "finish"}
    assert len(agent.history.messages) == 1


def test_chat_turn_stream_falls_back_to_non_streaming_on_error(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken stream")

        return _stream

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _bad_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn_stream(message="test fallback", trace=False)

    assert result["assistant_response"] == "echo:test fallback"
    assert any("stream error" in m for m in result.get("status_messages", []))


# ---------------------------------------------------------------------------
# iter_chat_turn_stream tests
# ---------------------------------------------------------------------------


def test_iter_chat_turn_stream_emits_ordered_events(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("say hi", trace=False))

    kinds = [e.kind for e in events]
    assert kinds[0] == "status"
    assert "assistant_token" in kinds
    assert kinds[-1] == "final"

    final_event = events[-1]
    assert final_event.text == "hello world"
    assert len(agent.history.messages) == 1


@pytest.mark.asyncio
async def test_aiter_chat_turn_stream_passes_core_memory(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
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


def test_iter_chat_turn_stream_cancelled_emits_partial_and_marks_history(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

    call_count = 0

    def _cancel_check():
        nonlocal call_count
        call_count += 1
        return call_count > 2

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
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


def test_iter_chat_turn_stream_fallback_on_stream_exception(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken stream")

        return _stream

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _bad_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("fallback now", trace=False))
    assert events[0].kind == "status"
    assert events[-1].kind == "final"
    assert events[-1].text == "echo:fallback now"
    assert len(agent.history.messages) == 1
