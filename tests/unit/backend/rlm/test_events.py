"""Behavioral contracts for RLM Runtime Events, tool observation, and trajectory projection."""

from __future__ import annotations

import asyncio
import time
from threading import Thread
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.rlm.budget import BudgetDimension, BudgetLimits, TurnBudget, TurnBudgetExhausted
from fleet_rlm.rlm.events import (
    ObservationSession,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    RunStarted,
    Status,
    StepFinished,
    StepStarted,
    ToolCompleted,
    ToolEventView,
    ToolFailed,
    ToolStarted,
    observe_tool,
    reconcile_trajectory,
    record_phase_failure,
)
from fleet_rlm.rlm.execution import RunToolGuards
from fleet_rlm.rlm.result import TrajectoryStep, observed_usage, validate_rlm_usage

# --- Execution Trace & Observation Session Contracts ---

# --- from test_events_execution_trace.py ------------------------------


def test_record_phase_failure_preserves_callback_reasoning_flag_on_empty_parse() -> None:
    import dspy
    from dspy.utils.exceptions import AdapterParseError

    class _Sig(dspy.Signature):
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    error = AdapterParseError(
        adapter_name="JSONAdapter",
        signature=_Sig,
        lm_response="",
        message="The LM returned an empty or null response.",
    )

    record_phase_failure(
        phase,
        0.0,
        None,
        None,
        error,
        last_lm_call={"call_index": 14, "has_reasoning_content": True},
    )

    last_call = outputs[-1]["last_lm_call"]
    assert last_call["parse_failure_kind"] == "empty"
    assert last_call["has_reasoning_content"] is True


def test_phase_trace_records_wrap_up_diagnostics_without_changing_trajectory() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    diagnostics = {
        "wrap_up_entered": True,
        "wrap_up_attempts": 2,
        "wrap_up_rejection_reason": "exploration_or_additional_code",
    }

    record_phase_failure(phase, 0.0, None, None, TimeoutError("deadline"), wrap_up=diagnostics)

    assert outputs[-1]["wrap_up_entered"] is True
    assert outputs[-1]["wrap_up_attempts"] == 2
    assert outputs[-1]["wrap_up_rejection_reason"] == "exploration_or_additional_code"


def test_record_phase_failure_marks_token_usage_unavailable_without_observed_usage() -> None:
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(phase, 0.0, None, None, ValueError("boom"))

    assert outputs[-1]["token_usage_status"] == "unavailable"
    assert outputs[-1]["delegation_metrics"]["lm_token_totals"] == []


def test_record_phase_success_marks_token_usage_observed_from_lm_metrics() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 40, "output_tokens": 7})
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(trajectory=[{"reasoning": "r", "code": "c", "output": "o"}], get_lm_usage=lambda: {})

    record_phase_success(phase, prediction, 0.0, None, metrics)

    final = outputs[-1]
    assert final["request_status"] == "completed"
    assert final["token_usage_status"] == "observed"
    assert final["delegation_metrics"]["lm_token_totals"] == [
        {"role": "root", "recursive_depth": 0, "input_tokens": 40, "output_tokens": 7, "tokens": 47}
    ]
    assert final["delegation_metrics"]["token_usage_status"] == "observed"


def test_record_phase_success_cost_only_prediction_usage_reports_unavailable() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"cost": 0.001, "cached": True}},
    )

    record_phase_success(phase, prediction, 0.0, None, DelegationMetrics())

    final = outputs[-1]
    assert final["observed_lm_usage"] == {"gpt-test": {"cost": 0.001, "cached": True}}
    assert final["token_usage_status"] == "unavailable"


