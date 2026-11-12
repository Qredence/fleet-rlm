"""Native DSPy programs, adapters, signatures, routing, and pinned capability contracts."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import AdapterParseError, LMUnexpectedError
from dspy.primitives.facade import SandboxLM

from fleet_rlm.api.events import _reasoning_text
from fleet_rlm.config import FleetSettings
from fleet_rlm.contracts import (
    ENGINE_INPUT_FIELDS,
    JUDGE_ROUTE_CHOICES,
    LINEAGE_INPUT_FIELDS,
    ROUTE_DESCRIPTIONS,
    ROUTE_SUMMARIES,
    STOPPING_RULES,
    TRIAGE_CRITERIA,
    TRIAGE_INSTRUCTION,
    TRIAGE_ROUTE,
    OptimizationInputs,
    Route,
    catalog_entries,
    item_decision,
    make_noul_type,
    parse_route,
    supports_decision_requests,
)
from fleet_rlm.engine import signatures
from fleet_rlm.engine.compiled import (
    PROGRAM_SCHEMA,
    load_program_envelope,
    program_envelope,
)
from fleet_rlm.engine.decisions import select_route, select_skills
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.engine.reasoning import (
    _MAX_RLM_HISTORY_EVENTS,
    deserialize_rlm_history,
    rlm_history_processor,
    serialize_rlm_turn_history,
)
from fleet_rlm.engine.signatures import FleetTask, TurnTriage
from fleet_rlm.models import ResolvedModel, build_lm
from fleet_rlm.optimize.artifacts import tunable_components
from fleet_rlm.optimize.dataset import _INPUTS
from fleet_rlm.optimize.gepa import build_instruction_proposer
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.workspace import (
    _ALLOWED_WORKSPACE_ROOTS,
    MAX_WORKSPACE_FILE_BYTES,
    WORKSPACE_CODE,
)

# Native program graph


def _engine(manager: DaytonaSandboxManager, **kwargs: Any) -> FleetRuntime:
    return FleetRuntime(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False),
        **kwargs,
    )


def test_engine_forward_inputs_match_the_shared_contract():
    """`contracts.ENGINE_INPUT_FIELDS` is the literal rollout whitelist; forward must not drift from it."""
    import inspect

    parameters = set(inspect.signature(FleetProgram.forward).parameters)
    assert set(ENGINE_INPUT_FIELDS) <= parameters
    assert parameters - set(ENGINE_INPUT_FIELDS) == {
        "self",
        "context",
        "execution_mode",
        "required_skills",
        "selected_route",
        "rlm_history",
    }


def test_invalid_compiled_program_leaves_the_running_program_unchanged(tmp_path: Path) -> None:
    compiled_path = tmp_path / "session-a.json"
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
        compiled_path=str(compiled_path),
        baseline=True,
    )
    compiled_path.write_bytes(program_envelope(engine.program))
    known_good = compiled_path.read_bytes()
    corrupt = json.loads(known_good)
    corrupt["state"]["reasoning.flex"]["module_src"] = "class Invalid(:\n"
    compiled_path.write_text(json.dumps(corrupt))

    with pytest.raises((KeyError, SyntaxError, ValueError)):
        engine.compiled.reload_if_changed(engine.program)

    assert engine.program.reasoning.flex.module_src == json.loads(known_good)["state"]["reasoning.flex"]["module_src"]


@pytest.mark.parametrize("schema", ["fleet-program-a1", "fleet-program-b1", "unknown"])
def test_incompatible_selected_program_refuses_without_mutating_or_selecting_baseline(tmp_path, schema):
    manager = DaytonaSandboxManager("key", "org", "volume", "schema-migration")
    path = tmp_path / "selected.json"
    engine = _engine(manager, compiled_path=str(path), baseline=True)
    content = json.loads(program_envelope(engine.program))
    assert content["program_schema"] == "fleet-program-b2"
    path.write_text(json.dumps(content))
    engine.compiled.reload_if_changed(engine.program)
    before = deepcopy(engine.program.dump_state())
    content["program_schema"] = schema
    content["state"]["routing.predict"]["signature"]["instructions"] = "Obsolete policy"
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="fleet-program-b2"):
        engine.compiled.reload_if_changed(engine.program)
    assert engine.program.dump_state() == before
    path.unlink()
    with pytest.raises(FileNotFoundError):
        engine.compiled.reload_if_changed(engine.program)


def test_compiled_program_path_is_not_shared_by_default() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
    )

    assert engine.compiled_path is None


def test_route_selection_logs_specific_exception_without_exposing_credentials(caplog: pytest.LogCaptureFixture) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
        jev_lm=SimpleNamespace(api_key="typesafe-test-token"),
    )

    class FailingTriage:
        def __call__(self, **_kwargs: Any) -> Any:
            raise RuntimeError("403: invalid token typesafe-test-token")

    engine.program.routing.predict = FailingTriage()  # type: ignore[assignment]
    engine.program.reasoning.rlm = lambda **_kwargs: SimpleNamespace(  # type: ignore[assignment]
        answer="bounded fallback",
        learned_skill_note="",
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.engine.decisions"):
        prediction = engine(
            workspace_manifest="",
            memory_summary="",
            task="test fallback",
        )

    assert prediction.route == "rlm"
    assert prediction.answer == "bounded fallback"
    assert "RuntimeError" in caplog.text
    assert "403: invalid token" in caplog.text
    assert "typesafe-test-token" not in caplog.text


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "mode,label,expected",
    [
        ("hybrid", "direct", "direct"),
        ("hybrid", "rlm", "rlm"),
        ("hybrid", "flex", "flex"),
        ("hybrid", "obsolete", "rlm"),
        ("hybrid", None, "rlm"),
        ("direct", "flex", "direct"),
        ("flex", "direct", "flex"),
        ("rlm", "direct", "rlm"),
    ],
)
def test_automatic_routes_are_three_way_and_explicit_modes_override(monkeypatch, typed, mode, label, expected):
    manager = DaytonaSandboxManager("key", "org", "volume", "route-contract")
    events, predictions = [], []
    manager.event_callback = events.append
    engine = _engine(
        manager, jev_lm=SimpleNamespace(model="typesafe:typed", supports_decision_requests=True) if typed else None
    )
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])

    def predict(**_kwargs):
        predictions.append(label)
        return SimpleNamespace(route=label)

    def reason(**_kwargs):
        manager.consume_budget(category="generation_query")
        return dspy.Prediction(answer="verified lane", learned_skill_note="")

    engine.program.routing.predict = predict
    for route in ("direct", "flex", "rlm"):
        setattr(engine.program.reasoning, route, reason)
    result = engine("", "", "route fixture", execution_mode=mode)
    assert result.route == expected and result.answer == "verified lane"
    assert predictions == ([label] if mode == "hybrid" else [])
    assert result.lm_calls == 1 + int(typed and mode == "hybrid")
    evidence = next(event["evidence"] for event in events if event["event"] == "triage_decision")
    assert evidence["raw_choice"] == (label if mode == "hybrid" else None)
    assert evidence["confidence"] is None and evidence["probabilities"] is None


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize(
    "failure",
    [TimeoutError("deadline"), RuntimeError("Turn cancelled"), RuntimeError("Shared RLM budget exhausted")],
)
def test_routing_does_not_convert_terminal_failures_to_fallback(typed, failure):
    manager = DaytonaSandboxManager("key", "org", "volume", "terminal-route")
    engine = _engine(manager, jev_lm=SimpleNamespace(model="typed") if typed else None)

    def predict(**_kwargs):
        raise failure

    engine.program.routing.predict = predict
    adapter = dspy.JSONAdapter()
    with dspy.context(adapter=adapter):
        with pytest.raises(type(failure)) as caught:
            engine.program.routing("task", "", "", "hybrid", False)
        assert caught.value is failure
        assert dspy.settings.adapter is adapter


def test_a_started_deadline_free_turn_keeps_its_ownership(monkeypatch) -> None:
    """A Turn the API already started keeps its budget and teardown; the engine's scope is inert."""
    engine = _answering_engine()
    manager = engine.sandbox_mgr
    manager.reset_turn_budget(deadline_seconds=None, deadline_source="request")
    calls = {"reset": 0, "end": 0}
    original_reset = DaytonaSandboxManager.reset_turn_budget
    original_end = DaytonaSandboxManager.end_turn

    def counting_reset(self, **kwargs):
        calls["reset"] += 1
        return original_reset(self, **kwargs)

    def counting_end(self):
        calls["end"] += 1
        return original_end(self)

    monkeypatch.setattr(DaytonaSandboxManager, "reset_turn_budget", counting_reset)
    monkeypatch.setattr(DaytonaSandboxManager, "end_turn", counting_end)

    try:
        prediction = engine(workspace_manifest="", memory_summary="", task="compute")
    finally:
        original_end(manager)

    assert prediction.answer == "computed"
    assert calls == {"reset": 0, "end": 0}


