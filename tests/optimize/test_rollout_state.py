"""Offline parity and ownership contracts for native rollout state transport."""

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

import dspy
import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, Route
from fleet_rlm.engine.compiled import program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.optimize import validation
from fleet_rlm.optimize.validation import IsolatedFleetProgram, RolloutCleanup, RolloutCleanupError
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
    return FleetRuntime(
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
            for route in (Route.DIRECT, Route.RLM):
                decision_lm = dspy.utils.DummyLM(
                    [
                        {
                            "route": json.dumps(
                                {
                                    "probabilities": {
                                        item.value: float(item is route) for item in (Route.DIRECT, Route.RLM)
                                    },
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


def test_native_load_failure_does_not_mutate_candidate_or_target():
    candidate, target = _engine().program, _engine().program
    before = deepcopy(candidate.dump_state())
    invalid = deepcopy(before)
    invalid["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    with pytest.raises((SyntaxError, ValueError)):
        target.load_state(invalid)
    assert target.dump_state() == before
    assert candidate.dump_state() == before


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
        assert program.reasoning.flex._interpreter_factory().rlm_defaults == {"max_iters": 16, "max_llm_calls": 7}
        assert program.reasoning.rlm._user_tools["spawn_agent"].func.__self__ is engine.delegation
    assert engines[0].delegation is not engines[1].delegation


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


def test_factory_rejects_wrapper_state_before_allocating_runtime(rollout):
    candidate, example, factory, managers, _cleanup = rollout
    with pytest.raises(TypeError, match="unwrapped FleetProgram candidate"):
        factory(example, IsolatedFleetProgram(candidate, factory))
    assert managers == []


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
