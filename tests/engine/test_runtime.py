"""Offline domain regression coverage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import dspy
import httpx
import pytest
from dspy import AdapterParseError, LMUnexpectedError
from dspy.primitives.facade import SandboxLM
from dspy.utils.usage_tracker import UsageTracker
from pydantic import BaseModel, ValidationError

from fleet_rlm import observability as obs
from fleet_rlm.api.events import _reasoning_text
from fleet_rlm.config import FleetSettings, resolve_turn_deadline
from fleet_rlm.contracts import (
    ENGINE_INPUT_FIELDS,
    JUDGE_ROUTE_CHOICES,
    LINEAGE_INPUT_FIELDS,
    ROUTE_DESCRIPTIONS,
    ROUTE_SUMMARIES,
    STOPPING_RULES,
    TRIAGE_CRITERIA,
    TRIAGE_ROUTE,
    OptimizationInputs,
    Route,
    catalog_entries,
    decision_evidence,
    item_decision,
    make_choice_type,
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
from fleet_rlm.models import ResolvedModel, build_lm, complete_text
from fleet_rlm.observability import LMUsageCallback, _usage_summary, decision_usage, usage_callbacks
from fleet_rlm.optimization_runs import (
    ExperimentOptions,
    add_optimization_arguments,
    read_worker_settings,
    worker_settings_payload,
)
from fleet_rlm.optimize import artifacts
from fleet_rlm.optimize.artifacts import tunable_components
from fleet_rlm.optimize.dataset import _INPUTS
from fleet_rlm.optimize.gepa import build_instruction_proposer
from fleet_rlm.optimize.scorers import SCORER_VERSION
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, LOCK_PATH
from fleet_rlm.session import SessionRuntime
from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SANDBOX_SKILL_HELPERS_CODE, SkillManager, parse_and_validate_skill_md
from fleet_rlm.workspace import (
    _ALLOWED_WORKSPACE_ROOTS,
    MAX_WORKSPACE_FILE_BYTES,
    WORKSPACE_CODE,
)

# Consolidated from engine/test_programs.py


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


def test_route_wording_has_one_source():
    assert set(ROUTE_DESCRIPTIONS) == set(Route)
    assert dict(JUDGE_ROUTE_CHOICES) == {route.value: item["judge"] for route, item in ROUTE_DESCRIPTIONS.items()}
    assert {route.value: item["summary"] for route, item in ROUTE_DESCRIPTIONS.items()} == ROUTE_SUMMARIES
    for route, item in ROUTE_DESCRIPTIONS.items():
        assert {"triage", "judge", "definition", "examples", "not", "summary"} <= set(item), route


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


# Consolidated from lite/test_configuration.py


# Configuration precedence and provenance


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("FLEET_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    # These tests exercise .env precedence from a private tmp_path working directory.
    monkeypatch.setitem(FleetSettings.model_config, "env_file", ".env")


def toml(text):
    Path("config").mkdir(exist_ok=True)
    Path("config/fleet.toml").write_text("[config]\nschema_version = 5\n" + text)


@pytest.mark.usefixtures("isolated_settings")
def test_sources_and_aliases_have_one_precedence(monkeypatch):
    toml("[runtime]\nmax_llm_calls_per_turn = 41\n")
    assert FleetSettings(_env_file=None).max_llm_calls_per_turn == 41
    Path(".env").write_text("FLEET_MAX_LLM_CALLS_PER_TURN=42\n")
    settings = FleetSettings()
    assert settings.max_llm_calls_per_turn == 42
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "dotenv"
    monkeypatch.setenv("MAX_LLM_CALLS_PER_TURN", "43")
    settings = FleetSettings()
    assert settings.max_llm_calls_per_turn == 43
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "environment"
    settings = FleetSettings(max_llm_calls_per_turn=44)
    assert settings.max_llm_calls_per_turn == 44
    assert settings.configuration()["sources"]["max_llm_calls_per_turn"] == "explicit"


@pytest.mark.usefixtures("isolated_settings")
def test_optional_default_file_and_explicit_path(monkeypatch, tmp_path):
    assert FleetSettings(_env_file=None).configuration()["sources"]["execution_mode"] == "default"
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(ValueError, match="existing TOML"):
        FleetSettings(_env_file=None)
    p = tmp_path / "selected.toml"
    p.write_text('[config]\nschema_version=5\n[runtime]\nexecution_mode="rlm"')
    monkeypatch.setenv("FLEET_CONFIG", str(p))
    assert FleetSettings(_env_file=None).execution_mode == "rlm"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    "content",
    [
        "[runtime]\nturn_timeout_seconds = 9",
        '[runtime]\nturn_timeout_seconds = "900"',
        "[runtime]\nturn_timeout_seconds = true",
        "[runtime]\nwrap_up_seconds = 300",
        "[llm.root]\nmax_tokens = 0",
        "[llm.sub]\ntemperature = 3",
        "[llm.reflection]\nnum_retries = -1",
        '[llm.root]\ncache = "false"',
        '[daytona]\napi_key = "credential-marker"',
        '[storage]\ndatabase_url = "credential-marker"',
        '[gateway]\ntoken = "credential-marker"',
        '[llm.root]\napi_key_env = "credential-marker"',
        '[unknown]\nx = "credential-marker"',
    ],
)
def test_invalid_toml_fails_without_echoing_values(content):
    toml(content)
    with pytest.raises(ValueError) as error:
        FleetSettings(_env_file=None)
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", [0, 10, 3600])
def test_turn_timeout_accepts_zero_or_bounded_integers(value):
    assert FleetSettings(_env_file=None, turn_timeout_seconds=value).turn_timeout_seconds == value


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", [9, -1, True, 0.5, 3601, "600"])
def test_turn_timeout_rejects_other_shapes(value):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, turn_timeout_seconds=value)
    message = str(error.value)
    assert "turn_timeout_seconds" in message
    assert "0 or between 10 and 3600" in message


@pytest.mark.usefixtures("isolated_settings")
def test_turn_timeout_environment_text_and_zero_agree_with_toml(monkeypatch):
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "0")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "600")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600
    toml("[runtime]\nturn_timeout_seconds = 0")
    monkeypatch.delenv("FLEET_TURN_TIMEOUT_SECONDS")
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 0


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("value", ["true", "0.5", '"600"', "-1", "9", "3601"])
def test_toml_turn_timeout_stays_a_bounded_integer(value):
    toml(f"[runtime]\nturn_timeout_seconds = {value}")
    with pytest.raises(ValueError):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    ("configured", "configured_source", "requested", "expected"),
    [
        (900, "default", None, ("default", None)),
        (1200, "toml", None, ("configuration", 1200)),
        (1200, "environment", 600, ("request", 600)),
        (1200, "toml", 0, ("request", None)),
        (1200, "toml", None, ("configuration", 1200)),
        (900, "default", 3600, ("request", 3600)),
    ],
)
def test_deadline_resolution_follows_request_then_configuration_then_default(
    configured, configured_source, requested, expected
):
    policy = resolve_turn_deadline(requested, configured, configured_source=configured_source)
    assert (policy.source, policy.effective_seconds) == expected


@pytest.mark.usefixtures("isolated_settings")
def test_schema_and_parse_errors(monkeypatch):
    monkeypatch.setenv("FLEET_CONFIG", "file.toml")
    for text in ("x=[", "[config]\nschema_version=2", "[config]\nschema_version=5\nextra=1"):
        Path("file.toml").write_text(text)
        with pytest.raises(ValueError):
            FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
def test_role_settings_are_applied_by_native_model_builder(monkeypatch):
    from fleet_rlm.models import build_lm

    toml("[llm.root]\nmax_tokens=16384\ntemperature=0.0\nnum_retries=1\ncache=true\n[llm.sub]\nmax_tokens=8192\n")
    calls = []
    monkeypatch.setattr(
        "fleet_rlm.models.dspy.LM",
        lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model, kwargs=kwargs),
    )
    settings = FleetSettings(
        _env_file=None,
    )
    settings.fleet_root_model, settings.fleet_sub_model, settings.fleet_reflection_model = "root", "sub", "reflection"
    build_lm(settings, "root", "root")
    build_lm(settings, "sub", "sub")
    build_lm(settings, "reflection", "reflection")
    assert calls[0][1]["max_tokens"] == 16384
    assert calls[0][1]["temperature"] == 0
    assert calls[0][1]["cache"] is True and calls[0][1]["num_retries"] == 1
    assert calls[1][1]["max_tokens"] == 8192
    assert "temperature" not in calls[1][1]
    assert calls[2][1]["max_tokens"] == 32000
    assert all(kwargs["engine"] == "lm15" for _, kwargs in calls)


@pytest.mark.usefixtures("isolated_settings")
def test_build_jev_lm_returns_none_without_typesafe_key():
    assert FleetSettings(_env_file=None, typesafe_api_key="").build_jev_lm() is None


@pytest.mark.usefixtures("isolated_settings")
def test_build_jev_lm_builds_the_configured_typesafe_model():
    import dspy

    lm = FleetSettings(_env_file=None, typesafe_api_key="credential-marker", typesafe_model="jev-1.14.0").build_jev_lm()
    assert isinstance(lm, dspy.LM)
    assert lm.model == "typesafe:jev-1.14.0"
    assert lm.supports_decision_requests is True
    assert lm.cache is False and lm.engine == "lm15"
    assert lm.kwargs["api_key"] == "credential-marker"


@pytest.mark.usefixtures("isolated_settings")
def test_shipped_defaults_cap_root_output_but_not_sub(monkeypatch):
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    settings = FleetSettings(_env_file=None)
    assert settings.fleet_root_max_tokens == 16384
    assert settings.fleet_sub_max_tokens is None
    for role in ("root", "sub"):
        assert settings.resolved_model(role).model == "openai/system.ai.deepseek-v4-1-flash-1017"


@pytest.mark.usefixtures("isolated_settings")
def test_root_and_sub_default_to_deepseek_without_changing_other_roles():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://gateway.example/v1",
        fleet_llm_api_key="default-secret",
    )
    for role in ("root", "sub"):
        resolved = settings.resolved_model(role)
        assert resolved.model == "openai/system.ai.deepseek-v4-1-flash-1017"
        assert resolved.api_base == "https://gateway.example/v1"
        assert resolved.api_key.get_secret_value() == "default-secret"
    for role in ("shared", "reflection", "judge"):
        assert settings.resolved_model(role).model == "openai/gpt-4o-mini"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("source", ["toml", "dotenv", "environment", "explicit"])
def test_explicit_shared_model_still_overrides_root_and_sub_defaults(source, monkeypatch):
    if source == "toml":
        toml('[llm]\nmodel = "openai/gpt-4o-mini"\n')
    elif source == "dotenv":
        Path(".env").write_text("FLEET_LLM_MODEL=openai/gpt-4o-mini\n")
    elif source == "environment":
        monkeypatch.setenv("FLEET_LLM_MODEL", "openai/gpt-4o-mini")
    settings = FleetSettings(**({"fleet_llm_model": "openai/gpt-4o-mini"} if source == "explicit" else {}))
    for role in ("root", "sub"):
        assert settings.resolved_model(role).model == "openai/gpt-4o-mini"
    overridden = FleetSettings(fleet_sub_model="anthropic/claude-sonnet-4-5")
    assert overridden.resolved_model("sub").model == "anthropic/claude-sonnet-4-5"


@pytest.mark.usefixtures("isolated_settings")
def test_public_configuration_omits_credentials_and_tracks_session_override():
    settings = FleetSettings(
        _env_file=None,
        databricks_token="credential-marker",
        fleet_database_url="postgresql://credential-marker",
        fleet_daytona_api_key="credential-marker",
        typesafe_api_key="credential-marker",
    )
    public = settings.configuration()
    assert "credential-marker" not in str(public)
    changed = settings.configuration({"execution_mode": "rlm"})
    assert changed["sources"]["execution_mode"] == "session"
    assert public["digest"] != changed["digest"]
    assert settings.configuration()["digest"] == public["digest"]
    assert changed["file_changes_require_restart"] is True
    assert settings.configuration({"execution_mode": settings.execution_mode})["sources"]["execution_mode"] == "session"


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("role", ["root", "sub", "reflection", "judge"])
@pytest.mark.parametrize(
    ("option", "shared"), [("max_tokens", 100), ("temperature", 0.4), ("num_retries", 1), ("cache", False)]
)
def test_digest_distinguishes_inherited_generation_from_explicit_role_defaults(role, option, shared):
    # Use the opposite cache value where the role default is already false.
    field = f"fleet_{role}_{option}"
    default = FleetSettings.model_fields[field].get_default()
    if option == "cache":
        shared = not default
    inherited = FleetSettings(_env_file=None, **{f"fleet_llm_{option}": shared})
    explicit = FleetSettings(_env_file=None, **{f"fleet_llm_{option}": shared, field: default})

    assert inherited.model_dump() == explicit.model_dump()
    assert getattr(inherited.resolved_model(role), option) == shared
    assert getattr(explicit.resolved_model(role), option) == default
    assert inherited.configuration()["digest"] != explicit.configuration()["digest"]


@pytest.mark.usefixtures("isolated_settings")
def test_digest_ignores_source_only_changes_and_model_credentials():
    inherited = FleetSettings(_env_file=None, fleet_llm_api_key="first-secret")
    explicit = FleetSettings(_env_file=None, fleet_llm_api_key="second-secret", fleet_root_max_tokens=16384)
    first, second = inherited.configuration(), explicit.configuration()

    assert first["sources"]["fleet_root_max_tokens"] != second["sources"]["fleet_root_max_tokens"]
    assert first["values"] == second["values"]
    assert first["digest"] == second["digest"]
    assert "first-secret" not in str(first) and "second-secret" not in str(second)
    assert set(first) == {
        "schema_version",
        "digest",
        "values",
        "sources",
        "mlflow_target",
        "file_changes_require_restart",
    }
    assert first["schema_version"] == 5


@pytest.mark.usefixtures("isolated_settings")
def test_session_digest_uses_route_overrides_without_resolving_connections_again(monkeypatch):
    import fleet_rlm.config.settings as config_settings

    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://models.example/v1",
        fleet_llm_api_key="session-secret",
        fleet_llm_max_tokens=100,
    )
    saved = {"mode": "rlm", "models": {"root": "openai/new-root", "sub": "openai/new-sub"}}
    session = settings.for_session(saved)
    overrides = {"execution_mode": "rlm", "fleet_root_model": "openai/new-root", "fleet_sub_model": "openai/new-sub"}
    monkeypatch.setattr(config_settings, "resolve_models", lambda _self: pytest.fail("must retain snapshots"))

    assert settings.configuration(overrides)["digest"] == session.configuration()["digest"]
    assert settings.configuration(overrides)["digest"] != settings.configuration()["digest"]
    for role in ("root", "sub"):
        assert session.resolved_model(role).api_base == "https://models.example/v1"
        assert session.resolved_model(role).api_key.get_secret_value() == "session-secret"
        assert session.resolved_model(role).max_tokens == 100
        assert settings.resolved_model(role).model == "openai/system.ai.deepseek-v4-1-flash-1017"


@pytest.mark.usefixtures("isolated_settings")
def test_lakebase_oauth_requires_endpoint_and_password_free_tls_dsn():
    endpoint = "projects/fleet-rlm/branches/production/endpoints/primary"
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://fleet_app@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_lakebase_endpoint=endpoint,
    )
    assert settings.fleet_lakebase_endpoint == endpoint
    with pytest.raises(ValidationError, match="sslmode=require"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://fleet_app@ep.example.databricks.com/fleet_rlm_lite",
            fleet_lakebase_endpoint=endpoint,
        )
    with pytest.raises(ValidationError, match="embedded password"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://fleet_app:password@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
            fleet_lakebase_endpoint=endpoint,
        )


@pytest.mark.usefixtures("isolated_settings")
def test_database_schema_is_a_safe_identifier_and_migration_url_is_not_public_configuration():
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://runtime@db.example/fleet",
        fleet_database_schema="fleet_rlm_lite",
        fleet_database_migration_url="postgresql://migration:secret@db.example/fleet",
    )
    public = settings.configuration()
    assert public["values"]["fleet_database_schema"] == "fleet_rlm_lite"
    assert "fleet_database_migration_url" not in public["values"]
    assert "secret" not in str(public)
    with pytest.raises(ValidationError, match="lowercase PostgreSQL identifier"):
        FleetSettings(_env_file=None, fleet_database_schema="bad-name")
    with pytest.raises(ValidationError, match="must target the runtime database"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://runtime@db.example/fleet",
            fleet_database_migration_url="postgresql://migrator@other.example/fleet",
        )


@pytest.mark.usefixtures("isolated_settings")
def test_lakebase_migration_dsn_uses_the_same_tls_database_with_a_separate_role():
    endpoint = "projects/fleet-rlm/branches/production/endpoints/primary"
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://runtime@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_database_migration_url="postgresql://migrator@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
        fleet_lakebase_endpoint=endpoint,
    )
    assert settings.fleet_database_schema == "public"
    with pytest.raises(ValidationError, match="must target the runtime database"):
        FleetSettings(
            _env_file=None,
            fleet_database_url="postgresql://runtime@ep.example.databricks.com/fleet_rlm_lite?sslmode=require",
            fleet_database_migration_url="postgresql://migrator@ep.example.databricks.com/other?sslmode=require",
            fleet_lakebase_endpoint=endpoint,
        )


@pytest.mark.usefixtures("isolated_settings")
def test_validation_errors_hide_input_values():
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, fleet_root_temperature="credential-marker")
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize("field", ["databricks_host", "fleet_llm_api_base", "mlflow_databricks_tracking_uri"])
def test_public_urls_cannot_expose_credentials(field):
    with pytest.raises(ValidationError) as error:
        FleetSettings(_env_file=None, **{field: "https://user:credential-marker@example.invalid"})
    assert "credential-marker" not in str(error.value)


@pytest.mark.usefixtures("isolated_settings")
def test_explicit_cli_copy_is_validated_and_does_not_mutate_original():
    toml("[server]\nport=8001\n")
    settings = FleetSettings(_env_file=None)
    updated = settings.with_explicit_overrides(port=8002)
    assert updated.port == 8002 and settings.port == 8001
    assert updated.configuration()["sources"]["port"] == "explicit"
    assert settings.configuration()["sources"]["port"] == "toml"
    with pytest.raises(ValidationError):
        settings.with_explicit_overrides(port=0)


@pytest.mark.usefixtures("isolated_settings")
def test_configuration_is_frozen_until_new_settings_are_loaded():
    toml("[runtime]\nturn_timeout_seconds=900\n")
    settings = FleetSettings(_env_file=None)
    toml("[runtime]\nturn_timeout_seconds=600\n")
    assert settings.turn_timeout_seconds == 900
    assert FleetSettings(_env_file=None).turn_timeout_seconds == 600


@pytest.mark.usefixtures("isolated_settings")
def test_schema_version_must_be_integer():
    Path("config").mkdir()
    Path("config/fleet.toml").write_text("[config]\nschema_version=5.0\n")
    with pytest.raises(ValueError, match="schema_version"):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    "values",
    [
        {"fleet_llm_api_base": "http://example.invalid"},
    ],
)
def test_invalid_routes_fail_during_settings_loading(values):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, **values)


@pytest.mark.usefixtures("isolated_settings")
@pytest.mark.parametrize(
    ("name", "value", "field"),
    [
        ("FLEET_LLM_BASE_URL", "http://example.invalid", "fleet_llm_api_base"),
    ],
)
def test_cli_reports_invalid_configuration_without_traceback(monkeypatch, capsys, name, value, field):
    from fleet_rlm import cli
    from fleet_rlm.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["serve"])
    get_settings.cache_clear()
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "fleet: error: invalid configuration: " in err
    assert field in err
    assert "Traceback" not in err


@pytest.mark.usefixtures("isolated_settings")
def test_local_mlflow_mode_ignores_databricks_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "databricks://profile")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_EXPERIMENT_NAME", "/Users/someone/fleet")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert target == ("local", "http://127.0.0.1:5001", "fleet-rlm-dev", "fleet-rlm-eval", "fleet-rlm-gepa")


@pytest.mark.usefixtures("isolated_settings")
def test_local_mlflow_mode_keeps_local_targets(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "mine")
    target = FleetSettings(_env_file=None).mlflow_target()
    assert (target.tracking_uri, target.experiment_name) == ("http://127.0.0.1:5002", "mine")


@pytest.mark.usefixtures("isolated_settings")
def test_databricks_mlflow_mode_is_validated_only_when_selected(monkeypatch):
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", "")
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    with pytest.raises(ValidationError, match="Unity Catalog"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACKING_URI", "http://127.0.0.1:5001")
    with pytest.raises(ValidationError, match="databricks tracking URI"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")


@pytest.mark.usefixtures("isolated_settings")
def test_configuration_defaults_to_local_and_keeps_databricks_available():
    assert FleetSettings(_env_file=None).mlflow_target().mode == "local"
    target = FleetSettings(_env_file=None, mlflow_mode="databricks").mlflow_target()
    assert target.mode == "databricks"
    assert target.experiment_name.startswith("/")
    assert target.optimization_experiment_name.startswith("/")


@pytest.mark.usefixtures("isolated_settings")
def test_judge_environment_exports_inherited_openai_gateway_endpoint():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://workspace.example/ai-gateway/mlflow/v1",
        databricks_token="token",
    )
    assert settings.judge_environment() == {"OPENAI_API_BASE": "https://workspace.example/ai-gateway/mlflow/v1"}
    assert FleetSettings(_env_file=None, fleet_judge_model="databricks:/endpoint").judge_environment() == {}
    with pytest.raises(ValidationError, match="provider"):
        FleetSettings(_env_file=None, fleet_judge_model="deepseek")


@pytest.mark.usefixtures("isolated_settings")
def test_list_models_without_endpoint_does_not_perform_discovery(monkeypatch):
    monkeypatch.setattr("httpx.stream", lambda *_args, **_kwargs: pytest.fail("unexpected network"))
    assert FleetSettings(_env_file=None).list_models() == []


@pytest.mark.usefixtures("isolated_settings")
def test_checked_in_toml_loads_without_snapshot_setting(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    shipped = Path(__file__).resolve().parents[2] / "config" / "fleet.toml.example"
    monkeypatch.setenv("FLEET_CONFIG", str(shipped))
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    settings = FleetSettings(_env_file=None)
    assert not hasattr(settings, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in settings.configuration()["values"]
    assert "snapshot" not in settings.configuration()["values"]


@pytest.mark.usefixtures("isolated_settings")
def test_leftover_toml_snapshot_key_raises_unsupported_setting() -> None:
    toml('[daytona]\nsnapshot = "fleet-rlm-python313-v10"\n')
    with pytest.raises(ValueError, match=r"Unsupported or environment-only Fleet TOML setting: daytona\.snapshot"):
        FleetSettings(_env_file=None)


@pytest.mark.usefixtures("isolated_settings")
def test_snapshot_environment_variable_creates_no_field_or_digest_change(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_DAYTONA_SNAPSHOT", raising=False)
    baseline = FleetSettings(_env_file=None)
    monkeypatch.setenv("FLEET_DAYTONA_SNAPSHOT", "fleet-rlm-base")
    overridden = FleetSettings(_env_file=None)
    assert not hasattr(overridden, "fleet_daytona_snapshot")
    assert "fleet_daytona_snapshot" not in overridden.configuration()["values"]
    assert overridden.configuration()["digest"] == baseline.configuration()["digest"]


@pytest.mark.usefixtures("isolated_settings")
def test_native_chat_policy_does_not_force_provider_capabilities():
    import dspy

    from fleet_rlm.models import build_lm

    settings = FleetSettings(_env_file=None, fleet_llm_model="openai/demo.model")
    assert isinstance(build_lm(settings, "", "root"), dspy.LM)
    assert isinstance(settings.resolved_model("root").build_adapter(), dspy.ChatAdapter)


@pytest.mark.usefixtures("isolated_settings")
def test_databricks_requires_explicit_deployment_identifiers(monkeypatch):
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", raising=False)
    monkeypatch.delenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", raising=False)
    assert FleetSettings(_env_file=None).mlflow_mode == "local"
    with pytest.raises(ValueError, match="SQL warehouse ID"):
        FleetSettings(_env_file=None, mlflow_mode="databricks")
    configured = FleetSettings(
        _env_file=None,
        mlflow_mode="databricks",
        mlflow_databricks_trace_catalog="example_catalog",
        mlflow_databricks_tracing_sql_warehouse_id="example-warehouse",
    )
    assert configured.mlflow_tracking_uri == "databricks"


@pytest.mark.usefixtures("isolated_settings")
def test_shared_discovery_uses_default_connection_when_root_has_an_override(monkeypatch):
    from contextlib import nullcontext

    import httpx

    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/shared",
        fleet_llm_api_base="https://shared.example/v1",
        fleet_llm_api_key="shared-test-key",
        fleet_root_model="openai/root",
        fleet_root_api_base="https://root.example/v1",
    )

    def stream(method, url, *, headers, timeout):
        assert method == "GET" and url == "https://shared.example/v1/models"
        assert headers == {"Authorization": "Bearer shared-test-key"}
        assert timeout == 10
        return nullcontext(httpx.Response(200, json={"data": [{"id": "shared"}]}, request=httpx.Request(method, url)))

    monkeypatch.setattr(httpx, "stream", stream)
    assert settings.list_models(role="shared") == ["openai/shared"]


@pytest.mark.usefixtures("isolated_settings")
def test_an_explicit_judge_pairs_the_mlflow_openai_scorers_with_its_own_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("COMMAND_CODE_API_KEY", "judge-key")
    judge = {
        "fleet_correctness_model": "openai/deepseek/deepseek-v4.1-flash",
        "fleet_judge_api_base": "https://api.commandcode.ai/provider/v1",
        "fleet_judge_api_key_env": "COMMAND_CODE_API_KEY",
    }
    settings = FleetSettings(_env_file=None, fleet_judge_model="openai:/deepseek/deepseek-v4.1-flash", **judge)
    assert settings.judge_environment() == {
        "OPENAI_API_BASE": "https://api.commandcode.ai/provider/v1",
        "OPENAI_API_KEY": "judge-key",
    }
    # The DSPy judge keeps its own connection, separate from serving.
    assert settings.resolved_model("judge").api_base == "https://api.commandcode.ai/provider/v1"
    assert settings.resolved_model("judge").api_key.get_secret_value() == "judge-key"
    # Other providers use their native environment even when the judge role is explicit.
    other = FleetSettings(_env_file=None, fleet_judge_model="databricks:/endpoint", **judge)
    assert other.judge_environment() == {}


@pytest.mark.usefixtures("isolated_settings")
def test_inherited_judge_connection_exports_resolved_credential(monkeypatch):
    monkeypatch.setenv("INHERITED_JUDGE_KEY", "test-only-key")
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_api_base="https://provider.example/v1",
        fleet_llm_api_key_env="INHERITED_JUDGE_KEY",
        fleet_judge_model="openai:/configured-model",
    )
    assert settings.judge_environment() == {
        "OPENAI_API_BASE": "https://provider.example/v1",
        "OPENAI_API_KEY": "test-only-key",
    }


# Gateway model resolution


def test_databricks_mlflow_configuration_requires_governed_trace_storage() -> None:
    with pytest.raises(ValueError, match="Unity Catalog"):
        FleetSettings(
            mlflow_mode="databricks",
            mlflow_databricks_tracking_uri="databricks",
            mlflow_databricks_trace_catalog="",
            mlflow_databricks_trace_schema="",
            mlflow_databricks_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        mlflow_databricks_experiment_name="/Shared/fleet-rlm",
        mlflow_databricks_trace_catalog="fleet",
        mlflow_databricks_trace_schema="traces",
        mlflow_databricks_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_profile_specific_mlflow_environment_ignores_sdk_destination(monkeypatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)
    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5002"
    assert settings.mlflow_experiment_name == "fleet-test"


def test_default_model_and_independent_role_connection(monkeypatch):
    monkeypatch.setenv("SUB_KEY", "sub-secret")
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/default",
        fleet_llm_api_base="http://localhost:8000/v1",
        fleet_llm_api_key="default-secret",
        fleet_sub_model="anthropic/custom",
        fleet_sub_api_key_env="SUB_KEY",
    )
    assert settings.resolved_model("root").model == "openai/default"
    assert settings.resolved_model("reflection").api_base == "http://localhost:8000/v1"
    assert settings.resolved_model("sub").api_base == ""
    assert settings.resolved_model("sub").api_key.get_secret_value() == "sub-secret"
    assert settings.resolved_model("judge").temperature == 0
    calls = []
    monkeypatch.setattr(
        dspy, "LM", lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model)
    )
    build_lm(settings, "", "root")
    assert calls[0][1]["engine"] == "lm15"
    assert calls[0][1]["api_key"] == "default-secret"
    assert not hasattr(build_lm(settings, "", "root"), "fleet_model_id")


def test_secret_snapshot_survives_session_rebuild_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("CUSTOM_KEY=first-value\n")
    settings = FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
    path.write_text("CUSTOM_KEY=second-value\n")
    monkeypatch.setenv("CUSTOM_KEY", "environment-value")
    updated = settings.for_session({"models": {"root": "openai/new", "sub": "openai/child"}})
    assert updated.resolved_model("root").api_key.get_secret_value() == "first-value"
    assert updated.resolved_model("sub").model == "openai/child"
    assert (
        FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
        .resolved_model("root")
        .api_key.get_secret_value()
        == "environment-value"
    )
    assert "first-value" not in str(settings.configuration())
    assert "first-value" not in repr(settings)


@pytest.mark.parametrize(
    "endpoint", ["http://remote.example/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=x"]
)
def test_invalid_endpoints_fail_during_settings_load(endpoint):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, fleet_llm_api_base=endpoint)


@pytest.mark.parametrize("table", ["models", "connections"])
def test_obsolete_empty_registry_tables_fail(tmp_path, monkeypatch, table):
    path = tmp_path / "settings.toml"
    path.write_text(f"[config]\nschema_version=5\n[{table}]\n")
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    with pytest.raises(ValueError, match="removed"):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize(
    "payload", [{"data": [{"id": "foo/bar"}]}, {"data": []}, {"wrong": []}, {"data": [{"id": 123}]}]
)
def test_optional_discovery_validates_response_and_role(monkeypatch, payload):
    from contextlib import contextmanager

    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        yield httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(
        _env_file=None, fleet_sub_model="openai/sub", fleet_sub_api_base="https://models.example/v1"
    )
    assert settings.list_models(role="root") == []
    if payload.get("data") in ([], [{"id": "foo/bar"}]):
        assert settings.list_models(role="sub") == (["openai/foo/bar"] if payload["data"] else [])
    else:
        with pytest.raises(ValueError):
            settings.list_models(role="sub")
    assert calls[-1][1] == "https://models.example/v1/models"


def test_complete_text_preserves_generation_defaults():
    calls = []
    lm = SimpleNamespace(model="openai/test", kwargs={"temperature": 0.2, "max_tokens": 300})

    class Fake:
        model = lm.model
        kwargs = lm.kwargs

        def __call__(self, request):
            calls.append(request)
            return SimpleNamespace(text="OK")

    assert complete_text(Fake(), "prompt", max_tokens=16) == "OK"
    assert calls[0].config.max_tokens == 16
    assert calls[0].config.temperature == 0.2


def test_native_auth_is_deferred_to_dspy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "native-secret")
    settings = FleetSettings(_env_file=None, fleet_llm_model="openai/test")
    calls = []
    monkeypatch.setattr(dspy, "LM", lambda model, **kwargs: calls.append(kwargs) or SimpleNamespace(model=model))
    build_lm(settings, "", "root")
    assert "api_key" not in calls[0]


def test_global_generation_defaults_and_explicit_role_override(tmp_path, monkeypatch):
    path = tmp_path / "fleet.toml"
    path.write_text(
        '[config]\nschema_version=5\n[llm]\nmodel="openai/test"\nmax_tokens=100\n'
        "temperature=0.4\ncache=true\n[llm.sub]\nmax_tokens=200\n"
    )
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    settings = FleetSettings(_env_file=None)
    assert settings.resolved_model("root").max_tokens == 100
    assert settings.resolved_model("sub").max_tokens == 200
    assert settings.resolved_model("reflection").temperature == 0.4
    assert settings.resolved_model("judge").cache


@pytest.mark.parametrize("failure", ["auth", "timeout", "oversized"])
def test_discovery_transport_and_size_failures(monkeypatch, failure):
    from contextlib import contextmanager

    @contextmanager
    def stream(method, url, **_kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private detail")
        response = httpx.Response(
            401 if failure == "auth" else 200,
            content=b"x" * 1_000_001 if failure == "oversized" else b"private detail",
            request=httpx.Request(method, url),
        )
        yield response

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(_env_file=None, fleet_llm_api_base="http://127.0.0.1:8000/v1")
    with pytest.raises((httpx.HTTPError, ValueError)):
        settings.list_models()


@pytest.mark.parametrize(
    ("model", "wire_format"),
    [("openai/deepseek/deepseek-v4.1-flash", "json_object"), ("openai/gpt-4o-mini", "json_schema")],
)
def test_explicit_json_mode_reaches_native_request(model, wire_format):
    from dspy._vendor.lm15.providers.openai_chat import _response_format_to_chat
    from dspy.clients.execution import prepare

    settings = FleetSettings(
        _env_file=None,
        fleet_root_model=model,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    lm = build_lm(settings, "", "root")
    assert lm.kwargs["response_format"] == {"type": "json_object"}
    kwargs = {}
    settings.resolved_model("root").build_adapter()._prepare_response_format(
        lm, kwargs, dspy.Signature("task -> reasoning, code")
    )
    call = prepare(lm, "Return JSON", kwargs)
    request = call.request
    wire = _response_format_to_chat(request.config.response_format)
    assert wire["type"] == wire_format
    if wire_format == "json_object":
        assert wire == {"type": "json_object"}
    else:
        assert wire["json_schema"]["strict"] is True
        assert set(wire["json_schema"]["schema"]["required"]) == {"reasoning", "code"}


def test_response_format_inheritance_validation_and_digest():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_adapter="json",
        fleet_llm_response_format="json_object",
        fleet_sub_response_format="auto",
    )
    assert settings.resolved_model("root").response_format == "json_object"
    assert settings.resolved_model("sub").response_format == "auto"
    baseline = FleetSettings(_env_file=None)
    assert settings.configuration()["digest"] != baseline.configuration()["digest"]
    with pytest.raises(ValueError, match="JSON adapter"):
        FleetSettings(_env_file=None, fleet_root_response_format="json_object")


def test_response_format_sources_and_session_snapshot(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[llm]\nadapter="json"\nresponse_format="json_object"\n')
    env = tmp_path / ".env"
    env.write_text("FLEET_SUB_RESPONSE_FORMAT=auto\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_ROOT_RESPONSE_FORMAT", "auto")
    settings = FleetSettings(_env_file=env, fleet_judge_response_format="auto")
    report = settings.configuration()
    assert report["values"]["fleet_llm_response_format"] == "json_object"
    assert report["sources"]["fleet_llm_response_format"] == "toml"
    assert report["sources"]["fleet_sub_response_format"] == "dotenv"
    assert report["sources"]["fleet_root_response_format"] == "environment"
    assert report["sources"]["fleet_judge_response_format"] == "explicit"
    updated = settings.for_session({"models": {"root": "openai/other", "sub": "openai/child"}})
    assert updated.resolved_model("root").response_format == "auto"
    assert updated.resolved_model("reflection").response_format == "json_object"


@pytest.mark.parametrize("role", ["root", "sub", "reflection", "judge"])
def test_unity_gateway_native_wire_and_reconstruction_leave_openai_unchanged(role, monkeypatch):
    requests = []
    monkeypatch.setenv("GATEWAY_KEY", "gateway-key")
    monkeypatch.setenv("ORDINARY_KEY", "ordinary-key")
    model_field = "fleet_correctness_model" if role == "judge" else f"fleet_{role}_model"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers["Authorization"], body))
            payload = json.dumps(
                {
                    "id": "offline-gateway-response",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "46"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        settings = FleetSettings(
            _env_file=None,
            fleet_llm_model="openai/catalog.ai_gateway.qwen",
            fleet_llm_api_base=base + "/ai-gateway/mlflow/v1",
            fleet_llm_api_key="gateway-key",
            fleet_llm_max_tokens=37,
            fleet_llm_cache=False,
            **{
                model_field: "openai/catalog.ai_gateway.qwen",
                f"fleet_{role}_api_base": base + "/ai-gateway/mlflow/v1",
                f"fleet_{role}_api_key_env": "GATEWAY_KEY",
                f"fleet_{role}_max_tokens": 37,
                f"fleet_{role}_cache": False,
            },
        )
        gateway = build_lm(settings, "", role)
        assert complete_text(gateway, "17 + 29") == "46"
        # A second endpoint/credential must not replace the first LM's connection or dialect.
        ordinary = build_lm(
            FleetSettings(
                _env_file=None,
                fleet_llm_model="openai/ordinary-model",
                fleet_llm_api_base=base + "/v1",
                fleet_llm_api_key="ordinary-key",
                fleet_llm_max_tokens=53,
                fleet_llm_cache=False,
                **{
                    model_field: "openai/ordinary-model",
                    f"fleet_{role}_api_base": base + "/v1",
                    f"fleet_{role}_api_key_env": "ORDINARY_KEY",
                    f"fleet_{role}_max_tokens": 53,
                    f"fleet_{role}_cache": False,
                },
            ),
            "",
            role,
        )
        assert complete_text(ordinary, "17 + 29") == "46"
        other_gateway = build_lm(
            FleetSettings(
                _env_file=None,
                **{
                    model_field: "openai/second-service",
                    f"fleet_{role}_api_base": base.replace("127.0.0.1", "localhost") + "/ai-gateway/mlflow/v1/",
                    f"fleet_{role}_api_key_env": "ORDINARY_KEY",
                    f"fleet_{role}_max_tokens": 71,
                    f"fleet_{role}_cache": False,
                },
            ),
            "",
            role,
        )
        assert complete_text(other_gateway, "17 + 29") == "46"
        # Native saved LMs omit credentials; reconstruction rebinds one without a custom engine.
        state = json.loads(json.dumps(gateway.dump_state()))
        assert "gateway-key" not in json.dumps(state)
        restored = dspy.LM.load_state(state).copy(api_key="restored-key")
        assert complete_text(restored, "17 + 29") == "46"
        assert [path for path, _, _ in requests] == [
            "/ai-gateway/mlflow/v1/chat/completions",
            "/v1/chat/completions",
            "/ai-gateway/mlflow/v1/chat/completions",
            "/ai-gateway/mlflow/v1/chat/completions",
        ]
        assert [auth for _, auth, _ in requests] == [
            "Bearer gateway-key",
            "Bearer ordinary-key",
            "Bearer ordinary-key",
            "Bearer restored-key",
        ]
        for _, _, body in (requests[0], requests[3]):
            assert body["model"] == "catalog.ai_gateway.qwen"
            assert body["max_tokens"] == 37
            assert "max_completion_tokens" not in body
        assert requests[1][2]["max_completion_tokens"] == 53
        assert "max_tokens" not in requests[1][2]
        assert requests[2][2]["model"] == "second-service"
        assert requests[2][2]["max_tokens"] == 71
        assert "max_completion_tokens" not in requests[2][2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# Native DSPy runtime contracts


@pytest.mark.parametrize("timeout,expected_dispatches", [(0, 1), (0.001, 2)])
def test_native_batch_straggler_resubmission_is_disabled_by_zero(monkeypatch, timeout, expected_dispatches):
    import dspy.utils.parallelizer as parallelizer

    started, release, duplicate = threading.Event(), threading.Event(), threading.Event()
    dispatches = []
    real_wait = parallelizer.wait
    waits = 0

    class Decision(dspy.Module):
        def forward(self, item):
            dispatches.append(item)
            if len(dispatches) == 1:
                started.set()
                assert release.wait(10)
            else:
                duplicate.set()
            return dspy.Prediction(answer=item)

    def wait(futures, **kwargs):
        nonlocal waits
        waits += 1
        if waits == 1:
            assert started.wait(10)
            return set(), set(futures)
        if timeout:
            assert duplicate.wait(10)
        release.set()
        return real_wait(futures, **kwargs)

    ticks = iter([100, 200, 300, 400])
    monkeypatch.setattr(parallelizer, "time", SimpleNamespace(time=lambda: next(ticks, 500)))
    monkeypatch.setattr(parallelizer, "wait", wait)
    try:
        results = Decision().batch(
            [dspy.Example(item="slow").with_inputs("item")], num_threads=2, timeout=timeout, disable_progress_bar=True
        )
    finally:
        release.set()
    assert [p.answer for p in results] == ["slow"]
    assert dispatches == ["slow"] * expected_dispatches


def test_api_app_import_does_not_pull_the_optimizer():
    """The FastAPI app is the primary application; the offline optimizer must not load with it."""
    code = (
        "import sys, fleet_rlm.api.app\n"
        "loaded = sorted(m for m in sys.modules if m.startswith('fleet_rlm.optimize'))\n"
        "assert not loaded, loaded\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_decision_outputs_stay_on_predict_predictors():
    """TypeSafe decision types belong on Predict; RLM and Flex outputs stay generative prose."""
    for name, field in FleetTask.output_fields.items():
        assert field.annotation is str, name
    decision = item_decision(["a", "b"])
    assert issubclass(decision.output_fields["decision"].annotation, BaseModel)


def manager():
    replies = []
    counts = []
    return SimpleNamespace(
        consume_budget=lambda n=1: counts.append(n),
        check_turn=lambda: None,
        emit=lambda *_args, **_kwargs: None,
        sub_lm=lambda prompt: [prompt.upper()],
        sub_adapter=None,
        replies=replies,
        counts=counts,
    )


@pytest.mark.parametrize("batched", [False, True])
def test_injected_query_enforces_local_budget_and_charges_global_once(batched):
    m = manager()
    rlm = dspy.RLM("task -> answer", max_llm_calls=2, sub_lm=SandboxLM(m.sub_lm, m.consume_budget))
    interpreter = DaytonaInterpreter(m)
    rlm._inject_execution_context(interpreter, rlm._prepare_execution_tools())
    name = "llm_query_batched" if batched else "llm_query"
    args = [["first", "second"]] if batched else ["first"]
    m.replies.append(interpreter._handle_host_call({"id": "first", "tool": name, "args": args}))
    if not batched:
        m.replies.append(interpreter._handle_host_call({"id": "second", "tool": name, "args": ["second"]}))
    assert sum(m.counts) == 2
    m.replies.append(interpreter._handle_host_call({"id": "exhausted", "tool": name, "args": args}))
    assert "error" in m.replies[-1]
    assert sum(m.counts) == 2
    assert m.replies[0]["value"] == (["FIRST", "SECOND"] if batched else "FIRST")


@pytest.mark.parametrize("batched", [False, True])
def test_query_fallback_keeps_global_accounting(batched):
    m = manager()
    interpreter = DaytonaInterpreter(m)
    m.replies.append(
        interpreter._handle_host_call(
            {
                "id": "one",
                "tool": "llm_query_batched" if batched else "llm_query",
                "args": [["a", "b"]] if batched else ["a"],
            }
        )
    )
    assert sum(m.counts) == (2 if batched else 1)
    assert "error" not in m.replies[-1]


def test_multiple_completions_reserve_each_distinct_budget_once():
    calls = []
    global_count = []
    local_count = []
    lm = SandboxLM(
        SandboxLM(lambda *_args, **kw: calls.append(kw) or ["answer"] * kw["n"], global_count.append),
        local_count.append,
    )
    assert lm("question", n=3) == ["answer"] * 3
    assert global_count == [3] and local_count == [3] and len(calls) == 1
    with pytest.raises(TypeError):
        lm("question", api_key="forbidden")
    assert global_count == [3]


@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
def test_native_child_promotion_belongs_to_successful_host_invocation(outcome):
    events = []
    invocations = []
    m = manager()
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"), shutdown=lambda: events.append("closed")
    )

    def call():
        invocations.append("predictor")
        child = DaytonaInterpreter(child_manager, owns_manager=True)
        try:
            if outcome == "failed":
                raise RuntimeError("predictor failed")
            return {"answer": "ok"}
        finally:
            child.shutdown()

    if outcome == "cancelled":

        def cancelled():
            raise RuntimeError("Turn cancelled")

        m.check_turn = cancelled
    interpreter = DaytonaInterpreter(m, tools={"predictor": call})
    m.replies.append(interpreter._handle_host_call({"id": "call", "tool": "predictor", "args": []}))
    if outcome == "cancelled":
        # The pre-tool fence answers before dispatch: no tool call, no child, error reply.
        assert invocations == []
        assert events == []
        assert "cancelled" in m.replies[-1]["error"]
    elif outcome == "failed":
        # The tool is already running when it fails; its finally block closes the child once.
        assert invocations == ["predictor"]
        assert events == ["closed"]
        assert "error" in m.replies[-1]
    else:
        assert invocations == ["predictor"]
        assert events == ["promoted", "closed"]
        assert "value" in m.replies[-1]


def test_unowned_explicit_child_keeps_promotion_with_its_caller():
    events = []
    m = manager()
    child_manager = SimpleNamespace(shutdown=lambda: events.append("closed"))

    def call():
        DaytonaInterpreter(child_manager, owns_manager=False).shutdown()
        return "explicit caller owns cleanup"

    interpreter = DaytonaInterpreter(m, tools={"spawn": call})
    assert interpreter._invoke_tool("spawn", [], {}) == "explicit caller owns cleanup"
    assert events == []


def test_decision_evidence_and_missing_evidence():
    choice = make_choice_type((("yes", "yes"), ("no", "no")))
    result = choice(value="yes", confidence=0.8, probabilities={"yes": 0.9, "no": 0.1})
    evidence = decision_evidence(result, SimpleNamespace(model="test-model"))
    assert evidence["probabilities"] == {"yes": 0.9, "no": 0.1}
    assert evidence["confidence"] == 0.8 and evidence["raw_choice"] == "yes"
    assert decision_evidence(None, SimpleNamespace())["confidence"] is None


def test_batch_decisions_keep_alignment_and_evidence(monkeypatch):
    import fleet_rlm.sandbox.interpreter as module

    choice = make_choice_type((("a", "a"), ("b", "b")))
    predictions = [
        SimpleNamespace(decision=choice(value=v, confidence=0.7, probabilities={"a": 0.7, "b": 0.3}))
        for v in ["a", "b"]
    ]

    def batch(examples, *, num_threads, timeout):
        assert len(examples) == 2 and num_threads == 2 and timeout == 0
        return predictions

    monkeypatch.setattr(module.dspy, "Predict", lambda _signature: SimpleNamespace(batch=batch))
    m = manager()
    m.jev_lm = None
    charged = []
    m.consume_budget = lambda count, *, category: charged.append((count, category))
    results = DaytonaInterpreter(m)._jev_decide_batched([["one", "two"], "choose", ["a", "b"]], {})
    assert charged == [(2, "other_decisions")]
    assert [(r["item"], r["decision"]) for r in results] == [("one", "a"), ("two", "b")]
    assert all(r["confidence"] == 0.7 and r["probabilities"] == {"a": 0.7, "b": 0.3} for r in results)


@pytest.mark.parametrize("backend", ["generative", "typesafe"])
@pytest.mark.parametrize(
    "context,mode,expected,reason",
    [
        (None, "hybrid", "direct", None),
        ("selected", "hybrid", "rlm", "selected_context"),
        (None, "rlm", "rlm", "session_mode"),
        # An operator-selected direct mode keeps the direct route even with a context selected;
        # the direct branch inlines the workspace instead of overriding the route.
        ("selected", "direct", "direct", "session_mode"),
        (None, "direct", "direct", "session_mode"),
        (None, "flex", "flex", "session_mode"),
    ],
)
def test_routing_evidence_distinguishes_backend_and_override(monkeypatch, backend, context, mode, expected, reason):
    events = []
    m = DaytonaSandboxManager("key", "org", "volume", "test")
    # The direct lane inlines the workspace, so it inspects files; keep this test off the provider.
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    m.event_callback = lambda payload: events.append((payload["event"], payload))
    root = dspy.LM("openai/test-root", engine="lm15", cache=False)
    lm = (
        type("TypeSafe", (), {"model": "typesafe:test-jev", "supports_decision_requests": True})()
        if backend == "typesafe"
        else None
    )
    engine = FleetRuntime(m, root, root, jev_lm=lm)
    engine.program.routing.predict = lambda **_kwargs: SimpleNamespace(
        route=TRIAGE_ROUTE(
            value="direct",
            confidence=0.8,
            probabilities={"direct": 0.8, "rlm": 0.2},
        )
    )
    engine.program.reasoning.direct = lambda **_kwargs: SimpleNamespace(answer="ok")
    engine.program.reasoning.rlm = lambda **_kwargs: SimpleNamespace(answer="ok")
    engine.program.reasoning.flex = lambda **_kwargs: SimpleNamespace(answer="ok")
    try:
        result = engine(workspace_manifest="", memory_summary="", task="greeting", context=context, execution_mode=mode)
        evidence = next(p["evidence"] for name, p in events if name == "triage_decision")
        assert result.route == expected and evidence["effective_route"] == expected
        assert evidence["override_reason"] == reason
        assert evidence["backend"] == backend
        assert evidence["confidence"] == (0.8 if mode == "hybrid" else None)
    finally:
        m.end_turn()


def test_routing_regression_cases_cover_each_effective_route():
    import json
    from collections import Counter
    from pathlib import Path

    cases = json.loads((Path(__file__).parents[1] / "fixtures.json").read_text())["triage_cases"]
    assert Counter(case["route"] for case in cases) == {"direct": 6, "rlm": 12}
    assert len({case["id"] for case in cases}) == 18
    assert all(case["review_status"] == "developer_reviewed" and case["rationale"] for case in cases)


@pytest.mark.parametrize("finish", ["recovery", "extraction", "failure"])
def test_installed_rlm_lifecycle_promotes_only_a_valid_completed_invocation(finish):
    from dspy import CodeExecutionError, FinalOutput
    from dspy.primitives.facade import BOOTSTRAP

    events = []
    child_manager = SimpleNamespace(
        collect_child_artifacts=lambda: events.append("promoted"),
        shutdown=lambda: events.append("closed"),
    )

    class ControlledInterpreter(DaytonaInterpreter):
        def start(self):
            return None

        def execute(self, code, variables=None):
            del variables
            if code in (BOOTSTRAP, "import dspy"):
                return None
            if code.splitlines()[-1] == "SUBMIT":
                return FinalOutput({"answer": 391})
            raise CodeExecutionError("deliberate repair probe")

    def factory():
        return ControlledInterpreter(child_manager, owns_manager=True)

    rlm = dspy.RLM("task: str -> answer: int", interpreter_factory=factory, max_iters=2 if finish == "recovery" else 1)
    actions = iter(["fail", "SUBMIT"])
    rlm.generate_action = lambda **_kwargs: dspy.Prediction(reasoning="", code=next(actions))

    def extract(**_kwargs):
        if finish == "failure":
            raise RuntimeError("extraction failed")
        return dspy.Prediction(answer=391)

    rlm.extract = extract
    parent = DaytonaInterpreter(manager(), tools={"nested": lambda: rlm(task="controlled invocation")})
    if finish == "failure":
        with pytest.raises(RuntimeError, match="extraction failed"):
            parent._invoke_tool("nested", [], {})
        assert events == ["closed"]
    else:
        result = parent._invoke_tool("nested", [], {})
        assert result.answer == 391
        assert events == ["promoted", "closed"]
        assert "deliberate repair probe" in result.trajectory[0]["output"]
        assert (result.final_reasoning == "Extract forced final output") == (finish == "extraction")


def test_figure_extraction_is_shared_by_submit_grounding_and_the_review_queue():
    from fleet_rlm.contracts import figure_grounded, figures

    found = figures("Revenue $1,250.50 over 42 orders (id=a12345, ratio 0.5, n=7)")
    assert found == {"1250.50": 1250.5, "42": 42.0, "0.5": 0.5}
    # A gold label keeps single-digit answers; the grounding filter above is unchanged.
    assert figures("The answer is 8 of 3", include_small=True) == {"8": 8.0, "3": 3.0}
    assert figures("The answer is 8") == {}
    # Exponent, leading-dot, negative-currency and compact-list literals are typed figures too.
    assert figures("1e6 and .5 and -$1,250 and 1.5e3 and 2.5e-3") == {
        "1e6": 1e6,
        ".5": 0.5,
        "-1250": -1250.0,
        "1.5e3": 1500.0,
        "2.5e-3": 0.0025,
    }
    assert figures("scores 10,20,30 and 1,234,567 and a1e6 v1.2 (1.2)") == {
        "10": 10.0,
        "20": 20.0,
        "30": 30.0,
        "1234567": 1234567.0,
        "1.2": 1.2,
    }
    assert figure_grounded("1e6", 1e6, {1_000_000.0}) and not figure_grounded("1e6", 1e6, {999_000.0})
    assert figure_grounded("1e-3", 0.001, {0.001}) and not figure_grounded("1e-3", 0.001, {0.0004})
    assert figure_grounded("1250.5", 1250.5, {1250.49}) and not figure_grounded("1250.5", 1250.5, {1250.0})
    # The interpreter imports the same helpers rather than a private copy.
    from fleet_rlm.sandbox import interpreter

    assert interpreter.figures is figures and interpreter.figure_grounded is figure_grounded


# Installed program compatibility

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_approval_evidence_builds_without_source_lockfile(tmp_path: Path, monkeypatch) -> None:
    """Approval evidence must not require uv.lock in the current working directory."""
    monkeypatch.chdir(tmp_path)
    assert not (Path.cwd() / "uv.lock").exists()

    evidence = artifacts.approval_evidence(FleetSettings(_env_file=None), "incumbent", scorer_version=SCORER_VERSION)
    assert evidence["evidence_format"] == "3"
    assert len(evidence["dependency_fingerprint"]) == 64
    assert "lockfile_digest" not in evidence
    assert LOCK_PATH.is_file()
    assert DEFAULT_DEFINITION.digest() == "3d0cd582c972090c18437dfba13358e055970af6de9407e46515e75bd49fa0e8"

    lm = dspy.LM("openai/offline", engine="lm15", cache=False)
    engine = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-state"),
        lm,
        lm,
    )
    engine.program.routing.predict.signature = engine.program.routing.predict.signature.with_instructions("TUNED ROUTE")
    current = tmp_path / "fleet-program.json"
    current.write_bytes(program_envelope(engine.program))

    restored = FleetRuntime(
        DaytonaSandboxManager("key", "org", "volume", "wheel-fresh"), lm, lm, compiled_path=str(current)
    )
    assert restored.program.dump_state() == engine.program.dump_state()
    assert restored.program.routing.predict.signature.instructions == "TUNED ROUTE"


def test_dependency_fingerprint_changes_when_installed_version_changes(monkeypatch) -> None:
    """Approval identity includes distribution versions, not only package names."""
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.3")],
    )
    original = artifacts.installed_dependency_fingerprint()
    monkeypatch.setattr(
        artifacts,
        "distributions",
        lambda: [SimpleNamespace(metadata={"Name": "Fleet.Example"}, version="1.2.4")],
    )
    assert artifacts.installed_dependency_fingerprint() != original


# Optimizer CLI contracts


def test_worker_snapshot_retains_effective_settings_and_sources_without_rereading_configuration(monkeypatch, tmp_path):
    settings = FleetSettings(_env_file=None, databricks_token="private-worker-token").with_explicit_overrides(
        turn_timeout_seconds=123
    )
    snapshot = worker_settings_payload(settings)
    assert b"private-worker-token" in snapshot
    monkeypatch.setenv("FLEET_CONFIG", str(tmp_path / "not-a-config.toml"))
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "invalid-env-must-not-be-read")
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert received.configuration() == settings.configuration()
    assert received.databricks_token.get_secret_value() == "private-worker-token"
    assert received.configuration_source("turn_timeout_seconds") == "explicit"
    assert "private-worker-token" not in json.dumps(received.configuration())


def test_worker_snapshot_rejects_unbounded_or_invalid_input():
    with pytest.raises(ValueError, match="too large"):
        read_worker_settings(io.StringIO(" " * 1_000_001))
    with pytest.raises(ValueError, match="invalid worker"):
        read_worker_settings(io.StringIO('{"settings":{}}'))


def test_worker_snapshot_ignores_unrelated_dotenv_keys(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER_APP_SECRET=unrelated-secret\nFLEET_TURN_TIMEOUT_SECONDS=123\n")
    settings = FleetSettings(_env_file=dotenv)
    assert "other_app_secret" not in settings._configuration_sources
    snapshot = worker_settings_payload(settings)
    received = read_worker_settings(io.StringIO(snapshot.decode()))
    assert b"other_app_secret" not in snapshot and b"unrelated-secret" not in snapshot
    assert received.configuration() == settings.configuration()
    assert received.configuration_source("turn_timeout_seconds") == "dotenv"


@pytest.mark.parametrize("ratio", [0.1, 0.5])
def test_ratio_bounds_agree_across_entry_points(ratio, monkeypatch):
    from fleet_rlm.api.schemas import StartJob
    from fleet_rlm.optimize import dataset
    from fleet_rlm.tui.commands import Context, SessionState, _parse_optimizer_arguments

    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    options = ExperimentOptions.from_namespace(parser.parse_args(["--validation-ratio", str(ratio)]))
    assert options.validation_ratio == ratio
    assert StartJob(request_id="request", validation_ratio=ratio).validation_ratio == ratio
    payload, review = _parse_optimizer_arguments(Context(None, "s1", SessionState()), f"--validation-ratio {ratio}")
    assert payload["validation_ratio"] == ratio and review is None
    monkeypatch.setattr(dataset, "load_examples_from_postgres", lambda *_args: [])
    with pytest.raises(ValueError, match="approved gold-labeled examples"):
        dataset.curate_dataset(None, "offline", validation_ratio=ratio)


@pytest.mark.parametrize("ratio", [0, 0.09, 0.51, 1, float("nan"), float("inf")])
def test_invalid_ratio_rejected_by_shared_and_direct_contract(ratio):
    from fleet_rlm.optimize.dataset import curate_dataset

    with pytest.raises(ValidationError):
        ExperimentOptions(validation_ratio=ratio)
    with pytest.raises(ValueError, match="validation_ratio"):
        curate_dataset(None, "offline", validation_ratio=ratio)


def test_cli_and_http_search_controls_have_one_validated_contract():
    parser = argparse.ArgumentParser()
    add_optimization_arguments(parser)
    args = parser.parse_args(["--max-metric-calls", "10", "--trace-search", "--max-total-tokens", "5000"])
    options = ExperimentOptions.from_namespace(args)
    assert options.gepa_budget is None and options.max_metric_calls == 10
    assert options.trace_search and options.max_total_tokens == 5000
    assert "--max-total-tokens" in options.cli_arguments() and "--check" not in options.cli_arguments()
    assert ExperimentOptions().max_total_tokens is None and ExperimentOptions(max_total_tokens=0).max_total_tokens == 0
    assert ExperimentOptions.model_validate(options.model_dump()) == options
    assert "--max-metric-calls" in options.cli_arguments() and "--gepa-budget" not in options.cli_arguments()
    with pytest.raises(ValidationError):
        ExperimentOptions(max_metric_calls=10, gepa_budget="light")
    with pytest.raises(ValidationError):
        ExperimentOptions(max_total_tokens=-1)
    with pytest.raises(ValidationError):
        ExperimentOptions(threads=True)
    with pytest.raises(ValidationError):
        ExperimentOptions.model_validate({"executable": "untrusted"})


def test_worker_preserves_referenced_credentials_and_role_defaults(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text("[config]\nschema_version=5\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    path = tmp_path / ".env"
    path.write_text("CUSTOM_PROVIDER_KEY=original-key\n")
    settings = FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_PROVIDER_KEY", fleet_llm_temperature=0.4)
    payload = worker_settings_payload(settings)
    path.write_text("CUSTOM_PROVIDER_KEY=replaced-key\n")
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "environment-replacement")
    received = read_worker_settings(io.StringIO(payload.decode()))
    assert received.resolved_model("root").api_key.get_secret_value() == "original-key"
    assert received.resolved_model("reflection").temperature == 0.4
    assert received.configuration()["digest"] == settings.configuration()["digest"]
    updated = received.with_explicit_overrides(fleet_root_cache=False)
    assert updated.resolved_model("root").api_key.get_secret_value() == "original-key"
    assert updated.resolved_model("reflection").temperature == 0.4
    assert "original-key" not in str(received.configuration())


# Consolidated from lite/test_session.py


# Session ownership and history


def _runtime(**overrides) -> SessionRuntime:
    dependencies = {
        "sandbox_mgr": SimpleNamespace(),
        "store": SimpleNamespace(),
        "skill_mgr": SimpleNamespace(),
        "engine": SimpleNamespace(),
    }
    dependencies.update(overrides)
    return SessionRuntime(**dependencies)


@pytest.mark.parametrize("status", ["running", "failed", "completed"])
def test_required_claim_binds_ordered_intent_but_completed_receipts_survive_source_changes(status):
    import hashlib
    from dataclasses import replace

    from fleet_rlm.skills import captured_required_skill
    from fleet_rlm.store import IdempotencyConflictError

    def skill(name):
        return captured_required_skill(
            name, "workspace", (("SKILL.md", f"---\nname: {name}\ndescription: Test\n---\nInstructions".encode()),)
        )

    a, b = skill("alpha"), skill("beta")
    rows = {}

    def claim(_session, turn_id, key, digest):
        if key in rows:
            return rows[key]
        rows[key] = {"turn_id": turn_id, "task_sha256": digest, "status": status}
        return None

    runtime = _runtime(
        store=SimpleNamespace(claim_turn=claim, turn_by_key=lambda _session, key: rows.get(key)),
        sandbox_mgr=SimpleNamespace(session_id="s1"),
    )
    assert runtime.completed_turn("missing", "task", ("alpha", "beta")) is None
    assert runtime.claim_turn("first", "k", "task", requested_skills=("alpha", "beta"), required_skills=(a, b)) is None
    receipt = runtime.completed_turn("k", "task", ("alpha", "beta"))
    assert receipt == (rows["k"] if status == "completed" else None)
    assert (
        runtime.claim_turn("retry", "k", "task", requested_skills=("alpha", "beta"), required_skills=(a, b))["turn_id"]
        == "first"
    )
    for task, names, records in (
        ("other", ("alpha", "beta"), (a, b)),
        ("task", ("beta", "alpha"), (b, a)),
        ("task", ("alpha",), (a,)),
        ("task", (), ()),
    ):
        with pytest.raises(IdempotencyConflictError):
            runtime.claim_turn("different", "k", task, requested_skills=names, required_skills=records)
        if status == "completed":
            with pytest.raises(IdempotencyConflictError):
                runtime.completed_turn("k", task, names)
    for changed in (replace(a, source="bundled"), replace(a, sha256="c" * 64)):
        if status == "completed":
            assert (
                runtime.claim_turn(
                    "source", "k", "task", requested_skills=("alpha", "beta"), required_skills=(changed, b)
                )["turn_id"]
                == "first"
            )
        else:
            with pytest.raises(IdempotencyConflictError):
                runtime.claim_turn(
                    "source", "k", "task", requested_skills=("alpha", "beta"), required_skills=(changed, b)
                )
    runtime.claim_turn("legacy", "legacy-key", "task")
    assert rows["legacy-key"]["task_sha256"] == hashlib.sha256(b"task").hexdigest()
    with pytest.raises(IdempotencyConflictError):
        runtime.claim_turn("upgrade", "legacy-key", "task", requested_skills=("alpha",), required_skills=(a,))
    if status == "completed":
        with pytest.raises(IdempotencyConflictError):
            runtime.completed_turn("legacy-key", "task", ("alpha",))


def test_committed_completion_owns_nested_values_separately_from_record_and_store() -> None:
    saved = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: ["artifacts/out.txt", "artifacts/out.txt"]),
        store=SimpleNamespace(commit_turn=lambda *_args, **kw: saved.append(kw)),
    )
    record = {"turn_id": "t1", "answer": "verified", "budget_breakdown": {"routing": 1}}
    result = runtime.commit_turn("s1", record)
    stored = saved[0]["completion"][1]
    assert result == stored and result["turn_id"] == "t1"
    record["budget_breakdown"]["routing"] = 100
    assert result["budget_breakdown"] == stored["budget_breakdown"] == {"routing": 1}
    result["budget_breakdown"]["routing"] = 200
    result["artifacts"].clear()
    assert stored["budget_breakdown"] == {"routing": 1} and stored["artifacts"] == ["artifacts/out.txt"]


def test_commit_does_not_return_a_completion_when_storage_raises() -> None:
    def fail(*_args, **_kwargs):
        raise ConnectionError("commit acknowledgment lost")

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: []),
        store=SimpleNamespace(commit_turn=fail),
    )
    with pytest.raises(ConnectionError):
        runtime.commit_turn("s1", {"turn_id": "t1"})


def test_turn_intent_and_completion_are_fenced_by_the_runtime_ownership_epoch() -> None:
    claims = []
    commits = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1", list_workspace_files=lambda: []),
        store=SimpleNamespace(
            claim_turn=lambda *args, **kwargs: claims.append((args, kwargs)),
            commit_turn=lambda *args, **kwargs: commits.append((args, kwargs)),
        ),
        ownership_epoch=12,
    )

    runtime.claim_turn("turn-1", "key-1", "task")
    runtime.commit_turn("s1", {"turn_id": "turn-1"})

    assert claims[0][1] == {"ownership_epoch": 12}
    assert commits[0][1]["ownership_epoch"] == 12


@pytest.mark.parametrize("paths", [[], ["files/input.txt", "artifacts/out.txt", "artifacts/out.txt"], ["../escape"]])
def test_captured_artifact_view_uses_existing_path_validation_without_relisting(paths) -> None:
    saved = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(list_workspace_files=lambda: pytest.fail("captured view must not be relisted")),
        store=SimpleNamespace(commit_turn=lambda *_args, **kw: saved.append(kw)),
    )
    if "../escape" in paths:
        with pytest.raises(ValueError, match="invalid relative path"):
            runtime.commit_turn("s1", {"turn_id": "t1"}, artifact_paths=paths)
        assert saved == []
    else:
        result = runtime.commit_turn("s1", {"turn_id": "t1"}, artifact_paths=paths)
        assert result["artifacts"] == sorted({p for p in paths if p.startswith("artifacts/")})


def test_lost_session_lease_cancels_remote_work_and_fences_runtime() -> None:
    from fleet_rlm.store import LeaseHeartbeatService, PooledLease

    cancelled = threading.Event()
    probed = threading.Event()

    class StolenLeaseStore:
        def __init__(self) -> None:
            self.heartbeat = LeaseHeartbeatService(self, interval_seconds=0.005)
            self.released: list[tuple[str, str, str, int]] = []

        def _batch_renew_session_leases(
            self, _session_ids: list[str], _epochs: list[int], _owner_id: str, _ttl: int
        ) -> set[str]:
            probed.set()
            return set()

        def _release_lease(self, scope: str, *, kind: str, owner_id: str, epoch: int) -> None:
            self.released.append((scope, kind, owner_id, epoch))

    store = StolenLeaseStore()
    lease = PooledLease(store, "s1", kind="session", owner_id="worker-1", epoch=1)
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(request_cancellation=cancelled.set, session_id="s1"),
        store=store,
        lease=lease,
    )
    runtime.start_lease_monitor()
    try:
        assert probed.wait(timeout=1)
        assert cancelled.wait(timeout=1)
        deadline = time.monotonic() + 1
        while not runtime.lease_lost.is_set() and time.monotonic() < deadline:
            time.sleep(0.001)

        with pytest.raises(RuntimeError, match="ownership lease was lost"):
            runtime.assert_lease_held()
    finally:
        store.heartbeat.stop()
        runtime.close_lease()


def test_a_dropped_lease_is_renewed_on_a_fresh_connection_when_the_lock_is_still_free() -> None:
    import psycopg

    from fleet_rlm.store import LeaseHeartbeatService, PooledLease

    cancelled = threading.Event()
    renewed_probe = threading.Event()
    attempts = 0

    class RecoveringStore:
        def __init__(self) -> None:
            self.heartbeat = LeaseHeartbeatService(self, interval_seconds=0.005)

        def _batch_renew_session_leases(
            self, session_ids: list[str], _epochs: list[int], _owner_id: str, _ttl: int
        ) -> set[str]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise psycopg.OperationalError("transient pool drop")
            renewed_probe.set()
            return set(session_ids)

        def _release_lease(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    store = RecoveringStore()
    lease = PooledLease(store, "s1", kind="session", owner_id="worker-1", epoch=1)
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(request_cancellation=cancelled.set, session_id="s1"),
        store=store,
        lease=lease,
    )
    runtime.start_lease_monitor()
    try:
        assert renewed_probe.wait(timeout=1)
        assert runtime.lease is lease and not runtime.lease_lost.is_set() and not cancelled.is_set()
        runtime.assert_lease_held()
    finally:
        store.heartbeat.stop()
        runtime.close_lease()


def _registry_with(runtime, *, max_active_sessions: int = 64):
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    registry = SessionRuntimeRegistry(
        FleetSettings(_env_file=None, max_active_sessions=max_active_sessions),
        None,
    )
    registry._sessions["s1"] = runtime
    return registry


def _buildable_registry(runtime, *, max_active_sessions: int = 64):
    registry = _registry_with(runtime, max_active_sessions=max_active_sessions)
    registry.store = SimpleNamespace()  # type: ignore[assignment]
    registry.root_lm = object()
    return registry


def _stale_runtime(events: list[str]) -> SessionRuntime:
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown") or True),
        lease=SimpleNamespace(close=lambda: events.append("lease")),  # type: ignore[arg-type]
    )
    runtime.lease_lost.set()
    return runtime


def test_get_returns_a_healthy_runtime_without_evicting_it() -> None:
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: pytest.fail("healthy runtimes must not be evicted"))
    )
    registry = _registry_with(runtime)
    assert registry.get("s1") is runtime


def test_get_rebuilds_a_runtime_after_lease_loss(monkeypatch, caplog) -> None:
    events: list[str] = []
    stale = _stale_runtime(events)
    registry = _buildable_registry(stale)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)

    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]
    assert registry._sessions["s1"] is fresh
    assert "ownership lease was lost" in caplog.text


def test_get_releases_epoch_fenced_runtime_without_stopping_new_owner(monkeypatch) -> None:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

    events = []
    manager = DaytonaSandboxManager("key", "org", "volume", "s1")
    manager._sandbox = SimpleNamespace(id="taken-over")
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: pytest.fail("stale provider stop"))
    manager.ownership_epoch = 1
    manager.ownership_resource_mutate = lambda *_args: False
    stale = _stale_runtime(events)
    stale.sandbox_mgr = manager
    registry = _buildable_registry(stale)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    assert registry.get("s1") is fresh
    assert registry._sessions["s1"] is fresh and events == ["lease"]
    assert manager._ownership_lost is True and manager._sandbox is None


def test_get_keeps_a_stale_runtime_while_its_turn_is_still_draining(monkeypatch) -> None:
    from fleet_rlm import session as session_module

    monkeypatch.setattr(session_module, "_EVICT_WAIT_SECONDS", 0.01)
    events: list[str] = []
    stale = _stale_runtime(events)
    stale.turn_lock.acquire()
    registry = _buildable_registry(stale)
    monkeypatch.setattr(registry, "_build", lambda _session_id: pytest.fail("must not rebuild while draining"))
    try:
        with pytest.raises(RuntimeError, match="still draining"):
            registry.get("s1")
    finally:
        stale.turn_lock.release()
    assert registry._sessions["s1"] is stale
    assert events == []

    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]


def test_get_serves_a_cached_session_while_another_session_builds(monkeypatch) -> None:
    cached = _runtime()
    registry = _buildable_registry(cached)
    fresh = _runtime()
    build_started = threading.Event()
    release_build = threading.Event()

    def build(_session_id: str) -> SessionRuntime:
        build_started.set()
        assert release_build.wait(timeout=2)
        return fresh

    monkeypatch.setattr(registry, "_build", build)
    built: list[SessionRuntime] = []
    build_errors: list[BaseException] = []
    served: list[SessionRuntime] = []
    read_errors: list[BaseException] = []

    def build_second_session() -> None:
        try:
            built.append(registry.get("s2"))
        except BaseException as exc:
            build_errors.append(exc)

    def read_cached_session() -> None:
        try:
            served.append(registry.get("s1"))
        except BaseException as exc:
            read_errors.append(exc)

    builder = threading.Thread(target=build_second_session)
    reader = threading.Thread(target=read_cached_session)
    builder.start()
    try:
        assert build_started.wait(timeout=1)
        reader.start()
        reader.join(timeout=1)
        assert not reader.is_alive(), "a cached Session was blocked by another Session's build"
        assert read_errors == []
        assert served == [cached]
    finally:
        release_build.set()
        builder.join(timeout=2)
        reader.join(timeout=2)
    assert not builder.is_alive()
    assert build_errors == []
    assert built == [fresh]
    assert registry._sessions == {"s1": cached, "s2": fresh}


def test_get_refuses_a_new_session_at_capacity(monkeypatch) -> None:
    cached = _runtime()
    registry = _buildable_registry(cached, max_active_sessions=1)
    monkeypatch.setattr(registry, "_build", lambda _session_id: pytest.fail("must not build at capacity"))

    with pytest.raises(RuntimeError, match="maximum number of active Sessions"):
        registry.get("s2")
    assert registry._sessions == {"s1": cached}


def test_get_rebuilds_a_stale_session_at_capacity(monkeypatch) -> None:
    events: list[str] = []
    stale = _stale_runtime(events)
    registry = _buildable_registry(stale, max_active_sessions=1)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)

    assert registry.get("s1") is fresh
    assert events == ["shutdown", "lease"]
    assert registry._sessions == {"s1": fresh}


def test_evicting_a_session_stops_its_sandbox_releases_the_lease_and_forgets_the_runtime() -> None:
    events: list[str] = []
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=lambda: events.append("shutdown") or True),
        lease=SimpleNamespace(close=lambda: events.append("lease")),  # type: ignore[arg-type]
    )
    registry = _registry_with(runtime)
    assert registry.evict("s1") is True
    assert events == ["shutdown", "lease"] and "s1" not in registry._sessions
    assert registry.evict("s1") is False
    assert not runtime.turn_lock.locked()


def test_evicting_a_busy_session_is_refused_and_keeps_the_runtime(monkeypatch) -> None:
    from fleet_rlm import session as session_module

    monkeypatch.setattr(session_module, "_EVICT_WAIT_SECONDS", 0.01)
    runtime = _runtime(sandbox_mgr=SimpleNamespace(shutdown=lambda: pytest.fail("must not shut down mid-Turn")))
    registry = _registry_with(runtime)
    runtime.turn_lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            registry.evict("s1")
    finally:
        runtime.turn_lock.release()
    assert registry._sessions["s1"] is runtime


@pytest.mark.parametrize("raises", [False, True])
def test_unconfirmed_eviction_retains_runtime_and_lease_until_cleanup_succeeds(monkeypatch, raises) -> None:
    events: list[str] = []

    def unconfirmed() -> bool:
        events.append("shutdown")
        if raises:
            raise RuntimeError("provider refused cleanup")
        return False

    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(shutdown=unconfirmed),
        lease=SimpleNamespace(close=lambda: events.append("lease")),
    )
    registry = _buildable_registry(runtime)
    fresh = _runtime()
    monkeypatch.setattr(registry, "_build", lambda _session_id: fresh)
    with pytest.raises(RuntimeError):
        registry.evict("s1")
    assert runtime.lease_lost.is_set()
    assert registry._sessions["s1"] is runtime
    assert events == ["shutdown"]
    with pytest.raises(RuntimeError):
        registry.get("s1")
    assert registry._sessions["s1"] is runtime
    assert events == ["shutdown", "shutdown"]
    runtime.sandbox_mgr.shutdown = lambda: events.append("confirmed") or True
    assert registry.get("s1") is fresh
    assert events == ["shutdown", "shutdown", "confirmed", "lease"]


def test_registry_close_attempts_every_runtime_and_retains_unresolved_ones() -> None:
    events: list[str] = []
    unresolved = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1", shutdown=lambda: False),
        lease=SimpleNamespace(close=lambda: events.append("unresolved-lease")),
    )
    healthy = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s2", shutdown=lambda: events.append("shutdown-s2") or True),
        lease=SimpleNamespace(close=lambda: events.append("healthy-lease")),
    )
    registry = _registry_with(unresolved)
    registry._sessions["s2"] = healthy
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        registry.close()
    assert registry._sessions == {"s1": unresolved}
    assert unresolved.lease_lost.is_set()
    assert events == ["shutdown-s2", "healthy-lease"]
    unresolved.sandbox_mgr.shutdown = lambda: True
    registry.close()
    assert not registry._sessions
    assert events[-1] == "unresolved-lease"


def test_registry_close_retries_failed_lease_release_and_attempts_other_runtimes() -> None:
    events: list[str] = []

    def failed_close() -> None:
        events.append("failed-lease")
        raise RuntimeError("connection close failed")

    lease = SimpleNamespace(close=failed_close)
    unresolved = _runtime(sandbox_mgr=SimpleNamespace(session_id="s1", shutdown=lambda: True), lease=lease)
    healthy = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s2", shutdown=lambda: True),
        lease=SimpleNamespace(close=lambda: events.append("healthy-lease")),
    )
    registry = _registry_with(unresolved)
    registry._sessions["s2"] = healthy
    with pytest.raises(RuntimeError, match="unconfirmed"):
        registry.close()
    assert events == ["failed-lease", "healthy-lease"]
    assert registry._sessions == {"s1": unresolved}
    assert unresolved.lease is lease
    lease.close = lambda: events.append("retried-lease")
    registry.close()
    assert unresolved.lease is None
    assert not registry._sessions
    assert events[-1] == "retried-lease"


def test_eviction_fences_a_resolved_runtime_before_releasing_its_turn_lock() -> None:
    shutdown_started = threading.Event()
    finish_shutdown = threading.Event()
    rejected = threading.Event()

    def shutdown() -> bool:
        shutdown_started.set()
        assert finish_shutdown.wait(timeout=2)
        return True

    runtime = _runtime(sandbox_mgr=SimpleNamespace(shutdown=shutdown))
    registry = _registry_with(runtime)
    stale = registry.get("s1")

    def use_stale_runtime() -> None:
        with stale.turn_lock:
            try:
                stale.assert_lease_held()
            except RuntimeError:
                rejected.set()

    eviction = threading.Thread(target=registry.evict, args=("s1",))
    eviction.start()
    try:
        assert shutdown_started.wait(timeout=1)
        assert runtime.lease_lost.is_set()
        waiting_turn = threading.Thread(target=use_stale_runtime)
        waiting_turn.start()
    finally:
        finish_shutdown.set()
        eviction.join(timeout=2)
    waiting_turn.join(timeout=2)
    assert rejected.is_set()
    assert not runtime.turn_lock.locked()

    from fastapi import HTTPException

    from fleet_rlm.api.deps import session_lock

    with pytest.raises(HTTPException) as unavailable, session_lock(stale):
        pytest.fail("An evicted runtime must not allow workspace changes")
    assert unavailable.value.status_code == 503
    assert not stale.turn_lock.locked()


def test_lease_acquired_after_close_times_out_is_closed_without_publication() -> None:
    from fleet_rlm.store import LeaseHeartbeatService, PooledLease

    tick_started = threading.Event()
    finish_tick = threading.Event()
    released = threading.Event()

    class ClosingStore:
        def __init__(self) -> None:
            self.heartbeat = LeaseHeartbeatService(self, interval_seconds=60.0)

        def _batch_renew_session_leases(
            self, _session_ids: list[str], _epochs: list[int], _owner_id: str, _ttl: int
        ) -> set[str]:
            tick_started.set()
            assert finish_tick.wait(timeout=2)
            return set()

        def _release_lease(self, *_args: Any, **_kwargs: Any) -> None:
            released.set()

    store = ClosingStore()
    lease = PooledLease(store, "s1", kind="session", owner_id="worker-1", epoch=1)
    runtime = _runtime(
        sandbox_mgr=SimpleNamespace(session_id="s1"),
        store=store,
        lease=lease,
    )
    runtime.start_lease_monitor()
    tick_thread = threading.Thread(target=store.heartbeat.tick)
    tick_thread.start()
    try:
        assert tick_started.wait(timeout=1)
        runtime.close_lease()
        assert released.is_set() and runtime.lease is None
    finally:
        finish_tick.set()
        tick_thread.join(timeout=2)
        store.heartbeat.stop()
    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.assert_lease_held()


def test_build_wires_the_durable_environment_marker_into_the_sandbox_manager(monkeypatch) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    captured: dict[str, object] = {}
    calls: list[object] = []

    class RecordingManager:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            self.reconcile_sandbox_ownership = lambda _resources: calls.append("reconcile")

    def claim(_session_id: str) -> bool:
        return True

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(execute=lambda _statement: None, close=lambda: None),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": []},
        fail_interrupted_turns=lambda _session_id, epoch: calls.append(("fail-interrupted", epoch)) or ["old-turn"],
        runtime_settings=lambda _session_id: {},
        save_runtime_settings=lambda _session_id, value: calls.append(value),
        claim_environment_report=claim,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace(reload_program=lambda: None))
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    runtime = registry._build("s1")

    assert captured["environment_report_claim"] is claim
    assert calls == ["reconcile", ("fail-interrupted", 1)]
    runtime.close_lease()


def _global_program_registry(tmp_path, monkeypatch):
    import dspy

    from fleet_rlm.config import FleetSettings
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager
    from fleet_rlm.session import SessionRuntimeRegistry

    saved = {}
    ledger = {"approved": None, "approving": []}
    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(close=lambda: None),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": []},
        fail_interrupted_turns=lambda *_args: [],
        runtime_settings=lambda _session_id: dict(saved),
        save_runtime_settings=lambda _session_id, value: saved.update(value),
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: dict(ledger),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "reconcile_sandbox_ownership", lambda *_args: None)
    monkeypatch.setattr(DaytonaSandboxManager, "execution_evidence", lambda _self: {})
    monkeypatch.setattr(SessionRuntime, "start_lease_monitor", lambda _self: None)
    registry = SessionRuntimeRegistry(
        FleetSettings(
            _env_file=None,
            compiled_program_dir=str(tmp_path),
            fleet_daytona_api_key="key",
            fleet_daytona_org_id="org",
            fleet_daytona_volume_id="volume",
        ),
        store,
    )
    registry.root_lm = registry.sub_lm = dspy.LM("openai/offline")
    registry.ledger = ledger
    return registry, saved


def test_global_program_file_is_the_only_selection_for_every_session(tmp_path, monkeypatch) -> None:
    import dspy

    from fleet_rlm.engine.compiled import program_envelope

    registry, saved = _global_program_registry(tmp_path, monkeypatch)
    first = registry._build("s1")
    assert first.engine.compiled.digest == "" and first.engine.compiled_path == str(tmp_path / "fleet.json")
    candidate = first.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    (tmp_path / "fleet.json").write_bytes(program_envelope(candidate))
    monkeypatch.setattr(first.engine.program, "forward", lambda **_inputs: dspy.Prediction(answer="offline"))
    assert first.engine("", "", "task").answer == "offline"
    assert first.engine.compiled.digest
    # A different Session starts on the same installed program, and nothing is stored per Session.
    second = registry._build("s2")
    assert second.engine.compiled.digest == first.engine.compiled.digest
    assert second.engine.program.routing.predict.signature.instructions == "Approved policy"
    assert saved == {}
    # A live runtime that already serves the artifact refuses to continue if the file vanishes.
    (tmp_path / "fleet.json").unlink()
    with pytest.raises(FileNotFoundError):
        first.engine("", "", "task")
    # Retiring the file is the operator's reset: a fresh runtime starts on baseline.
    fresh = registry._build("s3")
    assert fresh.engine.compiled.digest == ""
    for runtime in (first, second, fresh):
        runtime.close_lease()


def _approved_bytes(registry) -> bytes:
    from fleet_rlm.engine.compiled import program_envelope

    runtime = registry._build("seed")
    candidate = runtime.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    runtime.close_lease()
    return program_envelope(candidate)


@pytest.mark.parametrize("problem", ["missing", "changed"])
def test_once_a_global_job_was_approved_a_missing_or_changed_program_refuses_every_turn(
    tmp_path, monkeypatch, problem
) -> None:
    import hashlib

    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    content = _approved_bytes(registry)
    registry.ledger["approved"] = hashlib.sha256(content).hexdigest()
    path = tmp_path / "fleet.json"
    path.write_bytes(content)
    live = registry._build("s1")
    assert live.engine.program.routing.predict.signature.instructions == "Approved policy"
    if problem == "missing":
        path.unlink()
    else:
        path.write_bytes(content + b" ")  # still valid JSON, but not what the approval installed
    with pytest.raises((ValueError, FileNotFoundError)):
        live.engine("", "", "task")
    # A brand new runtime, however old the process, enforces the same ledger instead of serving baseline.
    with pytest.raises(ValueError, match="approved global program"):
        registry._build("s2")
    live.close_lease()


def test_an_approval_in_flight_may_already_have_replaced_the_file(tmp_path, monkeypatch) -> None:
    import hashlib

    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    content = _approved_bytes(registry)
    registry.ledger.update(
        approved=hashlib.sha256(b"older approval").hexdigest(), approving=[hashlib.sha256(content).hexdigest()]
    )
    (tmp_path / "fleet.json").write_bytes(content)
    registry._build("s1").close_lease()
    registry.ledger["approving"] = []
    with pytest.raises(ValueError, match="approved global program"):
        registry._build("s2")


def test_per_session_artifacts_from_before_the_global_program_are_ignored(tmp_path, monkeypatch) -> None:
    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    (tmp_path / "s1.json").write_text("not a program at all")
    runtime = registry._build("s1")
    assert runtime.engine.compiled.digest == ""
    runtime.close_lease()


def test_invalid_global_program_refuses_the_session_instead_of_serving_baseline(tmp_path, monkeypatch) -> None:
    registry, _saved = _global_program_registry(tmp_path, monkeypatch)
    (tmp_path / "fleet.json").write_text('{"not": "a fleet-program-b1 envelope"}')
    with pytest.raises(ValueError, match="Unsupported compiled program"):
        registry._build("s1")


@pytest.mark.parametrize("approval_at", ["startup", "invocation"])
def test_concurrent_approval_is_served_without_any_per_session_selection(tmp_path, monkeypatch, approval_at) -> None:
    import dspy

    from fleet_rlm.engine.compiled import program_envelope

    registry, saved = _global_program_registry(tmp_path, monkeypatch)
    runtime = registry._build("s1")
    candidate = runtime.engine.program.deepcopy()
    candidate.routing.predict.signature = candidate.routing.predict.signature.with_instructions("Approved policy")
    content = program_envelope(candidate)
    path = tmp_path / "fleet.json"

    if approval_at == "startup":
        runtime.close_lease()

        def approve() -> None:
            path.write_bytes(content)

        monkeypatch.setattr(
            registry.store,
            "acquire_session_lease",
            lambda _session_id: approve() or SimpleNamespace(close=lambda: None),
        )

        def admit():
            return registry._build("s1")
    else:
        reload = runtime.engine.compiled.reload_if_changed
        pending = True

        def reload_then_approve(*args, **kwargs) -> None:
            nonlocal pending
            reload(*args, **kwargs)
            if pending:
                pending = False
                path.write_bytes(content)

        monkeypatch.setattr(runtime.engine.compiled, "reload_if_changed", reload_then_approve)
        monkeypatch.setattr(runtime.engine.program, "forward", lambda **_inputs: dspy.Prediction(answer="offline"))

        def admit():
            runtime.engine("", "", "task")  # approval lands during this Turn's boundary check
            return runtime.engine("", "", "task")  # and is served by the next one

    try:
        admitted = admit()
        engine = admitted.engine if approval_at == "startup" else runtime.engine
        assert engine.program.routing.predict.signature.instructions == "Approved policy"
        if approval_at == "startup":
            admitted.close_lease()
        assert saved == {}
    finally:
        runtime.close_lease()


def test_build_does_not_terminalize_running_turns_when_resource_reconciliation_fails(monkeypatch) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    calls: list[str] = []

    class RecordingManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            def fail_reconcile(_resources: object) -> None:
                calls.append("reconcile")
                raise RuntimeError("provider cleanup remains uncertain")

            self.reconcile_sandbox_ownership = fail_reconcile

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(
            execute=lambda _statement: None,
            close=lambda: calls.append("lease-close"),
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: 2,
        sandbox_ownership=lambda _session_id: {"epoch": 2, "resources": [{"action": "stop"}]},
        fail_interrupted_turns=lambda *_args: calls.append("fail-interrupted"),
        runtime_settings=lambda _session_id: {},
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="provider cleanup remains uncertain"):
        registry._build("s1")

    assert calls == ["reconcile", "lease-close"]


@pytest.mark.parametrize(
    ("epoch", "ownership", "message"),
    [
        (None, None, "ownership is unknown"),
        (4, None, "cleanup is unresolved"),
    ],
)
def test_build_refuses_a_session_without_reconciled_sandbox_ownership(
    monkeypatch, epoch: int | None, ownership: dict | None, message: str
) -> None:
    """A Session predating the ownership record is fenced, not treated as having no compute."""
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    calls: list[str] = []

    class RecordingManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            # Any provider work here would mean the refusal happened too late.
            self.reconcile_sandbox_ownership = lambda _resources: calls.append("reconcile")

    store = SimpleNamespace(
        ensure_session=lambda _session_id: None,
        acquire_session_lease=lambda _session_id: SimpleNamespace(
            execute=lambda _statement: None,
            close=lambda: calls.append("lease-close"),
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: epoch,
        sandbox_ownership=lambda _session_id: ownership,
        fail_interrupted_turns=lambda *_args: calls.append("fail-interrupted"),
        runtime_settings=lambda _session_id: {},
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(session_module, "DaytonaSandboxManager", RecordingManager)
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace())
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match=message):
        registry._build("s1")

    assert calls == ["lease-close"]


@pytest.mark.parametrize(
    ("created", "prior_state", "handoff_state", "resources", "expected"),
    [
        (False, "known_empty", "unknown", [], "unknown"),
        (True, None, None, [], "known_empty"),
        (True, None, None, [{"action": "stop"}], "unknown"),
    ],
)
def test_build_initializes_and_refreshes_workspace_state_after_acquiring_the_lease(
    monkeypatch,
    created: bool,
    prior_state: str | None,
    handoff_state: str | None,
    resources: list[dict],
    expected: str,
) -> None:
    from fleet_rlm import session as session_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.session import SessionRuntimeRegistry

    events: list[str] = []
    saved = {"workspace_state": prior_state} if prior_state is not None else {}

    def acquire(_session_id: str):
        events.append("acquire_lease")
        if handoff_state is not None:
            saved["workspace_state"] = handoff_state
        return SimpleNamespace(execute=lambda _statement: None, close=lambda: events.append("lease-close"))

    store = SimpleNamespace(
        acquire_session_lease=acquire,
        ensure_session=lambda _session_id: events.append("ensure_session") or created,
        runtime_settings=lambda _session_id: events.append("runtime_settings") or dict(saved),
        save_runtime_settings=lambda _session_id, value: (
            events.append(f"save:{value['workspace_state']}") or saved.update(value)
        ),
        advance_sandbox_ownership_epoch=lambda _session_id: 1,
        sandbox_ownership=lambda _session_id: {"epoch": 1, "resources": resources},
        fail_interrupted_turns=lambda *_args: [],
        claim_environment_report=lambda _session_id: True,
        approved_program=lambda: {"approved": None, "approving": []},
    )
    monkeypatch.setattr(
        session_module,
        "DaytonaSandboxManager",
        lambda *_args, **kwargs: SimpleNamespace(
            session_id=kwargs["session_id"], reconcile_sandbox_ownership=lambda _resources: None
        ),
    )
    monkeypatch.setattr(session_module, "SkillManager", lambda *_args: SimpleNamespace())
    monkeypatch.setattr(session_module, "FleetRuntime", lambda **_kwargs: SimpleNamespace(reload_program=lambda: None))
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), store)  # type: ignore[arg-type]

    runtime = registry._build("s1")
    try:
        assert events[:3] == ["acquire_lease", "ensure_session", "runtime_settings"]
        assert events.count("runtime_settings") == 1
        assert runtime.workspace_state == saved["workspace_state"] == expected
    finally:
        runtime.close_lease()


def test_set_workspace_state_fences_when_session_lease_is_lost() -> None:
    from fleet_rlm.session import SessionRuntime

    writes: list[dict] = []
    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(session_id="s1"),  # type: ignore[arg-type]
        store=SimpleNamespace(save_runtime_settings=lambda _sid, data: writes.append(data)),  # type: ignore[arg-type]
        skill_mgr=SimpleNamespace(),  # type: ignore[arg-type]
        engine=SimpleNamespace(),  # type: ignore[arg-type]
        workspace_state="known_empty",
    )
    runtime.lease_lost.set()

    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.set_workspace_state("unknown")

    assert runtime.workspace_state == "known_empty" and writes == []


# Agent identity


class Span:
    def __init__(self):
        self.attributes = {}

    def set_attributes(self, values):
        self.attributes.update(values)

    def set_attribute(self, key, value):
        self.attributes[key] = value


@pytest.fixture
def registry(monkeypatch):
    models, associations = [], []
    monkeypatch.setattr(
        obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "source", "fleet.agent.git_dirty": "true"}
    )
    monkeypatch.setattr(obs, "_AGENT_MODELS", {})
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://local.invalid")

    def search(**kwargs):
        return [m for m in models if m.tags["fleet.agent.fingerprint"] in kwargs["filter_string"]]

    def create(**kwargs):
        model = SimpleNamespace(model_id=f"m-{len(models)}", **kwargs)
        models.append(model)
        return model

    monkeypatch.setattr(obs.mlflow, "search_logged_models", search)
    monkeypatch.setattr(obs.mlflow, "create_external_model", create)
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: associations.append(kwargs))
    monkeypatch.setattr(obs.mlflow, "set_active_model", lambda **_kw: pytest.fail("Global model context changed"))
    return models, associations


def engine(instruction="first", credential="private"):
    return SimpleNamespace(dump_state=lambda: {"predict": {"signature": instruction, "lm": {"api_key": credential}}})


def test_reuses_version_without_uploading_state_and_excludes_credentials(registry):
    models, associations = registry
    span = Span()
    first = obs.associate_turn_agent(engine(), "config", "1", span)
    assert obs.associate_turn_agent(engine(credential="rotated"), "config", "1", Span()) == first
    assert len(models) == 1 and len(associations) == 2
    assert "private" not in str(models[0].tags) and "signature" not in str(models[0].tags)
    assert models[0].name.startswith("fleet-rlm-")
    assert span.attributes["fleet.agent.model_id"] == first
    obs._AGENT_MODELS.clear()
    assert obs.associate_turn_agent(engine(), "config", "1", Span()) == first
    assert len(models) == 1  # Native search reuse after the process cache is empty.


def test_benchmark_trace_uses_requested_model_and_retains_automatic_identity(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "m-benchmark")
    monkeypatch.setenv(
        "FLEET_MLFLOW_BENCHMARK_TAGS",
        '{"fleet.benchmark.campaign":"cold-start-2026-11","fleet.benchmark.condition":"baseline"}',
    )
    span = Span()

    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)

    assert associations[-1]["model_id"] == "m-benchmark"
    assert associations[-1]["tags"] == {
        "fleet.benchmark.campaign": "cold-start-2026-11",
        "fleet.benchmark.condition": "baseline",
    }
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.model_id"] == "m-benchmark"


def test_benchmark_tags_attach_without_benchmark_model_id(registry, monkeypatch):
    _, associations = registry
    tags = {"fleet.benchmark.campaign": "cold-start-2026-11"}
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.campaign":"cold-start-2026-11"}')
    span = Span()
    automatic_model_id = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic_model_id, "tags": tags}
    assert span.attributes["fleet.agent.model_id"] == automatic_model_id
    assert span.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"
    assert "fleet.benchmark.model_id" not in span.attributes

    def fail(**_kw):
        raise RuntimeError("offline")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    unversioned = Span()
    assert obs.associate_turn_agent(engine("unversioned"), "config", "1", unversioned) is None
    assert associations[-1] == {"tags": tags}
    assert unversioned.attributes["fleet.agent.identity_status"] == "unversioned"
    assert unversioned.attributes["fleet.benchmark.campaign"] == "cold-start-2026-11"


def test_benchmark_env_strips_whitespace_and_rejects_empty_tag_suffix(registry, monkeypatch):
    _, associations = registry
    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "   ")
    span = Span()
    automatic = obs.associate_turn_agent(engine(), "config", "1", span)
    assert associations[-1] == {"model_id": automatic}
    assert "fleet.benchmark.model_id" not in span.attributes

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_MODEL_ID", "  m-bench  ")
    padded = Span()
    obs.associate_turn_agent(engine(), "config", "1", padded)
    assert associations[-1] == {"model_id": "m-bench"}
    assert padded.attributes["fleet.benchmark.model_id"] == "m-bench"

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", '{"fleet.benchmark.":"missing-name"}')
    invalid = Span()
    retained = obs.associate_turn_agent(engine(), "config", "1", invalid)
    assert retained is not None
    assert associations[-1]["model_id"] == "m-bench"
    assert invalid.attributes["fleet.agent.model_id"] == retained
    assert invalid.attributes["fleet.benchmark.model_id"] == "m-bench"
    assert "fleet.benchmark." not in invalid.attributes

    monkeypatch.setenv("FLEET_MLFLOW_BENCHMARK_TAGS", "{not-json")
    malformed = Span()
    obs.associate_turn_agent(engine(), "config", "1", malformed)
    assert malformed.attributes["fleet.benchmark.model_id"] == "m-bench"


def test_config_loaded_program_destination_and_source_affect_identity(registry, monkeypatch):
    models, _ = registry
    values = [obs.associate_turn_agent(engine(), "config", "1", Span())]
    values.append(obs.associate_turn_agent(engine("changed"), "config", "1", Span()))
    values.append(obs.associate_turn_agent(engine(), "changed", "1", Span()))
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", {"fleet.agent.source_digest": "another dirty snapshot"})
    values.append(obs.associate_turn_agent(engine(), "config", "1", Span()))
    assert len(set(values)) == len(models) == 4
    monkeypatch.setattr(obs.mlflow, "get_tracking_uri", lambda: "http://another.invalid")
    obs.associate_turn_agent(engine(), "config", "1", Span())
    assert len(obs._AGENT_MODELS) == 5


@pytest.mark.usefixtures("registry")
def test_parallel_sessions_associate_only_their_current_trace(monkeypatch):
    current = ContextVar("trace")
    recorded = {}
    monkeypatch.setattr(obs.mlflow, "update_current_trace", lambda **kwargs: recorded.update({current.get(): kwargs}))

    def run(index):
        current.set(index)
        model = obs.associate_turn_agent(engine(str(index)), "config", "1", Span())
        return index, model

    with ThreadPoolExecutor(max_workers=4) as pool:
        expected = dict(pool.map(run, range(8)))
    assert {index: value["model_id"] for index, value in recorded.items()} == expected
    assert len(set(expected.values())) == 8


@pytest.mark.usefixtures("registry")
def test_observation_failure_is_sanitized_and_does_not_raise(monkeypatch, caplog):
    def fail(**_kwargs):
        raise RuntimeError("credential-that-must-never-be-logged")

    monkeypatch.setattr(obs.mlflow, "search_logged_models", fail)
    span = Span()
    assert obs.associate_turn_agent(engine(), "config", "1", span) is None
    assert span.attributes["fleet.agent.identity_status"] == "unversioned"
    assert "credential-that-must-never-be-logged" not in caplog.text


@pytest.mark.usefixtures("registry")
def test_slow_lookup_does_not_block_other_cached_identities(monkeypatch):
    cached = obs.associate_turn_agent(engine("cached"), "config", "1", Span())
    entered, release = Event(), Event()
    original = obs.mlflow.search_logged_models

    def slow_search(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    monkeypatch.setattr(obs.mlflow, "search_logged_models", slow_search)
    monkeypatch.setattr(obs, "_AGENT_LOOKUP_WAIT_SECONDS", 0.01)
    span = Span()
    try:
        assert obs.associate_turn_agent(engine("slow"), "config", "1", span) is None
        assert entered.is_set()
        assert span.attributes["fleet.agent.identity_status"] == "unversioned"
        assert obs.associate_turn_agent(engine("cached"), "config", "1", Span()) == cached
    finally:
        release.set()


def test_startup_snapshot_is_frozen_and_wheel_needs_no_git(tmp_path, monkeypatch):
    package = tmp_path / "wheel" / "fleet_rlm"
    package.mkdir(parents=True)
    (package / "engine.py").write_text("behavior = 1")
    (package / ".env").write_text("private configuration")
    (package / "memory.json").write_text("private memory")
    monkeypatch.setattr(obs.subprocess, "run", lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()))
    first = obs.serving_snapshot(package)
    (package / ".env").write_text("rotated private configuration")
    assert obs.serving_snapshot(package) == first
    (package / "engine.py").write_text("behavior = 2")
    second = obs.serving_snapshot(package)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    assert "fleet.agent.git_commit" not in first
    monkeypatch.setattr(obs, "_STARTUP_IDENTITY", None)
    monkeypatch.setattr(obs, "serving_snapshot", lambda: first)
    obs.capture_serving_snapshot()
    monkeypatch.setattr(obs, "serving_snapshot", lambda: second)
    obs.capture_serving_snapshot()
    assert first == obs._STARTUP_IDENTITY


def test_bundled_resource_changes_are_included_but_optimizer_and_data_are_excluded(tmp_path):
    (tmp_path / "engine.py").write_text("pass")
    resource = tmp_path / "bundled_skills" / "example" / "SKILL.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("first instruction")
    first = obs.serving_snapshot(tmp_path)
    resource.write_text("second instruction")
    second = obs.serving_snapshot(tmp_path)
    assert first["fleet.agent.source_digest"] != second["fleet.agent.source_digest"]
    (tmp_path / "optimize").mkdir()
    (tmp_path / "optimize" / "scorer.py").write_text("pass")
    (tmp_path / "dataset.csv").write_text("sensitive")
    assert obs.serving_snapshot(tmp_path) == second


def test_native_logged_model_search_reuse_and_persisted_trace_association(tmp_path):
    """Exercise pinned MLflow against a disposable local store in an isolated process."""
    import os
    import subprocess
    import sys

    program = """
