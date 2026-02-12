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
        "fleet_rlm.react_tools.chunk_text",
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
        agent.iter_chat_turn_stream(
            "cancel me", trace=False, cancel_check=_cancel_check
        )
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


def test_load_document_directory_returns_file_listing(monkeypatch, tmp_path):
    """When load_document is given a directory, return a file listing instead of crashing."""
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    # Create a test directory structure
    (tmp_path / "file1.txt").write_text("content1")
    (tmp_path / "file2.txt").write_text("content2")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "file3.txt").write_text("content3")

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.load_document(str(tmp_path))

    assert result["status"] == "directory"
    assert result["path"] == str(tmp_path)
    # Files are now full paths relative to cwd (or absolute if tmp_path is absolute)
    files_str = " ".join(result["files"])
    assert "file1.txt" in files_str
    assert "file2.txt" in files_str
    assert "file3.txt" in files_str
    assert result["total_count"] == 3
    assert "hint" in result


def test_list_files_returns_glob_matches(monkeypatch, tmp_path):
    """list_files should return files matching a glob pattern."""
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    # Create test files
    (tmp_path / "test1.py").write_text("python1")
    (tmp_path / "test2.py").write_text("python2")
    (tmp_path / "readme.md").write_text("markdown")

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.list_files(str(tmp_path), pattern="*.py")

    assert result["status"] == "ok"
    assert result["count"] == 2
    assert "test1.py" in result["files"]
    assert "test2.py" in result["files"]
    assert "readme.md" not in result["files"]


def test_read_file_slice_returns_line_range(monkeypatch, tmp_path):
    """read_file_slice should return a specific range of lines with line numbers."""
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    # Create a test file with 10 lines
    test_file = tmp_path / "numbers.txt"
    test_file.write_text("\n".join(f"Line {i}" for i in range(1, 11)))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.read_file_slice(str(test_file), start_line=3, num_lines=3)

    assert result["status"] == "ok"
    assert result["total_lines"] == 10
    assert result["returned_count"] == 3
    assert len(result["lines"]) == 3
    assert result["lines"][0]["line"] == 3
    assert result["lines"][0]["text"] == "Line 3"
    assert result["lines"][2]["line"] == 5


def test_find_files_with_ripgrep(monkeypatch, tmp_path):
    """find_files should use ripgrep to search file contents."""
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    # Create test files with searchable content
    (tmp_path / "file1.txt").write_text("hello world\ngoodbye world")
    (tmp_path / "file2.txt").write_text("hello there\nno match here")

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.find_files("world", str(tmp_path))

    # If ripgrepy is not installed, should return error status
    if result["status"] == "error":
        assert "ripgrepy" in result["error"]
    else:
        assert result["status"] == "ok"
        assert result["pattern"] == "world"
        assert result["count"] > 0
        assert any("file1.txt" in hit["path"] for hit in result["hits"])


def test_new_tools_in_tool_registry(monkeypatch):
    """Verify that new filesystem tools are registered."""
    records = []
    monkeypatch.setattr("fleet_rlm.react_agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    tool_names = [getattr(tool, "__name__", str(tool)) for tool in agent.react_tools]

    assert "list_files" in tool_names
    assert "read_file_slice" in tool_names
    assert "find_files" in tool_names


def test_load_document_directory_recovery_workflow(monkeypatch, tmp_path):
    """Integration test: agent can recover from directory path by using listing to find files."""
    # Create test directory structure
    test_dir = tmp_path / "test_knowledge"
    test_dir.mkdir()
    (test_dir / "doc1.md").write_text("# Document 1\n\nContent for doc1.")
    (test_dir / "doc2.txt").write_text("Content for doc2.")

    monkeypatch.chdir(tmp_path)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())

    # Step 1: Agent tries to load directory (mimics original problem)
    result1 = agent.load_document("test_knowledge")

    # Verify it returns directory listing, not an error
    assert result1["status"] == "directory"
    assert "doc1.md" in result1["files"][0] or "doc2.txt" in result1["files"][0]
    assert result1["total_count"] == 2
    assert "Use load_document with a specific file path" in result1["hint"]

    # Step 2: Agent uses listing to load specific file
    first_file = result1["files"][0]
    result2 = agent.load_document(first_file)

    # Verify file loaded successfully
    assert result2["status"] == "ok"
    assert result2["alias"] == "active"
    assert result2["chars"] > 0
    assert result2["lines"] > 0
