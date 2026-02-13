"""Unit tests for the ReAct chat agent.

These tests mock DSPy ReAct + Modal interpreter behavior to avoid
cloud credentials while validating host-side orchestration logic.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace

import dspy
import pytest
from dspy.primitives.code_interpreter import FinalOutput
from dspy.streaming.messages import StatusMessage, StreamResponse

from fleet_rlm.react import RLMReActChatAgent, RLMReActChatSignature
from fleet_rlm.react import tools as react_tools


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


def test_react_agent_constructed_with_explicit_signature_and_tools(monkeypatch):
    records = []
    monkeypatch.setattr(
        "fleet_rlm.react.agent.dspy.ReAct",
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
    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in call["tools"]
    ]
    assert "parallel_semantic_map" in tool_names
    assert "analyze_long_document" in tool_names


def test_tool_registry_includes_specialized_tools_and_extra_tools(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def custom_tool(topic: str) -> dict:
        return {"topic": topic}

    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
        extra_tools=[custom_tool],
    )

    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in agent.react_tools
    ]
    assert "load_document" in tool_names
    assert "chunk_sandbox" in tool_names
    assert "extract_from_logs" in tool_names
    assert "custom_tool" in tool_names


def test_chat_turn_appends_history_and_preserves_session(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)
    agent.documents["doc"] = "alpha\nbeta\ngamma"
    agent.active_alias = "doc"
    monkeypatch.setattr(
        "fleet_rlm.react.tools.chunk_text",
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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    with agent:
        assert fake_interpreter.start_calls == 1

    assert fake_interpreter.shutdown_calls == 1


def test_chat_turn_stream_collects_chunks_and_status(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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
            raise RuntimeError("broken streamify")

        return _stream

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _bad_streamify)

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn_stream(message="fallback please", trace=True)

    assert result["assistant_response"] == "echo:fallback please"
    assert len(agent.history.messages) == 1


def test_iter_chat_turn_stream_emits_ordered_events(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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

    monkeypatch.setattr("fleet_rlm.react.agent.dspy.streamify", _fake_streamify)

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


def test_load_document_directory_returns_file_listing(monkeypatch, tmp_path):
    """When load_document is given a directory, return a file listing instead of crashing."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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


def test_load_document_pdf_includes_extraction_metadata(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 test bytes")

    monkeypatch.setattr(
        "fleet_rlm.react.tools._read_document_content",
        lambda _path: (
            "Page one\nPage two",
            {
                "source_type": "pdf",
                "extraction_method": "markitdown",
                "page_count": 2,
                "pages_with_text": 2,
            },
        ),
    )

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.load_document(str(pdf_file))

    assert result["status"] == "ok"
    assert result["source_type"] == "pdf"
    assert result["extraction_method"] == "markitdown"
    assert result["page_count"] == 2
    assert result["pages_with_text"] == 2


def test_pdf_extraction_falls_back_to_pypdf_when_markitdown_fails(
    monkeypatch, tmp_path
):
    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fallback test")

    class _BadMarkItDown:
        def convert(self, _path: str) -> object:
            raise RuntimeError("markitdown failed")

    class _Page:
        def __init__(self, text: str):
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _PdfReader:
        def __init__(self, _path: str):
            self.pages = [_Page("Alpha"), _Page("Beta")]

    monkeypatch.setitem(
        sys.modules, "markitdown", SimpleNamespace(MarkItDown=_BadMarkItDown)
    )
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=_PdfReader))

    text, meta = react_tools._read_document_content(pdf_file)

    assert "Alpha" in text
    assert meta["source_type"] == "pdf"
    assert meta["extraction_method"] == "pypdf"
    assert meta["page_count"] == 2
    assert meta["pages_with_text"] == 2


def test_pdf_extraction_returns_ocr_guidance_when_no_text(monkeypatch, tmp_path):
    pdf_file = tmp_path / "scanned.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 scanned bytes")

    class _EmptyMarkItDown:
        def convert(self, _path: str) -> object:
            return SimpleNamespace(text_content="")

    class _Page:
        def extract_text(self) -> str:
            return ""

    class _PdfReader:
        def __init__(self, _path: str):
            self.pages = [_Page(), _Page()]

    monkeypatch.setitem(
        sys.modules, "markitdown", SimpleNamespace(MarkItDown=_EmptyMarkItDown)
    )
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=_PdfReader))

    with pytest.raises(ValueError, match="OCR is required"):
        react_tools._read_document_content(pdf_file)


def test_read_file_slice_pdf_uses_extracted_text(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    pdf_file = tmp_path / "slice.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 slice bytes")
    monkeypatch.setattr(
        "fleet_rlm.react.tools._read_document_content",
        lambda _path: ("Line 1\nLine 2\nLine 3\nLine 4", {"source_type": "pdf"}),
    )

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.read_file_slice(str(pdf_file), start_line=2, num_lines=2)

    assert result["status"] == "ok"
    assert result["returned_count"] == 2
    assert result["lines"][0]["line"] == 2
    assert result["lines"][0]["text"] == "Line 2"


