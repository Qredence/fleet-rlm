"""Exercise the installed Flex facade through the emulated Daytona transport."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import dspy
import pytest
from test_interpreter import manager_on

from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager


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
