"""Offline contract tests for native DSPy GEPA and Flex code optimization."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import ChatAdapter, JSONAdapter
from dspy.primitives.facade import BOOTSTRAP
from dspy.utils import DummyLM

from fleet_rlm.optimize.artifacts import component_diffs, tunable_components
from fleet_rlm.optimize.validation import IsolatedFleetProgram
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter

BASELINE_SOURCE = """\
class FleetTaskModule(dspy.Module):
    def __init__(self):
        super().__init__()

    def forward(self, **inputs):
        return dspy.Prediction(answer="wrong")
"""

REVISED_SOURCE = """\
class FleetTaskModule(dspy.Module):
    def __init__(self):
        super().__init__()

    def forward(self, **inputs):
        return dspy.Prediction(answer=lookup(task=inputs["task"]))
"""


class ScriptedCodeInterpreter(DaytonaInterpreter):
    """Stand in for the remote interpreter; only recognizes fixed test source fixtures."""

    def __init__(self, tool: Any):
        super().__init__(manager=SimpleNamespace(), tools={"lookup": tool})
        self.source: str | None = None

    def start(self) -> None:
        pass

    def execute(self, code: str, variables: dict[str, Any] | None = None) -> Any:
        if code in (BOOTSTRAP, "import dspy"):
            return None
        source = code.strip()
        if source in {BASELINE_SOURCE.strip(), REVISED_SOURCE.strip()}:
            self.source = source
            return None
        if "__dspy_run_forward(globals(), 'FleetTaskModule', __dspy_flex_inputs)" in code:
            if self.source is None:
                raise AssertionError("Flex driver ran before a recognized module source was installed")
            inputs = (variables or {}).get("__dspy_flex_inputs")
            if not isinstance(inputs, dict) or not isinstance(inputs.get("task"), str):
                raise AssertionError("Flex driver did not pass its inputs through __dspy_flex_inputs")
            answer = "wrong" if self.source == BASELINE_SOURCE.strip() else self.tools["lookup"](task=inputs["task"])
            return json.dumps({"fields": {"answer": answer}})
        raise AssertionError(f"Refusing to execute unrecognized interpreter source: {code[:120]!r}")

    def shutdown(self) -> None:
        pass


def lookup(*, task: str) -> str:
    return "right" if task == "solve fixture" else "wrong"


def _flex() -> dspy.Flex:
    flex = dspy.Flex("task -> answer", tools=[lookup], interpreter_factory=lambda: ScriptedCodeInterpreter(lookup))
    # Keep a real Flex baseline source at construction, then use a small, fixed baseline fixture for rollouts.
    flex.load_state({"module_src": BASELINE_SOURCE})
    return flex


class _RolloutEngine(dspy.Module):
    def __init__(self, flex: dspy.Flex):
        super().__init__()
        self.flex_agent = flex
        self.sandbox_mgr = SimpleNamespace(shutdown=lambda: True)
        self.optimization = None

    def forward(self, task: str, **_kwargs: Any) -> dspy.Prediction:
        return self.flex_agent(task=task)


def _factory(example: dspy.Example, candidate: dspy.Module, loaded: list[dspy.Flex]):
    del example
    flex = _flex()
    flex.load_state(candidate.dump_state())
    loaded.append(flex)
    return _RolloutEngine(flex)


@pytest.mark.parametrize("bounded_canary", [False, True])
@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_native_gepa_rewrites_flex_code_with_whole_program_feedback_and_round_trips(
    tmp_path, bounded_canary, adapter_type
) -> None:
    from gepa.utils.stop_condition import MaxCandidateProposalsStopper

    baseline = _flex()
    assert "dspy.RLM" in baseline._baseline_src()
    assert set(tunable_components(baseline)) == {"self"}

    reflection_adapter = adapter_type()
    reflection_lm = DummyLM([{"revised_source": REVISED_SOURCE}] * 8, adapter=reflection_adapter)

    def metric(
        example: dspy.Example,
        prediction: dspy.Prediction,
        _trace: Any = None,
        _pred_name: str | None = None,
        _pred_trace: Any = None,
    ) -> dspy.Prediction:
        passed = prediction.answer == example.answer
        return dspy.Prediction(
            score=1.0 if passed else 0.0,
            feedback=("Answer matches the reviewed result." if passed else "Whole-program answer must be right."),
        )

    gepa = dspy.GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        max_metric_calls=10 if bounded_canary else 5,
        reflection_minibatch_size=1,
        use_merge=False,
        skip_perfect_score=False,
        num_threads=1,
        seed=0,
        track_stats=True,
        gepa_kwargs={"stop_callbacks": [MaxCandidateProposalsStopper(1)]} if bounded_canary else None,
    )
    example = dspy.Example(task="solve fixture", answer="right").with_inputs("task")
    ambient = ChatAdapter(use_json_adapter_fallback=False)
    with dspy.context(adapter=ambient, trace=[]):
        with dspy.context(adapter=reflection_adapter):
            candidate = gepa.compile(baseline, trainset=[example], valset=[example] * (3 if bounded_canary else 1))
        assert dspy.settings.adapter is ambient

    if bounded_canary:
        assert candidate.detailed_results.total_metric_calls == 8

    assert [item["component"] for item in component_diffs(baseline, candidate)] == ["self"]
    assert candidate.module_src.strip() == REVISED_SOURCE.strip()
    reflected = json.dumps(reflection_lm.history, default=str)
    assert "Whole-program answer must be right." in reflected
    assert "wrong" in reflected and "solve fixture" in reflected
    assert "current_source" in reflected and "available_context" in reflected

    loaded_flex: list[dspy.Flex] = []
    isolated = IsolatedFleetProgram(candidate, lambda ex, program: _factory(ex, program, loaded_flex))
    assert isolated(task="solve fixture").answer == "right"
    assert loaded_flex[-1].module_src.strip() == REVISED_SOURCE.strip()

    artifact = tmp_path / "native-gepa.json"
    isolated.save(str(artifact))
    restored = IsolatedFleetProgram(_flex(), lambda ex, program: _factory(ex, program, loaded_flex))
    restored.load(str(artifact))
    assert restored(task="solve fixture").answer == "right"
    assert loaded_flex[-1].module_src.strip() == REVISED_SOURCE.strip()


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_native_gepa_instruction_component_contract(adapter_type) -> None:
    from fleet_rlm.optimize.gepa import INSTRUCTION_MAX_CHARS, build_instruction_proposer

    reflection_adapter = adapter_type()
    proposer = build_instruction_proposer(adapter=reflection_adapter)
    assert proposer.adapter is reflection_adapter
    assert proposer.max_chars == INSTRUCTION_MAX_CHARS
    assert proposer.truncate_history_outputs is True

    student = dspy.Predict("task -> answer")
    student.signature = student.signature.with_instructions("Return a concise answer.")
    # The native proposer uses the reflection role's adapter for its typed field.
    reflection_lm = DummyLM(
        [{"new_instruction": "Return the reviewed answer exactly."}] * 8,
        adapter=reflection_adapter,
    )

    def metric(
        example: dspy.Example,
        prediction: dspy.Prediction,
        _trace: Any = None,
        _pred_name: str | None = None,
        _pred_trace: Any = None,
    ) -> dspy.Prediction:
        passed = prediction.answer == example.answer
        return dspy.Prediction(score=float(passed), feedback="Use the reviewed answer exactly.")

    gepa = dspy.GEPA(
        metric=metric,
        reflection_lm=reflection_lm,
        instruction_proposer=proposer,
        max_metric_calls=4,
        reflection_minibatch_size=1,
        use_merge=False,
        skip_perfect_score=False,
        num_threads=1,
        seed=0,
    )
    example = dspy.Example(task="fixture", answer="right").with_inputs("task")
    serving_lm = DummyLM([{"answer": "wrong"}, {"answer": "wrong"}, {"answer": "right"}, {"answer": "right"}])
    serving_adapter = ChatAdapter(use_json_adapter_fallback=False)
    with dspy.context(lm=serving_lm, adapter=serving_adapter, trace=[]):
        candidate = gepa.compile(student, trainset=[example], valset=[example])
        assert dspy.settings.adapter is serving_adapter
    assert candidate.signature.instructions == "Return the reviewed answer exactly."
    assert student.signature.instructions == "Return a concise answer."


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_instruction_proposal_failure_restores_ambient_adapter(adapter_type):
    from fleet_rlm.optimize.gepa import build_instruction_proposer

    reflection_adapter = adapter_type()
    proposer = build_instruction_proposer(adapter=reflection_adapter)
    ambient = JSONAdapter() if adapter_type is ChatAdapter else ChatAdapter()
    lm = DummyLM([{"unrelated_field": "missing instruction"}] * 4, adapter=reflection_adapter)
    with dspy.context(lm=lm, adapter=ambient), pytest.raises(dspy.AdapterParseError):
        try:
            proposer(
                candidate={"predict": "Answer incorrectly."},
                reflective_dataset={"predict": [{"Inputs": {"task": "fixture"}, "Feedback": "Fix it."}]},
                components_to_update=["predict"],
            )
        finally:
            assert dspy.settings.adapter is ambient


@pytest.mark.parametrize("adapter_type", [ChatAdapter, JSONAdapter])
def test_flex_proposal_parse_failure_keeps_source_and_restores_ambient_adapter(adapter_type):
    from dspy.teleprompt.gepa.gepa_flex_utils import propose_code

    reflection_adapter = adapter_type()
    ambient = JSONAdapter() if adapter_type is ChatAdapter else ChatAdapter()
    lm = DummyLM([{"unrelated_field": "missing revised source"}] * 4, adapter=reflection_adapter)
    candidate = {"self": BASELINE_SOURCE}
    with dspy.context(adapter=ambient):
        with dspy.context(adapter=reflection_adapter):
            result = propose_code(["self"], candidate, {"self": []}, {"self": "task -> answer"}, {}, lm)
        assert dspy.settings.adapter is ambient
    assert result == candidate
    assert lm.history


def test_public_parameter_traversal_finds_predictor_and_flex_together() -> None:
    class MixedProgram(dspy.Module):
        def __init__(self) -> None:
            super().__init__()
            self.route = dspy.Predict("task -> route")
            self.flex_agent = _flex()

    program = MixedProgram()
    components = tunable_components(program)
    assert set(components) == {"route", "flex_agent"}
    assert components["route"] == program.route.signature.instructions
    assert components["flex_agent"] == BASELINE_SOURCE