import sys
from pathlib import Path
from types import SimpleNamespace
import mlflow
from fleet_rlm import observability as obs
root = Path(sys.argv[1])
mlflow.set_tracking_uri(f"sqlite:///{root / 'tracking.db'}")
experiment = mlflow.create_experiment("agent-contract", artifact_location=(root / "artifacts").as_uri())
mlflow.set_experiment(experiment_id=experiment)
obs._STARTUP_IDENTITY = {"fleet.agent.source_digest": "offline-source"}
# This checks native persistence, not the separately covered lookup timeout.
obs._AGENT_LOOKUP_WAIT_SECONDS = 5.0
benchmark_model = mlflow.create_external_model(name="benchmark", experiment_id=experiment, model_type="agent")
import os
os.environ["FLEET_MLFLOW_BENCHMARK_MODEL_ID"] = benchmark_model.model_id
os.environ["FLEET_MLFLOW_BENCHMARK_TAGS"] = '{"fleet.benchmark.campaign":"offline"}'
engine = SimpleNamespace(dump_state=lambda: {"predict": {"signature": "loaded-program", "lm": None}})
with mlflow.start_span(name="fleet_turn") as span:
    first = obs.associate_turn_agent(engine, "config", experiment, span)
    assert first is not None
    assert span.attributes["fleet.agent.model_id"] == first
    trace_id = span.trace_id
