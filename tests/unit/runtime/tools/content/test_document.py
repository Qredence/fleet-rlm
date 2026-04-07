"""Unit tests for document tool wrappers at their new location.

Covers fleet_rlm.runtime.tools.content.document.build_document_tools factory —
the closure-based DSPy tool builder that binds document ops to an agent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import dspy
import pytest

from tests.unit.fixtures_daytona import (
    FakeDaytonaWorkspaceInterpreter,
    FakeDaytonaWorkspaceSession,
)


def _make_fake_agent(tmp_path: Path) -> Any:
    """Minimal fake agent compatible with build_document_tools closures."""
    doc_cache: dict[str, str] = {}

    def _set_document(alias: str, content: str) -> None:
        doc_cache[alias] = content

    agent = SimpleNamespace(
        documents=doc_cache,
        _document_cache=doc_cache,
        _max_documents=10,
        active_alias=None,
        _set_document=_set_document,
        interpreter=None,
    )
    return agent


# ---------------------------------------------------------------------------
# Factory production
# ---------------------------------------------------------------------------


def test_build_document_tools_returns_dspy_tools():
    """build_document_tools should return a non-empty list of dspy.Tool objects."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(Path("/tmp"))
    tools = build_document_tools(agent)

    assert isinstance(tools, list)
    assert len(tools) > 0
    for t in tools:
        assert isinstance(t, dspy.Tool), f"Expected dspy.Tool, got {type(t)}"


def test_build_document_tools_includes_expected_names():
    """The factory should expose load_document, fetch_web_document, set_active_document, list_documents."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(Path("/tmp"))
    tools = build_document_tools(agent)
    names = {t.name for t in tools}

    for expected in (
        "load_document",
        "fetch_web_document",
        "set_active_document",
        "list_documents",
    ):
        assert expected in names, f"Missing tool: {expected}"


def test_fetch_web_document_marks_retriever_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """fetch_web_document should annotate retrieval work with a retriever span."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    fetch_fn = next(t.func for t in tools if t.name == "fetch_web_document")

    trace_calls: list[tuple[str | None, str | None]] = []

    class _TraceSpan:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeMlflow:
        @staticmethod
        def start_span(*, name=None, span_type=None):
            trace_calls.append((name, span_type))
            return _TraceSpan()

    monkeypatch.setattr(
        "fleet_rlm.runtime.tools.content.document.mlflow", _FakeMlflow()
    )

    with patch(
        "fleet_rlm.runtime.tools.content.document.fetch_url_document_content",
        return_value=("hello", {"source_type": "text"}),
    ):
        result = fetch_fn("https://example.com/doc.txt", alias="web")

    assert result["status"] == "ok"
    assert trace_calls == [("fetch_web_document", "RETRIEVER")]


# ---------------------------------------------------------------------------
# load_document — local file
# ---------------------------------------------------------------------------


def test_load_document_local_file(tmp_path: Path):
    """load_document with a local file path should populate agent._document_cache."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    readme = tmp_path / "readme.md"
    readme.write_text("# Hello\nThis is the readme.")

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    # Patch the read_document_content dependency to avoid real parser
    with patch(
        "fleet_rlm.runtime.tools.content.document._read_document_content",
        return_value=("# Hello\nThis is the readme.", {"source_type": "text"}),
    ):
        result = load_fn(str(readme))

    assert result["status"] == "ok"
    assert result["alias"] == "active"
    assert agent._document_cache.get("active") == "# Hello\nThis is the readme."


def test_load_document_missing_file_raises(tmp_path: Path):
    """load_document with a non-existent path should raise FileNotFoundError."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    with pytest.raises(FileNotFoundError):
        load_fn("/absolutely/nonexistent/file.txt")


