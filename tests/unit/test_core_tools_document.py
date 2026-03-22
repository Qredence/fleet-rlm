"""Unit tests for document tool wrappers at their new location.

Covers fleet_rlm.runtime.tools.document.build_document_tools factory —
the closure-based DSPy tool builder that binds document ops to an agent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import dspy
import pytest


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
    )
    return agent


# ---------------------------------------------------------------------------
# Factory production
# ---------------------------------------------------------------------------


def test_build_document_tools_returns_dspy_tools():
    """build_document_tools should return a non-empty list of dspy.Tool objects."""
    from fleet_rlm.runtime.tools.document import build_document_tools

    agent = _make_fake_agent(Path("/tmp"))
    tools = build_document_tools(agent)

    assert isinstance(tools, list)
    assert len(tools) > 0
    for t in tools:
        assert isinstance(t, dspy.Tool), f"Expected dspy.Tool, got {type(t)}"


def test_build_document_tools_includes_expected_names():
    """The factory should expose load_document, fetch_web_document, set_active_document, list_documents."""
    from fleet_rlm.runtime.tools.document import build_document_tools

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


# ---------------------------------------------------------------------------
# load_document — local file
# ---------------------------------------------------------------------------


def test_load_document_local_file(tmp_path: Path):
    """load_document with a local file path should populate agent._document_cache."""
    from fleet_rlm.runtime.tools.document import build_document_tools

    readme = tmp_path / "readme.md"
    readme.write_text("# Hello\nThis is the readme.")

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    # Patch the read_document_content dependency to avoid real parser
    with patch(
        "fleet_rlm.runtime.tools.document._read_document_content",
        return_value=("# Hello\nThis is the readme.", {"source_type": "text"}),
    ):
        result = load_fn(str(readme))

    assert result["status"] == "ok"
    assert result["alias"] == "active"
    assert agent._document_cache.get("active") == "# Hello\nThis is the readme."


def test_load_document_missing_file_raises(tmp_path: Path):
    """load_document with a non-existent path should raise FileNotFoundError."""
    from fleet_rlm.runtime.tools.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    load_fn = next(t.func for t in tools if t.name == "load_document")

    with pytest.raises(FileNotFoundError):
        load_fn("/absolutely/nonexistent/file.txt")


def test_load_document_directory_returns_listing(tmp_path: Path):
    """load_document with a directory returns a file listing, not content."""
    from fleet_rlm.runtime.tools.document import build_document_tools

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
    from fleet_rlm.runtime.tools.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    list_fn = next(t.func for t in tools if t.name == "list_documents")

    result = list_fn()
    assert result["documents"] == []
    assert result["active_alias"] is None
    assert result["cache_size"] == 0


def test_list_documents_populated_cache(tmp_path: Path):
    from fleet_rlm.runtime.tools.document import build_document_tools

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
    from fleet_rlm.runtime.tools.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    agent._document_cache["myfile"] = "some content"
    agent.documents["myfile"] = "some content"

    tools = build_document_tools(agent)
    set_fn = next(t.func for t in tools if t.name == "set_active_document")

    result = set_fn("myfile")
    assert result["status"] == "ok"
    assert agent.active_alias == "myfile"


def test_set_active_document_invalid_alias_raises(tmp_path: Path):
    from fleet_rlm.runtime.tools.document import build_document_tools

    agent = _make_fake_agent(tmp_path)
    tools = build_document_tools(agent)
    set_fn = next(t.func for t in tools if t.name == "set_active_document")

    with pytest.raises(ValueError, match="Unknown document alias"):
        set_fn("nonexistent-alias")