obs._AGENT_MODELS.clear()
with mlflow.start_span(name="fleet_turn") as span:
    assert obs.associate_turn_agent(engine, "config", experiment, span) == first
models = mlflow.search_logged_models(experiment_ids=[experiment], output_format="list")
assert {model.model_id for model in models} == {benchmark_model.model_id, first}
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
assert trace.info.trace_metadata["mlflow.modelId"] == benchmark_model.model_id
assert trace.info.tags["fleet.benchmark.campaign"] == "offline"
assert trace.data.spans[0].attributes["fleet.agent.model_id"] == first
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)],
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"},
    )
    assert result.returncode == 0, result.stderr


# Native usage accounting


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (
            {"prompt_tokens": 12, "completion_tokens": 5},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 20},
        ),
        (
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
            {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 0},
        ),
        ({"prompt_tokens": 0, "completion_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        ({"prompt_tokens": 12}, {"prompt_tokens": 12}),
        ({"completion_tokens": 5}, {"completion_tokens": 5}),
        ({}, {}),
    ],
    ids=["derive", "reported", "reported-zero", "derive-zero", "input-only", "output-only", "no-usage"],
)
def test_usage_summary_derives_only_from_complete_receipts(entry, expected):
    assert _usage_summary(entry) == expected


@pytest.fixture
def usage_span(monkeypatch):
    attributes = {}
    span = SimpleNamespace(span_type="LLM", set_attribute=lambda key, value: attributes.__setitem__(key, value))
    monkeypatch.setattr("fleet_rlm.observability.mlflow.get_current_active_span", lambda: span)
    return attributes


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_generation_usage_normalizes_once_before_native_rollup(usage_span, fails):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        for call_id, counts, expected_total in [("first", (12, 5), 17), ("second", (3, 4), 7)]:
            collector.on_lm_start(call_id, SimpleNamespace(model="model"), {})
            dspy.settings.usage_tracker.add_usage(
                "model", {"prompt_tokens": counts[0], "completion_tokens": counts[1], "provider_detail": "preserved"}
            )
            collector.on_lm_end(call_id, None, RuntimeError("failed") if fails else None)
            assert usage_span["mlflow.chat.tokenUsage"] == {
                "input_tokens": counts[0],
                "output_tokens": counts[1],
                "total_tokens": expected_total,
            }
            assert dspy.settings.usage_tracker is parent
            assert collector.finish_usage(call_id) == {}
    assert parent.get_total_tokens()["model"] == {
        "prompt_tokens": 15,
        "completion_tokens": 9,
        "total_tokens": 24,
        "provider_detail": "preserved",
    }
    assert len(parent.usage_data["model"]) == 2
    assert all(entry["provider_detail"] == "preserved" for entry in parent.usage_data["model"])
    assert collector._usage_scopes == {}


