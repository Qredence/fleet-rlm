"""Observation contracts for wrapped host tools."""

from __future__ import annotations

import asyncio

import dspy
import pytest

from fleet_rlm.rlm.events import ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


def test_observe_tool_preserves_metadata_and_emits_correlated_details() -> None:
    def lookup(key: str) -> dict[str, str]:
        """Look up a registered value."""
        return {"key": key, "value": "found"}

    source = dspy.Tool(
        lookup,
        name="knowledge_lookup",
        desc="Lookup registered knowledge",
        arg_desc={"key": "Registered key"},
    )
    observed: list[object] = []

    wrapped = observe_tool(source, observed.append, ToolEventView(max_chars=1_000))
    another = observe_tool(source, observed.append, ToolEventView(max_chars=1_000))
    result = wrapped(key="alpha")

    assert type(wrapped) is dspy.Tool
    assert wrapped is not source
    assert another is not wrapped
    assert wrapped.name == source.name
    assert wrapped.desc == source.desc
    assert wrapped.args == source.args
    assert wrapped.arg_types == source.arg_types
    assert wrapped.arg_desc == source.arg_desc
    assert result == {"key": "alpha", "value": "found"}
    assert [type(item) for item in observed] == [ToolStarted, ToolCompleted]
    assert observed[0].tool_call_id == observed[1].tool_call_id
    assert observed[0].tool_name == "knowledge_lookup"
    assert observed[0].input == {"args": (), "kwargs": {"key": "alpha"}}
    assert observed[1].output == {"key": "alpha", "value": "found"}


def test_observe_tool_projects_protected_values_and_reraises_original_failure() -> None:
    observed: list[object] = []

    def create_artifact(kind: str, content: str, title: str | None = None) -> dict[str, object]:
        return {
            "ok": True,
            "kind": kind,
            "title": title,
            "byte_size": len(content),
            "artifact_candidate_id": "private-candidate",
        }

    wrapped = observe_tool(create_artifact, observed.append, ToolEventView(max_chars=1_000))
    wrapped(kind="text", content="private artifact body", title="Report")

    assert observed[0].input == {"kind": "text", "title": "Report", "content_chars": 21}
    assert observed[1].output == {"ok": True, "kind": "text", "title": "Report", "byte_size": 21}
    assert "private artifact body" not in str(observed)
    assert "private-candidate" not in str(observed)

    failure = ValueError("api_key=secret-value")

    def fail(_value: str) -> None:
        raise failure

    failed = observe_tool(fail, observed.append, ToolEventView(max_chars=1_000))
    with pytest.raises(ValueError) as caught:
        failed(_value="input")

    assert caught.value is failure
    detail = observed[-1]
    assert isinstance(detail, ToolFailed)
    assert detail.tool_call_id == observed[-2].tool_call_id
    assert "secret-value" not in detail.error


def test_observe_tool_rejects_awaitable_results() -> None:
    observed: list[object] = []

    async def async_tool() -> str:
        await asyncio.sleep(0)
        return "unsupported"

    wrapped = observe_tool(async_tool, observed.append, ToolEventView())

    with pytest.raises(TypeError, match="async host tools"):
        wrapped()

    assert [type(item) for item in observed] == [ToolStarted, ToolFailed]


def test_observe_tool_bounds_generic_input_and_output() -> None:
    observed: list[object] = []

    def echo(value: str) -> str:
        return value

    wrapped = observe_tool(echo, observed.append, ToolEventView(max_chars=12))
    wrapped(value="x" * 100)

    assert len(observed[0].input["kwargs"]["value"]) <= 12
    assert len(observed[1].output) <= 12


def test_observe_tool_hides_session_history_message_bodies() -> None:
    observed: list[object] = []

    def read_session_history(offset: int, limit: int) -> dict[str, object]:
        return {
            "offset": offset,
            "next_offset": offset + 1,
            "total": 42,
            "messages": [{"ordinal": 1, "role": "user", "content": "private history body"}],
        }

    wrapped = observe_tool(read_session_history, observed.append, ToolEventView(max_chars=1_000))
    result = wrapped(offset=0, limit=1)

    assert result["messages"][0]["content"] == "private history body"
    assert observed[0].input == {"offset": 0, "limit": 1}
    assert observed[1].output == {
        "offset": 0,
        "next_offset": 1,
        "total": 42,
        "message_count": 1,
    }
    assert "private history body" not in str(observed)


