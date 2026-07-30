"""Observation contracts for validated, host-projected Tools."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import dspy
import pytest

from fleet_rlm.rlm.events import ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.tool_observer import ToolEventView, observe_tool


def test_observe_tool_creates_bounded_mlflow_tool_span(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.observability import turn_tracing

    spans: list[dict[str, Any]] = []

    class Span:
        def set_inputs(self, payload):
            spans[-1]["inputs"] = payload

        def set_outputs(self, payload):
            spans[-1]["outputs"] = payload

        def set_status(self, status):
            spans[-1]["status"] = status

    class SpanContext:
        def __enter__(self):
            spans.append({})
            return Span()

        def __exit__(self, *_args):
            return None

    monkeypatch.setitem(
        __import__("sys").modules,
        "mlflow",
        SimpleNamespace(get_current_active_span=lambda: Span(), start_span=lambda **_kwargs: SpanContext()),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "mlflow.entities",
        SimpleNamespace(SpanType=SimpleNamespace(CHAIN="CHAIN", TOOL="TOOL")),
    )
    token = turn_tracing._fleet_trace_active.set(True)
    try:
        wrapped = observe_tool(
            dspy.Tool(lambda query: {"found": query == "alpha"}, name="lookup"),
            lambda _event: None,
            ToolEventView(
                input_projection=lambda arguments: {"query": arguments["query"]},
                output_projection=lambda result: result,
            ),
        )
        assert wrapped.func("alpha") == {"found": True}
    finally:
        turn_tracing._fleet_trace_active.reset(token)

    assert spans[0]["inputs"]["tool_name"] == "lookup"
    assert spans[0]["inputs"]["input"] == {"query": "alpha"}
    assert spans[0]["outputs"] == {
        "tool_status": "completed",
        "output": {"found": True},
        "phase_status": "completed",
    }


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


def test_observed_semantic_tool_validates_exact_string_and_list_shape() -> None:
    calls: list[tuple[str, str, list[str], list[str]]] = []

    def verify_semantic_work(
        iteration_token: str,
        single_result: str,
        batch_results: list[str],
        accumulator: list[str],
    ) -> dict[str, bool]:
        calls.append((iteration_token, single_result, batch_results, accumulator))
        return {"ok": True}

    observed: list[Any] = []
    wrapped = observe_tool(
        dspy.Tool(verify_semantic_work),
        observed.append,
        ToolEventView.metadata_only(),
    )
    expected_batch = ["ALPHA", "BETA", "GAMMA"]
    expected_accumulator = ["iteration-1", "ROOT", *expected_batch]

    assert wrapped.func(
        iteration_token="iteration-1",
        single_result="ROOT",
        batch_results=expected_batch,
        accumulator=expected_accumulator,
    ) == {"ok": True}

    invalid_calls = (
        lambda: wrapped.func(
            iteration_token="iteration-1",
            single_result="ROOT",
            batch_results=expected_batch,
            wrong_accumulator=expected_accumulator,
        ),
        lambda: wrapped.func(
            iteration_token="iteration-1",
            single_result="ROOT",
            batch_results=expected_batch,
        ),
        lambda: wrapped.func(
            iteration_token="iteration-1",
            single_result="ROOT",
            batch_results=["ALPHA", 2, "GAMMA"],
            accumulator=expected_accumulator,
        ),
    )
    for invalid in invalid_calls:
        with pytest.raises((TypeError, ValueError)):
            invalid()

    assert calls == [("iteration-1", "ROOT", expected_batch, expected_accumulator)]
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


def test_after_result_hook_runs_between_started_and_completed_without_projecting_body() -> None:
    def load() -> str:
        return "private-skill-body"

    observed: list[Any] = []
    wrapped = observe_tool(
        dspy.Tool(load),
        observed.append,
        ToolEventView.metadata_only(),
        after_result=lambda result: observed.append(("lifecycle", len(result))),
    )

    assert wrapped() == "private-skill-body"
    assert [type(item) if not isinstance(item, tuple) else item[0] for item in observed] == [
        ToolStarted,
        "lifecycle",
        ToolCompleted,
    ]
    assert observed[1] == ("lifecycle", len("private-skill-body"))
    assert "private-skill-body" not in str((observed[0], observed[2]))


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

    with pytest.raises(TypeError, match=r"dspy.Tool"):
        observe_tool(plain, observed.append, ToolEventView())  # type: ignore[arg-type]

    async def async_tool() -> str:
        await asyncio.sleep(0)
        return "unsupported"

    wrapped = observe_tool(dspy.Tool(async_tool), observed.append, ToolEventView())
    with pytest.raises(TypeError, match="async host tools"):
        wrapped()
    assert [type(item) for item in observed] == [ToolStarted, ToolFailed]
