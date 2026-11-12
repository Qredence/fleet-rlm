"""Compiled artifacts, native GEPA, isolated rollout state, and source identity."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import ChatAdapter, JSONAdapter
from dspy.primitives.facade import BOOTSTRAP
from dspy.utils import DummyLM

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, Route
from fleet_rlm.engine.compiled import CompiledProgramFile, program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.optimize import artifacts, validation
from fleet_rlm.optimize.artifacts import component_diffs, tunable_components
from fleet_rlm.optimize.validation import IsolatedFleetProgram, RolloutCleanup, RolloutCleanupError
from fleet_rlm.replay import encode_snapshot
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from tests.optimize.test_optimization import _selected_snapshot

# Compiled artifacts


@pytest.mark.parametrize("kind", ["predictor", "flex"])
def test_component_diff_distinguishes_absence_from_existing_empty(kind):
    from fleet_rlm.optimize.artifacts import component_diffs

    class Program(dspy.Module):
        def __init__(self, present):
            super().__init__()
            if present:
                if kind == "predictor":
                    self.component = dspy.Predict("task -> answer")
                    self.component.signature.instructions = ""
                else:
                    self.component = dspy.Flex("task -> answer")
                    self.component._module_src = ""

    absent, empty = Program(False), Program(True)
    changes = component_diffs(absent, empty)
    assert changes == [{"component": "component", "before": "", "after": "", "diff": ""}]
    assert component_diffs(empty, empty.deepcopy()) == []
    # The existing comparison policy ignores removed candidate keys.
    assert component_diffs(empty, absent) == []


LM = dspy.LM("openai/test", engine="lm15", cache=False)


def engine(name="sess_art", compiled_path=None) -> FleetRuntime:
    return FleetRuntime(DaytonaSandboxManager("key", "org", "volume", name), LM, LM, compiled_path=compiled_path)


def tuned() -> FleetRuntime:
    runtime = engine()
    predictor = runtime.program.routing.predict
    predictor.signature = predictor.signature.with_instructions("TUNED triage")
    return runtime


@pytest.mark.parametrize("saved_from", ["root", "wrapper"])
def test_saved_candidate_loads_into_a_fresh_engine(tmp_path, saved_from):
    candidate = tuned().program
    path = tmp_path / "candidate.json"
    if saved_from == "wrapper":
        wrapped = IsolatedFleetProgram(candidate, None)
        candidate = wrapped.program
    path.write_bytes(program_envelope(candidate))

    fresh = engine("sess_fresh", compiled_path=str(path))

    assert fresh.program.routing.predict.signature.instructions == "TUNED triage"
    assert fresh.compiled.digest


def test_turn_time_reload_picks_up_a_changed_file(tmp_path):
    path = tmp_path / "sess_reload.json"
    path.write_bytes(program_envelope(engine().program))
    live = engine("sess_reload", compiled_path=str(path))
    assert live.program.routing.predict.signature.instructions != "TUNED triage"

    path.write_bytes(program_envelope(tuned().program))
    os.utime(path, ns=(1, 1))  # distinct mtime even on coarse filesystems
    live.compiled.reload_if_changed(live.program)

    assert live.program.routing.predict.signature.instructions == "TUNED triage"


def test_a_symlinked_artifact_is_refused(tmp_path):
    real = tmp_path / "real.json"
    real.write_bytes(program_envelope(engine().program))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="regular file"):
        engine("sess_link", compiled_path=str(link))


def test_exact_native_tree_has_one_owner_per_parameter():
    runtime = engine()
    expected = {
        "routing.predict",
        "skill_selection.predict",
        "reasoning.direct.predict",
        "reasoning.rlm.generate_action",
        "reasoning.rlm.extract",
        "reasoning.flex",
        "child_policies.leaf_action",
        "child_policies.delegating_action",
    }
    parameters = runtime.program.named_parameters()
    assert {name for name, _ in parameters} == expected
    assert len({id(value) for _, value in parameters}) == 8
    assert {name for name, _ in runtime.program.named_predictors()} == expected - {"reasoning.flex"}
    assert not isinstance(runtime, dspy.Module)


@pytest.mark.parametrize(
    "invalid",
    [
        "legacy",
        "wrapped",
        "version",
        "schema",
        "extra",
        "metadata",
        "missing",
        "signature",
        "field_config",
        "unknown_predictor_key",
        "flex",
    ],
)
def test_invalid_artifacts_leave_incumbent_and_digest_unchanged(tmp_path, invalid):
    runtime = tuned()
    path = tmp_path / "selected.json"
    path.write_bytes(program_envelope(runtime.program))
    loader = CompiledProgramFile(str(path))
    loader.load_initial(runtime.program)
    before, digest = deepcopy(runtime.program.dump_state()), loader.digest
    envelope = json.loads(path.read_bytes())
    state = envelope["state"]
    # A valid change before the bad component must not be partially applied.
    state["routing.predict"]["signature"]["instructions"] = "MUST NOT APPLY"
    if invalid == "legacy":
        envelope = state
    elif invalid == "wrapped":
        envelope["state"] = {f"program.{key}": value for key, value in state.items()}
    elif invalid == "version":
        envelope["format_version"] = True
    elif invalid == "schema":
        envelope["program_schema"] = "fleet-program-a1"
    elif invalid == "extra":
        envelope["unknown"] = 1
    elif invalid == "metadata":
        envelope["metadata"] = {"code_digest": 7}
    elif invalid == "missing":
        del state["skill_selection.predict"]
    elif invalid == "signature":
        state["reasoning.rlm.extract"]["signature"]["fields"] = []
    elif invalid == "field_config":
        state["routing.predict"]["fields"] = {"nonexistent": {}}
    elif invalid == "unknown_predictor_key":
        state["reasoning.direct.predict"]["_runtime"] = "override"
    else:
        state["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    path.write_text(json.dumps(envelope))
    with pytest.raises((ValueError, SyntaxError)):
        loader.reload_if_changed(runtime.program)
    assert runtime.program.dump_state() == before and loader.digest == digest


@pytest.mark.parametrize(
    "name",
    [
        "routing.predict",
        "skill_selection.predict",
        "reasoning.direct.predict",
        "reasoning.rlm.generate_action",
        "reasoning.rlm.extract",
        "reasoning.flex",
        "child_policies.leaf_action",
        "child_policies.delegating_action",
    ],
)
def test_no_component_can_install_a_serialized_model(name):
    runtime = engine()
    before = deepcopy(runtime.program.dump_state())
    state = deepcopy(before)
    state[name]["lm"] = dspy.LM("openai/unmetered").dump_state()
    with pytest.raises(ValueError, match="runtime"):
        runtime.program.load_state(state, allow_unsafe_lm_state=True)
    assert runtime.program.dump_state() == before


def test_missing_selected_file_refuses_without_replacing_incumbent(tmp_path):
    path = tmp_path / "selected.json"
    with pytest.raises(FileNotFoundError):
        engine(compiled_path=str(path))
    path.write_bytes(program_envelope(tuned().program))
    runtime = engine(compiled_path=str(path))
    before, digest = deepcopy(runtime.program.dump_state()), runtime.compiled.digest
    path.unlink()
    with pytest.raises(FileNotFoundError):
        runtime.compiled.reload_if_changed(runtime.program)
    assert runtime.program.dump_state() == before and runtime.compiled.digest == digest
    assert engine(compiled_path=None).compiled.digest == ""  # explicit baseline, no silent fallback


def test_loader_checks_bytes_even_when_size_and_mtime_match(tmp_path):
    path = tmp_path / "selected.json"
    path.write_bytes(program_envelope(tuned().program))
    runtime = engine(compiled_path=str(path))
    original = path.stat()
    path.write_bytes(path.read_bytes().replace(b"TUNED triage", b"OTHER triage"))
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    runtime.compiled.reload_if_changed(runtime.program)
    assert runtime.program.routing.predict.signature.instructions == "OTHER triage"


@pytest.mark.parametrize("invalid", ["legacy", "missing-policy", "model"])
def test_approval_refuses_incompatible_state_even_with_matching_digest_and_evidence(tmp_path, invalid):
    from dataclasses import replace

    from fleet_rlm.optimize.artifacts import approve_candidate
    from tests.optimize.test_optimization import _evaluation, _manifest

    program = engine().program
    envelope = json.loads(program_envelope(program))
    if invalid == "legacy":
        envelope = envelope["state"]
    elif invalid == "missing-policy":
        del envelope["state"]["child_policies.leaf_action"]
    else:
        envelope["state"]["reasoning.flex"]["lm"] = dspy.LM("openai/unmetered").dump_state()
    candidate, destination = tmp_path / "candidate.json", tmp_path / "active.json"
    candidate.write_text(json.dumps(envelope))
    evidence = {"evidence_format": "2", "incumbent_digest": ""}
    manifest = replace(_manifest(candidate, "s1-1", _evaluation()), dependencies=evidence)
    before = deepcopy(program.dump_state())
    with pytest.raises(ValueError):
        approve_candidate(candidate, manifest, destination, evidence=evidence, program=program)
    assert not destination.exists() and program.dump_state() == before


# Native GEPA and Flex optimization

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


# Isolated rollout state


class FleetScriptedInterpreter(ScriptedCodeInterpreter):
    def execute(self, code, variables=None):
        result = super().execute(code, variables)
        if "__dspy_run_forward(globals(), 'FleetTaskModule', __dspy_flex_inputs)" in code:
            return json.dumps({"fields": {**json.loads(result)["fields"], "learned_skill_note": ""}})
        return result


@pytest.fixture
def _no_provider(monkeypatch):
    def refuse(*_args, **_kwargs):
        pytest.fail("Offline state tests must not provision a sandbox")

    monkeypatch.setattr(DaytonaSandboxManager, "_ensure_sandbox", refuse)


def _engine(mode="hybrid", jev=None):
    manager = DaytonaSandboxManager("key", "org", "volume", "state-parity", max_llm_calls=7)
    return FleetRuntime(
        manager,
        dspy.utils.DummyLM([{"plan": "Fixed offline response", "answer": "right"}] * 20),
        dspy.LM("openai/sub"),
        jev_lm=jev,
        execution_mode=mode,
    )


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("mode", ["hybrid", "flex", "rlm"])
@pytest.mark.parametrize("typed", [False, True])
def test_native_state_matches_durable_loader_and_preserves_constructor_policy(tmp_path, monkeypatch, mode, typed):
    monkeypatch.setattr(DaytonaSandboxManager, "check_turn", lambda _self: None)
    monkeypatch.setattr(
        DaytonaSandboxManager, "interpreter_factory", lambda *_args, **_kwargs: lambda: FleetScriptedInterpreter(lookup)
    )

    def rlm_forward(self, **inputs):
        assert inputs["execution_route"] == "rlm"
        assert self.max_llm_calls == 7
        return dspy.Prediction(answer="right", learned_skill_note="")

    monkeypatch.setattr(dspy.RLM, "forward", rlm_forward)
    jev = SimpleNamespace(model="typed-fixture") if typed else None
    candidate_runtime = _engine(jev=jev)
    candidate = candidate_runtime.program
    candidate.reasoning.direct.predict.signature = candidate.reasoning.direct.predict.signature.with_instructions(
        "Reviewed answer"
    )
    candidate.reasoning.direct.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    candidate.child_policies.leaf_action.signature = candidate.child_policies.leaf_action.signature.with_instructions(
        "Leaf policy"
    )
    candidate.child_policies.delegating_action.signature = (
        candidate.child_policies.delegating_action.signature.with_instructions("Delegating policy")
    )
    from fleet_rlm.contracts import TRIAGE_CRITERIA

    criteria = deepcopy(TRIAGE_CRITERIA)
    criteria["direct"]["definition"] = "Non-default reviewed criterion"
    candidate.routing.predict.set_criteria("route", criteria)
    candidate.reasoning.flex.load_state({"module_src": REVISED_SOURCE})
    before = deepcopy(candidate.dump_state())

    native_runtime = _engine(mode, jev)
    native = native_runtime.program
    native.load_state(deepcopy(before))
    assert native.dump_state() == before
    assert [name for name, _ in native.named_parameters()] == [name for name, _ in candidate.named_parameters()]
    wrapped = IsolatedFleetProgram(candidate, None)
    assert wrapped.dump_state() == {f"program.{key}": value for key, value in before.items()}
    assert [name for name, _ in wrapped.named_parameters()] == [f"program.{key}" for key in before]

    for program in (candidate, wrapped):
        path = tmp_path / "published.json"
        root = program.program if isinstance(program, IsolatedFleetProgram) else program
        path.write_bytes(program_envelope(root))
        durable_runtime = FleetRuntime(
            native_runtime.sandbox_mgr,
            native_runtime.root_lm,
            native_runtime.sub_lm,
            jev_lm=jev,
            compiled_path=str(path),
            execution_mode=mode,
        )
        assert durable_runtime.program.dump_state() == native.dump_state()
        for runtime in (native_runtime, durable_runtime):
            engine = runtime.program
            for route in Route:
                decision_lm = dspy.utils.DummyLM(
                    [
                        {
                            "route": json.dumps(
                                {
                                    "probabilities": {item.value: float(item is route) for item in Route},
                                    "confidence": 1.0,
                                }
                            )
                        }
                    ]
                )
                with dspy.context(lm=decision_lm, adapter=dspy.ChatAdapter(use_json_adapter_fallback=False)):
                    decision = engine.routing.predict(
                        task="solve fixture", workspace_manifest="", skills_catalog=""
                    ).route
                assert isinstance(decision, engine.routing.predict.signature.fields["route"].annotation)
                assert decision.value == route.value
            for route in Route:
                result = runtime("", "", "solve fixture", "", "", None, route.value)
                assert result.answer == "right" and result.route == route.value

    assert native_runtime.execution_mode == mode
    assert native_runtime.jev_lm is jev
    assert native_runtime._metered_root._lm is native_runtime.root_lm
    assert native_runtime._metered_sub._lm is native_runtime.sub_lm
    assert native.reasoning.rlm.max_llm_calls == 7
    assert native.reasoning.flex._interpreter_factory is not candidate.reasoning.flex._interpreter_factory
    for name in ("routing", "skill_selection"):
        original, restored = getattr(candidate, name).predict, getattr(native, name).predict
        assert restored.signature.dump_state() == original.signature.dump_state()
        assert {key: field.annotation for key, field in restored.signature.fields.items()} == {
            key: field.annotation for key, field in original.signature.fields.items()
        }
    assert native.routing.predict.fields == candidate.routing.predict.fields
    native.reasoning.direct.predict.demos[0]["nested"].append(2)
    assert candidate.dump_state() == before


@pytest.mark.usefixtures("_no_provider")
def test_native_load_keeps_unsafe_lm_state_disabled():
    candidate = _engine().program
    candidate.reasoning.direct.predict.lm = dspy.LM("openai/serialized", api_base="https://untrusted.invalid")
    state = deepcopy(candidate.dump_state())
    restored = _engine()
    root, sub = restored.root_lm, restored.sub_lm
    with pytest.raises(ValueError, match="runtime"):
        restored.program.load_state(state)
    assert restored.root_lm is root and restored.sub_lm is sub
    assert restored.program.reasoning.direct.predict.lm is None


@pytest.mark.usefixtures("_no_provider")
def test_native_load_failure_does_not_mutate_candidate_or_target():
    candidate, target = _engine().program, _engine().program
    before = deepcopy(candidate.dump_state())
    invalid = deepcopy(before)
    invalid["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    with pytest.raises((SyntaxError, ValueError)):
        target.load_state(invalid)
    assert target.dump_state() == before
    assert candidate.dump_state() == before


@pytest.mark.parametrize("threads", [1, 2])
@pytest.mark.parametrize("decision_kind", ["noul", "choice"])
def test_native_reanchor_calibrates_isolated_copies_with_attributed_evidence(threads, decision_kind):
    from dspy.experimental import Choice, Noul, ReAnchor

    is_noul = decision_kind == "noul"
    kind = Noul if is_noul else Choice[("keep", "Keep the eligible task"), ("skip", "Skip the ineligible task")]
    initial_fields = {"threshold": 0.5} if is_noul else {"weights": {"keep": 1.0, "skip": 1.0}}
    expected = True if is_noul else "keep"
    evidence = {"noul": 0.4} if is_noul else {"probabilities": {"keep": 0.4, "skip": 0.6}, "confidence": 0.6}
    signature = dspy.Signature(
        {
            "task": (str, dspy.InputField()),
            "eligible": (kind, dspy.OutputField(desc="Does this task meet the eligibility criteria?")),
        }
    )

    class DecisionProgram(dspy.Module):
        def __init__(self):
            super().__init__()
            self.predict = dspy.Predict(signature)
            self.predict.fields["eligible"] = deepcopy(initial_fields)

        def forward(self, **inputs):
            return self.predict(**inputs)

    class Engine:
        optimization = None

        def __init__(self, candidate):
            self.program = candidate.deepcopy()
            self.sandbox_mgr = SimpleNamespace(shutdown=lambda: True)

        def __call__(self, **inputs):
            return self.program(**inputs)

    student = IsolatedFleetProgram(DecisionProgram(), lambda _example, candidate: Engine(candidate))
    examples = [
        dspy.Example(task=f"eligible task {index}", expected=expected).with_inputs("task") for index in range(5)
    ]
    validation_examples = [
        dspy.Example(task=f"validation task {index}", expected=expected).with_inputs("task") for index in range(5)
    ]
    optimizer = ReAnchor(
        lambda gold, pred: float(pred.eligible.value == gold.expected), num_threads=threads, require_cache=False
    )
    lm = dspy.utils.DummyLM([{"eligible": evidence}] * 1000)
    with dspy.context(lm=lm, adapter=dspy.JSONAdapter(), disable_history=True):
        calibrated = optimizer.compile(student, trainset=examples, valset=validation_examples)
    assert optimizer.report["train_score_before"] == optimizer.report["val_score_before"] == 0.0
    assert optimizer.report["train_score"] == optimizer.report["val_score"] == 1.0
    assert optimizer.report["fitted"][0]["observed"]["calls"] == len(examples)
    assert calibrated.program.predict.fields["eligible"] != initial_fields
    assert student.program.predict.fields["eligible"] == initial_fields


@pytest.fixture
def rollout(monkeypatch, request):
    template = _engine()
    candidate = template.program
    candidate.reasoning.direct.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    candidate.child_policies.leaf_action.demos = [{"reasoning": "leaf demo", "code": "print('leaf')", "nested": [11]}]
    candidate.child_policies.delegating_action.signature = (
        candidate.child_policies.delegating_action.signature.with_instructions("Use reviewed delegated reasoning")
    )
    replay, _, _ = _selected_snapshot("s1", "t1", "fixture", {"files/input.csv": b"fixture"})
    replay["dataset_context"] = {
        "workspace_path": "files/input.csv",
        "size_bytes": 7,
        "sha256": replay["context_files"]["files/input.csv"],
    }
    if getattr(request, "param", False):
        import base64

        from fleet_rlm.skills import captured_required_skill

        raw = b"---\r\nname: recorded\r\ndescription: Immutable source\r\n---\r\nUse exact original text."
        skill = captured_required_skill("recorded", "workspace", (("SKILL.md", raw),))
        replay.update(
            schema_version=2,
            requested_skills=["recorded"],
            required_skills=[{"name": skill.name, "source": skill.source, "sha256": skill.sha256}],
        )
        replay["context_files"]["skills/recorded/SKILL.md"] = hashlib.sha256(raw).hexdigest()
        replay["replay_files"]["skills/recorded/SKILL.md"] = base64.b64encode(raw).decode()
        replay["workspace_manifest"] = ", ".join(f"/workspace/{path}" for path in sorted(replay["context_files"]))
        replay["skill_revision_sha256"] = hashlib.sha256(
            json.dumps(
                {"skills/recorded/SKILL.md": hashlib.sha256(raw).hexdigest()}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    digest, compressed = encode_snapshot(replay)
    example = dspy.Example(
        **{key: replay[key] for key in (*ENGINE_INPUT_FIELDS, "context_files")},
        source_session="s1",
        replay_turn_id="t1",
        replay_snapshot_sha256=digest,
        requested_skills=replay.get("requested_skills", []),
    ).with_inputs(
        *ENGINE_INPUT_FIELDS,
        "context_files",
        "source_session",
        "replay_turn_id",
        "replay_snapshot_sha256",
        "requested_skills",
    )
    managers = []

    class OfflineManager(DaytonaSandboxManager):
        pass

    def manager(**options):
        instance = OfflineManager(**options)
        instance.test_files = {}
        instance.shutdown_calls = 0
        managers.append(instance)
        return instance

    def write(self, path, content):
        self.test_files[path] = content

    def shutdown(self):
        self.shutdown_calls += 1
        return True

    monkeypatch.setattr(validation, "DaytonaSandboxManager", manager)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "stat_workspace_file",
        lambda self, path: {
            "size_bytes": len(self.test_files[path]),
            "sha256": hashlib.sha256(self.test_files[path]).hexdigest(),
        },
    )
    models = {"root": template.root_lm, "sub": template.sub_lm}
    monkeypatch.setattr(validation, "build_lm", lambda _settings, _model_id, role: models[role])
    cleanup = RolloutCleanup()
    factory = validation.build_isolated_engine_factory(
        FleetSettings(
            _env_file=None,
            max_llm_calls_per_turn=7,
            fleet_daytona_api_key="key",
            fleet_daytona_org_id="org",
            fleet_daytona_volume_id="volume",
        ),
        SimpleNamespace(replay_snapshot=lambda *_args: (digest, compressed)),
        cleanup=cleanup,
    )
    return candidate, example, factory, managers, cleanup


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("rollout", [True], indirect=True)
def test_isolated_invocation_uses_recorded_required_instructions_not_deployment_files(rollout):
    candidate, example, factory, managers, cleanup = rollout
    observed = []

    def prepared(item, program):
        runtime = factory(item, program)

        def answer(**inputs):
            observed.append(inputs["skills_catalog"])
            return dspy.Prediction(answer="recorded", learned_skill_note="")

        runtime.program.reasoning.rlm = answer
        return runtime

    result = IsolatedFleetProgram(candidate, prepared, cleanup)(**example.inputs())
    assert result.answer == "recorded" and result.route == "rlm"
    assert len(observed) == 1 and "Use exact original text." in observed[0]
    skill_bytes = managers[0].test_files["skills/recorded/SKILL.md"]
    assert skill_bytes.decode("utf-8") in observed[0]
    assert managers[0].shutdown_calls == 1


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("rollout", [False, True], indirect=True)
def test_concurrent_rollouts_own_state_workspace_and_context_without_transport_files(rollout, monkeypatch):
    candidate, example, factory, managers, _cleanup = rollout
    before = deepcopy(candidate.dump_state())
    rollout_thread = threading.local()

    def guard_transport_files(original):
        def guarded(*args, **kwargs):
            if getattr(rollout_thread, "active", False):
                pytest.fail("Rollout must not save, create or unlink candidate transport files")
            return original(*args, **kwargs)

        return guarded

    # Guard rollout workers, not unrelated MLflow exporter threads writing native trace artifacts.
    for owner, name in ((FleetProgram, "save"), (tempfile, "mkstemp"), (Path, "unlink")):
        monkeypatch.setattr(owner, name, guard_transport_files(getattr(owner, name)))
    barrier = threading.Barrier(2)

    def run(index):
        rollout_thread.active = True
        engine = factory(example, candidate)
        try:
            assert engine.program.dump_state() == before
            barrier.wait(timeout=10)
            program = engine.program
            program.reasoning.direct.predict.demos[0]["nested"].append(index)
            program.reasoning.direct.predict.signature = program.reasoning.direct.predict.signature.with_instructions(
                str(index)
            )
            program.child_policies.leaf_action.demos[0]["nested"].append(index)
            program.reasoning.flex.load_state({"module_src": REVISED_SOURCE + f"\n# rollout {index}\n"})
            engine.sandbox_mgr.write_workspace_file("files/input.csv", str(index).encode())
            return engine
        finally:
            engine.sandbox_mgr.shutdown()

    with ThreadPoolExecutor(max_workers=2) as pool:
        engines = list(pool.map(run, (2, 3)))
    assert candidate.dump_state() == before
    assert len({manager.session_id for manager in managers}) == 2
    assert engines[0].optimization.context is not engines[1].optimization.context
    if example.requested_skills:
        first, second = [engine.optimization.required_skills for engine in engines]
        assert first == second and first is not second and first[0] is not second[0]
        loaded = [
            engine.workspace.load_skills("", [], required_skills=engine.optimization.required_skills)
            for engine in engines
        ]
        assert loaded[0] == loaded[1] and "Use exact original text." in loaded[0]
    for index, engine in zip((2, 3), engines, strict=True):
        program = engine.program
        assert program.reasoning.direct.predict.demos[0]["nested"] == [1, index]
        assert program.child_policies.leaf_action.demos[0]["nested"] == [11, index]
        assert program.child_policies.delegating_action.signature.instructions == "Use reviewed delegated reasoning"
        assert program.reasoning.direct.predict.signature.instructions == str(index)
        assert program.reasoning.flex.module_src.endswith(f"# rollout {index}\n")
        assert engine.sandbox_mgr.test_files["files/input.csv"] == str(index).encode()
        assert engine.sandbox_mgr.shutdown_calls == 1
        assert engine.compiled_path is None
        assert program.reasoning.rlm.max_llm_calls == 7
        assert program.reasoning.flex._max_predictor_calls == 7
        assert program.reasoning.rlm._user_tools["spawn_agent"].func.__self__ is engine.delegation
    assert engines[0].delegation is not engines[1].delegation


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("failure", ["copy", "load", "restore", "execute"])
@pytest.mark.parametrize("cleanup_result", [True, False, "raises"])
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_rollout_failure_has_one_cleanup_owner(rollout, monkeypatch, failure, cleanup_result, error_type):
    candidate, example, factory, managers, cleanup = rollout
    before = deepcopy(candidate.dump_state())
    original = error_type(failure)

    def fail(*_args, **_kwargs):
        raise original

    if failure == "copy":
        monkeypatch.setattr(validation, "deepcopy", fail)
    elif failure == "load":
        monkeypatch.setattr(FleetProgram, "load_state", fail)
    elif failure == "restore":
        monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", fail)
    else:
        monkeypatch.setattr(FleetProgram, "forward", fail)

    def shutdown(self):
        self.shutdown_calls += 1
        if cleanup_result == "raises":
            raise OSError("cleanup unconfirmed")
        return cleanup_result

    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)

    isolated = IsolatedFleetProgram(candidate, factory, cleanup)
    with pytest.raises(error_type, match=failure) as raised:
        isolated(**example.inputs())
    assert raised.value is original
    assert len(managers) == 1 and managers[0].shutdown_calls == 1
    assert candidate.dump_state() == before
    if cleanup_result is True:
        cleanup.raise_if_failed()
    else:
        assert any("cleanup is unconfirmed" in note for note in original.__notes__)
        with pytest.raises(RolloutCleanupError):
            cleanup.raise_if_failed()


@pytest.mark.usefixtures("_no_provider")
def test_factory_rejects_wrapper_state_before_allocating_runtime(rollout):
    candidate, example, factory, managers, _cleanup = rollout
    with pytest.raises(TypeError, match="unwrapped FleetProgram candidate"):
        factory(example, IsolatedFleetProgram(candidate, factory))
    assert managers == []


@pytest.mark.usefixtures("_no_provider")
def test_actual_gepa_result_unwraps_engine_at_factory_boundary(rollout, monkeypatch, tmp_path):
    candidate, example, factory, managers, cleanup = rollout
    seen = []

    def tracked_factory(item, program):
        assert isinstance(program, FleetProgram)
        seen.append(program)
        return factory(item, program)

    monkeypatch.setattr(FleetProgram, "forward", lambda _self, **_kwargs: dspy.Prediction(answer="right"))
    baseline = IsolatedFleetProgram(candidate, tracked_factory, cleanup)
    optimizer = dspy.GEPA(
        metric=lambda *_args: dspy.Prediction(score=1.0, feedback="Reviewed answer"),
        reflection_lm=dspy.utils.DummyLM([]),
        max_metric_calls=1,
        num_threads=1,
        use_merge=False,
        reflection_minibatch_size=1,
    )
    optimized = optimizer.compile(baseline, trainset=[example], valset=[example])
    assert isinstance(optimized, IsolatedFleetProgram)
    assert isinstance(optimized.program, FleetProgram)
    assert optimized.dump_state() == {f"program.{key}": value for key, value in optimized.program.dump_state().items()}
    assert IsolatedFleetProgram(optimized.program, tracked_factory)(**example.inputs()).answer == "right"
    assert seen[-1] is optimized.program
    artifact = tmp_path / "published-candidate.json"
    artifact.write_bytes(program_envelope(optimized.program))
    assert json.loads(artifact.read_text())["state"] == optimized.program.dump_state()
    assert all(manager.shutdown_calls == 1 for manager in managers)


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("cleanup_result", [False, None, "raises"])
@pytest.mark.parametrize("native", ["evaluate", "gepa"])
def test_native_scoring_cannot_clear_cleanup_failure(rollout, monkeypatch, cleanup_result, native):
    candidate, example, factory, managers, cleanup = rollout
    monkeypatch.setattr(FleetProgram, "forward", lambda _self, **_kwargs: dspy.Prediction(answer="right"))

    def shutdown(self):
        self.shutdown_calls += 1
        if cleanup_result == "raises":
            raise OSError("private cleanup details")
        return cleanup_result

    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    baseline = IsolatedFleetProgram(candidate, factory, cleanup)

    def metric(*_args):
        return pytest.fail("An unconfirmed rollout must not reach the metric")

    if native == "evaluate":
        result = validation.score_split(baseline, [example], metric, 1)
        assert result.errors == 1 and result.scores == [0.0]
        assert result.failures[0]["type"] == "RolloutCleanupError"
    else:
        optimized = dspy.GEPA(
            metric=metric,
            reflection_lm=dspy.utils.DummyLM([]),
            max_metric_calls=1,
            num_threads=1,
            use_merge=False,
            reflection_minibatch_size=1,
            gepa_kwargs={"stop_callbacks": [cleanup.should_stop]},
        ).compile(baseline, trainset=[example], valset=[example])
        with pytest.raises(RolloutCleanupError, match="unconfirmed"):
            optimized(**example.inputs())
    assert len(managers) == 1 and managers[0].shutdown_calls == 1
    with pytest.raises(RolloutCleanupError, match="evidence is invalid"):
        cleanup.raise_if_failed()


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("cancel", [False, True])
def test_run_drains_late_cleanup_and_closes_admission_without_masking_cancellation(rollout, monkeypatch, cancel):
    candidate, example, factory, managers, cleanup = rollout
    entered = threading.Barrier(3)
    release = threading.Event()
    closing = threading.Event()
    original = KeyboardInterrupt("stop")

    def forward(_self, **_kwargs):
        entered.wait(timeout=10)
        assert release.wait(timeout=10)
        return dspy.Prediction(answer="right")

    def shutdown(self):
        self.shutdown_calls += 1
        return False

    monkeypatch.setattr(FleetProgram, "forward", forward)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    baseline = IsolatedFleetProgram(candidate, factory, cleanup)

    def finish():
        with cleanup.run():
            closing.set()
            if cancel:
                raise original
            cleanup.close()
            cleanup.raise_if_failed()

    with ThreadPoolExecutor(max_workers=3) as pool:
        attempts = [pool.submit(baseline, **example.inputs()) for _ in range(2)]
        entered.wait(timeout=10)
        finalized = pool.submit(finish)
        assert closing.wait(timeout=10)
        try:
            with pytest.raises(TimeoutError):
                finalized.result(timeout=0.05)
            with pytest.raises(RolloutCleanupError, match="admission is closed"):
                baseline(**example.inputs())
        finally:
            release.set()
        for attempt in attempts:
            with pytest.raises(RolloutCleanupError):
                attempt.result(timeout=10)
        with pytest.raises(KeyboardInterrupt if cancel else RolloutCleanupError) as raised:
            finalized.result(timeout=10)
    if cancel:
        assert raised.value is original
        assert any("cleanup is unconfirmed" in note for note in original.__notes__)
    assert len(managers) == 2
    assert len({manager.session_id for manager in managers}) == 2
    assert all(manager.shutdown_calls == 1 for manager in managers)


@pytest.mark.usefixtures("_no_provider")
@pytest.mark.parametrize("primary", [None, ValueError, KeyboardInterrupt])
def test_sigint_during_drain_waits_for_attempt_and_preserves_primary(monkeypatch, primary):
    cleanup = RolloutCleanup()
    original = primary("primary failure") if primary is not None else None
    interruption = KeyboardInterrupt("SIGINT during drain")
    admitted = threading.Event()
    waiting = threading.Event()
    delivered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    exited = threading.Event()
    wait = cleanup._condition.wait

    def observe_wait(timeout=None):
        waiting.set()
        return wait(timeout)

    # Observe the wait only to deliver the signal at the failing boundary, not to
    # assert an implementation detail. Assertions below concern completion/error identity.
    monkeypatch.setattr(cleanup._condition, "wait", observe_wait)

    def interrupt(_signum, _frame):
        delivered.set()
        raise interruption

    previous_handler = signal.signal(signal.SIGINT, interrupt)

    def attempt():
        with cleanup.attempt():
            admitted.set()
            assert release.wait(timeout=10)
            completed.set()

    def send_signal():
        try:
            assert waiting.wait(timeout=10)
            os.kill(os.getpid(), signal.SIGINT)
            assert delivered.wait(timeout=10)
            return exited.wait(timeout=0.05)
        finally:
            release.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(attempt)
            assert admitted.wait(timeout=10)
            sender = pool.submit(send_signal)
            try:
                with pytest.raises(BaseException) as raised, cleanup.run():
                    if original is not None:
                        raise original
                assert completed.is_set(), "Run must not exit while an admitted attempt is active"
                assert raised.value is (original if original is not None else interruption)
            finally:
                exited.set()
            assert sender.result(timeout=10) is False, "SIGINT must not bypass the drain"
            active.result(timeout=10)
        with pytest.raises(RolloutCleanupError, match="admission is closed"), cleanup.attempt():
            pytest.fail("Closed run must not admit another attempt")
    finally:
        release.set()
        signal.signal(signal.SIGINT, previous_handler)


# Source and dependency identity


@pytest.mark.parametrize(
    "relative",
    [
        "engine/programs.py",
        "engine/children.py",
        "engine/signatures.py",
        "optimize/objectives.py",
        "optimize/validation.py",
        "optimize/new_component.py",
        "sandbox/runtime/repl.py",
        "sandbox/snapshots.py",
        "sandbox/new_bridge.py",
        "sandbox/snapshot-requirements.txt",
        "bundled_skills/example/SKILL.md",
        "bundled_skills/example/references/policy.txt",
        "models.py",
        "config.py",
        "contracts.py",
        "observability.py",
        "execution_evidence.py",
        "replay.py",
        "workspace.py",
        "skills.py",
        "session.py",
        "store.py",
        "optimization_runs.py",
    ],
)
def test_source_identity_covers_mutation_addition_and_deletion(tmp_path, monkeypatch, relative):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    initial = artifacts.optimization_code_digest()
    path = package / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"first behavior")
    added = artifacts.optimization_code_digest()
    assert added != initial
    path.write_bytes(b"second behavior")
    assert artifacts.optimization_code_digest() not in {initial, added}
    path.unlink()
    assert artifacts.optimization_code_digest() == initial


def test_presentation_is_excluded_from_execution_identity(tmp_path, monkeypatch):
    package = tmp_path / "fleet_rlm"
    owner = package / "optimize/artifacts.py"
    owner.parent.mkdir(parents=True)
    owner.write_text("# identity owner\n")
    monkeypatch.setattr(artifacts, "__file__", str(owner))
    before = artifacts.optimization_code_digest()
    for name in ("api/schemas.py", "tui/app.py", "setup.py", "README.md"):
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("presentation-only fixture")
    assert artifacts.optimization_code_digest() == before
