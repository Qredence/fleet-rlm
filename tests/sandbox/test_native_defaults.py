"""Exercise the installed Flex facade through the emulated Daytona transport."""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest
from test_interpreter import manager_on

from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager


@pytest.mark.parametrize("route", ["rlm", "flex"])
def test_child_policies_reconstruct_and_reach_native_gepa_through_parallel_host_calls(tmp_path, monkeypatch, route):
    """Use real DSPy/Fleet/guest Python; fake only the model and Daytona provider."""
    from dspy.teleprompt.gepa.gepa_utils import DspyAdapter
    from emulator import emulated_sandbox

    from fleet_rlm.engine.compiled import CompiledProgramFile, program_envelope
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.optimize.artifacts import tunable_components
    from fleet_rlm.optimize.validation import IsolatedFleetProgram

    root_code = """leaf = spawn_agent(subtask='solo', allowed_tools=[], max_iters=1, max_llm_calls=1)
if 'error' in leaf:
    SUBMIT(answer=leaf['error'], learned_skill_note='')
batch = spawn_agents(subtasks=['alpha', 'beta'], max_iters=2, max_llm_calls=2)
assert all('error' not in item for item in [leaf, *batch]), [leaf, *batch]
SUBMIT(answer='|'.join(item['findings'] for item in [leaf, *batch]), learned_skill_note='')"""
    rendezvous = threading.Barrier(2, timeout=10)
    calls, managers, sandboxes = [], [], []
    malformed_leaf = False

    class PolicyLM(dspy.utils.DummyLM):
        def __call__(self, *, messages, **_kwargs):
            system = messages[0]["content"]
            if "Fleet child role: leaf_action." in system:
                if malformed_leaf:
                    return ["malformed child action"]
                role = "leaf"
                code = "assert 'spawn_agent' not in globals()\nassert 'max_iters=1' in subtask\n"
            elif "Fleet child role: delegating_action." in system:
                role = "delegating"
                rendezvous.wait()
                code = "assert callable(spawn_agent)\nassert 'max_llm_calls=2' in subtask\n"
            else:
                role, code = "root", root_code
                assert "2/16" not in messages[-1]["content"], messages[-1]["content"]
            if role != "root":
                version = "new" if "CHILD_POLICY_NEW" in system else "old"
                code += "assert 'private_child_value' not in globals()\nprivate_child_value = subtask\n"
                code += f"SUBMIT(findings={role + '-' + version!r} + ':' + subtask.split('\\n\\n')[-1])"
            calls.append(role)
            return [self._format_answer_fields({"reasoning": "offline policy-conditioned response", "code": code})]

    lm = PolicyLM([])

    def manager(name, parent=None):
        sandbox = emulated_sandbox(tmp_path / name)
        sandbox.id = name
        sandboxes.append(sandbox)
        result = manager_on(sandbox, name=name)
        result.parent = parent
        result._client = SimpleNamespace(
            stop=lambda *_args, **_kwargs: sandbox.code_interpreter.close(),
            delete=lambda *_args, **_kwargs: sandbox.code_interpreter.close(),
        )
        managers.append(result)
        return result

    def allocate(parent, **_kwargs):
        return manager(f"child-{uuid.uuid4().hex}", parent)

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate)
    monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])

    def engine():
        return FleetRuntime(manager(f"root-{len(managers)}"), lm, lm, execution_mode=route)

    candidate = engine().program
    for policy in (candidate.child_policies.leaf_action, candidate.child_policies.delegating_action):
        # Equal instructions must not cause cross-role attribution.
        policy.signature = policy.signature.with_instructions("CHILD_POLICY_OLD")
    artifact = tmp_path / "candidate.json"
    artifact.write_bytes(program_envelope(candidate))
    restored = engine()
    restored.compiled = CompiledProgramFile(str(artifact))
    restored.compiled.load_initial(restored.program)
    assert restored.program.dump_state() == candidate.dump_state()
    loaded = []

    def reconstruct(_example, program):
        instance = engine()
        instance.program.load_state(deepcopy(program.dump_state()))
        loaded.append(instance)
        return instance

    isolated = IsolatedFleetProgram(restored.program, reconstruct)
    names = ["program.child_policies.leaf_action", "program.child_policies.delegating_action"]
    expected = "leaf-old:solo|delegating-old:alpha|delegating-old:beta"

    def metric(_gold, prediction, _trace=None, _pred_name=None, _pred_trace=None):
        return dspy.Prediction(score=float(prediction.answer == expected), feedback="Inspect the child action policy.")

    def feedback(**kwargs):
        return dspy.Prediction(score=1.0, feedback=kwargs["predictor_output"].code)

    adapter = DspyAdapter(isolated, metric, dict.fromkeys(names, feedback), num_threads=1)
    example = dspy.Example(task="delegate", workspace_manifest="", memory_summary="").with_inputs(
        "task", "workspace_manifest", "memory_summary"
    )
    try:
        texts = tunable_components(isolated)
        evaluation = adapter.evaluate([example], texts, capture_traces=True)
        assert evaluation.outputs[0].answer == expected
        assert calls.count("root") == 1 and calls.count("leaf") == 1 and calls.count("delegating") == 2
        reflected = adapter.make_reflective_dataset(texts, evaluation, names)
        assert set(reflected) == set(names)
        assert "leaf-old" in reflected[names[0]][0]["Feedback"]
        assert "delegating-old" in reflected[names[1]][0]["Feedback"]
        assert "delegating-old" not in reflected[names[0]][0]["Feedback"]
        assert "leaf-old" not in reflected[names[1]][0]["Feedback"]
        # A native GEPA candidate mutation must reach only its named reconstructed child.
        texts[names[0]] = "CHILD_POLICY_NEW"
        changed = adapter.evaluate([example], texts, capture_traces=True)
        assert changed.outputs[0].answer == "leaf-new:solo|delegating-old:alpha|delegating-old:beta"
        assert calls.count("root") == 2 and calls.count("leaf") == 2 and calls.count("delegating") == 4
        assert restored.program.child_policies.leaf_action.signature.instructions == "CHILD_POLICY_OLD"
        assert len(loaded) == 2
        assert len({id(instance.program.child_policies.leaf_action) for instance in loaded}) == 2
        # Ordinary child parse failures remain tool results. Native GEPA cannot
        # reflect on a failed child step swallowed by that existing boundary.
        malformed_leaf = True
        failed = adapter.evaluate([example], texts, capture_traces=True)
        assert "AdapterParseError" in failed.outputs[0].answer
        with pytest.raises(Exception, match="No valid predictions found for any module"):
            adapter.make_reflective_dataset(texts, failed, names)
        assert len({id(instance) for instance in sandboxes}) == len(sandboxes)
        assert all(not sandbox.code_interpreter.contexts for sandbox in sandboxes)
        assert all(not instance._contexts and not instance._interpreters for instance in managers)
    finally:
        for instance in managers:
            instance.shutdown()
        for sandbox in sandboxes:
            sandbox.code_interpreter.close()


