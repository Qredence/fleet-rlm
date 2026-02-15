"""Unit tests for ReAct agent filesystem and document tools.

Tests cover load_document (text/PDF/directory), list_files, read_file_slice,
find_files (ripgrep), PDF extraction pipelines, long-document analysis,
and the tool-registry surface area.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from dspy.primitives.code_interpreter import FinalOutput

from fleet_rlm.react import RLMReActChatAgent
from fleet_rlm.react import tools as react_tools


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
# Filesystem tool tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# PDF extraction tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Long-document analysis tool tests
# ---------------------------------------------------------------------------


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

    monkeypatch.setattr("fleet_rlm.react.tools_sandbox.dspy.RLM", _FakeRLM)
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

    monkeypatch.setattr("fleet_rlm.react.tools_sandbox.dspy.RLM", _FakeRLM)
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

    monkeypatch.setattr("fleet_rlm.react.tools_sandbox.dspy.RLM", _FakeRLM)
    agent = RLMReActChatAgent(interpreter=_FakeInterpreter())
    agent.documents["doc"] = "hello"
    agent.active_alias = "doc"

    summary = agent.summarize_long_document("focus")
    logs = agent.extract_from_logs("query")
    assert summary["trajectory_steps"] == 1
    assert logs["trajectory_steps"] == 1


# ---------------------------------------------------------------------------
# Tool registry / file-search tests
# ---------------------------------------------------------------------------


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
