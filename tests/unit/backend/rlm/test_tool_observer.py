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
