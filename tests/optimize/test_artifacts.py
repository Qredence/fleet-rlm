"""Canonical compiled envelopes and explicit baseline selection at the host boundary."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.engine.compiled import CompiledProgramFile, program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.optimize.validation import IsolatedFleetProgram
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


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