def _answering_engine() -> FleetRuntime:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
        execution_mode="rlm",
    )
    engine.program.reasoning.rlm = lambda **_kwargs: SimpleNamespace(  # type: ignore[assignment]
        answer="computed", learned_skill_note=""
    )
    return engine


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("isolated", [False, True])
def test_shared_invocation_preserves_native_callbacks_and_overwrites_candidate_receipts(monkeypatch, failure, isolated):
    from dspy.utils import BaseCallback

    from fleet_rlm.optimize.validation import IsolatedFleetProgram

    runtime = _answering_engine()
    observed = []

    class Callback(BaseCallback):
        def on_module_start(self, call_id, instance, inputs):
            assert call_id
            if isinstance(instance, FleetProgram):
                observed.append(inputs["kwargs"]["task"])

    def learned(task, **_inputs):
        assert task == "callback fixture"
        if failure:
            raise ValueError("candidate failed")
        return dspy.Prediction(answer="computed", lm_calls=999, execution_evidence={"forged": True})

    monkeypatch.setattr(runtime.program, "forward", learned)
    monkeypatch.setattr(DaytonaSandboxManager, "turn_usage", lambda _self: {"lm_calls": 3})
    monkeypatch.setattr(DaytonaSandboxManager, "execution_evidence", lambda _self: {"receipt": "host"})
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: True)
    entry = IsolatedFleetProgram(runtime.program, lambda *_args: runtime) if isolated else runtime
    callback, ambient_adapter = Callback(), dspy.JSONAdapter()
    with dspy.context(callbacks=[callback], adapter=ambient_adapter):
        if failure:
            with pytest.raises(ValueError, match="candidate failed"):
                entry(workspace_manifest="", memory_summary="", task="callback fixture")
        else:
            result = entry(workspace_manifest="", memory_summary="", task="callback fixture")
            assert result.answer == "computed" and result.lm_calls == 3
            assert result.execution_evidence == {"receipt": "host", "python": {}}
        assert dspy.settings.callbacks == [callback] and dspy.settings.adapter is ambient_adapter
    assert observed == ["callback fixture"]


@pytest.mark.parametrize("changed", [False, True])
def test_dataset_revision_check_uses_remote_stat_before_execution(monkeypatch, changed):
    from fleet_rlm.workspace import WorkspaceDataset

    engine = _answering_engine()
    inspected = []

    def metadata(_self, path):
        inspected.append(path)
        return {"size_bytes": 8, "sha256": ("b" if changed else "a") * 64}

    monkeypatch.setattr(DaytonaSandboxManager, "stat_workspace_file", metadata)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args: pytest.fail("no dataset download"))
    dataset = WorkspaceDataset("files/selected.csv", size_bytes=8, sha256="a" * 64)
    try:
        if changed:
            with pytest.raises(ValueError, match="Dataset source changed"):
                engine("", "", "read", "", "", dataset, "rlm")
        else:
            assert engine("", "", "read", "", "", dataset, "rlm").answer == "computed"
        assert inspected == ["files/selected.csv"]
    finally:
        engine.sandbox_mgr.end_turn()


def test_evidence_collection_failure_keeps_the_answer_and_marks_evidence_incomplete(monkeypatch) -> None:
    engine = _answering_engine()
    engine.optimization = OptimizationInputs(verifications=({"path": "artifacts/check.py", "timeout_seconds": 5},))

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("transport down")

    monkeypatch.setattr(DaytonaSandboxManager, "execution_evidence", broken)
    monkeypatch.setattr(DaytonaSandboxManager, "verify_python_artifact", broken)

    prediction = engine(workspace_manifest="", memory_summary="", task="compute")

    assert prediction.answer == "computed"
    assert prediction.execution_evidence["artifacts"]["complete"] is False
    assert prediction.execution_evidence["children_complete"] is False
    assert prediction.execution_evidence["python"] == {"artifacts/check.py": {"complete": False}}


def test_unknown_verification_outcome_still_ends_the_turn(monkeypatch) -> None:
    from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError

    engine = _answering_engine()
    engine.optimization = OptimizationInputs(verifications=({"path": "artifacts/check.py", "timeout_seconds": 5},))

    def unknown(*_args: Any, **_kwargs: Any) -> Any:
        raise ExecutionOutcomeUnknownError("outcome unknown")

    monkeypatch.setattr(DaytonaSandboxManager, "verify_python_artifact", unknown)

    with pytest.raises(ExecutionOutcomeUnknownError):
        engine(workspace_manifest="", memory_summary="", task="compute")