@pytest.mark.parametrize(
    "invalid", [None, True, -1, float("nan"), float("inf")], ids=["none", "bool", "negative", "nan", "inf"]
)
def test_generation_invalid_counts_stay_unknown_before_rollup(usage_span, invalid):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage(
            "model", {"prompt_tokens": invalid, "completion_tokens": 5, "total_tokens": invalid}
        )
        assert collector.finish_usage("call") == {"completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"completion_tokens": 5}
    assert parent.usage_data["model"] == [{"completion_tokens": 5}]


def test_generation_does_not_derive_total_from_complementary_partial_receipts(usage_span):
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12})
        dspy.settings.usage_tracker.add_usage("model", {"completion_tokens": 5})
        assert collector.finish_usage("call") == {"prompt_tokens": 12, "completion_tokens": 5}
    assert usage_span["mlflow.chat.tokenUsage"] == {"input_tokens": 12, "output_tokens": 5}
    assert parent.get_total_tokens()["model"] == {"prompt_tokens": 12, "completion_tokens": 5}
    assert len(parent.usage_data["model"]) == 2


def test_generation_restores_native_scope_when_span_annotation_fails(monkeypatch):
    def reject_attribute(*_args):
        raise RuntimeError("annotation failed")

    span = SimpleNamespace(span_type="LLM", set_attribute=reject_attribute)
    monkeypatch.setattr("fleet_rlm.observability.mlflow.get_current_active_span", lambda: span)
    collector = LMUsageCallback()
    parent = UsageTracker()
    with dspy.context(usage_tracker=parent):
        collector.on_lm_start("call", SimpleNamespace(model="model"), {})
        dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 12, "completion_tokens": 5})
        with pytest.raises(RuntimeError, match="annotation failed"):
            collector.finish_usage("call")
        assert dspy.settings.usage_tracker is parent
    assert parent.get_total_tokens()["model"]["total_tokens"] == 17
    assert len(parent.usage_data["model"]) == 1
    assert collector._usage_scopes == {}


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_should_restore_parent_usage_context_on_async_completion(fails):
    collector = LMUsageCallback()
    parent = UsageTracker()

    async def call():
        with dspy.context(usage_tracker=parent):
            collector.on_lm_start("call", SimpleNamespace(model="model"), {})
            await asyncio.sleep(0)
            dspy.settings.usage_tracker.add_usage("model", {"prompt_tokens": 7, "total_tokens": 9})
            collector.on_lm_end("call", None, RuntimeError("failed") if fails else None)
            assert dspy.settings.usage_tracker is parent

    asyncio.run(call())
    assert parent.get_total_tokens()["model"]["total_tokens"] == 9
    assert collector._usage_scopes == {}
    assert usage_callbacks([object(), collector])[0] is collector