def test_read_file_slice_binary_file_returns_user_friendly_error(monkeypatch, tmp_path):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    binary_file = tmp_path / "payload.bin"
    binary_file.write_bytes(b"\x00\x01\x02\x03\x04\xff\xfe")

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    with pytest.raises(ValueError) as excinfo:
        agent.read_file_slice(str(binary_file))

    message = str(excinfo.value)
    assert "Binary file detected" in message
    assert "UnicodeDecodeError" not in message


def test_analyze_long_document_includes_trajectory_by_default(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    class _FakeRLM:
        def __init__(self, **kwargs):
            pass

        def __call__(self, **kwargs):
            return SimpleNamespace(
                findings=["f1"],
                answer="a1",
                sections_examined=2,
                trajectory=[{"reasoning": "step1", "code": "pass", "output": "ok"}],
                final_reasoning="done",
            )

    monkeypatch.setattr("fleet_rlm.react.tools.dspy.RLM", _FakeRLM)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.documents["doc"] = "hello"
    agent.active_alias = "doc"

    result = agent.analyze_long_document("question")
    assert result["trajectory_steps"] == 1
    assert result["trajectory"][0]["reasoning"] == "step1"
    assert result["final_reasoning"] == "done"


def test_analyze_long_document_can_suppress_trajectory(monkeypatch):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    class _FakeRLM:
        def __init__(self, **kwargs):
            pass

        def __call__(self, **kwargs):
            return SimpleNamespace(
                findings=["f1"],
                answer="a1",
                sections_examined=2,
                trajectory=[{"reasoning": "step1"}],
                final_reasoning="done",
            )

    monkeypatch.setattr("fleet_rlm.react.tools.dspy.RLM", _FakeRLM)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.documents["doc"] = "hello"
    agent.active_alias = "doc"

    result = agent.analyze_long_document("question", include_trajectory=False)
    assert "trajectory_steps" not in result
    assert "trajectory" not in result
    assert "final_reasoning" not in result


def test_react_runners_include_trajectory_defaults_for_summarize_and_extract(
    monkeypatch,
):
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    class _FakeRLM:
        def __init__(self, **kwargs):
            pass

        def __call__(self, **kwargs):
            if "focus" in kwargs:
                return SimpleNamespace(
                    summary="s1",
                    key_points=["k1"],
                    coverage_pct=90,
                    trajectory=[{"reasoning": "sum"}],
                )
            return SimpleNamespace(
                matches=[{"k": "v"}],
                patterns=["p1"],
                time_range="all",
                trajectory=[{"reasoning": "logs"}],
            )

    monkeypatch.setattr("fleet_rlm.react.tools.dspy.RLM", _FakeRLM)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.documents["doc"] = "hello"
    agent.active_alias = "doc"

    summary = agent.summarize_long_document("focus")
    logs = agent.extract_from_logs("query")
    assert summary["trajectory_steps"] == 1
    assert logs["trajectory_steps"] == 1


def test_find_files_with_ripgrep(monkeypatch, tmp_path):
    """find_files should use ripgrep to search file contents."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

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
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    tool_names = [
        getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
        for tool in agent.react_tools
    ]

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


# -----------------------------------------------------------------------
# Phase 1 Tests — dspy.Module subclass, forward(), dspy.Tool wrappers,
#                 typed Signature generics
# -----------------------------------------------------------------------


def test_agent_is_dspy_module_subclass(monkeypatch):
    """RLMReActChatAgent must subclass dspy.Module."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    assert issubclass(RLMReActChatAgent, dspy.Module)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    assert isinstance(agent, dspy.Module)


def test_agent_has_react_as_discoverable_submodule(monkeypatch):
    """self.react (dspy.ReAct) must appear in named_sub_modules."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    assert hasattr(agent, "react")
    # The fake isn't a real dspy.Module so it won't appear in named_sub_modules,
    # but the attribute assignment itself is correct.
    assert agent.react is not None


def test_forward_delegates_to_react_and_starts_interpreter(monkeypatch):
    """forward() should call self.react(...) and start the interpreter."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    fake_interpreter = _FakeInterpreter()
    agent = RLMReActChatAgent(interpreter=fake_interpreter)

    prediction = agent.forward(user_request="test query")
    assert prediction.assistant_response == "echo:test query"
    assert fake_interpreter.start_calls == 1


def test_forward_accepts_custom_history(monkeypatch):
    """forward() should use the provided history, not the agent's own."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    custom_history = dspy.History(
        messages=[{"user_request": "prior", "assistant_response": "old"}]
    )

    prediction = agent.forward(user_request="new", history=custom_history)
    assert prediction.assistant_response == "echo:new"
    # Agent's own history should be unmodified
    assert len(agent.history.messages) == 0


def test_chat_turn_uses_forward_internally(monkeypatch):
    """chat_turn() should delegate to forward() and append history."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    result = agent.chat_turn("hello")

    assert result["assistant_response"] == "echo:hello"
    assert result["history_turns"] == 1
    assert agent.history.messages[0]["user_request"] == "hello"


def test_all_tools_are_dspy_tool_wrappers(monkeypatch):
    """All tools in react_tools should be dspy.Tool instances."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    for tool in agent.react_tools:
        assert isinstance(tool, dspy.Tool), (
            f"Tool {tool} is {type(tool).__name__}, expected dspy.Tool"
        )
        assert tool.name, f"Tool {tool} has no name"
        assert tool.desc, f"Tool {tool.name} has no description"


def test_extra_tools_auto_wrapped_in_dspy_tool(monkeypatch):
    """Extra tools passed as raw callables should be auto-wrapped in dspy.Tool."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def my_custom_tool(x: str) -> str:
        """A custom helper."""
        return x.upper()

    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
        extra_tools=[my_custom_tool],
    )
    # Last tool should be the wrapped custom tool
    last_tool = agent.react_tools[-1]
    assert isinstance(last_tool, dspy.Tool)
    assert last_tool.name == "my_custom_tool"