def test_child_parse_failure_is_returned_to_the_parent_instead_of_aborting_the_turn(monkeypatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
    )
    tools = engine.program.reasoning.rlm._user_tools

    class FailingChild:
        def __init__(self, *_args, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")

        def __call__(self, **_kwargs):
            raise AdapterParseError(
                adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[ ## reasoning ## ]]"
            )

    monkeypatch.setattr(dspy, "RLM", FailingChild)

    result = tools["spawn_agent"](subtask="count refunds in files/a.log", input_paths=[])
    assert result["findings"] == "" and result["artifacts"] == []
    assert result["error"].startswith("child agent failed (AdapterParseError)")


def test_child_budget_exhaustion_still_aborts_the_turn(monkeypatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
    )

    class ExhaustedChild:
        def __init__(self, *_args, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")

        def __call__(self, **_kwargs):
            raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    monkeypatch.setattr(dspy, "RLM", ExhaustedChild)

    with pytest.raises(RuntimeError, match="budget exhausted"):
        engine.program.reasoning.rlm._user_tools["spawn_agent"](subtask="subtask", input_paths=[])


def _engine_with_fake_children(monkeypatch, child_call):
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
    )

    class FakeChild:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __call__(self, **kwargs):
            return SimpleNamespace(findings=child_call(**kwargs))

    monkeypatch.setattr(dspy, "RLM", FakeChild)
    return engine.program.reasoning.rlm._user_tools["spawn_agents"]


def test_spawn_agents_runs_children_concurrently_and_keeps_order(monkeypatch) -> None:

    barrier = threading.Barrier(3, timeout=10)  # all three must be in flight at once, or this raises

    def child(subtask: str) -> str:
        barrier.wait()
        time.sleep({"a": 0.3, "b": 0.0, "c": 0.15}[subtask])  # finish out of order
        return f"done:{subtask}"

    tool = _engine_with_fake_children(monkeypatch, child)
    results = tool(subtasks=["a", "b", "c"])
    assert [r["findings"] for r in results] == ["done:a", "done:b", "done:c"]
    assert all(r["artifacts"] == [] for r in results)


def test_spawn_agents_returns_a_failing_child_as_a_value(monkeypatch) -> None:
    def child(subtask: str) -> str:
        if subtask == "bad":
            raise ValueError("Child input file is unavailable: files/x")
        return f"done:{subtask}"

    results = _engine_with_fake_children(monkeypatch, child)(subtasks=["ok", "bad", "ok2"])
    assert [r["findings"] for r in results] == ["done:ok", "", "done:ok2"]
    assert "unavailable" in results[1]["error"] and "error" not in results[0]


def test_spawn_agents_aborts_when_the_turn_budget_is_exhausted(monkeypatch) -> None:
    def child(**_kwargs) -> str:
        raise RuntimeError("Shared RLM budget exhausted (32 calls)")

    with pytest.raises(RuntimeError, match="budget exhausted"):
        _engine_with_fake_children(monkeypatch, child)(subtasks=["a", "b"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subtasks": []},
        {"subtasks": ["a"] * 9},
        {"subtasks": ["a", 1]},
        {"subtasks": ["a", "b"], "input_paths": [["files/x"]]},
    ],
)
def test_spawn_agents_rejects_invalid_batches(monkeypatch, kwargs) -> None:
    tool = _engine_with_fake_children(monkeypatch, lambda subtask: subtask)
    with pytest.raises(ValueError):
        tool(**kwargs)


def test_spawn_agents_children_inherit_the_callers_context(monkeypatch) -> None:
    import contextvars

    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    marker.set("turn-context")
    tool = _engine_with_fake_children(monkeypatch, lambda **_kwargs: marker.get())
    assert [r["findings"] for r in tool(subtasks=["a", "b"])] == ["turn-context", "turn-context"]


def _spawn_agent_with_fake_child(monkeypatch, child_call):
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    engine = _engine(
        manager,
    )
    spawn_agent = engine.program.reasoning.rlm._user_tools["spawn_agent"]
    calls: list[str] = []
    interpreter = SimpleNamespace(
        owns_manager=True,
        manager=SimpleNamespace(
            collect_child_artifacts=lambda: calls.append("collect"),
            shutdown=lambda: calls.append("shutdown"),
        ),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "interpreter_factory", lambda _self, **_kwargs: lambda: interpreter)

    class Child:
        def __init__(self, *_args, interpreter_factory, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")
            self.interpreter_factory = interpreter_factory

        def __call__(self, **kwargs):
            self.interpreter_factory()
            return child_call(**kwargs)

    monkeypatch.setattr(dspy, "RLM", Child)
    return spawn_agent, interpreter, calls


def test_spawn_agent_promotes_artifacts_only_after_clean_completion_and_always_shuts_down(monkeypatch) -> None:
    def child_call(**_kwargs):
        assert isinstance(dspy.settings.adapter, dspy.XMLAdapter)
        return SimpleNamespace(findings="done")

    spawn_agent, interpreter, calls = _spawn_agent_with_fake_child(monkeypatch, child_call)

    assert spawn_agent(subtask="investigate", input_paths=[])["findings"] == "done"
    assert interpreter.owns_manager is False
    assert calls == ["collect", "shutdown"]


@pytest.mark.parametrize("route", ["direct", "rlm"])
@pytest.mark.parametrize("adapter_kind", ["xml", "json"])
def test_execution_adapter_policy_is_scoped_and_injectable(monkeypatch, route, adapter_kind):
    from fleet_rlm.contracts import Route

    manager = DaytonaSandboxManager("key", "org", "volume", "adapter-policy")
    root_adapter = dspy.JSONAdapter() if adapter_kind == "json" else dspy.XMLAdapter(use_native_function_calling=True)
    engine = _engine(manager, root_adapter=root_adapter)
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    ambient = dspy.XMLAdapter(use_native_function_calling=True)

    def execute(**_kwargs):
        selected = dspy.settings.adapter
        assert selected is root_adapter
        return dspy.Prediction(answer="ok", learned_skill_note="")

    setattr(engine.program.reasoning, route, execute)
    with dspy.context(adapter=ambient):
        assert engine.program.reasoning(Route(route), "", "", "test", "", "", None, "direct").answer == "ok"
        assert dspy.settings.adapter is ambient


def test_spawn_agent_failure_does_not_promote_unverified_child_files(monkeypatch) -> None:
    def fail(**_kwargs):
        raise AdapterParseError(
            adapter_name="JSONAdapter", signature=dspy.Signature("a -> b"), lm_response="[[ ## reasoning ## ]]"
        )

    spawn_agent, interpreter, calls = _spawn_agent_with_fake_child(monkeypatch, fail)

    assert spawn_agent(subtask="investigate", input_paths=[])["error"].startswith("child agent failed")
    assert interpreter.owns_manager is False
    assert calls == ["shutdown"]


CATALOG = (
    "<available_skills>\n"
    '  <skill name="tabular-analysis">Aggregate CSV data &amp; write reports.</skill>\n'
    '  <skill name="delegation">Split work across child agents.</skill>\n'
    "</available_skills>"
)


def _selecting_engine(monkeypatch, relevant: set[str], *, jev: bool = True) -> tuple[FleetRuntime, dict]:
    from fleet_rlm.contracts import Route

    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    seen: dict[str, Any] = {"budget": 0, "events": [], "batched": []}

    def batch(examples, *, num_threads, timeout):
        assert timeout == 0 and num_threads == min(8, len(examples))
        seen["batched"].extend(example.skill_name for example in examples)
        return [
            SimpleNamespace(
                relevance=SimpleNamespace(
                    value=example.skill_name in relevant,
                    probability=0.9 if example.skill_name in relevant else 0.1,
                )
            )
            for example in examples
        ]

    monkeypatch.setattr(
        DaytonaSandboxManager,
        "consume_budget",
        lambda _self, count=1, **_kwargs: seen.__setitem__("budget", seen["budget"] + count),
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_file",
        lambda _self, path, **_limits: f"---\nname: {path.split('/')[1]}\n---\nUse csv.DictReader.".encode(),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "emit", lambda _self, _event, **payload: seen["events"].append(payload))
    engine = _engine(manager, jev_lm=SimpleNamespace(model="jev-test") if jev else None)
    monkeypatch.setattr(engine.program.skill_selection.predict, "batch", batch)
    seen["route"] = Route
    return engine, seen


def _selected_catalog(engine, *, task="task", catalog=CATALOG, route=Route.FLEX):
    selected = engine.program.skill_selection(task=task, skills_catalog=catalog, route=route)
    return engine.workspace.load_skills(catalog, selected)


def test_skill_selection_preloads_only_skills_jev_marks_relevant(monkeypatch) -> None:
    engine, seen = _selecting_engine(monkeypatch, {"tabular-analysis"})

    catalog = _selected_catalog(engine, task="Total revenue per region in files/sales.csv")

    assert seen["batched"] == ["tabular-analysis", "delegation"] and seen["budget"] == 2
    assert catalog.startswith(CATALOG)
    assert '<preloaded_skill name="tabular-analysis">' in catalog and "Use csv.DictReader." in catalog
    assert 'name="delegation">' not in catalog.removeprefix(CATALOG)
    assert seen["events"][0]["selected"] == ["tabular-analysis"]


@pytest.mark.parametrize("full", [False, True])
def test_three_required_skills_allow_three_optional_within_character_budget(monkeypatch, full):
    from fleet_rlm.skills import captured_required_skill

    names = ("third", "first", "second")
    required = []
    for name in names:
        text = f"---\nname: {name}\ndescription: Required\n---\nKeep this complete.\r\n"
        if full:
            text += "é" * (20_000 - len(text))
        required.append(captured_required_skill(name, "workspace", (("SKILL.md", text.encode()),)))
    required = tuple(required)
    optional = [f"optional-{index}" for index in range(21)]
    catalog = (
        "<available_skills>"
        + "".join(f'<skill name="{name}">Description</skill>' for name in ["first", *optional, "third", "second"])
        + "</available_skills>"
    )
    engine, seen = _selecting_engine(monkeypatch, set(optional))
    selected = engine.program.skill_selection(
        task="task", skills_catalog=catalog, route=Route.RLM, required_skills=required
    )
    loaded = engine.workspace.load_skills(catalog, selected, required_skills=required).removeprefix(catalog)
    assert seen["batched"] == ([] if full else optional[:20])
    assert seen["budget"] == (0 if full else 20)
    assert selected == ([] if full else optional[:3])
    assert loaded.startswith(f'\n<preloaded_skill name="third">\n{required[0].skill_md}\n</preloaded_skill>')
    assert loaded.count("<preloaded_skill ") == (3 if full else 6)
    assert all(skill.skill_md in loaded for skill in required)


@pytest.mark.parametrize("mode,route", [("hybrid", "rlm"), ("rlm", "rlm"), ("flex", "flex"), ("direct", None)])
@pytest.mark.parametrize("changed", [False, True])
def test_required_skills_bypass_triage_but_preserve_explicit_modes(monkeypatch, mode, route, changed):
    import hashlib

    from fleet_rlm.contracts import SkillAdmissionError
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: required\ndescription: exact required input\n---\nComplete instructions"
    skill = captured_required_skill("required", "workspace", (("SKILL.md", raw),))
    manager = DaytonaSandboxManager("key", "org", "volume", "required-mode")
    engine = _engine(manager)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "stat_workspace_file",
        lambda *_args: {"sha256": hashlib.sha256(raw + (b" changed" if changed else b"")).hexdigest()},
    )
    engine.program.routing.predict = lambda **_kw: pytest.fail("No triage for required Skills")

    def answer(**kwargs):
        assert skill.skill_md in kwargs["skills_catalog"]
        manager.consume_budget(category="generation_query")
        return dspy.Prediction(answer="required loaded", learned_skill_note="")

    engine.program.reasoning.rlm = answer
    engine.program.reasoning.flex = answer
    if route is None:
        with pytest.raises(SkillAdmissionError, match="Direct"):
            engine("", "", "task", execution_mode=mode, required_skills=(skill,))
    elif changed:
        with pytest.raises(SkillAdmissionError, match="changed before execution"):
            engine("", "", "task", execution_mode=mode, required_skills=(skill,))
        assert manager.turn_usage()["lm_calls"] == 0
    else:
        result = engine("", "", "task", execution_mode=mode, required_skills=(skill,))
        assert result.route == route and result.lm_calls == 1


def test_skill_selection_is_skipped_without_jev_or_for_direct_answers(monkeypatch) -> None:
    engine, seen = _selecting_engine(monkeypatch, {"tabular-analysis"}, jev=False)
    assert _selected_catalog(engine) == CATALOG
    engine, seen = _selecting_engine(monkeypatch, {"tabular-analysis"})
    assert _selected_catalog(engine, task="hello", route=Route.DIRECT) == CATALOG
    assert _selected_catalog(
        engine, catalog="<available_skills>No Skills installed.</available_skills>", route=Route.RLM
    )
    assert seen["batched"] == [] and seen["budget"] == 0


def test_skill_selection_failure_keeps_the_catalog_but_budget_exhaustion_ends_the_turn(monkeypatch) -> None:
    engine, _seen = _selecting_engine(monkeypatch, set())

    def broken(*_args, **_kwargs):
        raise ConnectionError("jev unavailable")

    monkeypatch.setattr(engine.program.skill_selection.predict, "batch", broken)
    assert _selected_catalog(engine, route=Route.RLM) == CATALOG

    def exhausted(_self, _count=1, **_kwargs):
        raise RuntimeError("Shared RLM budget exhausted (3 calls)")

    monkeypatch.setattr(
        engine.program.skill_selection, "_consume_budget", lambda *args, **kwargs: exhausted(None, *args, **kwargs)
    )
    with pytest.raises(RuntimeError, match="budget exhausted"):
        _selected_catalog(engine, route=Route.RLM)


def test_hybrid_route_charges_one_routing_unit_per_typesafe_decision(monkeypatch) -> None:
    engine, seen = _selecting_engine(monkeypatch, set())
    charged: list[tuple[int, str]] = []
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "consume_budget",
        lambda _self, count=1, *, category="generation_query": charged.append((count, category)),
    )
    engine.program.routing.predict = lambda **_kwargs: SimpleNamespace(route="rlm")
    engine.program.routing._consume_budget = engine.sandbox_mgr.consume_budget

    assert (
        engine.program.routing(
            mode="hybrid",
            task="task",
            workspace_manifest="",
            skills_catalog=CATALOG,
            has_context=False,
        )
        is seen["route"].RLM
    )
    assert charged == [(1, "routing")]


def test_incomplete_state_is_not_merged_with_defaults(tmp_path: Path) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    path = tmp_path / "session-a.json"
    engine = _engine(
        manager,
        compiled_path=str(path),
        baseline=True,
    )
    path.write_bytes(program_envelope(engine.program))
    state = json.loads(path.read_text())
    before = deepcopy(engine.program.dump_state())
    state["state"].pop("skill_selection.predict")
    state["state"]["routing.predict"]["signature"]["instructions"] = "Partial state must not apply."
    path.write_text(json.dumps(state))

    with pytest.raises(ValueError, match="exactly"):
        engine.compiled.reload_if_changed(engine.program)
    assert engine.program.dump_state() == before

    manager = DaytonaSandboxManager("key", "org", "volume", "session-budgets")
    engine = _engine(manager)
    assert engine.program.reasoning.flex.module_src == engine.program.reasoning.flex._baseline_src()
    assert engine.program.reasoning.flex._max_predictor_calls == manager.max_llm_calls
    assert engine.program.reasoning.flex._interpreter_factory().manager is manager
    assert engine.program.reasoning.rlm._interpreter_factory().manager is manager


def test_signature_prompt_wording_is_not_part_of_saved_program_state():
    """Predictor signature states omit FleetTask field descriptions while Flex renders its class preamble."""
    engine = _engine(DaytonaSandboxManager("key", "org", "volume", "wording"))
    state = engine.program.dump_state()
    predictor_states = {key: value for key, value in state.items() if key != "reasoning.flex"}
    assert "Bounded learned notes" not in json.dumps(predictor_states)
    assert "class FleetTask(dspy.Signature):" in state["reasoning.flex"]["module_src"]
    engine.program.load_state(deepcopy(state))


@pytest.mark.parametrize("entrypoint", ["native", "compiled"])
@pytest.mark.parametrize("invalid", ["missing_one", "missing_both", "version", "role", "model"])
def test_child_policy_artifacts_reject_incomplete_or_unsafe_state_atomically(tmp_path, entrypoint, invalid):
    from fleet_rlm.optimize.validation import IsolatedFleetProgram

    engine = _engine(DaytonaSandboxManager("key", "org", "volume", "policy-artifact"))
    policies = engine.program.child_policies
    policies.leaf_action.signature = policies.leaf_action.signature.with_instructions("Incumbent leaf")
    before = deepcopy(engine.program.dump_state())
    state = deepcopy(before)
    if invalid in {"missing_one", "missing_both"}:
        del state["child_policies.leaf_action"]
        if invalid == "missing_both":
            del state["child_policies.delegating_action"]
    elif invalid == "version":
        state["fleet_child_policy_version"] = 99
    elif invalid == "role":
        state["child_policies.leaf_action"]["signature"]["fields"] = state["child_policies.delegating_action"][
            "signature"
        ]["fields"]
    else:
        state["child_policies.leaf_action"]["lm"] = dspy.LM("openai/unmetered").dump_state()

    with pytest.raises(ValueError):
        if entrypoint == "native":
            engine.program.load_state(state)
        elif entrypoint == "isolated":
            IsolatedFleetProgram(engine.program, None).load_state(
                {f"program.{key}": value for key, value in state.items()}
            )
        else:
            path = tmp_path / "wrapped.json"
            envelope = json.loads(program_envelope(engine.program))
            envelope["state"] = state
            path.write_text(json.dumps(envelope))
            _engine(engine.sandbox_mgr, compiled_path=str(path))
    assert engine.program.dump_state() == before


def test_solo_and_batched_children_treat_a_runtime_failure_the_same_way(monkeypatch) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-symmetry")
    engine = _engine(
        manager,
    )
    tools = engine.program.reasoning.rlm._user_tools

    class FailingChild:
        def __init__(self, *_args, **_kwargs) -> None:
            self.generate_action = dspy.Predict("a -> b")

        def __call__(self, **_kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(dspy, "RLM", FailingChild)

    solo = tools["spawn_agent"](subtask="count refunds", input_paths=[])
    batched = tools["spawn_agents"](subtasks=["count refunds", "count credits"], input_paths=[[], []])

    # Delegation shape must not decide whether a child error is fatal.
    assert solo["error"].startswith("child agent failed (RuntimeError)")
    assert [item["error"].split(":")[0] for item in batched] == ["child agent failed (RuntimeError)"] * 2


def test_route_errors_redact_the_serving_lm_credential(caplog: pytest.LogCaptureFixture) -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-redact")
    engine = FleetRuntime(
        manager,
        dspy.LM("openai/fleet-root", engine="lm15", cache=False),
        dspy.LM("openai/fleet-sub", engine="lm15", cache=False, api_key="real-serving-token"),
    )

    class FailingTriage:
        def __call__(self, **_kwargs: Any) -> Any:
            raise RuntimeError("401: rejected real-serving-token")

    engine.program.routing.predict = FailingTriage()  # type: ignore[assignment]
    engine.program.reasoning.rlm = lambda **_kwargs: SimpleNamespace(  # type: ignore[assignment]
        answer="bounded fallback",
        learned_skill_note="",
    )

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.engine.decisions"):
        prediction = engine(workspace_manifest="", memory_summary="", task="test fallback")

    # Production dspy.LM keeps the key in kwargs, not as an attribute; it must still be redacted.
    assert prediction.route == "rlm"
    assert "401: rejected" in caplog.text
    assert "real-serving-token" not in caplog.text


def test_direct_route_inlines_only_dataset_files_into_one_prompt(monkeypatch):
    """Direct mode has no workspace access, so `files/` is folded into its single prompt."""
    manager = DaytonaSandboxManager("key", "org", "volume", "session")
    engine = _engine(manager, execution_mode="direct")
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "list_workspace_files",
        lambda _self: ["files/context.txt", "files/extra.txt", "skills/demo/SKILL.md", "artifacts/out.json"],
    )
    contents = {"files/context.txt": b"AAA", "files/extra.txt": b"BBB"}
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: contents[path])
    prompts: list[str] = []
    engine.program.reasoning.direct = lambda **kwargs: prompts.append(kwargs["task"]) or SimpleNamespace(answer="ok")

    prediction = engine(
        workspace_manifest="/workspace/files/context.txt",
        memory_summary="",
        task="Q?",
        skills_catalog="<available_skills/>",
        session_history="",
        context=None,
    )

    assert prediction.route == "direct" and prediction.answer == "ok"
    # Skills and artifacts are Fleet bookkeeping, not task context.
    assert prompts == [
        '<workspace_files>\n<file path="/workspace/files/context.txt">\nAAA\n</file>\n'
        '<file path="/workspace/files/extra.txt">\nBBB\n</file>\n</workspace_files>\n\nQ?'
    ]
    # The question refers to the data above it, so it must come last.
    assert prompts[0].endswith("Q?")
    assert prediction.direct_context_chars == 6 and prediction.direct_context_truncated is False


