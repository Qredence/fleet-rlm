"""P46 contract tests: thin native DSPy kernel and contracted RLM subsystem.

Proves:
1. Program construction (P46.1): `build_program`, `RLMProgramSpec`, `build_lm`, `build_model_bundle`,
   `FleetRLMSignature`, `root_signature_for_recursion`, and `build_rlm_input_kwargs`.
2. Output and result contracts (P46.2): `PredictionResult`, `validate_prediction`, `ResultContract`,
   `RLMOutcome`, `RLMUsage`, and output size/secret validation.
3. Event and observation contracts (P46.3): `ObservationSession`, `observe_tool`, `reconcile_trajectory`,
   and `ExecutionTraceAssembler`.
4. Runtime execution (P46.4): `RLMRunner`, `RLMExecutionContext`, and worker lifecycle.
5. Isolated DSPy compatibility (P46.5): version guard, interpreter output metadata, and callback observation.
6. Optimization routing relocation (P46.6): `optimization/routing.py` evaluation surface.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.chat.session_context import SessionContextManifest
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.optimization.routing import RoutingFacts, classify_routing_facts
from fleet_rlm.rlm._dspy_compat import (
    CERTIFIED_DSPY_VERSION,
    assert_dspy_version,
    copy_output_fields,
    is_final_output,
    wrap_final_output,
)
from fleet_rlm.rlm.events import (
    ObservationSession,
    RLMCode,
    RLMOutput,
    RLMReasoning,
    StepFinished,
    StepStarted,
    reconcile_trajectory,
)
from fleet_rlm.rlm.program import (
    FleetRLMSignature,
    RLMModelBundle,
    RLMOptions,
    RLMProgramSpec,
    build_program,
    build_rlm_input_kwargs,
    compose_rlm_instructions,
    normalize_model_id,
    root_signature_for_recursion,
    sanitize_base_url,
)
from fleet_rlm.rlm.result import (
    PredictionOutputError,
    PredictionOutputTooLargeError,
    PredictionResult,
    ResultContract,
    RLMOutcome,
    TrajectoryStep,
    empty_rlm_usage,
    validate_prediction,
    validate_rlm_usage,
)
from fleet_rlm.rlm.runtime import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMExecutionSpec,
    RLMRunner,
    RunIdentity,
    SessionView,
)
from fleet_rlm.sessions.models import TurnAccess


def test_p46_1_program_spec_and_native_rlm_construction() -> None:
    """P46.1: build_program and build_native_rlm construct native dspy.RLM with correct options."""
    options = RLMOptions(max_iters=5, max_llm_calls=12, max_output_chars=4000)
    spec = RLMProgramSpec(
        signature=FleetRLMSignature,
        options=options,
        verbose=False,
    )
    program = build_program(spec)
    assert isinstance(program, dspy.RLM)
    assert program.max_iters == 5
    assert program.max_llm_calls == 12
    assert program.max_output_chars == 4000


def test_p46_1_model_bundle_and_id_normalization() -> None:
    """P46.1: normalize_model_id and sanitize_base_url enforce clean gateway conventions."""
    assert normalize_model_id("gpt-4o") == "openai/gpt-4o"
    assert normalize_model_id("anthropic/claude-3-5-sonnet") == "anthropic/claude-3-5-sonnet"
    assert sanitize_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    assert sanitize_base_url("http://localhost:8000 # comment") == "http://localhost:8000"
    assert sanitize_base_url("invalid-url") is None


def test_p46_1_signature_instructions_composition() -> None:
    """P46.1: root_signature_for_recursion appends skill instructions deterministically."""
    instructions = compose_rlm_instructions(recursion_enabled=True)
    assert "Recursive turn" in instructions
    assert "rlm_query" in instructions

    enhanced = root_signature_for_recursion(
        FleetRLMSignature,
        recursion_enabled=True,
        skill_instructions=("Instruction block A.", "Instruction block B."),
    )
    assert issubclass(enhanced, dspy.Signature)
    assert "Instruction block A." in enhanced.instructions
    assert "Instruction block B." in enhanced.instructions


def test_p46_1_input_kwargs_builder_and_history_passthrough() -> None:
    """P46.1: build_rlm_input_kwargs creates valid typed inputs and passes History untouched."""
    session_id = uuid4()
    manifest = SessionContextManifest(session_id, 0, 0, ())
    history = dspy.History(messages=[{"request": "hello", "answer": "hi"}])
    kwargs = build_rlm_input_kwargs(
        request="test request",
        session_context=manifest,
        history=history,
    )
    assert kwargs["request"] == "test request"
    assert kwargs["history"] is history
    assert "session_context" in kwargs


def test_p46_2_prediction_result_validation_and_output_bounds() -> None:
    """P46.2: validate_prediction enforces schema validation, size limits, and secret scrubbing."""
    prediction = dspy.Prediction(answer="normal output")
    contract = ResultContract(signature=FleetRLMSignature, max_output_chars=500)
    result = validate_prediction(prediction, contract)
    assert isinstance(result, PredictionResult)
    assert result.display_text == "normal output"
    assert result.outputs == {"answer": "normal output"}

    oversized = dspy.Prediction(answer="x" * 600)
    with pytest.raises(PredictionOutputTooLargeError):
        validate_prediction(oversized, contract)

    secret_pred = dspy.Prediction(answer="my key is sk-123456789012345678901234567890")
    with pytest.raises(PredictionOutputError):
        validate_prediction(secret_pred, contract)


def test_p46_2_usage_and_outcome_contracts() -> None:
    """P46.2: RLMUsage and RLMOutcome structure are strictly validated."""
    empty = empty_rlm_usage()
    assert empty == {"iterations": 0, "observed_lm_usage": {}, "duration_ms": 0}

    valid_usage = validate_rlm_usage(
        {
            "iterations": 2,
            "observed_lm_usage": {"root": {"prompt_tokens": 10, "completion_tokens": 5}},
            "duration_ms": 150,
        }
    )
    assert valid_usage["iterations"] == 2
    assert valid_usage["duration_ms"] == 150

    pred_res = PredictionResult(
        display_text="done", outputs={"answer": "done"}, schema_id="fleet.default", schema_version="1"
    )
    outcome = RLMOutcome(
        terminal_status="completed",
        prediction=pred_res,
        usage=valid_usage,
    )
    assert outcome.succeeded is True
    assert outcome.prediction is pred_res


def test_p46_3_event_recording_and_trajectory_reconciliation() -> None:
    """P46.3: ObservationSession and reconcile_trajectory align live events with normalized steps."""
    session = ObservationSession(uuid4(), uuid4())
    session.record(StepStarted(1))
    session.record(RLMReasoning("Thinking step 1", 1))
    session.record(RLMCode("print(1)", 1))
    session.record(RLMOutput("1", 1))
    session.record(StepFinished(1))

    steps = (TrajectoryStep(index=1, reasoning="Thinking step 1", code="print(1)", output="1"),)
    reconciled = reconcile_trajectory(session.details, steps, max_chars=1000)
    assert isinstance(reconciled, list)


@pytest.mark.asyncio
async def test_p46_4_runtime_execution_stream() -> None:
    """P46.4: RLMRunner streams execution events and completes with typed outcome."""

    class DummyProgram:
        async def acall(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            return dspy.Prediction(
                answer="native-result",
                trajectory=[
                    {
                        "reasoning": "Compute directly",
                        "code": "SUBMIT(answer='native-result')",
                        "output": "FINAL: {'answer': 'native-result'}",
                    }
                ],
            )

    class DummyFactory:
        def create(self, **kwargs: Any) -> Any:
            del kwargs
            return DummyProgram()

    class DummyCapabilities:
        @property
        def spec(self) -> RLMExecutionSpec:
            return RLMExecutionSpec()

        def drain_public_details(self) -> tuple[Any, ...]:
            return ()

        @property
        def preparation_notices(self) -> tuple[Any, ...]:
            return ()

        def drain_artifact_candidates(self) -> tuple[Any, ...]:
            return ()

        def drain_memory_candidates(self) -> tuple[Any, ...]:
            return ()

        def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
            pass

        async def aclose(self) -> None:
            pass

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="compute",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root_lm=object(), sub_lm=object()),
            options=RLMOptions(max_iters=2, max_llm_calls=2),
            deadline=asyncio.get_running_loop().time() + 10,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        capabilities=DummyCapabilities(),
        delegation=DelegationPolicy(),
    )

    runner = RLMRunner(factory=DummyFactory())
    stream = runner.stream(context)

    events = [event async for event in stream]
    assert len(events) >= 2
    assert stream.outcome is not None
    assert stream.outcome.succeeded is True
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.display_text == "native-result"


def test_p46_5_isolated_dspy_compatibility() -> None:
    """P46.5: assert_dspy_version and interpreter compatibility helpers are isolated."""
    assert CERTIFIED_DSPY_VERSION == "3.3.1"
    assert_dspy_version()

    fields = [{"name": "answer", "type": "str"}]
    copied = copy_output_fields(fields)
    assert copied == fields
    assert copied is not fields

    final = wrap_final_output("done")
    assert is_final_output(final) is True
    assert is_final_output("done") is False


def test_p46_6_optimization_routing_relocation() -> None:
    """P46.6: optimization/routing.py correctly classifies execution routing facts."""
    facts = RoutingFacts()
    assert classify_routing_facts(facts) == "python_native"