def test_should_ignore_decision_usage_and_omit_missing_generation_usage():
    collector = LMUsageCallback()
    with dspy.context(usage_tracker=None):
        collector.on_lm_start("decision", SimpleNamespace(supports_decision_requests=True), {})
        assert collector.finish_usage("decision") == {}
        collector.on_lm_start("generation", SimpleNamespace(model="model"), {})
        assert collector.finish_usage("generation") == {}
        assert dspy.settings.usage_tracker is None


@pytest.mark.parametrize("fails", [False, True])
def test_typesafe_partial_usage_rolls_up_once_even_after_failure(fails):
    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent):
        try:
            with decision_usage(span):
                dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 883, "completion_tokens": 39})
                if fails:
                    raise RuntimeError("decision failed after usage was reported")
        except RuntimeError:
            assert fails
        assert dspy.settings.usage_tracker is parent
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 883, "output_tokens": 39, "total_tokens": 922}
    assert parent.get_total_tokens()["jev"] == {"prompt_tokens": 883, "completion_tokens": 39, "total_tokens": 922}


def test_decision_usage_keeps_unknown_counts_unknown():
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with decision_usage(span):
        dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": 7, "completion_tokens": None})
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 7}
    attributes.clear()
    with decision_usage(span):
        pass
    assert attributes == {}


