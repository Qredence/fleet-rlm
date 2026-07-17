"""Observation contracts for validated, host-projected Tools."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import dspy
import pytest

from fleet_rlm.rlm.events import ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


def test_extracted_observer_func_binds_defaults_validates_and_enters_host_once() -> None:
    calls: list[tuple[int, str]] = []

    def calculate(count: int, label: str = "default") -> str:
        calls.append((count, label))
        return f"{label}:{count}"

    observed: list[Any] = []
    wrapped = observe_tool(dspy.Tool(calculate), observed.append, ToolEventView.metadata_only())

    assert wrapped.func(2) == "default:2"
    assert wrapped.func(count=3, label="named") == "named:3"
    assert calls == [(2, "default"), (3, "named")]
    assert observed[0].input == {}
    assert observed[1].output == {}

    for invalid in (
        lambda: wrapped.func(count="wrong"),
        lambda: wrapped.func(unknown=1),
        lambda: wrapped.func(),
    ):
        with pytest.raises((TypeError, ValueError)):
            invalid()

    assert calls == [(2, "default"), (3, "named")]
    failures = [item for item in observed if isinstance(item, ToolFailed)]
    assert [item.error for item in failures] == ["Tool arguments are invalid"] * 3


def test_observe_tool_preserves_metadata_and_correlates_explicit_view() -> None:
    def lookup(key: str) -> dict[str, str]:
        """Look up a registered value."""
        return {"key": key, "value": "private-result"}

    source = dspy.Tool(
        lookup,
        name="knowledge_lookup",
        desc="Lookup registered knowledge",
        arg_desc={"key": "Registered key"},
    )
    view = ToolEventView(
        input_projection=lambda arguments: {"key": arguments["key"]},
        output_projection=lambda result: {"found": isinstance(result, Mapping)},
    )
    observed: list[Any] = []

    wrapped = observe_tool(source, observed.append, view)
    another = observe_tool(source, observed.append, view)
    result = wrapped(key="alpha")

    assert type(wrapped) is dspy.Tool
    assert wrapped is not source
    assert another is not wrapped
    assert wrapped.name == source.name
    assert wrapped.desc == source.desc
    assert wrapped.args == source.args
    assert wrapped.arg_types == source.arg_types
    assert wrapped.arg_desc == source.arg_desc
    assert result == {"key": "alpha", "value": "private-result"}
    assert [type(item) for item in observed] == [ToolStarted, ToolCompleted]
    assert observed[0].tool_call_id == observed[1].tool_call_id
    assert observed[0].input == {"key": "alpha"}
    assert observed[1].output == {"found": True}
    assert "private-result" not in str(observed)


def test_metadata_only_fallback_never_exposes_arguments_results_or_failures() -> None:
    observed: list[Any] = []

    def echo(value: str) -> str:
        if value == "fail":
            raise RuntimeError("provider secret and internal path")
        return value

    wrapped = observe_tool(dspy.Tool(echo), observed.append, ToolEventView.metadata_only())

    assert wrapped(value="private input") == "private input"
    assert observed[0].input == {}
    assert observed[1].output == {}
    assert "private input" not in str(observed)

    with pytest.raises(RuntimeError, match="provider secret"):
        wrapped(value="fail")
    assert observed[-1].error == "Tool failed"
    assert "internal path" not in str(observed[-1])


def test_projection_defect_fails_closed_without_changing_tool_result() -> None:
    observed: list[Any] = []

    def echo(value: str) -> str:
        return value

    def broken(_value: Any) -> Any:
        raise RuntimeError("projection defect")

    wrapped = observe_tool(
        dspy.Tool(echo),
        observed.append,
        ToolEventView(input_projection=broken, output_projection=broken),
    )

    assert wrapped(value="still returned") == "still returned"
    assert observed[0].input == {}
    assert observed[1].output == {}


def test_observe_tool_rejects_non_tools_and_awaitable_results() -> None:
    observed: list[Any] = []

    def plain() -> str:
        return "plain"

    with pytest.raises(TypeError, match="dspy.Tool"):
        observe_tool(plain, observed.append, ToolEventView())  # type: ignore[arg-type]

    async def async_tool() -> str:
        await asyncio.sleep(0)
        return "unsupported"

    wrapped = observe_tool(dspy.Tool(async_tool), observed.append, ToolEventView())
    with pytest.raises(TypeError, match="async host tools"):
        wrapped()
    assert [type(item) for item in observed] == [ToolStarted, ToolFailed]