def test_record_phase_success_marks_token_usage_observed_from_prediction_usage() -> None:
    from fleet_rlm.rlm.events import record_phase_success
    from fleet_rlm.rlm.recursion import DelegationMetrics

    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    )

    record_phase_success(phase, prediction, 0.0, None, DelegationMetrics())

    final = outputs[-1]
    assert final["observed_lm_usage"] == {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
    assert final["token_usage_status"] == "observed"


def test_observed_usage_merges_delegation_keys_through_validate_round_trip() -> None:
    from fleet_rlm.rlm.recursion import DelegationMetrics

    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_delegated_input_bytes(512)
    prediction = SimpleNamespace(trajectory=[{"reasoning": "r", "code": "c", "output": "o"}], get_lm_usage=lambda: {})

    usage = observed_usage(
        prediction,
        duration_ms=120,
        delegation={"recursive_call_count": 1, "delegation_metrics": metrics.snapshot().as_dict()},
    )

    assert usage["recursive_call_count"] == 1
    delegation_metrics = usage["delegation_metrics"]
    assert delegation_metrics["delegated_input_bytes"] == 512
    assert delegation_metrics["lm_call_counts"] == [{"role": "root", "recursive_depth": 0, "count": 1}]
    # Full snapshot extras (latency, token status) survive; only the two
    # required keys are normalized.
    assert delegation_metrics["token_usage_status"] == "unavailable"
    assert validate_rlm_usage(dict(usage)) == usage


def test_observed_usage_without_delegation_keeps_historical_shape() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    usage = observed_usage(prediction, duration_ms=50)

    assert set(usage) == {"iterations", "observed_lm_usage", "duration_ms"}
    assert validate_rlm_usage(dict(usage)) == usage


def test_observed_usage_keeps_tracker_tokens_alongside_delegation() -> None:
    prediction = SimpleNamespace(
        trajectory=[],
        get_lm_usage=lambda: {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}},
    )

    usage = observed_usage(
        prediction,
        duration_ms=10,
        delegation={
            "recursive_call_count": 0,
            "delegation_metrics": {"lm_call_counts": [], "delegated_input_bytes": 0},
        },
    )

    assert usage["observed_lm_usage"] == {"gpt-test": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}}
    assert usage["recursive_call_count"] == 0


def test_observed_usage_rejects_non_mapping_delegation() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError):
        observed_usage(prediction, duration_ms=1, delegation="none")  # type: ignore[arg-type]


def test_observed_usage_rejects_unknown_delegation_keys() -> None:
    prediction = SimpleNamespace(trajectory=[], get_lm_usage=lambda: {})

    with pytest.raises(ValueError, match="unsupported keys"):
        observed_usage(prediction, duration_ms=1, delegation={"delegation_metric": {}})


def test_phase_trace_records_parse_repair_diagnostics() -> None:
    """Corrective re-asks must reach the Turn span as bounded adapter diagnostics."""
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(
        phase,
        0.0,
        None,
        None,
        TimeoutError("deadline"),
        repair={"parse_repairs_used": 2},
    )

    assert outputs[-1]["parse_repairs_used"] == 2


def test_phase_trace_omits_parse_repair_diagnostics_when_absent() -> None:
    """A Turn without an adapter must not gain a phantom repair field."""
    outputs: list[dict[str, object]] = []
    phase = SimpleNamespace(set_outputs=outputs.append)

    record_phase_failure(phase, 0.0, None, None, TimeoutError("deadline"))

    assert "parse_repairs_used" not in outputs[-1]


# --- from test_events_observation.py ----------------------------------
@pytest.mark.asyncio
async def test_observation_session_separates_stream_envelopes_from_execution_details() -> None:
    session = ObservationSession(uuid4(), uuid4())

    started = session.record_event(RunStarted(delivery="live"))
    status = session.record_event(Status("execution", "running"))
    detail = session.record(RLMOutput("answer", 1))

    assert [started.sequence, status.sequence, detail.sequence] == [1, 2, 3]
    assert session.details == [RLMOutput("answer", 1)]


# --- Tool Observer Contracts ---


def test_observe_tool_admits_calls_against_the_turn_budget() -> None:
    calls: list[str] = []
    observed: list[Any] = []
    budget = TurnBudget(
        deadline=time.monotonic() + 60,
        limits=BudgetLimits(tool_calls=1),
    )
    wrapped = observe_tool(
        dspy.Tool(lambda query: calls.append(query) or query, name="lookup"),
        observed.append,
        ToolEventView.metadata_only(),
        guards=RunToolGuards(budget=budget),
    )

    assert wrapped.func("first") == "first"
    with pytest.raises(TurnBudgetExhausted):
        wrapped.func("second")

    assert calls == ["first"]
    assert budget.snapshot()[BudgetDimension.TOOL_CALLS.value] == 1
    assert sum(isinstance(item, ToolFailed) for item in observed) == 1