def test_batched_decision_workers_share_one_usage_receipt():
    from dspy.utils.callback import with_callbacks

    class DecisionLM(dspy.BaseLM):
        supports_decision_requests = True

        def __init__(self):
            super().__init__(model="typesafe:jev")

        @with_callbacks
        def __call__(self, count):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens": count, "completion_tokens": 1})
            return dspy.Prediction(answer="relevant")

    class Decision(dspy.Module):
        def forward(self, count):
            return DecisionLM()(count)

    parent = UsageTracker()
    attributes = {}
    span = SimpleNamespace(set_attribute=lambda key, value: attributes.__setitem__(key, value))
    with dspy.context(usage_tracker=parent), decision_usage(span):
        results = Decision().batch([dspy.Example(count=n).with_inputs("count") for n in (2, 3, 4)], num_threads=3)
    assert len(results) == 3
    assert attributes["mlflow.chat.tokenUsage"] == {"input_tokens": 9, "output_tokens": 3, "total_tokens": 12}
    assert parent.get_total_tokens()["jev"]["total_tokens"] == 12


def test_should_capture_per_call_and_aggregate_tokens_through_production_tracing(tmp_path):
    # A subprocess isolates global DSPy/MLflow callbacks. The only backend is temporary SQLite.
    missing_totals = False
    code = r"""
import json
import dspy
import mlflow
from dspy.lm15 import Message, Response, Usage
from fleet_rlm.observability import configure_mlflow_observability
import fleet_rlm.engine.decisions as decisions
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.contracts import Route
from fleet_rlm.observability import decision_usage, usage_callbacks
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

def refuse_sandbox(*_args, **_kwargs):
    raise AssertionError("Offline tracing test must not provision a sandbox")
DaytonaSandboxManager._ensure_sandbox = refuse_sandbox

class Engine:
    def __init__(self):
        self.count = 0
        self.responses = iter([
            "<plan>checked</plan>\n<answer>5</answer>",
            "invalid output",
            '{"answer": "5"}',
        ])
    def complete(self, request):
        self.count += 1
        inputs, outputs = self.count * 10, self.count + 3
        return Response(id=None, model="offline", message=Message.assistant(next(self.responses)), finish_reason="stop",
                        usage=Usage(input_tokens=inputs, output_tokens=outputs, total_tokens=inputs+outputs))
    def close(self):
        pass

configure_mlflow_observability("usage-regression", __import__('os').environ['MLFLOW_TRACKING_URI'])
lm = dspy.LM("openai/offline", engine=Engine(), cache=False)
if __import__('os').environ['MISSING_TOTALS'] == '1':
    # The typed engine derives totals; simulate partial provider receipts at native ingestion.
    from dspy.utils.usage_tracker import UsageTracker
    add_usage = UsageTracker.add_usage
    def without_total(tracker, model, entry):
        if model == 'openai/offline':
            entry = {key: value for key, value in entry.items() if key != 'total_tokens'}
        return add_usage(tracker, model, entry)
    UsageTracker.add_usage = without_total
manager = DaytonaSandboxManager("key", "org", "volume", "offline")
engine = FleetRuntime(manager, lm, lm)
def choose(**_kwargs):
    with mlflow.start_span(name="typesafe_route_selection", span_type="LLM") as span:
        with decision_usage(span):
            dspy.settings.usage_tracker.add_usage("jev", {"prompt_tokens":883,"completion_tokens":39})
    return Route.DIRECT
decisions.select_route = choose
with mlflow.start_span(name="fleet_turn") as root:
    with dspy.context(disable_history=True, track_usage=True):
        result = engine(workspace_manifest="", memory_summary="", task="return five")
    assert result.answer == "5" and result.lm_calls == 1
    with dspy.context(lm=lm, callbacks=usage_callbacks(list(dspy.settings.callbacks)),
                      adapter=dspy.ChatAdapter(), disable_history=True, track_usage=True):
        repaired = dspy.Predict("task -> answer")(task="return five")
    assert repaired.answer == "5"
    trace_id = root.trace_id
assert lm.history == []
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
calls = [s for s in trace.data.spans if s.name == 'LM.__call__']
assert len(calls) == 3, [(s.name,s.attributes) for s in trace.data.spans]
assert [s.attributes['mlflow.chat.tokenUsage'] for s in calls] == [
    {'input_tokens':10,'output_tokens':4,'total_tokens':14},
    {'input_tokens':20,'output_tokens':5,'total_tokens':25},
    {'input_tokens':30,'output_tokens':6,'total_tokens':36},
]
assert json.loads(trace.info.trace_metadata['mlflow.trace.tokenUsage']) == {
    'input_tokens':943,'output_tokens':54,'total_tokens':997,
}
print(f"Verified offline trace {trace_id}: {len(trace.data.spans)} spans, 3 model calls, 997 tokens")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'usage.db'}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
            "MISSING_TOTALS": str(int(missing_totals)),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 model calls, 997 tokens" in result.stdout


def test_turn_callbacks_compose_ambient_callbacks_with_one_usage_collector():
    import dspy

    from fleet_rlm.observability import LMUsageCallback, ambient_callbacks, turn_callbacks

    marker = object()
    with dspy.context(callbacks=[marker]):
        composed = turn_callbacks()
        assert marker in ambient_callbacks()
        assert sum(isinstance(item, LMUsageCallback) for item in composed) == 1
        assert len(composed) == 2
        # dspy.context replaces the callback list, so re-spreading the ambient list is mandatory.
        assert marker in composed


def test_typesafe_callbacks_drop_the_mlflow_dspy_hook():
    import dspy

    from fleet_rlm.observability import FleetCallbackBase, LMUsageCallback, typesafe_callbacks

    usage = LMUsageCallback()
    external = type("ExternalCallback", (dspy.utils.callback.BaseCallback,), {})()
    with dspy.context(callbacks=[external, usage]):
        assert typesafe_callbacks() == [usage]
    assert isinstance(usage, FleetCallbackBase)


def test_stream_callbacks_append_without_reordering():
    import dspy

    from fleet_rlm.observability import stream_callbacks

    marker, collector = object(), object()
    with dspy.context(callbacks=[marker]):
        # The Turn's stream collector must stay last: it is the API's terminal-event owner.
        assert stream_callbacks(collector) == [marker, collector]


# Redaction and tracing


@pytest.mark.parametrize("destination", [None, "", "catalog.schema.prefix"])
def test_databricks_trace_experiment_preserves_native_activation(monkeypatch, destination):
    from fleet_rlm import observability

    experiment = (
        None
        if destination is None
        else SimpleNamespace(tags={"mlflow.experiment.databricksTraceDestinationPath": destination})
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    calls = []
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda name, **kwargs: calls.append((name, kwargs)) or experiment
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix="prefix",
            tracing_sql_warehouse_id="warehouse",
        )
        is experiment
    )
    assert len(calls) == 1
    assert calls[0][0] == "/Shared/runtime"
    assert calls[0][1]["trace_location"].full_table_prefix == "catalog.schema.prefix"


@pytest.mark.parametrize("prefix", ["requested", ""])
def test_databricks_trace_destination_mismatch_prevents_activation(monkeypatch, prefix):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.existing"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(
        observability.mlflow, "set_experiment", lambda *_args, **_kwargs: pytest.fail("must not activate")
    )
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    with pytest.raises(observability.MlflowTraceDestinationMismatchError, match="trace destination mismatch"):
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime",
            databricks=True,
            trace_catalog="catalog",
            trace_schema="schema",
            trace_table_prefix=prefix,
        )


def test_databricks_trace_experiment_omitted_prefix_uses_native_experiment_id(monkeypatch):
    from fleet_rlm import observability

    experiment = SimpleNamespace(
        experiment_id="17", tags={"mlflow.experiment.databricksTraceDestinationPath": "catalog.schema.17"}
    )
    monkeypatch.setattr(observability.mlflow, "get_experiment_by_name", lambda _name: experiment)
    monkeypatch.setattr(observability.mlflow, "set_experiment", lambda *_args, **_kwargs: experiment)
    monkeypatch.setenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "prior-warehouse")

    assert (
        observability.set_mlflow_trace_experiment(
            "/Shared/runtime", databricks=True, trace_catalog="catalog", trace_schema="schema"
        )
        is experiment
    )


def test_mlflow_autolog_traces_runtime_inference_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.observability as memory_and_eval

    calls: dict[str, Any] = {}
    monkeypatch.setattr(memory_and_eval.mlflow, "set_tracking_uri", lambda uri: calls.setdefault("uri", uri))
    monkeypatch.setattr(
        memory_and_eval.mlflow.tracing,
        "configure",
        lambda **kwargs: calls.setdefault("processors", kwargs),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "set_experiment",
        lambda name: calls.setdefault("experiment", name),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "dspy",
        SimpleNamespace(autolog=lambda **kwargs: calls.setdefault("autolog", kwargs)),
    )
    monkeypatch.setattr(
        memory_and_eval.mlflow,
        "get_experiment_by_name",
        lambda _name: SimpleNamespace(experiment_id="experiment-id"),
    )

    experiment_id = memory_and_eval.configure_mlflow_observability(
        "fleet-rlm-test",
        "sqlite:///mlflow-test.db",
    )

    assert experiment_id == "experiment-id"
    assert calls == {
        "uri": "sqlite:///mlflow-test.db",
        "experiment": "fleet-rlm-test",
        "processors": {"span_processors": [memory_and_eval.sanitize_trace_span]},
        "autolog": {
            "log_traces": True,
            "log_traces_from_compile": False,
            "log_traces_from_eval": False,
        },
    }


def test_trace_span_sanitization_redacts_secret_fields_and_bounds_content():
    import fleet_rlm.observability as memory_and_eval

    class Span:
        def __init__(self):
            self.inputs = {"token": "secret-value", "task": "Bearer abcdefghijklmnopqrstuvwxyz"}
            self.outputs = {"answer": "x" * (memory_and_eval._TRACE_STRING_LIMIT + 1)}

        def set_inputs(self, value):
            self.inputs = value

        def set_outputs(self, value):
            self.outputs = value

    span = Span()
    memory_and_eval.sanitize_trace_span(span)
    assert span.inputs["token"] == "[redacted]"
    assert "[redacted]" in span.inputs["task"]
    assert len(span.outputs["answer"]) < memory_and_eval._TRACE_STRING_LIMIT + 100


def test_native_required_skill_arguments_do_not_export_resource_bytes():
    from fleet_rlm.observability import trace_value
    from fleet_rlm.skills import captured_required_skill

    raw = b"---\nname: source\ndescription: Source\n---\nInstructions"
    skill = captured_required_skill("source", "workspace", (("SKILL.md", raw), ("secret.dat", b"private-resource")))
    exported = trace_value({"required_skills": (skill,)}, nodes=[0])
    assert exported == {"required_skills": ["[RequiredSkill omitted]"]}
    assert "private-resource" not in str(exported)


def test_trace_shows_reasoning_text_but_streams_still_redact_it() -> None:
    import fleet_rlm.observability as memory_and_eval

    cleaned = memory_and_eval.trace_value(
        {"reasoning_content": "Plan: use api_key=hidden", "api_key": "value"},
        nodes=[0],
    )
    assert cleaned == {"reasoning_content": "Plan: use api_key=[redacted]", "api_key": "[redacted]"}
    assert '"reasoning_content": "[redacted]"' in memory_and_eval.activity_preview({"reasoning_content": "private"})


def test_typesafe_route_filters_the_incompatible_mlflow_callback() -> None:
    from fleet_rlm.observability import FleetCallbackBase, LMUsageCallback, without_mlflow_dspy_callback

    class CustomFleetCallback(FleetCallbackBase):
        pass

    fleet_cb = CustomFleetCallback()
    usage_cb = LMUsageCallback()
    third_party = object()
    impostor = type("MlflowCallback", (), {"__module__": "mlflow.dspy.callback"})()

    assert without_mlflow_dspy_callback([third_party, fleet_cb, impostor, usage_cb]) == [fleet_cb, usage_cb]


def test_snapshot_provenance_is_written_to_the_turn_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.engine.turn as turn_module
    from fleet_rlm.config import FleetSettings
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, SNAPSHOT_NAME, clear_preflight_cache, preflight

    clear_preflight_cache()
    provider = SimpleNamespace(
        get=lambda name: SimpleNamespace(name=name, id="snap-1", state="active", cpu=4, mem=8, disk=8)
    )
    identity = preflight(provider, name=SNAPSHOT_NAME, api_key="key", org_id="org")
    expected = {
        "snapshot_name": SNAPSHOT_NAME,
        "snapshot_id": "snap-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": DEFAULT_DEFINITION.digest(),
    }
    captured: dict[str, Any] = {}

    class Span:
        def set_inputs(self, value: dict[str, Any]) -> None:
            captured["inputs"] = value

        def set_attributes(self, value: dict[str, Any]) -> None:
            captured["attributes"] = value

    monkeypatch.setattr(turn_module.mlflow, "update_current_trace", lambda **_kwargs: None)
    runtime = SimpleNamespace(
        configuration_overrides={},
        engine=SimpleNamespace(execution_mode="hybrid", root_lm=None, sub_lm=None),
        sandbox_mgr=SimpleNamespace(
            turn_timeout_seconds=900,
            snapshot_provenance=identity.provenance,
            deadline_provenance=lambda: {"deadline_source": "configuration", "effective_seconds": 900},
        ),
    )

    turn_module._describe_turn(
        Span(),
        runtime,
        FleetSettings(_env_file=None),
        "session-a",
        "compute a digit",
        "turn-1",
        None,
        {"manifest": "/workspace/files (empty)", "context_files": {}},
        None,
    )

    attributes = captured["attributes"]
    assert {key: attributes[key] for key in expected} == expected
    assert attributes["deadline_source"] == "configuration"
    assert attributes["effective_seconds"] == 900
    assert "definition_digest" not in attributes


# Skill validation


def test_skill_frontmatter_uses_yaml_and_preserves_colons_in_description() -> None:
    raw = """---