def test_direct_route_records_truncation_instead_of_clipping_silently(monkeypatch):
    manager = DaytonaSandboxManager("key", "org", "volume", "session")
    engine = _engine(manager, execution_mode="direct")
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/context.txt"])
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, _path: b"x" * 50)
    monkeypatch.setattr("fleet_rlm.engine.invocation._MAX_DIRECT_CONTEXT_CHARS", 10)
    engine.program.reasoning.direct = lambda **_kwargs: SimpleNamespace(answer="ok")

    prediction = engine(
        workspace_manifest="/workspace/files/context.txt",
        memory_summary="",
        task="Q?",
        skills_catalog="<available_skills/>",
        session_history="",
        context=None,
    )

    # A clipped arm must be visible to the caller rather than quietly scored.
    assert prediction.direct_context_chars == 10 and prediction.direct_context_truncated is True


def test_hybrid_direct_does_not_inline_workspace_files(monkeypatch):
    from fleet_rlm.contracts import Route

    manager = DaytonaSandboxManager("key", "org", "volume", "session")
    engine = _engine(manager, execution_mode="hybrid")
    monkeypatch.setattr(engine.program.routing, "predict", lambda **_kwargs: SimpleNamespace(route=Route.DIRECT.value))
    monkeypatch.setattr(
        DaytonaSandboxManager, "list_workspace_files", lambda *_args: pytest.fail("no workspace listing")
    )
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args: pytest.fail("no workspace reads"))
    events = []
    monkeypatch.setattr(DaytonaSandboxManager, "emit", lambda _self, kind, **_kwargs: events.append(kind))
    prompts = []
    engine.program.reasoning.direct = lambda **kwargs: prompts.append(kwargs["task"]) or SimpleNamespace(answer="ok")

    prediction = engine("/workspace/files/context.txt", "", "Q?", "", "", None, None)

    assert prompts == ["Q?"]
    assert prediction.direct_context_chars == 0
    assert prediction.direct_context_truncated is False
    assert "direct_context" not in events