def test_observe_tool_creates_bounded_mlflow_tool_span(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.observability import tracing as turn_tracing

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


def test_observe_tool_keeps_optional_argument_schema_metadata_while_relaxing_outer_validation() -> None:
    def optional(value: int = 3) -> int:
        return value

    source = dspy.Tool(optional, arg_desc={"value": "Optional numeric value"})
    wrapped = observe_tool(source, lambda _event: None, ToolEventView.metadata_only())

    assert wrapped.args["value"]["type"] == "Any"
    assert wrapped.args["value"]["default"] == source.args["value"]["default"] == 3
    assert wrapped.arg_desc == source.arg_desc
    assert wrapped.format_as_litellm_function_call()["function"]["parameters"]["required"] == []
    assert wrapped.func() == 3


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


def test_observe_tool_rejects_non_tools_and_async_results_without_bridge() -> None:
    observed: list[Any] = []

    def plain() -> str:
        return "plain"

    with pytest.raises(TypeError, match=r"dspy.Tool"):
        observe_tool(plain, observed.append, ToolEventView())  # type: ignore[arg-type]

    async def async_tool() -> str:
        await asyncio.sleep(0)
        return "unsupported"

    wrapped = observe_tool(dspy.Tool(async_tool), observed.append, ToolEventView())
    with pytest.raises(RuntimeError, match="persistent async bridge"):
        wrapped()
    assert [type(item) for item in observed] == [ToolStarted, ToolFailed]


@pytest.mark.asyncio
async def test_async_tool_uses_the_composition_bridge_inside_dspy_event_loop() -> None:
    observed: list[Any] = []
    bridge_calls = 0

    class Bridge:
        def run(self, awaitable: Any, **_kwargs: Any) -> Any:
            nonlocal bridge_calls
            bridge_calls += 1
            result: list[Any] = []
            failure: list[Exception] = []

            def resolve() -> None:
                try:
                    result.append(asyncio.run(awaitable))
                except Exception as exc:  # pragma: no cover - assertion below reports it
                    failure.append(exc)

            worker = Thread(target=resolve)
            worker.start()
            worker.join()
            if failure:
                raise failure[0]
            return result[0]

    async def async_tool(value: int) -> dict[str, int]:
        await asyncio.sleep(0)
        return {"value": value}

    wrapped = observe_tool(
        dspy.Tool(async_tool),
        observed.append,
        ToolEventView.metadata_only(),
        async_bridge=Bridge(),
    )

    assert wrapped.func(value=7) == {"value": 7}
    assert bridge_calls == 1
    assert [type(event).__name__ for event in observed] == ["ToolStarted", "ToolCompleted"]


def test_tool_failure_span_carries_closed_cause_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished: list[dict[str, Any]] = []

    class _Span:
        def finish(self, *, phase_status: str, outputs: dict[str, Any] | None = None) -> None:
            finished.append({"phase_status": phase_status, **(outputs or {})})

    monkeypatch.setattr("fleet_rlm.observability.tracing.start_turn_span", lambda *_a, **_k: _Span())
    observed: list[Any] = []

    def batched(prompts: list[str]) -> list[str]:
        if len(prompts) > 1:
            raise RuntimeError(f"LLM call limit exceeded: 0 + {len(prompts)} > 32. Use Python code instead.")
        # An unlabeled opaque credential in an exception must never reach the span.
        raise RuntimeError("upstream rejected AKIAIOSFODNN7EXAMPLEKEY")

    wrapped = observe_tool(dspy.Tool(batched), observed.append, ToolEventView.metadata_only())
    with pytest.raises(RuntimeError):
        wrapped(prompts=["a"] * 40)
    with pytest.raises(RuntimeError):
        wrapped(prompts=["a"])

    budget, opaque = finished[-2], finished[-1]
    assert budget == {
        "phase_status": "failed",
        "tool_status": "failed",
        "failure_category": "tool_error",
        "failure_cause_class": "RuntimeError",
        "failure_detail": "llm_call_limit",
    }
    assert opaque == {
        "phase_status": "failed",
        "tool_status": "failed",
        "failure_category": "tool_error",
        "failure_cause_class": "RuntimeError",
    }
    assert "AKIA" not in str(finished)
    assert observed[-1].error == "Tool failed"


# --- Trajectory Projection & Reconciliation Contracts ---


def test_trajectory_reconciliation_replaces_live_details_without_duplicates() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMCode("stale code", 1),
        RLMOutput("stale output", 1),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
    ]
    assert details == [
        StepStarted(1),
        RLMReasoning("native reasoning", 1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]
    assert (
        reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )


def test_trajectory_reconciliation_inserts_missing_earlier_step_before_later_live_step() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(2),
        RLMReasoning("live reasoning 2", 2),
        RLMCode("live code 2", 2),
        RLMOutput("live output 2", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (
            TrajectoryStep(1, "native reasoning 1", "native code 1", "native output 1"),
            TrajectoryStep(2, "live reasoning 2", "live code 2", "live output 2"),
        ),
        max_chars=100,
    )

    assert details == [
        StepStarted(1),
        RLMReasoning("native reasoning 1", 1),
        RLMCode("native code 1", 1),
        RLMOutput("native output 1", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("live reasoning 2", 2),
        RLMCode("live code 2", 2),
        RLMOutput("live output 2", 2),
        StepFinished(2),
    ]
    assert emissions == [
        StepStarted(1),
        RLMReasoning("native reasoning 1", 1),
        RLMCode("native code 1", 1),
        RLMOutput("native output 1", 1),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_reemits_code_when_a_midrun_iteration_has_no_live_execution() -> None:
    """Live/trajectory misalignment re-emits corrected code under stable step IDs.

    Regression for the live pi-digit proof: when one DSPy iteration yields no live execution
    (malformed model code takes the no-execute path while still appending a trajectory
    entry), later live steps no longer align with trajectory indices. Reconciliation then
    re-emits the corrected same-step code plus the canonical backfill, so the raw SSE stream
    carries MORE code chunks than trajectory steps (4 for 3 here) while distinct steps stay
    bounded by max_iters. Live assertions must count distinct steps, not raw chunks.
    """
    from fleet_rlm.rlm.events import RLMOutput

    # Live observations: iterations 1 and 3 executed (steps 1-2); iteration 2 produced
    # malformed code, so DSPy recorded a trajectory entry without any live execution.
    details = [
        StepStarted(1),
        RLMReasoning("reasoning A", 1),
        RLMCode("code A", 1),
        RLMOutput("output A", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("reasoning C", 2),
        RLMCode("code C", 2),
        RLMOutput("output C", 2),
        StepFinished(2),
    ]
    live_codes = [item for item in details if isinstance(item, RLMCode)]

    emissions = reconcile_trajectory(
        details,
        (
            TrajectoryStep(1, "reasoning A", "code A", "output A"),
            TrajectoryStep(2, "reasoning B", "code B", "output B"),
            TrajectoryStep(3, "reasoning C", "code C", "output C"),
        ),
        max_chars=100,
    )

    emitted_codes = [item for item in emissions if isinstance(item, RLMCode)]
    # One same-step correction (step 2) plus one canonical backfill (step 3).
    assert [(item.code, item.step) for item in emitted_codes] == [("code B", 2), ("code C", 3)]
    # Raw stream emissions exceed the trajectory length by design (2 live + 2 reconcile)...
    assert len(live_codes) + len(emitted_codes) == 4
    # ...while distinct steps stay bounded by the iteration count.
    assert {item.step for item in [*live_codes, *emitted_codes]} == {1, 2, 3}
    assert [(item.code, item.step) for item in details if isinstance(item, RLMCode)] == [
        ("code A", 1),
        ("code B", 2),
        ("code C", 3),
    ]


@pytest.mark.parametrize("preceding_length", [6, 100])
def test_trajectory_reconciliation_folds_terminal_overflow_into_last_executed_step(preceding_length: int) -> None:
    """A terminal DSPy record cannot create an SSE step beyond max_iters."""
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMReasoning("reasoning 1", 1),
        RLMCode("code 1", 1),
        RLMOutput("output 1", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("reasoning 2", 2),
        RLMCode("code 2", 2),
        RLMOutput("output 2", 2),
        StepFinished(2),
        StepStarted(3),
        RLMReasoning("reasoning 3", 3),
        RLMCode("code 3", 3),
        RLMOutput("output 3", 3),
        StepFinished(3),
    ]

    reconcile_trajectory(
        details,
        (
            TrajectoryStep(1, "reasoning 1", "code 1", "output 1"),
            TrajectoryStep(2, "reasoning 2", "code 2", "output 2"),
            TrajectoryStep(
                3, "reasoning 3", "code 3".ljust(preceding_length, "x"), "output 3".ljust(preceding_length, "x")
            ),
            TrajectoryStep(4, "final reasoning", "SUBMIT(answer='ok')", "FINAL: ok"),
        ),
        max_chars=100,
        max_steps=3,
    )

    code = [item for item in details if isinstance(item, RLMCode)]
    output = [item for item in details if isinstance(item, RLMOutput)]
    assert {item.step for item in code} == {1, 2, 3}
    assert "code 3" in code[-1].code
    assert "SUBMIT(answer='ok')" in code[-1].code
    assert "output 3" in output[-1].output
    assert output[-1].output.endswith("\n\nFINAL submitted")
    assert len(code[-1].code) <= 100
    assert len(output[-1].output) <= 100


def test_trajectory_reconciliation_replaces_incremental_output_with_one_canonical_part() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMReasoning("", 1),
        RLMCode("", 1),
        RLMOutput("native ", 1, "output-1", True, False),
        RLMOutput("stale", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "", "", "canonical output"),),
        max_chars=100,
    )

    assert emissions == [RLMOutput("canonical output", 1, "output-1", False, True)]
    assert details == [
        StepStarted(1),
        RLMReasoning("", 1),
        RLMCode("", 1),
        RLMOutput("canonical output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_keeps_live_reasoning_emitted_before_step_started() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        RLMReasoning("native reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    assert (
        reconcile_trajectory(
            details,
            (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
            max_chars=100,
        )
        == []
    )
    assert details == [
        RLMReasoning("native reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_updates_pre_step_live_reasoning_when_canonical_differs() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        RLMReasoning("stale reasoning", 1),
        StepStarted(1),
        RLMCode("native code", 1),
        RLMOutput("native output", 1),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", "native code", "native output"),),
        max_chars=100,
    )

    assert emissions == [RLMReasoning("native reasoning", 1)]
    assert details[0] == RLMReasoning("native reasoning", 1)


@pytest.mark.asyncio
async def test_runner_deduplicates_final_reasoning_against_nonadjacent_normalized_trajectory() -> None:
    from fleet_rlm.rlm.execution import (
        ExecutionRuntime,
        RLMExecutionContext,
        RLMRunner,
        RunIdentity,
        SessionView,
    )
    from fleet_rlm.rlm.program import RLMOptions
    from fleet_rlm.rlm.result import truncate_public_text
    from fleet_rlm.sessions.context import SessionContextManifest
    from fleet_rlm.sessions.models import TurnAccess
    from tests.unit.backend.rlm.fakes import EmptyCapabilities

    repeated = "reasoning requiring public truncation"

    class Factory:
        def create(self, **_kwargs):
            class Program:
                async def acall(self, **_call_kwargs):
                    return dspy.Prediction(
                        answer="done",
                        final_reasoning=repeated,
                        trajectory=[
                            {"reasoning": "first distinct reason", "code": "", "output": ""},
                            {"reasoning": repeated, "code": "", "output": ""},
                        ],
                    )

            return Program()

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=SimpleNamespace(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_output_chars=16),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=None,
            cancellation_requested=not_cancelled,
        ),
        capabilities=EmptyCapabilities(),
    )

    events = [event async for event in RLMRunner(program_builder=Factory().create).stream(context)]

    assert [event.detail.text for event in events if isinstance(event.detail, RLMReasoning)] == [
        truncate_public_text("first distinct reason", max_len=16),
        truncate_public_text(repeated, max_len=16),
    ]


def test_trajectory_reconciliation_silently_upserts_flag_drifted_identical_streams() -> None:
    """RC-4a: live deltas equal to the canonical text emit nothing at turn end."""
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("native out", 1, "output-1", True, False),
        RLMOutput("put", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "why", "print(1)", "native output"),),
        max_chars=100,
    )

    # Identical public payload: no re-emission, but the durable row is
    # upserted to the canonical full-text flags while keeping the live stream.
    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("native output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_treats_submit_label_and_live_terminal_frame_as_identical() -> None:
    """RC-4a: the pre-fix live log (delta + full final frame) reconciles silently."""
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMReasoning("done", 1),
        RLMCode("SUBMIT(answer='ok')", 1),
        RLMOutput("before\n", 1, "output-1", True, False),
        RLMOutput("FINAL submitted", 1, "output-1", False, True),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "done", "SUBMIT(answer='ok')", 'FINAL: {"answer": "ok"}'),),
        max_chars=100,
    )

    # The non-delta FINAL label row restarts the stream projection, so the
    # projected live payload matches the canonical label exactly.
    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMReasoning("done", 1),
        RLMCode("SUBMIT(answer='ok')", 1),
        RLMOutput("FINAL submitted", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_re_emits_once_for_a_true_correction() -> None:
    """RC-4a: corrected text still emits exactly one canonical replacement."""
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("live out", 1, "output-1", True, False),
        RLMOutput("put", 1, "output-1", True, False),
        StepFinished(1),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "why", "print(1)", "corrected output"),),
        max_chars=100,
    )

    assert emissions == [RLMOutput("corrected output", 1, "output-1", False, True)]
    assert details == [
        StepStarted(1),
        RLMReasoning("why", 1),
        RLMCode("print(1)", 1),
        RLMOutput("corrected output", 1, "output-1", False, True),
        StepFinished(1),
    ]