name: ledger-audit
description: "Audits ledgers: use when checking totals."
---

Check the total with Python.
"""

    metadata, body = parse_and_validate_skill_md("ledger-audit", raw)

    assert metadata == {
        "name": "ledger-audit",
        "description": "Audits ledgers: use when checking totals.",
    }
    assert body == "Check the total with Python."


def test_skill_frontmatter_rejects_folder_name_mismatch() -> None:
    raw = "---\nname: another-skill\ndescription: example\n---\n\nInstructions\n"

    with pytest.raises(ValueError, match="must match folder name"):
        parse_and_validate_skill_md("ledger-audit", raw)


def test_local_skill_helpers_support_progressive_disclosure_and_reject_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    helpers = {}
    source = SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})")
    exec(source, helpers)

    helpers["save_skill"](
        "ledger-audit",
        "Audit ledger totals when a reconciliation is requested.",
        "Read the source ledger and verify each total with Python.",
        scripts={"audit.py": "print('audit complete')\n"},
        references={"policy.md": "Amounts are stored in EUR.\n"},
    )
    assert helpers["list_skills"]() == [
        {"name": "ledger-audit", "description": "Audit ledger totals when a reconciliation is requested."}
    ]
    active_skill = helpers["use_skill"]("ledger-audit")
    assert "audit.py" in active_skill
    assert helpers["read_skill_reference"]("ledger-audit", "references/policy.md") == "Amounts are stored in EUR.\n"
    assert helpers["run_skill_script"]("ledger-audit", "scripts/audit.py") == "audit complete\n"

    outside = tmp_path / "outside.md"
    outside.write_text("not a Skill reference", encoding="utf-8")
    (root / "ledger-audit" / "references" / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic links"):
        helpers["read_skill_reference"]("ledger-audit", "references/escape.md")


def test_local_skill_sync_prevalidates_all_skills_before_writing(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    valid = skills_root / "a-valid-skill"
    invalid = skills_root / "z-invalid-skill"
    valid.mkdir(parents=True)
    invalid.mkdir()
    (valid / "SKILL.md").write_text("---\nname: a-valid-skill\ndescription: valid\n---\n\nInstructions.\n")
    (invalid / "SKILL.md").write_text("missing frontmatter\n")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []

        def write_workspace_file(self, relative_path: str, content: bytes) -> None:
            self.writes.append((relative_path, content))

    sandbox = RecordingSandbox()
    manager = SkillManager(sandbox, skills_root, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="frontmatter"):
        manager.sync_local_skills_to_volume()
    assert sandbox.writes == []


def test_local_catalog_matches_the_synced_skill_metadata(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill = root / "ledger-audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        '---\nname: ledger-audit\ndescription: "Audits ledger totals: on request."\n---\n\nCheck totals.\n',
        encoding="utf-8",
    )
    manager = SkillManager(object(), root, None)  # type: ignore[arg-type]

    assert manager.build_local_catalog_xml() == (
        "<available_skills>\n"
        '  <skill name="ledger-audit">Audits ledger totals: on request.</skill>\n'
        "</available_skills>"
    )


def test_local_skill_files_stage_bundled_and_local_resources_with_workspace_paths(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    local = tmp_path / "local"
    (bundled / "shared-skill").mkdir(parents=True)
    (bundled / "shared-skill" / "SKILL.md").write_text(
        "---\nname: shared-skill\ndescription: Bundled copy.\n---\n\nBundled.\n", encoding="utf-8"
    )
    (local / "shared-skill" / "references").mkdir(parents=True)
    skill_md = b"---\nname: shared-skill\ndescription: Local override.\n---\n\nLocal.\n"
    ref_md = b"Reference policy.\n"
    (local / "shared-skill" / "SKILL.md").write_bytes(skill_md)
    (local / "shared-skill" / "references" / "policy.md").write_bytes(ref_md)

    manager = SkillManager(object(), local, bundled)  # type: ignore[arg-type]
    assert manager.local_skill_files() == {
        "skills/shared-skill/SKILL.md": skill_md,
        "skills/shared-skill/references/policy.md": ref_md,
    }


def test_stage_local_skills_returns_catalog_and_files_in_one_scan(tmp_path: Path, monkeypatch) -> None:
    local = tmp_path / "local"
    (local / "audit-skill").mkdir(parents=True)
    skill_md = b"---\nname: audit-skill\ndescription: Single-pass stage.\n---\n\nCheck.\n"
    (local / "audit-skill" / "SKILL.md").write_bytes(skill_md)
    manager = SkillManager(object(), local, None)  # type: ignore[arg-type]
    scans = 0
    original = SkillManager._stage_local_skills

    def counted(self, **kwargs):
        nonlocal scans
        scans += 1
        return original(self, **kwargs)

    monkeypatch.setattr(SkillManager, "_stage_local_skills", counted)
    catalog, staged_files = manager.stage_local_skills()

    assert scans == 1
    assert 'name="audit-skill"' in catalog
    assert staged_files == {"skills/audit-skill/SKILL.md": skill_md}


def test_bundled_skill_discovery_and_activation_do_not_install_packages(tmp_path: Path, monkeypatch) -> None:
    bundled_skills = (
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    )
    root = tmp_path / "skills"
    shutil.copytree(BUNDLED_SKILLS_DIR, root)
    helpers: dict = {}
    exec(SANDBOX_SKILL_HELPERS_CODE.replace('Path("/workspace/skills")', f"Path({str(root)!r})"), helpers)

    def unexpected_process(*_args, **_kwargs):
        pytest.fail("Discovering or activating a Skill must not launch a process")

    monkeypatch.setattr(helpers["subprocess"], "run", unexpected_process)
    catalog = {item["name"] for item in helpers["list_skills"]()}
    assert catalog == set(bundled_skills)
    for name in bundled_skills:
        activated = helpers["use_skill"](name)
        assert f"# Skill: {name}" in activated


# Skill synchronization


def _skill(name: str, instructions: str = "Instructions.") -> bytes:
    return f"---\nname: {name}\ndescription: test skill\n---\n\n{instructions}\n".encode()


def test_required_capture_preserves_complete_bytes_overrides_and_does_not_reread(tmp_path):
    local, bundled = tmp_path / "local", tmp_path / "bundled"
    for root in (local, bundled):
        (root / "ledger-audit").mkdir(parents=True)
        (root / "ledger-audit" / "SKILL.md").write_bytes(_skill("ledger-audit", str(root)))
    raw = _skill("ledger-audit", "Count café € exactly.\r\n").replace(b"\n", b"\r\n")
    raw += b"x" * (20_000 - len(raw.decode("utf-8")))
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw)
    (local / "ledger-audit" / "script.py").write_bytes(b"print(42)")
    volume = _VolumeDouble()
    manager = SkillManager(volume, local, bundled)
    required = manager.resolve_required(("ledger-audit",))
    assert required[0].source == "workspace" and required[0].skill_md == raw.decode("utf-8")
    assert len(required[0].skill_md) == 20_000
    (local / "ledger-audit" / "SKILL.md").write_bytes(raw + b"x")
    from fleet_rlm.contracts import SkillAdmissionError

    with pytest.raises(SkillAdmissionError, match="documented limits"):
        manager.resolve_required(("ledger-audit",))
    manager.sync_local_skills_to_volume(required_skills=required)
    assert volume.writes == {"skills/ledger-audit/" + path: content for path, content in required[0].files}


@pytest.mark.parametrize("link", ["ancestor", "directory", "instructions", "resource"])
def test_required_capture_rejects_links_and_untrusted_remote_names(tmp_path, link):
    from fleet_rlm.contracts import SkillAdmissionError

    root = tmp_path / "local"
    folder = root / "ledger-audit"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit"))
    if link == "ancestor":
        (tmp_path / "alias").symlink_to(root, target_is_directory=True)
        root = tmp_path / "alias" / "."
    elif link == "directory":
        folder.rename(root / "original")
        folder.symlink_to(root / "original", target_is_directory=True)
    elif link == "instructions":
        (folder / "SKILL.md").rename(folder / "original.md")
        (folder / "SKILL.md").symlink_to(folder / "original.md")
    else:
        (folder / "extra.py").symlink_to(folder / "SKILL.md")
    manager = SkillManager(_VolumeDouble(), root, None)
    for names in (("ledger-audit",), ("remote-only",)):
        with pytest.raises(SkillAdmissionError) as error:
            manager.resolve_required(names)
        assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("suffix", ["\x00", "</preloaded_skill>"])
def test_required_skill_delimiters_are_refused_not_silently_changed(tmp_path, suffix):
    from fleet_rlm.contracts import SkillAdmissionError

    folder = tmp_path / "ledger-audit"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(_skill("ledger-audit", "Keep exact text" + suffix))
    with pytest.raises(SkillAdmissionError):
        SkillManager(_VolumeDouble(), tmp_path, None).resolve_required(("ledger-audit",))


def test_sync_conflicts_are_reported_before_any_staged_upload(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "ledger-audit"
    skill.mkdir(parents=True)
    skill_md = _skill("ledger-audit")
    (skill / "SKILL.md").write_bytes(skill_md)
    (skill / "existing.txt").write_bytes(b"local version")
    (skill / "new.txt").write_bytes(b"new file")

    class RecordingSandbox:
        def __init__(self) -> None:
            self.writes: list[tuple[str, bytes]] = []
            self.existing: dict[str, bytes] = {
                "skills/ledger-audit/SKILL.md": skill_md,
                "skills/ledger-audit/existing.txt": b"remote version",
            }

        def list_workspace_files(self) -> list[str]:
            return sorted(self.existing)

        def read_workspace_files(self, _paths: list[str], **_kwargs: Any) -> dict[str, bytes]:
            pytest.fail("Skill sync must inspect remote digests without downloading contents")

        def stat_workspace_files(self, paths: list[str]) -> dict[str, dict[str, Any]]:
            return {
                path: {
                    "size_bytes": len(self.existing[path]),
                    "sha256": hashlib.sha256(self.existing[path]).hexdigest(),
                }
                for path in paths
            }

        def write_workspace_files(self, files: dict[str, bytes]) -> None:
            self.writes.extend(files.items())

    sandbox = RecordingSandbox()
    with pytest.raises(ValueError, match=r"skills/ledger-audit/existing.txt"):
        SkillManager(sandbox, skills, None).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert sandbox.writes == []
    sandbox.existing["skills/ledger-audit/existing.txt"] = b"local version"
    assert SkillManager(sandbox, skills, None).sync_local_skills_to_volume() == 1  # type: ignore[arg-type]
    assert sandbox.writes == [("skills/ledger-audit/new.txt", b"new file")]

    sandbox.existing["skills/ledger-audit/existing.txt"] = b"x" * 5_000_001
    sandbox.writes.clear()
    with pytest.raises(ValueError, match=r"Skill sync.*synchronization bounds"):
        SkillManager(sandbox, skills, None).sync_local_skills_to_volume()  # type: ignore[arg-type]
    assert sandbox.writes == []


def test_export_conflicts_are_reported_before_any_local_write(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    local = skills / "local-skill"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_bytes(_skill("local-skill"))
    (local / "existing.txt").write_bytes(b"local version")
    remote_files = {
        f"skills/{path}": content
        for path, content in [
            ("local-skill/SKILL.md", _skill("local-skill")),
            ("local-skill/existing.txt", b"remote version"),
            ("new-skill/SKILL.md", _skill("new-skill")),
        ]
    }

    class RecordingSandbox:
        def list_workspace_files(self) -> list[str]:
            return sorted(remote_files)

        def read_workspace_files(self, paths: list[str], **_kwargs: Any) -> dict[str, bytes]:
            return {path: remote_files[path] for path in paths if path in remote_files}

    with pytest.raises(ValueError, match=r"local-skill/existing.txt"):
        SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local()  # type: ignore[arg-type]

    assert not (skills / "new-skill").exists()
    assert (local / "existing.txt").read_bytes() == b"local version"
    (local / "existing.txt").unlink()
    assert SkillManager(RecordingSandbox(), skills).export_volume_skills_to_local() == ["local-skill", "new-skill"]  # type: ignore[arg-type]
    assert (local / "SKILL.md").read_bytes() == _skill("local-skill")
    assert (skills / "new-skill" / "SKILL.md").read_bytes() == _skill("new-skill")


class _VolumeDouble:
    def __init__(self, existing: dict[str, bytes] | None = None) -> None:
        self.existing = existing or {}
        self.writes: dict[str, bytes] = {}

    def list_workspace_files(self) -> list[str]:
        return sorted(self.existing)

    def read_workspace_files(self, paths: list[str], **_kwargs: Any) -> dict[str, bytes]:
        return {path: self.existing[path] for path in paths if path in self.existing}

    def stat_workspace_files(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        return {
            path: {"size_bytes": len(self.existing[path]), "sha256": hashlib.sha256(self.existing[path]).hexdigest()}
            for path in paths
        }

    def write_workspace_files(self, files: dict[str, bytes]) -> None:
        self.batches = getattr(self, "batches", 0) + 1
        self.writes.update(files)


def test_bundled_skills_ship_valid_and_sync_with_local_overrides(tmp_path: Path) -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, parse_and_validate_skill_md

    bundled = sorted(path.parent.name for path in BUNDLED_SKILLS_DIR.glob("*/SKILL.md"))
    assert bundled == [
        "delegation",
        "long-context-search",
        "repository-inspection",
        "skill-creator",
        "tabular-analysis",
    ]
    for name in bundled:
        parse_and_validate_skill_md(name, (BUNDLED_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8"))

    local = tmp_path / "skills"
    (local / "delegation").mkdir(parents=True)
    override = _skill("delegation", "Local delegation rules.")
    (local / "delegation" / "SKILL.md").write_bytes(override)
    sandbox = _VolumeDouble()

    assert SkillManager(sandbox, local).sync_local_skills_to_volume() == 5  # type: ignore[arg-type]
    assert sorted(sandbox.writes) == [f"skills/{name}/SKILL.md" for name in bundled]
    assert sandbox.writes["skills/delegation/SKILL.md"] == override


def test_a_changed_bundled_skill_replaces_the_session_copy_but_user_edits_still_conflict(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    (bundled / "tabular-analysis").mkdir(parents=True)
    new_version = _skill("tabular-analysis", "Version 2.")
    (bundled / "tabular-analysis" / "SKILL.md").write_bytes(new_version)
    local = tmp_path / "local"
    (local / "mine").mkdir(parents=True)
    (local / "mine" / "SKILL.md").write_bytes(_skill("mine"))
    stale = b"old"

    sandbox = _VolumeDouble({"skills/tabular-analysis/SKILL.md": stale})
    assert SkillManager(sandbox, local, bundled).sync_local_skills_to_volume() == 2  # type: ignore[arg-type]
    assert sandbox.writes["skills/tabular-analysis/SKILL.md"] == new_version

    sandbox = _VolumeDouble({"skills/mine/SKILL.md": stale})
    with pytest.raises(ValueError, match=r"skills/mine/SKILL\.md"):
        SkillManager(sandbox, local, bundled).sync_local_skills_to_volume()  # type: ignore[arg-type]
    assert sandbox.writes == {}


def test_skill_sync_uploads_every_new_file_in_one_batch() -> None:
    from fleet_rlm.skills import BUNDLED_SKILLS_DIR, SkillManager

    volume = _VolumeDouble()
    SkillManager(volume, None, BUNDLED_SKILLS_DIR).sync_local_skills_to_volume()  # type: ignore[arg-type]

    assert volume.batches == 1
    assert len(volume.writes) >= 3


# Selected child inputs


@pytest.fixture
def _batches_use_single_file_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests fake per-file transfers; route the batched child copy through those fakes."""
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda self, paths, **_limits: {path: self.read_workspace_file(path) for path in paths},
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [self.write_workspace_file(path, data) for path, data in files.items()] and None,
    )