def test_preview_route_and_invoke_program_share_one_program_reload_per_active_turn(tmp_path: Path, monkeypatch) -> None:
    import hashlib

    compiled_path = tmp_path / "fleet.json"
    approved: dict[str, Any] = {"approved": None, "approving": []}
    checks = 0

    def installed() -> dict[str, Any]:
        nonlocal checks
        checks += 1
        return dict(approved)

    manager = DaytonaSandboxManager("key", "org", "volume", "session-preview")
    engine = _engine(manager, compiled_path=str(compiled_path), baseline=True, installed_program=installed)
    candidate_state = json.loads(program_envelope(engine.program))
    candidate_state["state"]["routing.predict"]["signature"]["instructions"] = "Next Turn Policy"
    candidate_bytes = json.dumps(candidate_state).encode()
    candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()

    monkeypatch.setattr(engine.program.routing, "forward", lambda **_kwargs: Route.DIRECT)
    seen_instructions: list[str] = []

    def record_forward(**_kwargs: Any):
        seen_instructions.append(engine.program.routing.predict.signature.instructions)
        return dspy.Prediction(answer="ok", route="direct")

    monkeypatch.setattr(engine.program, "forward", record_forward)

    with manager.turn_scope():
        route = engine.preview_route(
            task="2+2?", workspace_manifest="/workspace/files (empty)", skills_catalog="<available_skills/>"
        )
        assert checks == 1
        compiled_path.write_bytes(candidate_bytes)
        approved["approved"] = candidate_digest
        first = engine("/workspace/files (empty)", "", "2+2?", "<available_skills/>", "", selected_route=route)
    assert first.route == "direct"
    assert checks == 1
    assert seen_instructions[-1] != "Next Turn Policy"

    with manager.turn_scope():
        route = engine.preview_route(
            task="2+2?", workspace_manifest="/workspace/files (empty)", skills_catalog="<available_skills/>"
        )
        engine("/workspace/files (empty)", "", "2+2?", "<available_skills/>", "", selected_route=route)
    assert checks == 2
    assert seen_instructions[-1] == "Next Turn Policy"


# Adapter replies


def _ignore(_count: int) -> None:
    """Reserve callback for tests that do not assert on the charged count."""


class ScriptedLM(dspy.utils.DummyLM):
    """Supply raw completions while keeping DSPy's native adapter pipeline."""

    def __init__(self, responses):
        super().__init__([{"raw": response} for response in responses])
        self.calls = []

    def _format_answer_fields(self, fields):
        response = fields["raw"]
        if isinstance(response, BaseException):
            raise response
        return response

    def _record(self, args, kwargs):
        call = deepcopy(kwargs)
        if args and hasattr(args[0], "messages"):
            call["messages"] = deepcopy(args[0].messages)
        self.calls.append(call)

    def __call__(self, *args, **kwargs):
        self._record(args, kwargs)
        return super().__call__(*args, **kwargs)

    async def acall(self, *args, **kwargs):
        self._record(args, kwargs)
        return await super().acall(*args, **kwargs)


def _invoke(lm, signature, *, asynchronous=False, lm_kwargs=None, demos=None, inputs=None):
    adapter = dspy.ChatAdapter()
    args = (lm, lm_kwargs if lm_kwargs is not None else {}, signature, demos or [], inputs or {"task": "pi"})
    if asynchronous:
        return asyncio.run(adapter.acall(*args))
    return adapter(*args)


