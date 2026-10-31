"""Offline parity and ownership contracts for native rollout state transport."""

import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, Route
from fleet_rlm.engine.programs import AdaptiveFleetEngine
from fleet_rlm.optimize import validation
from fleet_rlm.optimize.validation import IsolatedFleetProgram
from fleet_rlm.replay import encode_snapshot
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from tests.optimize.test_native_gepa import REVISED_SOURCE, ScriptedCodeInterpreter, lookup
from tests.optimize.test_optimization import _selected_snapshot


class FleetScriptedInterpreter(ScriptedCodeInterpreter):
    def execute(self, code, variables=None):
        result = super().execute(code, variables)
        if "__dspy_flex_instance = FleetTaskModule()" in code:
            return json.dumps({**json.loads(result), "learned_skill_note": ""})
        return result


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch):
    def refuse(*_args, **_kwargs):
        pytest.fail("Offline state tests must not provision a sandbox")

    monkeypatch.setattr(DaytonaSandboxManager, "_ensure_sandbox", refuse)


def _engine(mode="hybrid", jev=None):
    manager = DaytonaSandboxManager("key", "org", "volume", "state-parity", max_llm_calls=7)
    return AdaptiveFleetEngine(
        manager,
        dspy.utils.DummyLM([{"reasoning": "Fixed offline response", "answer": "right"}] * 20),
        dspy.LM("openai/sub"),
        jev_lm=jev,
        execution_mode=mode,
    )


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
    candidate = _engine(jev=jev)
    candidate.direct_cot.predict.signature = candidate.direct_cot.predict.signature.with_instructions("Reviewed answer")
    candidate.direct_cot.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    candidate.flex_agent.load_state({"module_src": REVISED_SOURCE})
    before = deepcopy(candidate.dump_state())

    native = _engine(mode, jev)
    native.load_state(deepcopy(before))
    assert native.dump_state() == before
    assert [name for name, _ in native.named_parameters()] == [name for name, _ in candidate.named_parameters()]
    wrapped = IsolatedFleetProgram(candidate, None)
    assert wrapped.dump_state() == {f"program.{key}": value for key, value in before.items()}
    assert [name for name, _ in wrapped.named_parameters()] == [f"program.{key}" for key in before]

    for program in (candidate, wrapped):
        path = tmp_path / "published.json"
        program.save(str(path))
        durable = AdaptiveFleetEngine(
            native.sandbox_mgr, native.root_lm, native.sub_lm, jev_lm=jev, compiled_path=str(path), execution_mode=mode
        )
        assert durable.dump_state() == native.dump_state()
        for engine in (native, durable):
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
                    decision = engine.triage(task="solve fixture", workspace_manifest="", skills_catalog="").route
                assert isinstance(decision, engine.triage.signature.fields["route"].annotation)
                assert decision.value == route.value
                result = engine._execute(route, "", "", "solve fixture", "", "", None)
                assert result.answer == "right" and result.route == route.value

    assert native.execution_mode == mode
    assert native.jev_lm is jev
    assert native._metered_root._lm is native.root_lm
    assert native._metered_sub._lm is native.sub_lm
    assert native.primary_rlm.max_llm_calls == 7
    assert native.flex_agent._interpreter_factory is not candidate.flex_agent._interpreter_factory
    for name in ("triage", "skill_selector"):
        original, restored = getattr(candidate, name), getattr(native, name)
        assert restored.signature.dump_state() == original.signature.dump_state()
        assert {key: field.annotation for key, field in restored.signature.fields.items()} == {
            key: field.annotation for key, field in original.signature.fields.items()
        }
    assert native.triage.fields == candidate.triage.fields
    native.direct_cot.predict.demos[0]["nested"].append(2)
    assert candidate.dump_state() == before


def test_native_load_keeps_unsafe_lm_state_disabled():
    candidate = _engine()
    candidate.direct_cot.predict.lm = dspy.LM("openai/serialized", api_base="https://untrusted.invalid")
    state = deepcopy(candidate.dump_state())
    restored = _engine()
    root, sub = restored.root_lm, restored.sub_lm
    restored.load_state(state)
    assert restored.root_lm is root and restored.sub_lm is sub
    assert restored.direct_cot.predict.lm.model == "openai/serialized"
    assert "api_base" not in restored.direct_cot.predict.lm.kwargs


def test_native_load_failure_does_not_mutate_candidate_or_target():
    candidate, target = _engine(), _engine()
    before = deepcopy(candidate.dump_state())
    invalid = deepcopy(before)
    invalid["flex_agent"]["module_src"] = "class Invalid(:\n"
    with pytest.raises((SyntaxError, ValueError)):
        target.load_state(invalid)
    assert target.dump_state() == before
    assert candidate.dump_state() == before


@pytest.fixture
def rollout(monkeypatch):
    candidate = _engine()
    candidate.direct_cot.predict.demos = [{"task": "fixture", "answer": "right", "nested": [1]}]
    replay, _, _ = _selected_snapshot("s1", "t1", "fixture", {"files/input.csv": b"fixture"})
    replay["dataset_context"] = {
        "workspace_path": "files/input.csv",
        "size_bytes": 7,
        "sha256": replay["context_files"]["files/input.csv"],
    }
    digest, compressed = encode_snapshot(replay)
    example = dspy.Example(
        **{key: replay[key] for key in (*ENGINE_INPUT_FIELDS, "context_files")},
        source_session="s1",
        replay_turn_id="t1",
        replay_snapshot_sha256=digest,
    ).with_inputs(*ENGINE_INPUT_FIELDS, "context_files", "source_session", "replay_turn_id", "replay_snapshot_sha256")
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

    monkeypatch.setattr(validation, "DaytonaSandboxManager", manager)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", shutdown)
    models = candidate.root_lm, candidate.sub_lm, None, candidate.jev_lm
    monkeypatch.setattr(FleetSettings, "build_lms", lambda _self: models)
    factory = validation.build_isolated_engine_factory(
        FleetSettings(
            _env_file=None,
            max_llm_calls_per_turn=7,
            fleet_daytona_api_key="key",
            fleet_daytona_org_id="org",
            fleet_daytona_volume_id="volume",
        ),
        SimpleNamespace(replay_snapshot=lambda *_args: (digest, compressed)),
    )
    return candidate, example, factory, managers