def test_observe_tool_hides_workspace_file_bodies() -> None:
    observed: list[object] = []

    def list_workspace_files(path: str = ".", limit: int = 100) -> dict[str, object]:
        return {
            "ok": True,
            "path": path,
            "count": 1,
            "entries": [
                {
                    "path": "notes/private.md",
                    "kind": "file",
                    "byte_size": 22,
                    "modified_at": "2026-07-16T12:00:00Z",
                }
            ][:limit],
        }

    list_tool = dspy.Tool(list_workspace_files, name="list_workspace_files")
    wrapped_list = observe_tool(list_tool, observed.append, ToolEventView(max_chars=1_000))
    wrapped_list(path=".", limit=100)

    assert observed[-1].output == {"ok": True, "path": ".", "count": 1}
    assert "private.md" not in str(observed[-1])
    observed.clear()

    def write_workspace_text(path: str, content: str, overwrite: bool = False) -> dict[str, object]:
        return {
            "ok": True,
            "path": path,
            "kind": "file",
            "byte_size": len(content.encode()),
            "modified_at": "2026-07-16T12:00:00Z",
            "private": content,
        }

    write_tool = dspy.Tool(write_workspace_text, name="write_workspace_text")
    wrapped_write = observe_tool(write_tool, observed.append, ToolEventView(max_chars=1_000))
    wrapped_write(path="notes/private.md", content="private workspace body", overwrite=False)

    assert observed[0].input == {
        "path": "notes/private.md",
        "overwrite": False,
        "content_chars": 22,
    }
    assert observed[1].output == {"ok": True, "path": "notes/private.md", "byte_size": 22}
    assert "private workspace body" not in str(observed)

    def read_workspace_text(path: str, max_chars: int = 10_000) -> dict[str, object]:
        return {
            "ok": True,
            "path": path,
            "content": "private workspace body",
            "encoding": "utf-8",
            "chars": 22,
            "byte_size": 22,
        }

    read_tool = dspy.Tool(read_workspace_text, name="read_workspace_text")
    wrapped_read = observe_tool(read_tool, observed.append, ToolEventView(max_chars=1_000))
    result = wrapped_read(path="notes/private.md", max_chars=100)

    assert result["content"] == "private workspace body"
    assert observed[-2].input == {"path": "notes/private.md", "max_chars": 100}
    assert observed[-1].output == {
        "ok": True,
        "path": "notes/private.md",
        "chars": 22,
        "byte_size": 22,
    }
    assert "private workspace body" not in str(observed)

    def stat_workspace_file(path: str) -> dict[str, object]:
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": 22,
                "modified_at": "2026-07-16T12:00:00Z",
            },
        }

    stat_tool = dspy.Tool(stat_workspace_file, name="stat_workspace_file")
    wrapped_stat = observe_tool(stat_tool, observed.append, ToolEventView(max_chars=1_000))
    wrapped_stat(path="notes/private.md")

    assert observed[-1].output == {
        "ok": True,
        "path": "notes/private.md",
        "byte_size": 22,
    }
    assert "modified_at" not in str(observed[-1])
    assert "kind" not in str(observed[-1])

    def failed_workspace_read(path: str, max_chars: int = 10_000) -> dict[str, object]:
        del path, max_chars
        raise RuntimeError("provider secret and internal path")

    failed_tool = dspy.Tool(failed_workspace_read, name="read_workspace_text")
    wrapped_failure = observe_tool(failed_tool, observed.append, ToolEventView(max_chars=1_000))
    with pytest.raises(RuntimeError, match="provider secret"):
        wrapped_failure(path="notes/private.md", max_chars=100)

    assert observed[-1].error == "Protected tool failed"
    assert "internal path" not in str(observed[-1])