def test_extra_dspy_tool_not_double_wrapped(monkeypatch):
    """Extra tools that are already dspy.Tool should not be re-wrapped."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    def raw_fn(x: str) -> str:
        return x

    pre_wrapped = dspy.Tool(raw_fn, name="pre_wrapped", desc="already wrapped")
    agent = RLMReActChatAgent(
        interpreter=_FakeInterpreter(),
        extra_tools=[pre_wrapped],
    )
    last_tool = agent.react_tools[-1]
    assert last_tool is pre_wrapped


def test_get_tool_returns_underlying_callable(monkeypatch):
    """_get_tool should return the underlying func from dspy.Tool wrappers."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    tool_fn = agent._get_tool("load_document")
    assert callable(tool_fn)
    # Should be the unwrapped function, not the dspy.Tool wrapper
    assert not isinstance(tool_fn, dspy.Tool)


def test_get_tool_raises_on_unknown_name(monkeypatch):
    """_get_tool should raise AttributeError for unknown tool names."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    with pytest.raises(AttributeError, match="nonexistent_tool"):
        agent._get_tool("nonexistent_tool")


def test_list_react_tool_names_handles_dspy_tool(monkeypatch):
    """list_react_tool_names should work with dspy.Tool wrappers."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    names = react_tools.list_react_tool_names(agent.react_tools)
    assert isinstance(names, list)
    assert "load_document" in names
    assert "parallel_semantic_map" in names
    assert len(names) == len(agent.react_tools)


def test_register_extra_tool_rebuilds_react(monkeypatch):
    """register_extra_tool should rebuild self.react with the new tool."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    initial_count = len(agent.react_tools)

    def new_tool(x: str) -> str:
        return x

    result = agent.register_extra_tool(new_tool)
    assert result["status"] == "ok"
    assert len(agent.react_tools) == initial_count + 1


def test_reset_clears_history_and_documents(monkeypatch):
    """reset() should clear history AND host-side document state."""
    records = []
    monkeypatch.setattr("fleet_rlm.react.agent.dspy.ReAct", _make_fake_react(records))

    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.chat_turn("hello")
    assert len(agent.history.messages) == 1
    # Simulate a loaded document
    agent._document_cache["test.txt"] = "some content"
    agent._document_access_order.append("test.txt")
    agent.active_alias = "test.txt"

    result = agent.reset(clear_sandbox_buffers=False)
    assert result["status"] == "ok"
    assert result["history_turns"] == 0
    assert len(agent.history.messages) == 0
    # Verify documents are also cleared
    assert len(agent._document_cache) == 0
    assert len(agent._document_access_order) == 0
    assert agent.active_alias is None


# -----------------------------------------------------------------------
# Signature typed generics tests
# -----------------------------------------------------------------------


def test_signature_output_types_are_generic():
    """All Signature output fields should use typed generics, not bare list/dict."""
    import typing
    from fleet_rlm.signatures import (
        AnalyzeLongDocument,
        ExtractAPIEndpoints,
        ExtractArchitecture,
        ExtractFromLogs,
        ExtractWithCustomTool,
        FindErrorPatterns,
        SummarizeLongDocument,
    )

    checks = [
        (ExtractArchitecture, "modules", list[str]),
        (ExtractArchitecture, "optimizers", list[str]),
        (ExtractAPIEndpoints, "api_endpoints", list[str]),
        (FindErrorPatterns, "error_categories", dict[str, str]),
        (ExtractWithCustomTool, "headers", list[str]),
        (ExtractWithCustomTool, "code_blocks", list[str]),
        (AnalyzeLongDocument, "findings", list[str]),
        (SummarizeLongDocument, "key_points", list[str]),
        (ExtractFromLogs, "matches", list[str]),
        (ExtractFromLogs, "patterns", dict[str, str]),
    ]

    hints = {}
    for sig_cls, field_name, expected_type in checks:
        if sig_cls not in hints:
            hints[sig_cls] = typing.get_type_hints(sig_cls)
        actual = hints[sig_cls].get(field_name)
        assert actual == expected_type, (
            f"{sig_cls.__name__}.{field_name}: expected {expected_type}, got {actual}"
        )