@pytest.mark.parametrize(
    "imports,constructor,arguments,expected",
    [
        ("", "dspy.RLM", "", (16, 150)),
        ("", "dspy.RLM", ", max_iters=3", (3, 150)),
        ("", "dspy.RLM", ", max_iters=3, max_llm_calls=7", (3, 7)),
        ("", "dspy.RLM", ", **{'max_llm_calls': 7}", (16, 7)),
        ("import dspy as dsp", "dsp.RLM", "", (16, 150)),
        ("from dspy import RLM as Recursive", "Recursive", "", (16, 150)),
        ("", "dspy.RLM", ", max_iters=None, max_llm_calls=None", (None, None)),
    ],
)
def test_native_flex_constructors_preserve_source(
    sandbox, monkeypatch, tmp_path, imports, constructor, arguments, expected
):
    manager = manager_on(sandbox)
    constructed = []
    invoke = DaytonaInterpreter._invoke_tool

    def observe(self, name, args, kwargs):
        value = invoke(self, name, args, kwargs)
        if name == "__dspy_construct__":
            native = self.tools[name].__self__._predictors[value]
            constructed.append(native)
        return value

    monkeypatch.setattr(DaytonaInterpreter, "_invoke_tool", observe)
    source = f'''{imports}
class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.first = {constructor}("task -> answer"{arguments})
        self.second = dspy.RLM("task -> answer")
        self.predict = dspy.Predict("task -> answer")
    def forward(self, **inputs):
        return dspy.Prediction(answer="ok")
'''
    flex = dspy.Flex(
        "task -> answer",
        interpreter_factory=manager.interpreter_factory(rlm_defaults={"max_iters": 16, "max_llm_calls": 150}),
    )
    flex.load_state({"module_src": source})
    assert flex.module_src == source
    assert flex(task="constructor fixture").answer == "ok"
    assert (constructed[0].max_iters, constructed[0].max_llm_calls) == expected
    assert (constructed[1].max_iters, constructed[1].max_llm_calls) == (16, 150)
    assert isinstance(constructed[2], dspy.Predict)
    path = tmp_path / "native.json"
    flex.save(str(path))
    assert json.loads(path.read_text())["module_src"] == source
    flex.load(str(path))
    assert flex.module_src == source
    assert flex(task="reload fixture").answer == "ok"
    assert flex.module_src == source
    assert not manager._contexts and not sandbox.code_interpreter.contexts