def test_trajectory_reconciliation_aligns_canonical_steps_after_setup_execution() -> None:
    """A context setup execution must not cause a duplicate canonical action stream."""
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMCode("load prepared context", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("native reasoning", 2),
        RLMCode('SUBMIT(answer="ok")', 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", 'SUBMIT(answer="ok")', "FINAL: ok"),),
        max_chars=100,
    )

    assert emissions == []
    assert details == [
        StepStarted(1),
        RLMCode("load prepared context", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("native reasoning", 2),
        RLMCode('SUBMIT(answer="ok")', 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]


def test_trajectory_reconciliation_treats_equivalent_action_formatting_as_the_same_payload() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    live_second = (
        'single_result = llm_query("Return exactly ROOT")\n'
        'batch_results = llm_query_batched(["Return exactly ALPHA", "Return exactly BETA", "Return exactly GAMMA"])\n'
        'print("SECOND_ITERATION_READY")'
    )
    trajectory_second = (
        "single_result = llm_query('Return exactly ROOT')\n"
        "batch_results = llm_query_batched(['Return exactly ALPHA', 'Return exactly BETA', 'Return exactly GAMMA'])\n"
        "print('SECOND_ITERATION_READY')"
    )
    submit = 'summary = "ok"\nSUBMIT(answer=summary, findings=findings)'
    details = [
        StepStarted(1),
        RLMCode(live_second, 1),
        RLMOutput("SECOND_ITERATION_READY", 1),
        StepFinished(1),
        StepStarted(2),
        RLMCode(submit, 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (
            TrajectoryStep(1, "", trajectory_second, "SECOND_ITERATION_READY"),
            TrajectoryStep(2, "", submit, "FINAL: ok"),
        ),
        max_chars=1000,
    )

    assert [item.code for item in emissions if isinstance(item, RLMCode)] == []


def test_trajectory_reconciliation_aligns_normalized_code_after_setup_offset() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    live_action = """```python
single_result = llm_query(\"Return exactly ROOT\")
```"""
    canonical_action = "single_result = llm_query('Return exactly ROOT')"
    details = [
        StepStarted(1),
        RLMCode("load prepared context", 1),
        StepFinished(1),
        StepStarted(2),
        RLMReasoning("native reasoning", 2),
        RLMCode(live_action, 2),
        RLMOutput("READY", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (TrajectoryStep(1, "native reasoning", canonical_action, "READY"),),
        max_chars=100,
    )

    assert emissions == []
    assert details[5] == RLMCode(canonical_action, 2)


def test_trajectory_reconciliation_reemits_earlier_code_correction_after_later_submit() -> None:
    from fleet_rlm.rlm.events import RLMOutput

    details = [
        StepStarted(1),
        RLMCode('single_result = llm_query("Return exactly ROOT")', 1),
        RLMOutput("SECOND_ITERATION_READY", 1),
        StepFinished(1),
        StepStarted(2),
        RLMCode('SUBMIT(answer="ok")', 2),
        RLMOutput("FINAL submitted", 2),
        StepFinished(2),
    ]

    emissions = reconcile_trajectory(
        details,
        (
            TrajectoryStep(
                1, "", 'single_result = llm_query("Reply with exactly: COMPLETE")', "SECOND_ITERATION_READY"
            ),
            TrajectoryStep(2, "", 'SUBMIT(answer="ok")', "FINAL: ok"),
        ),
        max_chars=100,
    )

    assert [item.code for item in emissions if isinstance(item, RLMCode)] == [
        'single_result = llm_query("Reply with exactly: COMPLETE")'
    ]
    assert [item.code for item in details if isinstance(item, RLMCode)] == [
        'single_result = llm_query("Reply with exactly: COMPLETE")',
        'SUBMIT(answer="ok")',
    ]


def test_trajectory_reconciliation_bounds_provider_backfill_steps() -> None:

    details = []
    reconcile_trajectory(
        details,
        (
            TrajectoryStep(1, "one", "one-code", "one-out"),
            TrajectoryStep(2, "two", "two-code", "two-out"),
            TrajectoryStep(4, "backfill", "backfill-code", "backfill-out"),
        ),
        max_chars=500,
        max_steps=2,
    )

    assert {detail.step for detail in details if isinstance(detail, RLMCode)} == {1, 2}
    assert any("backfill-code" in detail.code for detail in details if isinstance(detail, RLMCode) and detail.step == 2)