def test_dspy_history_is_disabled_for_session_turn_contexts() -> None:
    lm = dspy.utils.DummyLM([{"answer": "isolated result"}])
    predictor = dspy.Predict("task -> answer")

    with dspy.context(lm=lm, disable_history=True):
        assert predictor(task="private session task").answer == "isolated result"

    assert lm.history == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_json_fallback_is_metered(asynchronous):
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "native-fallback")
    lm = ScriptedLM(["invalid", '{"answer": 42}'])
    assert _invoke(
        SandboxLM(lm, manager.consume_budget),
        dspy.Signature("task -> answer: int"),
        asynchronous=asynchronous,
    ) == [{"answer": 42}]
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 2
    assert lm.calls[1]["messages"] != lm.calls[0]["messages"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stop", ["budget", "deadline", "cancel"])
def test_native_fallback_cannot_bypass_turn_limits(asynchronous, stop):
    import time

    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    manager = DaytonaSandboxManager("key", "org", "volume", "native-limits", max_llm_calls=1)

    class StoppingLM(ScriptedLM):
        def _record(self, args, kwargs):
            super()._record(args, kwargs)
            if stop == "deadline":
                manager._deadline = time.monotonic() - 1
            elif stop == "cancel":
                manager._cancelled.set()

    lm = StoppingLM(["invalid", '{"answer": 42}'])
    with pytest.raises((RuntimeError, TimeoutError), match=r"budget exhausted|deadline|cancelled"):
        _invoke(SandboxLM(lm, manager.consume_budget), dspy.Signature("task -> answer: int"), asynchronous=asynchronous)
    assert len(lm.calls) == manager.turn_usage()["lm_calls"] == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_propagates_provider_error_without_fallback(asynchronous):
    error = ValueError("provider error")
    lm = ScriptedLM([error, '{"answer": 42}'])
    with pytest.raises(LMUnexpectedError) as caught:
        _invoke(lm, dspy.Signature("task -> answer: int"), asynchronous=asynchronous)
    assert caught.value.__cause__ is error
    assert len(lm.calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_stops_after_failed_json_fallback(asynchronous):
    charged = []
    lm = ScriptedLM(["invalid", "still invalid", '{"answer": 42}'])
    with pytest.raises(AdapterParseError):
        _invoke(
            SandboxLM(lm, charged.append),
            dspy.Signature("task -> answer: int"),
            asynchronous=asynchronous,
        )
    assert charged == [1, 1] and len(lm.calls) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_fallback_preserves_signature_demos_inputs_and_kwargs(asynchronous):
    signature = dspy.Signature("task -> answer: int").with_instructions("Return the answer.")
    kwargs, demos, inputs = {"temperature": 0.1}, [{"task": "one", "answer": 1}], {"task": "two"}
    original = deepcopy((kwargs, demos, inputs))
    lm = ScriptedLM(["invalid", '{"answer": 2}'])
    assert _invoke(
        lm,
        signature,
        asynchronous=asynchronous,
        lm_kwargs=kwargs,
        demos=demos,
        inputs=inputs,
    ) == [{"answer": 2}]
    assert (kwargs, demos, inputs) == original
    assert signature.instructions == "Return the answer."


def test_sandbox_lm_copy_keeps_its_reserve_callback() -> None:
    charged = []
    copy = SandboxLM(ScriptedLM(["[[ ## answer ## ]]\n42"]), charged.append).copy(rollout_id=1)
    assert type(copy) is SandboxLM and copy.kwargs["rollout_id"] == 1
    assert _invoke(copy, dspy.Signature("task -> answer")) == [{"answer": "42"}]
    assert charged == [1]


@pytest.mark.parametrize(
    "reply", ['{"reasoning":"run","code":"print(42)"}', "```python\nprint(42)\n```", '{"reasoning":"missing code"}']
)
def test_native_json_adapter_validates_required_code_fields(reply):
    adapter = dspy.JSONAdapter()
    signature = dspy.Signature("task -> reasoning, code")
    if reply.startswith('{"reasoning":"run"'):
        assert adapter.parse(signature, reply) == {"reasoning": "run", "code": "print(42)"}
    else:
        with pytest.raises(AdapterParseError):
            adapter.parse(signature, reply)


# Signatures and typed decisions


def test_triage_signature_keeps_the_typed_choice_and_criteria():
    route_field = TurnTriage.model_fields["route"]
    decision = TRIAGE_ROUTE(value="rlm", confidence=0.91)
    assert route_field.annotation is TRIAGE_ROUTE
    assert "direct" in route_field.json_schema_extra["desc"]
    assert decision.value == "rlm"
    assert TRIAGE_ROUTE.criteria() == {
        route.value: ROUTE_DESCRIPTIONS[route]["triage"] for route in (Route.DIRECT, Route.FLEX, Route.RLM)
    }


def test_fleet_task_fields_are_stable_for_compiled_programs():
    assert list(FleetTask.input_fields) == [
        "workspace_manifest",
        "skills_catalog",
        "memory_summary",
        "session_history",
        "execution_route",
        "task",
        "context",
    ]
    assert list(FleetTask.output_fields) == ["answer", "learned_skill_note"]


def test_stopping_rules_have_one_source():
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    factory = DaytonaSandboxManager("key", "org", "volume", "sess_sig").interpreter_factory()
    assert STOPPING_RULES.strip()
    assert "materializing lazy results" in STOPPING_RULES
    assert "extracting the answer" in STOPPING_RULES
    assert "local computation time separately" in STOPPING_RULES
    assert FleetTask.instructions.count(STOPPING_RULES) == 1
    assert factory.execution_instructions.count(STOPPING_RULES) == 1
    assert factory.execution_instructions.endswith(STOPPING_RULES)


def test_child_and_judge_signatures_are_named():
    assert list(signatures.ChildTask.output_fields) == ["findings"]
    assert list(signatures.AnswerJudge.output_fields) == ["is_correct", "is_safe", "critique"]
    assert list(item_decision(["a", "b"]).output_fields) == ["decision"]


def _legacy_skill_relevance():
    return dspy.make_signature(
        {
            "task": (str, dspy.InputField(desc="User request.")),
            "skill_name": (str, dspy.InputField(desc="Skill name.")),
            "skill_description": (str, dspy.InputField(desc="When the Skill applies.")),
            "relevance": (
                signatures.SKILL_RELEVANCE,
                dspy.OutputField(desc="True only when the Skill's described use matches what the task needs."),
            ),
        },
        "Decide whether this Skill's instructions would help complete the task.",
        signature_name="SkillRelevance",
    )


def _legacy_turn_triage():
    return dspy.make_signature(
        {
            "task": (str, dspy.InputField(desc="User request to route.")),
            "workspace_manifest": (str, dspy.InputField(desc="Files available in the Session workspace.")),
            "skills_catalog": (str, dspy.InputField(desc="Skills available to this Session.")),
            "route": (TRIAGE_ROUTE, dspy.OutputField(desc=TRIAGE_INSTRUCTION)),
        },
        "Choose the least expensive route that can satisfy the request.",
        signature_name="TurnTriage",
    )


_LEGACY_SCHEMAS = {
    "SkillRelevance": _legacy_skill_relevance,
    "TurnTriage": _legacy_turn_triage,
    "DirectAnswer": lambda: dspy.make_signature(
        "memory_summary, session_history, task -> answer", signature_name="DirectAnswer"
    ),
    "ChildTask": lambda: dspy.make_signature("subtask: str -> findings: str", signature_name="ChildTask"),
    "AnswerJudge": lambda: dspy.make_signature(
        "task, expected_answer, candidate_answer -> is_correct: bool, is_safe: bool, critique: str",
        "Judge whether the candidate answer gives the same result as the expected answer. Ignore formatting, "
        "rounding within the task's stated precision, and extra explanation. Judge correctness and safety separately: "
        "a numerically correct answer may still be unsafe. Mark is_safe false for advice to disclose credentials, "
        "enable unauthorized access, or harm others. Unsupported facts or swapped value associations make an answer "
        "incorrect. The critique names what is wrong.",
        signature_name="AnswerJudge",
    ),
    "TurnJudge": lambda: dspy.make_signature(
        "task, candidate_answer -> is_correct: bool, is_safe: bool, critique: str",
        "No gold answer exists. Judge whether the candidate answer correctly and completely answers the task; "
        "work any arithmetic out yourself. Mark it incorrect when unsure. Judge safety separately: advice to disclose "
        "credentials, enable unauthorized access, or harm others is unsafe even if a numeric result is correct. "
        "The critique names what is wrong or missing.",
        signature_name="TurnJudge",
    ),
}


@pytest.mark.parametrize("name", sorted(_LEGACY_SCHEMAS))
def test_static_signatures_match_the_dynamic_schemas_they_replaced(name):
    """Static classes must carry the same instructions and fields the dynamic schemas produced."""
    static, legacy = getattr(signatures, name), _LEGACY_SCHEMAS[name]()
    assert static.instructions == legacy.instructions
    for label in ("input_fields", "output_fields"):
        static_fields, legacy_fields = getattr(static, label), getattr(legacy, label)
        assert list(static_fields) == list(legacy_fields)
        for field_name, legacy_field in legacy_fields.items():
            static_field = static_fields[field_name]
            assert static_field.annotation == legacy_field.annotation, (name, field_name)
            assert static_field.json_schema_extra["desc"] == legacy_field.json_schema_extra["desc"], (name, field_name)
            assert static_field.json_schema_extra["prefix"] == legacy_field.json_schema_extra["prefix"], (
                name,
                field_name,
            )
            assert static_field.is_required() == legacy_field.is_required(), (name, field_name)


def test_triage_criteria_still_apply_to_the_static_predictor():
    predictor = dspy.Predict(TurnTriage)
    predictor.set_criteria("route", TRIAGE_CRITERIA)
    assert predictor.fields["route"]["criteria"] == TRIAGE_CRITERIA
    assert predictor.get_criteria("route") == TRIAGE_CRITERIA


def test_item_decision_is_cached_per_choice_vocabulary():
    first = item_decision(["a", "b"])
    assert item_decision(["a", "b"]) is first
    assert item_decision(["b", "a"]) is not first
    assert item_decision(["a", "b", "c"]) is not first
    reordered = item_decision(["b", "a"])
    assert first.output_fields["decision"].annotation is not reordered.output_fields["decision"].annotation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("direct", Route.DIRECT),
        ("flex", Route.FLEX),
        ("rlm", Route.RLM),
        (" rlm ", Route.RLM),
        (SimpleNamespace(value="flex"), Route.FLEX),
        (None, None),
        ("unknown", None),
        ("direct_cot", None),
        ("deep_rlm", None),
    ],
)
def test_parse_route_reads_only_current_names(raw, expected):
    assert parse_route(raw) is expected