def test_default_maps_are_copied_and_do_not_propagate(monkeypatch):
    root = DaytonaSandboxManager("key", "org", "vol", "scope")
    defaults = {"max_iters": 16, "max_llm_calls": 150}
    factory = root.interpreter_factory(rlm_defaults=defaults)
    defaults["max_iters"] = 99
    first, second = factory(), factory()
    first.rlm_defaults["max_iters"] = 1
    assert second.rlm_defaults["max_iters"] == 16
    assert root.interpreter_factory()().rlm_defaults == {}
    child = DaytonaSandboxManager("key", "org", "vol", "child")
    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", lambda _self: child)
    token = _ACTIVE_MANAGER.set(root)
    try:
        nested = factory()
    finally:
        _ACTIVE_MANAGER.reset(token)
    assert nested.manager is child and nested.rlm_defaults == {}


@pytest.mark.parametrize("kwargs", [None, {}, [], "malformed", {"max_iters": None}, {"max_llm_calls": 4}])
def test_native_dispatch_retains_payload_validation(kwargs):
    from dspy.primitives.facade import FacadeInvocation

    manager = SimpleNamespace(check_turn=lambda: None)
    native = FacadeInvocation({}, lambda: None, None)
    interpreter = DaytonaInterpreter(manager, rlm_defaults={"max_iters": 16, "max_llm_calls": 150})
    interpreter.tools["__dspy_construct__"] = native.construct
    payload = {"kind": "RLM", "signature": "task -> answer", "attr_name": "rlm", "kwargs": kwargs}
    original = json.loads(json.dumps(payload))
    if kwargs and not isinstance(kwargs, dict):
        with pytest.raises((AttributeError, TypeError)):
            interpreter._invoke_tool("__dspy_construct__", [], payload)
    else:
        interpreter._invoke_tool("__dspy_construct__", [], payload)
        module = native._predictors["rlm"]
        defaults = (20, 50) if kwargs == [] else (16, 150)
        assert module.max_iters == (kwargs.get("max_iters", 16) if kwargs else defaults[0])
        assert module.max_llm_calls == (kwargs.get("max_llm_calls", 150) if kwargs else defaults[1])
    assert payload == original


def test_native_nested_rlm_keeps_host_aggregate_budget(sandbox, monkeypatch):
    from dspy.primitives.facade import SandboxLM

    manager = manager_on(sandbox)
    manager.max_llm_calls = 1
    children = []

    def allocate(_self):
        child = manager_on(sandbox, name="native-nested")
        children.append(child)
        return child

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate)
    monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])
    lm = dspy.utils.DummyLM([{"reasoning": "", "code": "llm_query('consume another unit')"}])
    source = """class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.rlm = dspy.RLM("task -> answer", max_llm_calls=999)
    def forward(self, **inputs):
        return self.rlm(**inputs)
"""
    flex = dspy.Flex(
        "task -> answer",
        interpreter_factory=manager.interpreter_factory(rlm_defaults={"max_iters": 2, "max_llm_calls": 150}),
    )
    flex.load_state({"module_src": source})
    with (
        dspy.context(lm=SandboxLM(lm, manager.consume_budget)),
        pytest.raises(dspy.CodeExecutionError, match="Shared RLM budget exhausted"),
    ):
        flex(task="budget fixture")
    assert manager.turn_usage()["lm_calls"] == 1
    assert children and all(not child._contexts for child in children)
    assert not manager._contexts and not sandbox.code_interpreter.contexts


def test_snapshot_recipe_digest_survives_resource_move():
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, LOCK_PATH

    assert DEFAULT_DEFINITION.digest() == "a5b63e62a03839b8a2fd4182feb09fa9478e2a2097f9074e0194cb4315d574a5"
    assert Path(__file__).parents[2] / "src/fleet_rlm/sandbox/snapshot-requirements.txt" == LOCK_PATH
