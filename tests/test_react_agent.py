"""Unit tests for the ReAct chat agent.

These tests mock DSPy ReAct + Modal interpreter behavior to avoid
cloud credentials while validating host-side orchestration logic.
"""

from __future__ import annotations

from types import SimpleNamespace

import dspy
from dspy.primitives.code_interpreter import FinalOutput
from dspy.streaming.messages import StatusMessage, StreamResponse

from fleet_rlm.react_agent import RLMReActChatAgent, RLMReActChatSignature


class _FakeInterpreter:
    def __init__(self):
        self.start_calls = 0
        self.shutdown_calls = 0
        self.execute_calls: list[tuple[str, dict]] = []

    def start(self):
        self.start_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1

    def execute(self, code, variables=None):
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


def test_react_agent_constructed_with_explicit_signature_and_tools(monkeypatch):
    records = []
    monkeypatch.setattr(
        "fleet_rlm.react_agent.dspy.ReAct",
        _make_fake_react(records),
    )

    fake_interpreter = _FakeInterpreter()
    RLMReActChatAgent(
        interpreter=fake_interpreter,
        react_max_iters=7,
    )

    assert records, "Expected dspy.ReAct(...) to be called during initialization."
    call = records[0]
    assert call["signature"] is RLMReActChatSignature
    assert call["max_iters"] == 7
    tool_names = [getattr(tool, "__name__", str(tool)) for tool in call["tools"]]
    assert "parallel_semantic_map" in tool_names
    assert "analyze_long_document" in tool_names


def test_tool_registry_includes_specialized_tools_and_extra_tools(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def custom_tool(topic: str) -> dict:
        return {"topic": topic}

    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
        extra_tools=[custom_tool],
    )

    tool_names = [getattr(tool, "__name__", str(tool)) for tool in agent.react_tools]
    assert "load_document" in tool_names
    assert "chunk_sandbox" in tool_names
    assert "extract_from_logs" in tool_names
    assert "custom_tool" in tool_names


def test_chat_turn_appends_history_and_preserves_session(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    first = agent.chat_turn("hello")
    second = agent.chat_turn("again")

    assert first["assistant_response"] == "echo:hello"
    assert second["assistant_response"] == "echo:again"
    assert len(agent.history.messages) == 2
    assert agent.history.messages[0]["user_request"] == "hello"
    assert agent.history.messages[0]["assistant_response"] == "echo:hello"
    assert fake_interpreter.start_calls == 1


def test_parallel_semantic_map_uses_llm_query_batched(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    agent.documents["doc"] = "alpha\nbeta\ngamma"
    agent.active_alias = "doc"
    monkeypatch.setattr(
        agent,
        "_chunk_text",
        lambda *args, **kwargs: ["chunk one", "chunk two"],
    )

    result = agent.parallel_semantic_map("find core topics", chunk_strategy="headers")

    assert result["status"] == "ok"
    assert fake_interpreter.execute_calls
    code, variables = fake_interpreter.execute_calls[-1]
    assert "llm_query_batched" in code
    assert len(variables["prompts"]) == 2


def test_context_manager_starts_and_stops_interpreter(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    with agent:
        assert fake_interpreter.start_calls == 1

    assert fake_interpreter.shutdown_calls == 1


def test_chat_turn_stream_collects_chunks_and_status(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            assert "user_request" in stream_kwargs
            assert "history" in stream_kwargs
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

    monkeypatch.setattr("fleet_rlm.react_agent.dspy.streamify", _fake_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn_stream(message="say hi", trace=False)

    assert result["assistant_response"] == "hello world"
    assert result["status_messages"] == ["reasoning"]
    assert result["stream_chunks"] == ["hello ", "world"]
    assert result["trajectory"] == {"tool_name_0": "finish"}
    assert len(agent.history.messages) == 1


def test_chat_turn_stream_falls_back_to_non_streaming_on_error(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken streamify")

        return _stream

    monkeypatch.setattr("fleet_rlm.react_agent.dspy.streamify", _bad_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn_stream(message="fallback please", trace=True)

    assert result["assistant_response"] == "echo:fallback please"
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_emits_ordered_events(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            assert "user_request" in stream_kwargs
            yield StatusMessage(message="Calling tool: grep")
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="alpha ",
                is_last_chunk=False,
            )
            yield StreamResponse(
                predict_name="react",
                signature_field_name="next_thought",
                chunk="thinking",
                is_last_chunk=True,
            )
            yield StatusMessage(message="Tool finished.")
            yield dspy.Prediction(
                assistant_response="alpha done",
                trajectory={"tool_name_0": "grep"},
            )

        return _stream

    monkeypatch.setattr("fleet_rlm.react_agent.dspy.streamify", _fake_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("hello", trace=True))
    kinds = [event.kind for event in events]

    assert kinds == [
        "status",
        "tool_call",
        "assistant_token",
        "reasoning_step",
        "status",
        "tool_result",
        "final",
    ]
    assert events[-1].text == "alpha done"
    assert events[-1].payload["trajectory"] == {"tool_name_0": "grep"}
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_cancelled_emits_partial_and_marks_history(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def _fake_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="partial ",
                is_last_chunk=False,
            )
            yield StreamResponse(
                predict_name="react",
                signature_field_name="assistant_response",
                chunk="tail",
                is_last_chunk=False,
            )

        return _stream

    monkeypatch.setattr("fleet_rlm.react_agent.dspy.streamify", _fake_streamify)

    checks = {"calls": 0}

    def _cancel_check() -> bool:
        checks["calls"] += 1
        return checks["calls"] >= 2

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    events = list(
        agent.iter_chat_turn_stream("cancel me", trace=False, cancel_check=_cancel_check)
    )

    assert any(event.kind == "cancelled" for event in events)
    cancelled = [event for event in events if event.kind == "cancelled"][0]
    assert "partial" in cancelled.text
    assert cancelled.text.endswith("[cancelled]")
    assert len(agent.history.messages) == 1
    assert agent.history.messages[0]["assistant_response"].endswith("[cancelled]")


def test_iter_chat_turn_stream_fallback_on_stream_exception(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    def _bad_streamify(*args, **kwargs):
        def _stream(**stream_kwargs):
            raise RuntimeError("broken stream")

        return _stream

    monkeypatch.setattr("fleet_rlm.react_agent.dspy.streamify", _bad_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    events = list(agent.iter_chat_turn_stream("fallback now", trace=False))
    assert events[0].kind == "status"
    assert events[-1].kind == "final"
    assert events[-1].text == "echo:fallback now"
    assert len(agent.history.messages) == 1