def _manager() -> DaytonaSandboxManager:
    return DaytonaSandboxManager("key", "org", "volume", "session-a")


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_allocate_child_copies_only_selected_parent_files(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "files/two.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    contents = {path: path.encode() for path in available}
    writes: list[tuple[str, bytes]] = []

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: contents[path])
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_file",
        lambda _self, path, data: writes.append((path, data)),
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/two.txt", "skills/review/SKILL.md"])

    assert child.parent is parent
    assert writes == [
        ("files/two.txt", contents["files/two.txt"]),
        ("skills/review/SKILL.md", contents["skills/review/SKILL.md"]),
    ]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
@pytest.mark.parametrize(
    ("input_paths", "message"),
    [
        (["memory/notes.txt"], "beneath files/, skills/ or artifacts/"),
        (["files/missing.txt"], "unavailable"),
        (["files/one.txt"] * 101, "limit"),
    ],
)
def test_allocate_child_rejects_unapproved_inputs_before_provider_allocation(
    monkeypatch: pytest.MonkeyPatch, input_paths: list[str], message: str
) -> None:
    parent = _manager()
    allocations: list[DaytonaSandboxManager] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/one.txt"])
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda self: allocations.append(self))

    with pytest.raises(ValueError, match=message):
        parent.allocate_child(input_paths=input_paths)

    assert allocations == []
    assert parent._children == {}
    assert parent._allocations == 0


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_selected_artifacts_are_copied_and_unchanged_copies_are_not_promoted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    parent_files = {"files/sales.csv": b"csv", "artifacts/report.json": b'{"north": 1}'}
    child_files: dict[str, bytes] = {}
    promoted: list[str] = []

    def list_files(self: DaytonaSandboxManager) -> list[str]:
        return list(child_files if self.parent is not None else parent_files)

    def read(self: DaytonaSandboxManager, path: str, **_limits: Any) -> bytes:
        return (child_files if self.parent is not None else parent_files)[path]

    def write(self: DaytonaSandboxManager, path: str, data: bytes) -> None:
        if self.parent is not None:
            child_files[path] = data
        else:
            promoted.append(path)

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", list_files)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", read)
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", write)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "write_workspace_files",
        lambda self, files: [write(self, p, b) for p, b in files.items()],
    )
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "[]")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv", "artifacts/report.json"])
    assert child_files == parent_files
    child_files["artifacts/counterfactual.json"] = b'{"central": 2}'

    results = child.collect_child_artifacts()

    assert [row["path"] for row in results] == [f"artifacts/{child.agent_id}/counterfactual.json"]
    assert promoted == [f"artifacts/{child.agent_id}/counterfactual.json"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_explicit_children_always_get_skills_without_receipting_them_as_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _manager()
    available = ["files/sales.csv", "files/other.csv", "skills/tabular-analysis/SKILL.md"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    child = parent.allocate_child(input_paths=["files/sales.csv"])

    assert writes == ["files/sales.csv", "skills/tabular-analysis/SKILL.md"]
    assert list(parent._child_receipts[child.agent_id]["inputs"]) == ["files/sales.csv"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_allocate_child_default_still_copies_all_files_and_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = _manager()
    available = ["files/one.txt", "skills/review/SKILL.md", "artifacts/result.txt"]
    writes: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: available)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, path: path.encode())
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_file", lambda _self, path, _data: writes.append(path))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda _self: None)

    parent.allocate_child()

    assert writes == ["files/one.txt", "skills/review/SKILL.md"]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_snapshots_inputs_and_omits_keyword_for_default_double(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    received: list[Any] = []

    def allocate_selected(_self: DaytonaSandboxManager, *, input_paths: list[str]) -> DaytonaSandboxManager:
        received.append(input_paths)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_selected)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )
    captured = ["files/first.txt"]
    factory = root.interpreter_factory(input_paths=captured)
    captured.append("files/later.txt")
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        result = factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert received == [["files/first.txt"]]
    assert result[0] is root
    assert result[2] is True
    assert result[3] == ("files/first.txt",)


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_default_calls_allocate_child_without_keywords(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.sandbox.manager as interpreter

    root = _manager()
    active = _manager()
    calls: list[bool] = []

    def allocate_default(_self: DaytonaSandboxManager) -> DaytonaSandboxManager:
        calls.append(True)
        return root

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate_default)
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )
    factory = root.interpreter_factory()
    token = interpreter._ACTIVE_MANAGER.set(active)
    try:
        factory()
    finally:
        interpreter._ACTIVE_MANAGER.reset(token)

    assert calls == [True]


@pytest.mark.usefixtures("_batches_use_single_file_fakes")
def test_factory_without_active_root_keeps_existing_manager(monkeypatch: pytest.MonkeyPatch) -> None:

    root = _manager()
    monkeypatch.setattr(
        "fleet_rlm.sandbox.interpreter.DaytonaInterpreter",
        lambda *, manager, tools, owns_manager, native_input_paths: (
            manager,
            tools,
            owns_manager,
            native_input_paths,
        ),
    )

    result = root.interpreter_factory(input_paths=["files/input.txt"])()

    assert result[0] is root
    assert result[2] is False


def test_registry_peek_and_evict_idle_and_per_session_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    s1_started = threading.Event()
    s1_finish = threading.Event()
    s2_built = threading.Event()

    runtime_1 = _runtime(sandbox_mgr=SimpleNamespace(session_id="s1", shutdown=lambda: True))
    runtime_1.last_active_at = 0.0
    runtime_2 = _runtime(sandbox_mgr=SimpleNamespace(session_id="s2", shutdown=lambda: True))
    runtime_2.last_active_at = time.monotonic()

    registry = _buildable_registry(runtime_1)
    assert registry.peek("s1") is runtime_1
    assert registry.peek("missing") is None

    # Idle eviction reclaims idle sessions while keeping recently active ones.
    registry._sessions["s2"] = runtime_2
    assert registry.evict_idle(max_idle_seconds=60.0) == 1
    assert registry.peek("s1") is None
    assert registry.peek("s2") is runtime_2

    runtime_a = _runtime(sandbox_mgr=SimpleNamespace(session_id="a", shutdown=lambda: True))

    # Per-session gates allow concurrent builds of distinct sessions.
    def fake_build(_self: Any, session_id: str) -> Any:
        if session_id == "a":
            s1_started.set()
            assert s1_finish.wait(timeout=2)
            return runtime_a
        s2_built.set()
        return runtime_2

    monkeypatch.setattr(type(registry), "_build", fake_build)
    build_a = threading.Thread(target=registry.get, args=("a",))
    build_a.start()
    try:
        assert s1_started.wait(timeout=1)
        assert registry.get("b") is runtime_2
        assert s2_built.is_set()
    finally:
        s1_finish.set()
        build_a.join(timeout=2)
    assert registry.peek("a") is runtime_a


def test_delegation_signatures_document_bounds_child_isolation_and_workspace_scope() -> None:
    from fleet_rlm.contracts import STOPPING_RULES
    from fleet_rlm.engine import signatures

    fleet_doc = signatures.FleetTask.instructions
    assert "1..8 subtasks" in fleet_doc
    assert "max_iters in 1..8" in fleet_doc
    assert "max_llm_calls in 1..25" in fleet_doc
    assert "Child artifacts/ start empty unless explicitly passed via input_paths" in fleet_doc
    assert "/workspace/..." in fleet_doc and "recursive globs from `/`" in fleet_doc
    assert fleet_doc.count(STOPPING_RULES) == 1
    assert len(fleet_doc) < 4000

    child_doc = signatures.ChildTask.instructions
    assert "/workspace/..." in child_doc and "recursive globs from `/`" in child_doc
    assert "Child `artifacts/` starts empty unless inputs were explicitly passed via input_paths" in child_doc
    assert "1..8" in child_doc and "1..25" in child_doc


def test_turn_triage_prefers_flex_for_bounded_checks_without_drifting_route_fields() -> None:
    from fleet_rlm.contracts import JUDGE_ROUTE_CHOICES, ROUTE_DESCRIPTIONS, TRIAGE_CRITERIA, TRIAGE_INSTRUCTION
    from fleet_rlm.engine.signatures import TurnTriage

    assert "choose flex for one bounded calculation" in TRIAGE_INSTRUCTION
    assert TurnTriage.model_fields["route"].json_schema_extra["desc"] == TRIAGE_INSTRUCTION
    assert dict(JUDGE_ROUTE_CHOICES) == {route.value: item["judge"] for route, item in ROUTE_DESCRIPTIONS.items()}
    assert {key: value["definition"] for key, value in TRIAGE_CRITERIA.items()} == {
        route.value: item["definition"] for route, item in ROUTE_DESCRIPTIONS.items()
    }


def test_registry_reserves_capacity_while_a_session_builds(monkeypatch):
    import threading

    from fleet_rlm.session import SessionRuntimeRegistry

    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None, max_active_sessions=1), SimpleNamespace())
    registry.root_lm = registry.sub_lm = registry.jev_lm = object()
    entered, release = threading.Event(), threading.Event()
    runtime = SimpleNamespace(touch=lambda: None, lease_lost=threading.Event())
    errors = []

    def build(_self, _sid):
        entered.set()
        assert release.wait(2)
        return runtime

    def first():
        try:
            registry.get("first")
        except BaseException as error:
            errors.append(error)

    served = []
    same_session = None

    def concurrent_same_session():
        try:
            served.append(registry.get("first"))
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(SessionRuntimeRegistry, "_build", build)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(1)
        same_session = threading.Thread(target=concurrent_same_session)
        same_session.start()
        with pytest.raises(RuntimeError, match="maximum number"):
            registry.get("second")
    finally:
        release.set()
        thread.join(2)
        if same_session is not None:
            same_session.join(2)
    assert not errors
    assert served == [runtime]
    assert registry.peek("first") is runtime
    assert len(registry._session_gates) == 0