def test_load_document_daytona_workspace_relative_file(tmp_path: Path):
    """Daytona workspace files should load when absent on the host filesystem."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    session = FakeDaytonaWorkspaceSession()
    session.files["/workspace/repo/paper.txt"] = "paper body"
    agent.interpreter = FakeDaytonaWorkspaceInterpreter(session)

    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")
    with patch(
        "fleet_rlm.runtime.tools.sandbox.common._get_daytona_session_sync",
        return_value=session,
    ):
        result = load_fn("paper.txt", alias="paper")

    assert result == {
        "status": "ok",
        "alias": "paper",
        "path": "/workspace/repo/paper.txt",
        "chars": len("paper body"),
        "lines": 1,
    }
    assert agent._document_cache["paper"] == "paper body"
    assert agent.active_alias == "paper"
    assert session.list_calls == ["/workspace/repo"]
    assert session.read_calls == ["/workspace/repo/paper.txt"]


def test_load_document_daytona_workspace_relative_file_wins_over_host(tmp_path: Path):
    """Daytona workspace files should win over colliding host-relative paths."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    readme = tmp_path / "README.md"
    readme.write_text("host readme")

    agent = _make_fake_agent(tmp_path)
    session = FakeDaytonaWorkspaceSession()
    session.files["/workspace/repo/README.md"] = "daytona readme"
    agent.interpreter = FakeDaytonaWorkspaceInterpreter(session)

    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")
    with patch(
        "fleet_rlm.runtime.tools.sandbox.common._get_daytona_session_sync",
        return_value=session,
    ):
        result = load_fn("README.md", alias="readme")

    assert result == {
        "status": "ok",
        "alias": "readme",
        "path": "/workspace/repo/README.md",
        "chars": len("daytona readme"),
        "lines": 1,
    }
    assert agent._document_cache["readme"] == "daytona readme"
    assert session.list_calls == ["/workspace/repo"]
    assert session.read_calls == ["/workspace/repo/README.md"]


def test_load_document_daytona_workspace_missing_file_raises(tmp_path: Path):
    """Missing Daytona workspace files should still fail cleanly."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    agent.interpreter = FakeDaytonaWorkspaceInterpreter(FakeDaytonaWorkspaceSession())

    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    with pytest.raises(FileNotFoundError, match="missing.txt"):
        load_fn("missing.txt")


def test_load_daytona_workspace_text_sync_rejects_parent_traversal(
    tmp_path: Path,
) -> None:
    from fleet_rlm.runtime.tools.sandbox.common import (
        _SandboxToolContext,
        _load_daytona_workspace_text_sync,
    )

    agent = _make_fake_agent(tmp_path)
    session = FakeDaytonaWorkspaceSession()
    agent.interpreter = FakeDaytonaWorkspaceInterpreter(session)

    loaded = _load_daytona_workspace_text_sync(
        _SandboxToolContext(agent=agent),
        path="../secrets.txt",
    )

    assert loaded is None
    assert session.list_calls == []
    assert session.read_calls == []


def test_load_document_directory_returns_listing(tmp_path: Path):
    """load_document with a directory returns a file listing, not content."""
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "b.txt").write_text("B")

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    result = load_fn(str(tmp_path))

    assert result["status"] == "directory"
    assert "files" in result
    assert result["total_count"] == 2


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


def test_list_documents_empty_cache(tmp_path: Path):
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    list_fn = next(t.func for t in tools if t.name == "list_documents")

    result = list_fn()
    assert result["documents"] == []
    assert result["active_alias"] is None
    assert result["cache_size"] == 0


def test_list_documents_populated_cache(tmp_path: Path):
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    agent._document_cache["doc1"] = "content one"
    agent._document_cache["doc2"] = "content two"
    agent.active_alias = "doc1"

    tools = build_document_tools(agent)
    list_fn = next(t.func for t in tools if t.name == "list_documents")

    result = list_fn()
    assert result["cache_size"] == 2
    assert result["active_alias"] == "doc1"
    aliases = {d["alias"] for d in result["documents"]}
    assert aliases == {"doc1", "doc2"}


# ---------------------------------------------------------------------------
# set_active_document
# ---------------------------------------------------------------------------


def test_set_active_document_valid_alias(tmp_path: Path):
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    agent._document_cache["myfile"] = "some content"
    agent.documents["myfile"] = "some content"

    tools = build_document_tools(agent)
    set_fn = next(t.func for t in tools if t.name == "set_active_document")

    result = set_fn("myfile")
    assert result["status"] == "ok"
    assert agent.active_alias == "myfile"


def test_set_active_document_invalid_alias_raises(tmp_path: Path):
    from fleet_rlm.runtime.tools.content.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    set_fn = next(t.func for t in tools if t.name == "set_active_document")

    with pytest.raises(ValueError, match="Unknown document alias"):
        set_fn("nonexistent-alias")