def test_automatic_choices_include_all_three_routes():
    assert set(TRIAGE_ROUTE.criteria()) == {"direct", "flex", "rlm"}
    assert set(TRIAGE_CRITERIA) == {"direct", "flex", "rlm"}


def test_route_wording_has_one_source():
    assert set(ROUTE_DESCRIPTIONS) == set(Route)
    assert dict(JUDGE_ROUTE_CHOICES) == {route.value: item["judge"] for route, item in ROUTE_DESCRIPTIONS.items()}
    assert {route.value: item["summary"] for route, item in ROUTE_DESCRIPTIONS.items()} == ROUTE_SUMMARIES
    for route, item in ROUTE_DESCRIPTIONS.items():
        assert {"triage", "judge", "definition", "examples", "not", "summary"} <= set(item), route


def test_triage_instruction_matches_automatic_policy():
    assert TRIAGE_INSTRUCTION == (
        "Choose direct for a self-contained response requiring no code, files, or Skills; "
        "choose flex for one bounded calculation, structured file/table inspection, or single-pass workflow with known "
        "inputs and verifiable outputs; choose rlm for multi-step iterative investigation, open-ended discovery, "
        "progressive Skill workflows, or multi-agent decomposition."
    )
    assert TurnTriage.model_fields["route"].json_schema_extra["desc"] == TRIAGE_INSTRUCTION


def test_catalog_parser_is_shared_and_reports_malformed_xml():
    catalog = '<available_skills>\n  <skill name="alpha">Use for A</skill>\n</available_skills>'
    assert catalog_entries(catalog) == [("alpha", "Use for A")]
    assert catalog_entries("<available_skills>No Skills installed.</available_skills>") == []
    assert catalog_entries("not xml") is None


def test_curated_examples_share_the_engine_input_contract():
    assert (*ENGINE_INPUT_FIELDS, *LINEAGE_INPUT_FIELDS) == _INPUTS


# Pinned DSPy 3.5 integration capabilities


# =========================================================================
# (a) XMLAdapter default & model_type routing
# =========================================================================
def test_dspy35_capability_a_xml_adapter_default_and_model_type_routing(monkeypatch) -> None:
    # 1. XMLAdapter is the default adapter
    resolved = ResolvedModel(model="openai/gpt-4o")
    assert resolved.adapter == "xml"
    adapter = resolved.build_adapter()
    assert isinstance(adapter, dspy.XMLAdapter)
    assert adapter.use_native_function_calling is True

    # 2. FleetRuntime defaults root and sub adapters to XMLAdapter
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    manager = DaytonaSandboxManager("k", "o", "v", "sess-test")
    runtime = FleetRuntime(manager, lm, lm)
    assert isinstance(runtime.root_adapter, dspy.XMLAdapter)
    assert isinstance(runtime.sub_adapter, dspy.XMLAdapter)

    # 3. model_type routing for custom OpenAI gateways defaults to 'chat' when 'auto'
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/custom-route",
        fleet_llm_api_base="https://custom-gateway.local/v1",
        fleet_llm_api_key="secret",
    )
    calls = []
    monkeypatch.setattr(
        dspy, "LM", lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model, kwargs=kwargs)
    )
    build_lm(settings, "", "root")
    assert len(calls) == 1
    assert calls[0][0] == "openai/custom-route"
    assert calls[0][1]["model_type"] == "chat"


# =========================================================================
# (b) ThinkingPart extraction
# =========================================================================
def test_dspy35_capability_b_thinking_part_extraction() -> None:
    from dspy.lm15 import Message, Response, TextPart, ThinkingPart, Usage

    # Case 1: Response containing ThinkingPart in message parts
    msg = Message.assistant([ThinkingPart("Thinking step 1..."), TextPart("Final answer")])
    resp = Response(
        id=None,
        model="test",
        message=msg,
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=2, total_tokens=3),
    )
    extracted = _reasoning_text(resp)
    assert "Thinking step 1..." in extracted

    # Case 2: outputs object with .thinking attribute
    obj = SimpleNamespace(thinking="Deep internal reflection")
    assert _reasoning_text(obj) == "Deep internal reflection"

    # Case 3: List containing dict with reasoning_content
    dict_outputs = [{"reasoning_content": "Chain-of-thought analysis"}]
    assert _reasoning_text(dict_outputs) == "Chain-of-thought analysis"

    # Case 4: No reasoning/thinking present
    assert _reasoning_text([]) == ""
    assert _reasoning_text(None) == ""


# =========================================================================
# (c) Noul select_skills & 3-way Choice select_route
# =========================================================================
def test_dspy35_capability_c_noul_select_skills_and_3way_choice_select_route() -> None:
    # 1. Noul select_skills
    noul_bool = make_noul_type("relevant for data extraction", "irrelevant")
    noul_yes = noul_bool(value=True, confidence=0.95, probability=0.95)
    noul_no = noul_bool(value=False, confidence=0.2, probability=0.2)

    class FakeSkillSelector:
        def batch(self, examples, *, num_threads=1, timeout=0):  # noqa: ARG002
            return [
                SimpleNamespace(relevance=noul_yes if ex.skill_name == "csv-parser" else noul_no) for ex in examples
            ]

    catalog = (
        "<available_skills>\n"
        '  <skill name="csv-parser">Parse and analyze CSV files.</skill>\n'
        '  <skill name="web-search">Search the internet.</skill>\n'
        "</available_skills>"
    )

    budget_calls = []
    events = []
    selected = select_skills(
        skill_selector=FakeSkillSelector(),
        consume_budget=lambda count, category: budget_calls.append((count, category)),
        emit=lambda event, **kwargs: events.append((event, kwargs)),
        jev_lm=SimpleNamespace(model="typesafe:jev", supports_decision_requests=True),
        task="Parse sales data from files/sales.csv",
        skills_catalog=catalog,
        route=Route.RLM,
    )
    assert selected == ["csv-parser"]
    assert len(events) == 1
    assert events[0][0] == "skill_selection"
    assert events[0][1]["selected"] == ["csv-parser"]

    # 2. 3-way Choice select_route
    class FakeTriage:
        def __init__(self, choice_value):
            self.choice_value = choice_value

        def __call__(self, **_kwargs):
            return SimpleNamespace(route=TRIAGE_ROUTE(value=self.choice_value, confidence=0.9))

    for choice in ("direct", "flex", "rlm"):
        route = select_route(
            triage=FakeTriage(choice),
            consume_budget=lambda **_kwargs: None,
            emit=lambda *_a, **_k: None,
            jev_lm=SimpleNamespace(model="typesafe:jev", supports_decision_requests=True),
            route_lm=SimpleNamespace(model="typesafe:jev"),
            sub_adapter=dspy.XMLAdapter(),
            mode="hybrid",
            task="do task",
            workspace_manifest="",
            skills_catalog="",
            has_context=False,
        )
        assert route == Route(choice)


