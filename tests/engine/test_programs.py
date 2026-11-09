from __future__ import annotations

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
from dspy import AdapterParseError

from fleet_rlm.contracts import ENGINE_INPUT_FIELDS, OptimizationInputs, Route
from fleet_rlm.engine.compiled import program_envelope
from fleet_rlm.engine.invocation import FleetRuntime
from fleet_rlm.engine.programs import FleetProgram
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


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
    assert parameters - set(ENGINE_INPUT_FIELDS) == {"self", "context", "execution_mode"}


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
        assert isinstance(dspy.settings.adapter, dspy.ChatAdapter)
        return SimpleNamespace(findings="done")

    spawn_agent, interpreter, calls = _spawn_agent_with_fake_child(monkeypatch, child_call)

    assert spawn_agent(subtask="investigate", input_paths=[])["findings"] == "done"
    assert interpreter.owns_manager is False
    assert calls == ["collect", "shutdown"]


@pytest.mark.parametrize("route", ["direct", "rlm", "flex"])
@pytest.mark.parametrize("adapter_kind", ["chat", "json"])
def test_execution_adapter_policy_is_scoped_and_injectable(monkeypatch, route, adapter_kind):
    from fleet_rlm.contracts import Route

    manager = DaytonaSandboxManager("key", "org", "volume", "adapter-policy")
    root_adapter = dspy.JSONAdapter() if adapter_kind == "json" else dspy.ChatAdapter(use_json_adapter_fallback=False)
    engine = _engine(manager, root_adapter=root_adapter)
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    ambient = dspy.ChatAdapter(use_json_adapter_fallback=False)

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
                relevance=SimpleNamespace(value="relevant" if example.skill_name in relevant else "not_relevant")
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
    engine.program.routing.predict = lambda **_kwargs: SimpleNamespace(route="flex")
    engine.program.routing._consume_budget = engine.sandbox_mgr.consume_budget

    assert (
        engine.program.routing(
            mode="hybrid",
            task="task",
            workspace_manifest="",
            skills_catalog=CATALOG,
            has_context=False,
        )
        is seen["route"].FLEX
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
    assert engine.program.reasoning.flex._interpreter_factory().rlm_defaults == {
        "max_iters": 16,
        "max_llm_calls": manager.max_llm_calls,
    }
    assert engine.program.reasoning.rlm._interpreter_factory().rlm_defaults == {}


def test_pre_consolidation_state_remains_inspectable_but_is_not_executable():
    from pathlib import Path

    manager = DaytonaSandboxManager("key", "org", "volume", "state-fixture")
    engine = _engine(manager)
    fixture = Path(__file__).parents[1] / "fixtures/pre_consolidation_program.json"
    state = json.loads(fixture.read_text())
    assert "flex_agent" in state and "max_iters=16" in state["flex_agent"]["module_src"]
    before = deepcopy(engine.program.dump_state())
    with pytest.raises(ValueError, match="exactly"):
        engine.program.load_state(state)
    assert engine.program.dump_state() == before


@pytest.mark.parametrize("entrypoint", ["native", "compiled", "isolated"])
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