def test_concurrent_rollouts_own_state_workspace_and_context_without_transport_files(rollout, monkeypatch):
    candidate, example, factory, managers = rollout
    before = deepcopy(candidate.dump_state())

    def refuse(*_args, **_kwargs):
        pytest.fail("Rollout must not save, create or unlink candidate transport files")

    monkeypatch.setattr(AdaptiveFleetEngine, "save", refuse)
    monkeypatch.setattr(tempfile, "mkstemp", refuse)
    monkeypatch.setattr(Path, "unlink", refuse)
    barrier = threading.Barrier(2)

    def run(index):
        engine = factory(example, candidate)
        try:
            assert engine.dump_state() == before
            barrier.wait(timeout=10)
            engine.direct_cot.predict.demos[0]["nested"].append(index)
            engine.direct_cot.predict.signature = engine.direct_cot.predict.signature.with_instructions(str(index))
            engine.flex_agent.load_state({"module_src": REVISED_SOURCE + f"\n# rollout {index}\n"})
            engine.sandbox_mgr.write_workspace_file("files/input.csv", str(index).encode())
            return engine
        finally:
            engine.sandbox_mgr.shutdown()

    with ThreadPoolExecutor(max_workers=2) as pool:
        engines = list(pool.map(run, (2, 3)))
    assert candidate.dump_state() == before
    assert len({manager.session_id for manager in managers}) == 2
    assert engines[0].optimization.context is not engines[1].optimization.context
    for index, engine in zip((2, 3), engines, strict=True):
        assert engine.direct_cot.predict.demos[0]["nested"] == [1, index]
        assert engine.direct_cot.predict.signature.instructions == str(index)
        assert engine.flex_agent.module_src.endswith(f"# rollout {index}\n")
        assert engine.sandbox_mgr.test_files["files/input.csv"] == str(index).encode()
        assert engine.sandbox_mgr.shutdown_calls == 1
        assert engine.compiled_path is None
        assert engine.primary_rlm.max_llm_calls == 7
        assert engine.flex_agent._interpreter_factory().rlm_defaults == {"max_iters": 16, "max_llm_calls": 7}


@pytest.mark.parametrize("failure", ["copy", "load", "restore", "execute"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_rollout_failure_has_one_cleanup_owner(rollout, monkeypatch, failure, cleanup_fails):
    candidate, example, factory, managers = rollout
    before = deepcopy(candidate.dump_state())

    def fail(*_args, **_kwargs):
        raise RuntimeError(failure)

    if failure == "copy":
        monkeypatch.setattr(validation, "deepcopy", fail)
    elif failure == "load":
        monkeypatch.setattr(AdaptiveFleetEngine, "load_state", fail)
    elif failure == "restore":
        monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", fail)
    else:
        monkeypatch.setattr(AdaptiveFleetEngine, "forward", fail)

    if cleanup_fails:

        def uncertain(self):
            self.shutdown_calls += 1
            raise OSError("cleanup unconfirmed")

        monkeypatch.setattr(DaytonaSandboxManager, "shutdown", uncertain)

    isolated = IsolatedFleetProgram(candidate, factory)
    with pytest.raises(
        OSError if cleanup_fails else RuntimeError, match="cleanup unconfirmed" if cleanup_fails else failure
    ):
        isolated(**example.inputs())
    assert len(managers) == 1 and managers[0].shutdown_calls == 1
    assert candidate.dump_state() == before


def test_factory_rejects_wrapper_state_before_allocating_runtime(rollout):
    candidate, example, factory, managers = rollout
    with pytest.raises(TypeError, match="Fleet engine candidate"):
        factory(example, IsolatedFleetProgram(candidate, factory))
    assert managers == []


def test_actual_gepa_result_unwraps_engine_at_factory_boundary(rollout, monkeypatch, tmp_path):
    candidate, example, factory, managers = rollout
    seen = []

    def tracked_factory(item, program):
        assert isinstance(program, AdaptiveFleetEngine)
        seen.append(program)
        return factory(item, program)

    monkeypatch.setattr(AdaptiveFleetEngine, "forward", lambda _self, **_kwargs: dspy.Prediction(answer="right"))
    baseline = IsolatedFleetProgram(candidate, tracked_factory)
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
    assert isinstance(optimized.program, AdaptiveFleetEngine)
    assert optimized.dump_state() == {f"program.{key}": value for key, value in optimized.program.dump_state().items()}
    assert IsolatedFleetProgram(optimized.program, tracked_factory)(**example.inputs()).answer == "right"
    assert seen[-1] is optimized.program
    artifact = tmp_path / "published-candidate.json"
    optimized.save(str(artifact))
    assert set(json.loads(artifact.read_text())) == {*optimized.dump_state(), "metadata"}
    assert all(manager.shutdown_calls == 1 for manager in managers)