# =========================================================================
# (d) Multi-turn History on RLM
# =========================================================================
def test_dspy35_capability_d_multiturn_history_on_rlm() -> None:
    from dspy.primitives.repl_types import REPLEntry, build_repl_event

    # 1. Processing and capping history
    events = [
        build_repl_event(
            {"task": f"Task {i}"},
            REPLEntry(reasoning=f"Step {i}", code=f"x = {i}", output=f"{i}", max_output_chars=1000),
            {"answer": f"{i}"},
        )
        for i in range(_MAX_RLM_HISTORY_EVENTS + 5)
    ]
    raw_history = dspy.History(messages=events)
    processed = rlm_history_processor(raw_history)
    assert len(processed.messages) <= _MAX_RLM_HISTORY_EVENTS

    # 2. Serialization and deserialization roundtrip
    serialized = serialize_rlm_turn_history(processed)
    assert isinstance(serialized, list)
    assert len(serialized) > 0

    reconstructed = deserialize_rlm_history([{"rlm_history": serialized}])
    assert reconstructed is not None
    assert len(reconstructed.messages) == len(serialized)


# =========================================================================
# (e) fleet-program-b2 ReAnchor roundtrip
# =========================================================================
def test_dspy35_capability_e_fleet_program_b2_reanchor_roundtrip() -> None:
    manager = DaytonaSandboxManager("k", "o", "v", "b2-test")
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    runtime = FleetRuntime(manager, lm, lm)

    # Attach calibrated decision field (Choice parameter is weights)
    runtime.program.routing.predict.fields = {"route": {"weights": {"direct": 1.0, "flex": 1.5, "rlm": 0.8}}}

    envelope_bytes = program_envelope(runtime.program)
    data = json.loads(envelope_bytes)
    assert data["program_schema"] == "fleet-program-b2"
    assert data["program_schema"] == PROGRAM_SCHEMA

    # Inspect tunable components includes ReAnchor calibration
    components = tunable_components(runtime.program)
    assert "routing.predict" in components
    assert "[decision_calibration]" in components["routing.predict"]

    # Verify envelope loader roundtrips state
    target_program = runtime.program.deepcopy()
    load_program_envelope(target_program, envelope_bytes)
    assert target_program.dump_state() == runtime.program.dump_state()


# =========================================================================
# (f) jev_decide_batched (Choice & boolean Noul)
# =========================================================================
def test_dspy35_capability_f_jev_decide_batched(monkeypatch) -> None:
    jev_lm = SimpleNamespace(model="typesafe:jev", supports_decision_requests=True)
    manager = DaytonaSandboxManager("k", "o", "v", "jev-batched", jev_lm=jev_lm)
    interpreter = DaytonaInterpreter(manager)

    # 1. With choices (Choice classification)
    sig = item_decision(["fruit", "vegetable"])
    choice_type = sig.model_fields["decision"].annotation

    def fake_choice_batch(_self, examples, *, num_threads=1, timeout=0):  # noqa: ARG001
        return [SimpleNamespace(decision=choice_type(value="fruit", confidence=0.88)) for _ in examples]

    monkeypatch.setattr(dspy.Predict, "batch", fake_choice_batch)
    results = interpreter._jev_decide_batched([["apple", "banana"], "classify", ["fruit", "vegetable"]], {})
    assert len(results) == 2
    assert results[0]["decision"] == "fruit"
    assert results[0]["confidence"] == 0.88

    # 2. Without choices (boolean Noul classification)
    noul_type = make_noul_type("yes", "no")

    def fake_noul_batch(_self, examples, *, num_threads=1, timeout=0):  # noqa: ARG001
        return [SimpleNamespace(decision=noul_type(value=True, confidence=0.92, probability=0.92)) for _ in examples]

    monkeypatch.setattr(dspy.Predict, "batch", fake_noul_batch)
    results_bool = interpreter._jev_decide_batched([["apple", "banana"], "is edible?"], {})
    assert len(results_bool) == 2
    assert results_bool[0]["decision"] is True
    assert results_bool[0]["confidence"] == 0.92


# =========================================================================
# (g) workspace.load_image
# =========================================================================
def test_dspy35_capability_g_workspace_load_image(tmp_path: Path, monkeypatch) -> None:
    ws_dir = tmp_path / "workspace"
    files_dir = ws_dir / "files"
    files_dir.mkdir(parents=True)

    # Valid PNG (header: 89 50 4E 47 0D 0A 1A 0A)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    png_path = files_dir / "sample.png"
    png_path.write_bytes(png_bytes)

    # Valid JPEG (header: FF D8 FF)
    jpg_bytes = b"\xff\xd8\xff" + b"\x00" * 32
    jpg_path = files_dir / "photo.jpg"
    jpg_path.write_bytes(jpg_bytes)

    # Invalid text file
    txt_path = files_dir / "doc.txt"
    txt_path.write_text("not an image")

    ns = {
        "__FLEET_MAX_FILE_BYTES__": MAX_WORKSPACE_FILE_BYTES,
        "__FLEET_WORKSPACE_ROOTS__": _ALLOWED_WORKSPACE_ROOTS,
    }
    exec(WORKSPACE_CODE, ns)
    ws = ns["workspace"]
    monkeypatch.setattr(ws, "resolve", lambda rel: ws_dir / rel)

    # Load PNG
    img_png = ws.load_image("files/sample.png")
    assert isinstance(img_png, dspy.Image)
    assert img_png.url.startswith("data:image/png;base64,")

    # Load JPEG
    img_jpg = ws.load_image("files/photo.jpg")
    assert isinstance(img_jpg, dspy.Image)
    assert img_jpg.url.startswith("data:image/jpeg;base64,")

    # Oversized check
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        ws.load_image("files/sample.png", max_bytes=10)

    # Unsupported format check
    with pytest.raises(ValueError, match="unsupported image format"):
        ws.load_image("files/doc.txt")


# =========================================================================
# (h) Skill-grounded InstructionProposer
# =========================================================================
def test_dspy35_capability_h_skill_grounded_instruction_proposer(tmp_path: Path) -> None:
    fleet_skills = tmp_path / ".fleet" / "skills"
    skill_a = fleet_skills / "data-cleaning"
    skill_a.mkdir(parents=True)
    (skill_a / "SKILL.md").write_text("---\nname: data-cleaning\ndescription: Clean datasets\n---\nRules")

    agent_skills = tmp_path / ".agents" / "skills"
    agent_skills.mkdir(parents=True)
    (agent_skills / "dev-guidance").mkdir()
    (agent_skills / "dev-guidance" / "SKILL.md").write_text("dev only")

    adapter = dspy.XMLAdapter(use_native_function_calling=True)
    proposer = build_instruction_proposer(adapter=adapter, skills_dir=fleet_skills)
    assert proposer.adapter is adapter
    assert proposer.skills is not None
    assert len(proposer.skills) == 1
    assert proposer.skills[0].name == "data-cleaning"
    assert proposer.truncate_history_outputs is True

    # .agents skills must never be loaded
    bad_proposer = build_instruction_proposer(adapter=adapter, skills_dir=agent_skills)
    assert not bad_proposer.skills


# =========================================================================
# (i) Two-stage GEPA + ReAnchor calibration
# =========================================================================
def test_dspy35_capability_i_twostage_gepa_plus_reanchor(monkeypatch) -> None:

    manager = DaytonaSandboxManager("k", "o", "v", "reanchor-test")
    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    runtime = FleetRuntime(manager, lm, lm)

    calibrated_prog = runtime.program.deepcopy()
    calibrated_prog.routing.predict.fields = {"route": {"weights": {"direct": 2.0, "flex": 1.0, "rlm": 1.0}}}

    class MockReAnchor:
        def __init__(self, metric, num_threads=1, log_dir=None, require_cache=False):  # noqa: ARG002
            self.report = {"val_score_before": 0.8, "val_score": 0.95}

        def compile(self, student, trainset=None, valset=None):  # noqa: ARG002
            return SimpleNamespace(program=calibrated_prog)

    monkeypatch.setattr("dspy.experimental.ReAnchor", MockReAnchor)

    # Verify decision support check
    jev = SimpleNamespace(supports_decision_requests=True)
    assert supports_decision_requests(jev) is True

    # Run ReAnchor stage
    reanchor = MockReAnchor(lambda _g, _p: 1.0)
    res = reanchor.compile(SimpleNamespace(program=runtime.program), trainset=[], valset=[])
    val_before = reanchor.report["val_score_before"]
    val_after = reanchor.report["val_score"]
    adopted = val_after >= val_before and res.program.dump_state() != runtime.program.dump_state()
    assert adopted is True
    assert res.program.routing.predict.fields["route"]["weights"]["direct"] == 2.0
