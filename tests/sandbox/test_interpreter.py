"""Daytona interpreter, host-tool bridge, native delegation, and DSPy defaults."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import CodeExecutionError, FinalOutput

import fleet_rlm.sandbox.interpreter as interpreter_module
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

# Interpreter and host-tool bridge

STOPPED: list[str] = []


@pytest.mark.parametrize("payload", ["42", "'not a workspace descriptor'", "{'path': 'files/input.csv'}"])
def test_native_host_values_refuse_guest_codec_reconstruction(payload):
    from dspy.primitives.facade import HostValues
    from dspy.primitives.sandbox_guest import _VALUE

    node = {"__dspy__": "codec", "name": "_input", "text": "", "assign": f"{_VALUE} = {payload}"}
    with pytest.raises(ValueError, match="Only the sandbox rebuilds"):
        HostValues().decode(node)


@pytest.mark.parametrize("route", ["rlm", "flex"])
def test_dataset_native_roundtrip_loads_once_and_keeps_iteration_state(sandbox, route, monkeypatch):
    """Exercise the real Fleet dispatch, native DSPy bridge, and remote runtime without providers."""
    import hashlib

    from pydantic_core import to_jsonable_python

    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.engine.signatures import FleetTask

    raw = b"__FILE_BODY_ONLY__,value\none,11\ntwo,17\n"
    sandbox.fs.upload_file(raw, "/workspace/files/selected.csv")
    dataset = WorkspaceDataset("files/selected.csv", "selected CSV", len(raw), hashlib.sha256(raw).hexdigest())
    serialized = []
    assignments = []
    transported = []
    original_serialize = WorkspaceDataset.to_sandbox
    original_assignment = WorkspaceDataset.sandbox_assignment

    def serialize(self):
        serialized.append(self)
        return original_serialize(self)

    def assign(self, name, data):
        assignments.append((self, name))
        return original_assignment(self, name, data)

    monkeypatch.setattr(WorkspaceDataset, "to_sandbox", serialize)
    monkeypatch.setattr(WorkspaceDataset, "sandbox_assignment", assign)
    manager = manager_on(sandbox)
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/selected.csv"])

    class RecordingInterpreter(DaytonaInterpreter):
        def execute(self, code, variables=None):
            transported.append(json.dumps(to_jsonable_python(variables or {})))
            return super().execute(code, variables)

    def echo(value: str) -> str:
        return value

    factory = lambda: RecordingInterpreter(manager)  # noqa: E731
    # A nested RLM receives native JSON metadata, not a guest-defined codec or its methods.
    read_lines = "workspace.read_text(context['path']).splitlines()" if route == "flex" else "list(context.lines())"
    remember_context = "context_metadata = dict(context)" if route == "flex" else "view_id = id(context)"
    check_context = "assert context == context_metadata" if route == "flex" else "assert id(context) == view_id"
    lm = dspy.utils.DummyLM(
        [
            {
                "plan": "Read the sandbox file",
                "code": "assert 'subtotal' not in globals()\n" + remember_context + "\n"
                f"subtotal = sum(int(line.split(',')[1]) for line in {read_lines}[1:])\nprint(subtotal)",
            },
            {
                "plan": "Reuse the computed value",
                "code": check_context + "\nSUBMIT(answer=str(subtotal), learned_skill_note='')",
            },
        ]
        * 2
    )
    engine = FleetRuntime(manager, lm, lm)
    if route == "flex":
        engine.program.reasoning.flex = dspy.Flex(FleetTask, tools=[echo], interpreter_factory=factory)
    else:
        engine.program.reasoning.rlm = dspy.RLM(FleetTask, interpreter_factory=factory, max_iters=3)
    with dspy.context(disable_history=True):
        for _ in range(2):
            prediction = engine.program.reasoning(
                route=Route(route),
                workspace_manifest="files/selected.csv",
                memory_summary="",
                task="Sum the values",
                skills_catalog="",
                session_history="",
                context=dataset,
                mode=route,
            )
            assert prediction.answer == "28" and prediction.route == route
            assert not manager._contexts and not manager._interpreters and not sandbox.code_interpreter.contexts
    assert serialized == [dataset, dataset]
    assert [item[0] for item in assignments] == [dataset, dataset]
    assert not any("__FILE_BODY_ONLY__" in payload for payload in transported)
    assert len(dataset.rlm_preview(40)) <= 40
    assert manager.turn_usage()["submit_rejections"] == manager.turn_usage()["sandbox_errors"] == 0
    assert lm.history == []


def test_dataset_view_forwarded_to_host_tool_is_plain_metadata(sandbox):
    sandbox.fs.upload_file(b"__BODY_STAYS_IN_DAYTONA__", "/workspace/files/input.txt")
    dataset = WorkspaceDataset("files/input.txt", description="Selected file")
    received = []

    def inspect_metadata(value: dict[str, Any]) -> str:
        received.append(value)
        assert type(value) is dict
        return value["path"]

    signature = dspy.Signature({"context": (Any, dspy.InputField()), "answer": (str, dspy.OutputField())})
    flex = dspy.Flex(signature, tools=[inspect_metadata], interpreter_factory=manager_on(sandbox).interpreter_factory())
    flex.load_state(
        {
            "module_src": """class Program(dspy.Module):
    def forward(self, context):
        return dspy.Prediction(answer=inspect_metadata(value=context))
"""
        }
    )
    assert flex(context=dataset).answer == "files/input.txt"
    assert received == [{"path": "files/input.txt", "description": "Selected file", "size_bytes": 0, "sha256": ""}]
    assert not sandbox.code_interpreter.contexts


@pytest.mark.parametrize("route", ["rlm", "flex"])
@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_dataset_initialization_failure_closes_native_interpreters(sandbox, route, failure, monkeypatch):
    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.engine.signatures import FleetTask

    if failure == "changed":
        sandbox.fs.upload_file(b"changed", "/workspace/files/selected.txt")
    dataset = WorkspaceDataset("files/selected.txt", sha256="0" * 64)
    manager = manager_on(sandbox)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "list_workspace_files",
        lambda _self: ["files/selected.txt"] if failure == "changed" else [],
    )
    lm = dspy.utils.DummyLM([])
    engine = FleetRuntime(manager, lm, lm)

    def echo(value: str) -> str:
        return value

    factory = lambda: DaytonaInterpreter(manager)  # noqa: E731
    if route == "flex":
        engine.program.reasoning.flex = dspy.Flex(FleetTask, tools=[echo], interpreter_factory=factory)
    else:
        engine.program.reasoning.rlm = dspy.RLM(FleetTask, interpreter_factory=factory)
    with pytest.raises(dspy.CodeInterpreterError, match=r"source changed|FileNotFoundError"):
        engine.program.reasoning(
            route=Route(route),
            workspace_manifest="",
            memory_summary="",
            task="Read the selected file",
            skills_catalog="",
            session_history="",
            context=dataset,
            mode=route,
        )
    assert not manager._contexts and not manager._interpreters and not sandbox.code_interpreter.contexts
    assert lm.history == []


@pytest.mark.parametrize("extract_fallback", [False, True])
def test_native_rlm_history_persists_all_new_events_after_pruning(sandbox, extract_fallback):
    from dspy.primitives.repl_types import REPLEntry, build_repl_event

    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.engine.reasoning import _MAX_RLM_HISTORY_EVENTS

    previous = dspy.History(
        messages=[
            build_repl_event({"task": "old task"}, REPLEntry(code=f"print({index})", output=str(index)))
            for index in range(_MAX_RLM_HISTORY_EVENTS)
        ]
    )
    before = deepcopy(previous.messages)
    responses = [{"plan": "Compute", "code": "print(17 + 23)"}]
    if extract_fallback:
        responses.append({"plan": "Verify", "code": "print(40)"})
    responses.append(
        {"answer": "40", "learned_skill_note": ""}
        if extract_fallback
        else {"plan": "Submit", "code": "SUBMIT(answer='40', learned_skill_note='')"}
    )
    lm = dspy.utils.DummyLM(responses)
    engine = FleetRuntime(manager_on(sandbox), lm, lm)
    engine.program.reasoning.rlm.max_iters = 2
    with dspy.context(disable_history=True):
        result = engine.program.reasoning(
            route=Route.RLM,
            workspace_manifest="",
            memory_summary="",
            task="Add the numbers",
            skills_catalog="",
            session_history="",
            context="",
            mode="rlm",
            rlm_history=previous,
        )
    assert result.answer == "40"
    assert len(result.rlm_history) == (3 if extract_fallback else 2)
    assert result.rlm_history[0]["repl_entry"]["code"] == "print(17 + 23)"
    assert result.rlm_history[-1]["outputs"]["answer"] == "40"
    assert previous.messages == before


def manager_on(sandbox, events=None, name="interp-test", claim=None) -> DaytonaSandboxManager:
    manager = DaytonaSandboxManager(
        "key",
        "org",
        "volume",
        name,
        event_callback=events.append if events is not None else None,
        environment_report_claim=claim,
    )
    manager._sandbox = sandbox
    manager._client = SimpleNamespace(stop=lambda stopped, **_kwargs: STOPPED.append(stopped.id))
    return manager


def test_batched_queries_use_scoped_lm_when_manager_has_no_sub_lm(sandbox):
    manager = manager_on(sandbox)
    lm = lambda prompt: [f"reply:{prompt}"]  # noqa: E731
    with dspy.context(lm=lm):
        interpreter = DaytonaInterpreter(manager)
        assert interpreter._llm_query_batched([["one", "two"]], {}) == ["reply:one", "reply:two"]


def test_fallback_batch_preserves_full_context_and_isolates_simultaneous_turns(sandbox):
    from dspy.utils.usage_tracker import UsageTracker

    from fleet_rlm.observability import LMUsageCallback
    from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER

    sentinel = ContextVar("batch_turn_sentinel")
    rendezvous = threading.Barrier(4)

    def turn(name):
        manager = manager_on(sandbox, name=name)
        tracker, callback = UsageTracker(), LMUsageCallback()

        def complete(prompt):
            rendezvous.wait(timeout=10)
            assert sentinel.get() == name
            assert _ACTIVE_MANAGER.get() is manager
            assert dspy.settings.usage_tracker is tracker
            assert dspy.settings.callbacks == [callback]
            assert dspy.settings.lm is complete and dspy.settings.disable_history
            sentinel.set("worker-local mutation")
            tracker.add_usage(name, {"prompt_tokens": 2, "completion_tokens": 1})
            return [f"{name}:{prompt}"]

        token = sentinel.set(name)
        active = _ACTIVE_MANAGER.set(manager)
        try:
            with dspy.context(lm=complete, callbacks=[callback], usage_tracker=tracker):
                results = DaytonaInterpreter(manager)._llm_query_batched([["one", "two"]], {})
            assert sentinel.get() == name
            assert results == [f"{name}:one", f"{name}:two"]
            assert manager.turn_usage()["lm_calls"] == 2
            assert tracker.get_total_tokens()[name] == {"prompt_tokens": 4, "completion_tokens": 2}
        finally:
            _ACTIVE_MANAGER.reset(active)
            sentinel.reset(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(turn, name) for name in ("turn-one", "turn-two")]
        for future in futures:
            future.result(timeout=15)


@pytest.mark.parametrize("error", [ValueError("provider"), TimeoutError("deadline"), RuntimeError("cancelled")])
def test_fallback_batch_precharges_once_and_propagates_worker_errors(sandbox, error):
    manager = manager_on(sandbox)

    def complete(_prompt):
        raise error

    with dspy.context(lm=complete), pytest.raises(type(error)) as caught:
        DaytonaInterpreter(manager)._llm_query_batched([["one", "two"]], {})
    assert caught.value is error
    assert manager.turn_usage()["lm_calls"] == 2


def test_fallback_batch_cancels_queued_work_on_failure(sandbox, monkeypatch):
    from concurrent.futures import Future

    futures = []

    class Pool:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def submit(self, *_args):
            future = Future()
            futures.append(future)
            if len(futures) == 2:
                future.set_exception(ValueError("provider"))
            return future

    monkeypatch.setattr(interpreter_module, "ThreadPoolExecutor", Pool)
    with dspy.context(lm=lambda prompt: [prompt]), pytest.raises(ValueError, match="provider"):
        DaytonaInterpreter(manager_on(sandbox))._llm_query_batched([["slow", "fail", "queued"]], {})
    assert futures[0].cancelled() and futures[2].cancelled()


def test_injected_batch_never_constructs_fallback_executor(sandbox, monkeypatch):
    def forbidden(**_kwargs):
        pytest.fail("native batching must bypass the fallback executor")

    monkeypatch.setattr(interpreter_module, "ThreadPoolExecutor", forbidden)
    calls = []
    manager = manager_on(sandbox)

    def native(prompts):
        calls.append(prompts)
        manager.consume_budget(len(prompts))
        return [p.upper() for p in prompts]

    interpreter = DaytonaInterpreter(manager)
    # Native RLM injects these reserved tools after interpreter construction.
    interpreter.tools["llm_query_batched"] = native
    assert interpreter._llm_query_batched([["one", "two"]], {}) == ["ONE", "TWO"]
    assert calls == [["one", "two"]] and manager.turn_usage()["lm_calls"] == 2


@contextmanager
def recording_span(recorded: list[dict[str, Any]], name: str, span_type: str):
    span: dict[str, Any] = {"name": name, "span_type": span_type, "attributes": {}, "open": True}
    recorded.append(span)
    try:
        yield SimpleNamespace(
            set_inputs=lambda value: span.__setitem__("inputs", value),
            set_outputs=lambda value: span.__setitem__("outputs", value),
            set_attributes=span["attributes"].update,
            set_attribute=lambda key, value: span["attributes"].__setitem__(key, value),
        )
    finally:
        span["open"] = False


def test_execute_returns_values_finals_and_code_errors(sandbox):
    interpreter = DaytonaInterpreter(manager_on(sandbox))
    assert interpreter.execute("import json\njson.dumps({'answer': 42})") == '{"answer": 42}'
    assert interpreter.execute("print(17)") == "17"
    final = interpreter.execute("SUBMIT(answer='17')")
    assert isinstance(final, FinalOutput) and final.output == {"answer": "17"}
    with pytest.raises(CodeExecutionError, match="ValueError: broken"):
        interpreter.execute("raise ValueError('broken')")
    assert interpreter.execute("print('only output')") == "only output"


def test_rlm_output_fields_make_positional_submit_work(sandbox):
    rlm = dspy.RLM("subtask -> findings, learned_skill_note")
    interpreter = DaytonaInterpreter(manager_on(sandbox))
    rlm._inject_execution_context(interpreter, {})
    assert [field["name"] for field in interpreter.output_fields] == ["findings", "learned_skill_note"]
    final = interpreter.execute("SUBMIT('found 3', 'note')")
    assert isinstance(final, FinalOutput) and final.output == {"findings": "found 3", "learned_skill_note": "note"}


def test_host_tools_run_in_the_interpreters_dspy_context(sandbox):
    lm = dspy.utils.DummyLM([])
    with dspy.context(lm=lm):
        interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"check_active_lm": lambda: dspy.settings.lm is lm})
    assert interpreter.execute("check_active_lm()") is True


@pytest.mark.parametrize("selected", [None, ("artifacts/prior.txt",), ("/workspace/artifacts/prior.txt",)])
def test_native_code_agent_manifest_matches_child_copy_policy(sandbox, monkeypatch, selected):
    from dspy.primitives.facade import SandboxLM

    manager = manager_on(sandbox)
    paths = ["files/source.txt", "skills/check/SKILL.md", "artifacts/prior.txt", "memory/private.txt"]
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: paths)
    monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])
    children = []

    def allocate(_self, **_kwargs):
        child = manager_on(sandbox, name=f"child-{len(children)}")
        children.append(child)
        return child

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate)
    expected = (
        ["files/source.txt", "skills/check/SKILL.md"]
        if selected is None
        else ["artifacts/prior.txt", "skills/check/SKILL.md"]
    )
    expected_manifest = ", ".join(f"/workspace/{p}" for p in expected)
    full_manifest = ", ".join(f"/workspace/{p}" for p in paths)
    prompts_seen: list[str] = []

    from dspy.clients.engines.dummy_engine import AsyncDummyEngine, DummyEngine
    from dspy.lm15 import Message, Response, TextPart, Usage

    class ManifestEngine(DummyEngine):
        def _complete_messages(self, messages):
            user_text = messages[-1]["content"]
            prompts_seen.append(user_text)
            if len(prompts_seen) == 1:
                assert expected_manifest in user_text
                assert "memory/private.txt" not in user_text
                if selected is None:
                    assert "artifacts/prior.txt" not in user_text
                else:
                    assert "files/source.txt" not in user_text
                payload = {"plan": "inspect child manifest", "code": "SUBMIT(answer=workspace_manifest)"}
            else:
                assert all(p in user_text for p in paths)
                payload = {"answer": "predict-ok"}
            return Response(
                id=None,
                model="dummy",
                message=Message.assistant([TextPart(self.owner._format_answer_fields(payload) or "")]),
                finish_reason="stop",
                usage=Usage(input_tokens=0, output_tokens=0, total_tokens=0),
            )

    lm = dspy.utils.DummyLM([])
    lm._engine_spec = ManifestEngine(lm)
    lm._async_engine_spec = AsyncDummyEngine(lm._engine_spec)
    source = """class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.rlm = dspy.RLM("workspace_manifest, task -> answer", max_iters=2)
        self.predict = dspy.Predict("workspace_manifest, task -> answer")

    def forward(self, **inputs):
        child = self.rlm(**inputs)
        host = self.predict(**inputs)
        return dspy.Prediction(answer=f"{child.answer}|{host.answer}")
"""
    factory = manager.interpreter_factory(input_paths=list(selected) if selected is not None else None)
    flex = dspy.Flex("workspace_manifest, task -> answer", interpreter_factory=factory)
    flex.load_state({"module_src": source})
    with dspy.context(lm=SandboxLM(lm, manager.consume_budget), disable_history=True):
        prediction = flex(workspace_manifest=full_manifest, task="read inputs")
    child_answer, host_answer = prediction.answer.split("|", 1)
    assert host_answer == "predict-ok"
    assert all(p in child_answer for p in expected)
    assert "memory/private.txt" not in child_answer
    assert ("artifacts/prior.txt" not in child_answer) if selected is None else ("files/source.txt" not in child_answer)
    assert len(prompts_seen) == 2
    assert children and all(not child._contexts for child in children)


def test_exec_span_records_the_execution_and_its_host_calls(sandbox, monkeypatch):
    from mlflow.entities import SpanType

    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(spans, name, span_type)
    )
    sandbox.get_metrics_latest = lambda: SimpleNamespace(
        cpu_used_pct=12.5, mem_used=2048, mem_total=4096, disk_used=1024
    )
    interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"lookup": lambda: 17})

    assert interpreter.execute("lookup() + lookup()") == 34

    [span] = [span for span in spans if span["name"] == "sandbox.exec"]
    assert {key: value for key, value in span["attributes"].items() if key.startswith("sandbox.")} == {
        "sandbox.cpu_used_pct": 12.5,
        "sandbox.mem_used_bytes": 2048,
        "sandbox.mem_total_bytes": 4096,
        "sandbox.disk_used_bytes": 1024,
    }
    assert (span["name"], span["span_type"], span["open"]) == ("sandbox.exec", SpanType.TOOL, False)
    assert span["attributes"]["host_calls"] == 2
    assert span["attributes"]["phase"] == "task" and span["attributes"]["namespace"] == interpreter.ns_id
    assert span["attributes"]["outcome"] == "completed"
    names = [item["name"] for item in spans]
    # A manager whose sandbox is already live is not "provisioning": a Turn calls this repeatedly,
    # and tracing each reuse buried a real trace under no-op spans.
    assert "sandbox.provision" not in names and "sandbox.namespace_prepare" in names
    prepared = next(item for item in spans if item["name"] == "sandbox.namespace_prepare")
    assert prepared["attributes"]["namespace"] == interpreter.ns_id
    assert prepared["attributes"]["agent_id"] == interpreter.manager.agent_id
    assert prepared["outputs"]["completed"] is True


def test_stalled_resource_sample_is_abandoned_not_awaited(sandbox, monkeypatch):
    release = threading.Event()
    calls: list[int] = []
    monkeypatch.setattr("fleet_rlm.sandbox.manager.RESOURCE_SAMPLE_TIMEOUT_SECONDS", 0.05)
    sandbox.get_metrics_latest = lambda: calls.append(1) or release.wait(5)
    manager = manager_on(sandbox)
    try:
        assert manager.resource_sample() is None
        assert manager.resource_sample() is None  # a stalled sampler is not stacked
        assert calls == [1]
    finally:
        release.set()


def test_exec_span_survives_an_unavailable_resource_sample(sandbox, monkeypatch):
    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(spans, name, span_type)
    )

    def unavailable() -> None:
        raise RuntimeError("metrics endpoint down")

    sandbox.get_metrics_latest = unavailable
    interpreter = DaytonaInterpreter(manager_on(sandbox))

    assert interpreter.execute("1 + 1") == 2

    [span] = [span for span in spans if span["name"] == "sandbox.exec"]
    assert span["attributes"]["outcome"] == "completed"
    assert not any(key.startswith("sandbox.") for key in span["attributes"])


def test_provisioning_is_traced_only_when_it_creates_the_sandbox(sandbox, monkeypatch):
    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(spans, name, span_type)
    )
    manager = manager_on(sandbox)
    manager._sandbox = None
    # The manager uses slots, so the provisioning call is replaced on the class, not the instance.
    monkeypatch.setattr(type(manager), "_ensure_sandbox", lambda _self: sandbox)

    assert manager.ensure_sandbox() is sandbox
    [provision] = [span for span in spans if span["name"] == "sandbox.provision"]
    assert provision["attributes"]["action"] == "create"
    assert provision["inputs"] == {"operation": "sandbox.provision"}
    assert provision["outputs"] == {"completed": True}


def test_sandbox_skill_receipts_reach_the_progressive_disclosure_scorer(sandbox, monkeypatch):
    from fleet_rlm.observability import sanitize_trace_span
    from fleet_rlm.optimize.scorers import _skill_feedback_scorer

    recorded = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(recorded, name, span_type)
    )
    interpreter = DaytonaInterpreter(manager_on(sandbox))
    interpreter.execute("save_skill('example', 'Use for examples', 'Example instructions')")
    # A helper returning successfully remains visible even if later code in the cell fails.
    with pytest.raises(CodeExecutionError):
        interpreter.execute("use_skill('example')\nraise ValueError('later failure')")
    spans = [
        SimpleNamespace(**{**item, "span_id": str(i), "parent_id": "root"})
        for i, item in enumerate(recorded)
        if item["name"] == "sandbox.exec"
    ]
    for span in spans:
        span.set_outputs = lambda value, target=span: setattr(target, "outputs", value)
        sanitize_trace_span(span)
    spans.insert(
        0,
        SimpleNamespace(name="fleet_turn", parent_id=None, inputs={"task": "Use a Skill"}, outputs={"answer": "Done"}),
    )
    trace = SimpleNamespace(search_spans=lambda: spans)
    contexts = []

    def judge(**kwargs):
        contexts.append(kwargs)
        return SimpleNamespace(value=True, rationale="Actual helper executions were reviewed.")

    assert _skill_feedback_scorer(judge)(trace=trace).value is True
    activity = contexts[0]["inputs"]["skill_activity"]
    assert [item["helper_receipts"][0]["helper"] for item in activity] == ["save_skill", "use_skill"]
    assert all(item["helper_receipts"][0]["completed"] for item in activity)


def test_bridge_and_execution_events_show_evidence_and_hide_secrets(sandbox):
    from dspy.primitives.facade import SandboxSession

    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events, "activity-test")
    interpreter = DaytonaInterpreter(manager, tools={"lookup": lambda: {"value": 17, "password": "hidden"}})
    interpreter.execute("lookup()")
    with pytest.raises(CodeExecutionError):
        interpreter.execute("missing_tool()")
    SandboxSession({}, interpreter_factory=lambda: interpreter).install(interpreter, import_dspy=False)

    assert "hidden" not in json.dumps(events)
    [tool_end] = [e for e in events if e["event"] == "tool_end"]
    assert tool_end["ok"] is True and '"value": 17' in tool_end["output"]
    outputs = [e for e in events if e["event"] == "repl_output"]
    assert [e["phase"] for e in outputs] == ["task", "task", "bootstrap"]
    assert outputs[1]["error"] is True and outputs[1]["error_type"] == "NameError"
    for output in outputs:
        assert any(e["event"] == "repl_code" and e["execution_id"] == output["execution_id"] for e in events)
        assert output["agent_id"] == manager.agent_id


def test_a_failing_host_tool_raises_in_the_sandbox_and_is_reported(sandbox):
    events: list[dict[str, Any]] = []

    def broken():
        raise LookupError("no row")

    interpreter = DaytonaInterpreter(manager_on(sandbox, events), tools={"broken": broken})
    with pytest.raises(CodeExecutionError, match="no row"):
        interpreter.execute("broken()")
    [tool_end] = [e for e in events if e["event"] == "tool_end"]
    assert tool_end["ok"] is False and tool_end["error_type"] == "LookupError"


def test_host_call_cancellation_skips_the_tool_and_returns_prompt_feedback(sandbox):
    events: list[dict[str, Any]] = []
    invoked: list[str] = []

    def sentinel() -> None:
        invoked.append("called")

    manager = manager_on(sandbox, events)
    manager._cancelled.set()
    interpreter = DaytonaInterpreter(manager, tools={"sentinel": sentinel})
    started = time.monotonic()
    reply = interpreter.answer_host_call({"id": "call-1", "tool": "sentinel", "args": [], "kwargs": {}})
    elapsed = time.monotonic() - started

    assert invoked == []
    assert "cancel" in reply["error"].lower()
    assert elapsed < 1
    assert [event["event"] for event in events] == ["tool_start", "tool_end"]
    assert events[-1]["ok"] is False and events[-1]["error_type"] == "RuntimeError"


def test_an_in_flight_success_after_cancellation_is_fenced_without_upload(sandbox):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    interpreter = DaytonaInterpreter(manager)

    def answer(_call: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        manager.cancel_turn()
        return {"value": "late answer"}

    interpreter.answer_host_call = answer
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.run_namespace(
            interpreter,
            {"code": "late_tool()", "variables": {}, "tools": ["late_tool"], "host_call_timeout": 0.3},
        )

    assert sandbox.fs.uploads == []
    [fenced] = [event for event in events if event["event"] == "fenced"]
    assert fenced["namespace"] == interpreter.ns_id


def test_a_lost_transport_stops_the_turn_without_replay(sandbox, monkeypatch):
    events = []
    manager = manager_on(sandbox, events)
    interpreter = DaytonaInterpreter(manager)
    interpreter.execute("x = 1")
    runs = sandbox.code_interpreter.run_calls

    def dropped(*_args, **_kwargs):
        raise ConnectionError("websocket closed")

    monkeypatch.setattr(sandbox.code_interpreter, "run_code", dropped)
    with pytest.raises(ExecutionOutcomeUnknownError):
        interpreter.execute("x = 2")
    assert manager._cancelled.is_set() and STOPPED[-1] == sandbox.id
    assert sandbox.code_interpreter.run_calls == runs
    assert manager._contexts == {}
    assert events[-1]["outcome"] == "transport_uncertain"


def test_an_expired_deadline_prevents_execution(sandbox):
    manager = manager_on(sandbox)
    manager._deadline = time.monotonic() - 1
    runs = sandbox.code_interpreter.run_calls
    with pytest.raises(TimeoutError, match="deadline"):
        DaytonaInterpreter(manager).execute("42")
    assert sandbox.code_interpreter.run_calls == runs
    assert manager._cancelled.is_set()


def test_a_sub_second_remainder_is_classified_as_a_deadline_not_transport_uncertainty(sandbox):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    interpreter = DaytonaInterpreter(manager)
    manager._contexts = {interpreter.ns_id: object()}  # a prepared namespace: no preamble run during the Turn
    manager._deadline = time.monotonic() + 0.4
    runs = sandbox.code_interpreter.run_calls

    with pytest.raises(TimeoutError, match="deadline"):
        interpreter.execute("42")

    assert sandbox.code_interpreter.run_calls == runs, "no execution may start"
    assert manager._cancelled.is_set()
    assert events[-1]["outcome"] == "deadline", "an unrepresentable bound is not a transport failure"


def test_shutdown_drops_the_namespace_context(sandbox):
    manager = manager_on(sandbox)
    interpreter = DaytonaInterpreter(manager)
    interpreter.execute("x = 1")
    assert len(sandbox.code_interpreter.list_contexts()) == 1
    interpreter.shutdown()
    assert sandbox.code_interpreter.list_contexts() == [] and manager._contexts == {}


def test_concurrent_interpreters_share_one_sandbox_with_separate_namespaces(sandbox):
    manager = manager_on(sandbox)
    left, right = DaytonaInterpreter(manager), DaytonaInterpreter(manager)
    threads = [
        threading.Thread(target=left.execute, args=("marker = 'left'",)),
        threading.Thread(target=right.execute, args=("marker = 'right'",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert (left.execute("marker"), right.execute("marker")) == ("left", "right")


def test_a_remote_code_error_marks_the_real_mlflow_span(tmp_path):
    # A fresh process gives a clean MLflow exporter; the run_namespace fake stands in for the sandbox.
    code = """
import mlflow
from types import SimpleNamespace
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from dspy import CodeExecutionError
mlflow.set_experiment('execution-test')
manager = SimpleNamespace(run_namespace=lambda *_a: {
    'error': True, 'error_type': 'ValueError', 'stderr': 'ValueError: verification_probe'},
    check_turn=lambda: None, emit=lambda *_a, **_kw: None, record_sandbox_error=lambda: None)
interpreter = DaytonaInterpreter(manager)
interpreter.start = lambda: None
with mlflow.start_span(name='execution-test') as root:
    try:
        interpreter.execute("raise ValueError('verification_probe')")
    except CodeExecutionError:
        pass
    else:
        raise AssertionError('remote exception was not raised')
    trace_id = root.trace_id
mlflow.flush_trace_async_logging()
trace = mlflow.get_trace(trace_id)
execution = next(s for s in trace.data.spans if s.name == 'sandbox.exec')
assert execution.status.status_code.name == 'ERROR'
assert execution.outputs['error'] is True
assert execution.attributes['remote_error_type'] == 'ValueError'
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            **os.environ,
            "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'traces.db'}",
            "MLFLOW_DISABLE_AGENT_HINT": "1",
        },
    )
    assert result.returncode == 0, result.stderr


FABRICATED = (
    'answer = ("No. central (12/60 = 0.20 refund rate) trails south ($16,995.71); '
    'counterfactual $12,753.67.")\nSUBMIT(answer=answer)'
)


def test_a_submit_with_typed_unseen_figures_is_rejected_then_accepted_once_computed(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))
    variables = {"task": "Would central overtake north ($33,891.86)?"}
    with pytest.raises(CodeExecutionError, match=r"SUBMIT rejected: figures .*16995\.71"):
        interpreter.execute(FABRICATED, variables)
    rejected = [event for event in events if event["event"] == "ungrounded_submit"]
    assert rejected and rejected[0]["rejected"] is True and "12753.67" in rejected[0]["figures"]

    assert interpreter.execute("total = 30286.42 + 5816.87\nprint(round(total, 2))", variables) == "36103.29"
    final = interpreter.execute('SUBMIT(answer="Yes: $36,103.29 beats north $33,891.86")', variables)
    assert isinstance(final, FinalOutput)


def test_computed_answers_and_rounded_copies_of_printed_values_pass(sandbox):
    interpreter = DaytonaInterpreter(manager_on(sandbox))
    interpreter.execute("refunded, orders = 12, 86\nrate = refunded / orders\nprint(refunded, orders, rate)")
    computed = interpreter.execute("SUBMIT(answer=f'central refund rate {rate:.4f}')")
    assert isinstance(computed, FinalOutput) and computed.output["answer"] == "central refund rate 0.1395"
    copied = interpreter.execute("SUBMIT(answer='central refund rate 0.1395 (12 of 86)')")
    assert isinstance(copied, FinalOutput)


def test_ungrounded_submit_is_accepted_and_flagged_after_the_rejection_cap(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))
    for _ in range(2):
        with pytest.raises(CodeExecutionError, match="SUBMIT rejected"):
            interpreter.execute(FABRICATED)
    final = interpreter.execute(FABRICATED)
    assert isinstance(final, FinalOutput)
    flags = [event["rejected"] for event in events if event["event"] == "ungrounded_submit"]
    assert flags == [True, True, False]


@pytest.mark.parametrize("positional", [False, True], ids=["keyword", "positional"])
def test_should_accept_note_constants_without_weakening_substantive_grounding(sandbox, positional):
    events = []
    manager = manager_on(sandbox, events)
    interpreter = DaytonaInterpreter(manager)
    interpreter.output_fields = [{"name": "findings"}, {"name": "learned_skill_note"}]
    interpreter.execute("result = 17\nprint(result)")
    submit = "SUBMIT(result, note)" if positional else "SUBMIT(findings=result, learned_skill_note=note)"
    final = interpreter.execute('note = "Use precision 200 and constant 10005"\n' + submit)
    assert final.output["findings"] == 17
    [warning] = [event for event in events if event["event"] == "learned_note_warning"]
    assert warning["field"] == "learned_skill_note" and warning["figures"] == ["10005", "200"]
    assert manager.turn_usage()["submit_rejections"] == 0
    with pytest.raises(CodeExecutionError, match="SUBMIT rejected"):
        interpreter.execute("SUBMIT(findings='unverified 426880', learned_skill_note='')")
    assert manager.turn_usage()["submit_rejections"] == 1
    assert manager.turn_usage()["sandbox_errors"] == 0
    [output] = [event for event in events if event["event"] == "repl_output" and event.get("error")]
    assert output["outcome"] == "ungrounded_submit_rejected"
    assert interpreter._ungrounded_rejections == 1
    with pytest.raises(CodeExecutionError, match="ValueError"):
        interpreter.execute("raise ValueError('remote failure')")
    assert events[-1]["outcome"] == "remote_code_error"
    assert manager.turn_usage()["sandbox_errors"] == manager.turn_usage()["submit_rejections"] == 1


def test_should_submit_captured_results_and_note_without_a_recovery_iteration(sandbox):
    """Native Evaluate/RLM contract over recorded output; no claim of improved live model behavior."""
    from dspy.primitives.facade import SandboxLM

    from fleet_rlm.contracts import STOPPING_RULES

    fixture = json.loads((Path(__file__).parents[1] / "fixtures/pi_digit_trace.json").read_text())
    events = []
    manager = manager_on(sandbox, events)
    # The scripted observation is captured evidence, not a fresh computation of pi.
    lm = dspy.utils.DummyLM(
        [
            {"plan": "Use the captured execution output", "code": f"print({fixture['captured_stdout']!r})"},
            {
                "plan": "Submit the observed result",
                "code": f"SUBMIT(answer='5', learned_skill_note={fixture['learned_note']!r})",
            },
        ]
    )
    signature = dspy.Signature("task -> answer, learned_skill_note").with_instructions(STOPPING_RULES)
    rlm = dspy.RLM(signature, interpreter_factory=lambda: DaytonaInterpreter(manager), max_iters=3)
    example = dspy.Example(task=fixture["task"], answer=fixture["answer"]).with_inputs("task")
    with dspy.context(lm=SandboxLM(lm, manager.consume_budget), disable_history=True):
        result = dspy.Evaluate(devset=[example], metric=dspy.evaluate.answer_exact_match, num_threads=1)(rlm)
    assert result.score == 100
    prediction = result.results[0][1]
    assert len(prediction.trajectory) == 2
    assert manager.turn_usage()["lm_calls"] == 2
    assert manager.turn_usage()["submit_rejections"] == manager.turn_usage()["sandbox_errors"] == 0
    assert any(event["event"] == "learned_note_warning" for event in events)
    assert lm.history == []
    assert fixture["neighborhood"][10] == fixture["answer"]


@pytest.mark.parametrize("note,figure", [("Use guard=100", "100"), ("Works at 1.49M digits", "1.49")])
def test_observed_pi_note_warnings_remain_advisory(sandbox, note, figure):
    events = []
    manager = manager_on(sandbox, events)
    interpreter = DaytonaInterpreter(manager)
    interpreter.output_fields = [{"name": "answer"}, {"name": "learned_skill_note"}]
    interpreter.execute("result = 17\nprint(result)\nprint(1492252)")
    final = interpreter.execute(f"SUBMIT(answer=result, learned_skill_note={note!r})")
    assert final.output["answer"] == 17
    [warning] = [event for event in events if event["event"] == "learned_note_warning"]
    assert warning["figures"] == [figure]
    assert manager.turn_usage()["submit_rejections"] == 0
    with pytest.raises(CodeExecutionError, match="SUBMIT rejected"):
        interpreter.execute("SUBMIT(answer='unobserved 426880', learned_skill_note='')")


_OBSERVED_ENVIRONMENT = (
    "Sandbox environment: Python 3.13.13; installed: numpy 2.5.1, pandas 3.0.5, mpmath 1.4.1; "
    "not installed: scipy, sympy, gmpy2, polars, pyarrow, sklearn; call "
    "sys.set_int_max_str_digits(0) before converting integers over 4300 digits to str."
)
_OBSERVED_MISSING = ["scipy", "sympy", "gmpy2", "polars", "pyarrow", "sklearn"]


def _environment_probe(missing: list[str], environment: str = _OBSERVED_ENVIRONMENT) -> str:
    return json.dumps({"digests": {}, "environment": environment, "missing": missing}, sort_keys=True)


def _install_environment_probe(monkeypatch, missing: list[str] = _OBSERVED_MISSING) -> None:
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: _environment_probe(missing))


@pytest.mark.parametrize(
    ("missing", "turn_count", "event_count"),
    [(_OBSERVED_MISSING, 3, 1), ([], 1, 0), (["scipy"], 1, 1)],
    ids=["reported-once-per-session", "complete-environment", "single-missing-module"],
)
def test_environment_incomplete_event_reports_missing_modules_once_without_failing(
    sandbox, monkeypatch, missing, turn_count, event_count
):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    _install_environment_probe(monkeypatch, missing)

    turns = [manager.turn_inputs() for _ in range(turn_count)]

    observed = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert len(observed) == event_count
    if missing:
        assert observed[0]["missing"] == missing
    assert all(turn["environment"] == _OBSERVED_ENVIRONMENT for turn in turns)
    assert manager.turn_usage()["sandbox_errors"] == 0


class _DurableEnvironmentClaims:
    """Durable Session-scope marker standing in for the Store's atomic claim."""

    def __init__(self) -> None:
        self.reported: set[str] = set()

    def __call__(self, session_id: str) -> bool:
        if session_id in self.reported:
            return False
        self.reported.add(session_id)
        return True


def test_environment_incomplete_event_survives_runtime_release_and_reopen(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    claims = _DurableEnvironmentClaims()
    _install_environment_probe(monkeypatch)

    first = manager_on(sandbox, events, claim=claims)
    first.turn_inputs()
    # Releasing the runtime keeps the durable Session; reopening builds a manager with no
    # in-memory flag, so only the durable marker can suppress the duplicate.
    reopened = manager_on(sandbox, events, claim=claims)
    reopened.turn_inputs()

    observed = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert len(observed) == 1
    assert observed[0]["missing"] == _OBSERVED_MISSING
    assert claims.reported == {"interp-test"}
    assert first.turn_usage()["sandbox_errors"] == reopened.turn_usage()["sandbox_errors"] == 0


def test_environment_incomplete_event_survives_a_lease_loss_rebuild(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    claims = _DurableEnvironmentClaims()
    _install_environment_probe(monkeypatch, ["scipy"])

    manager_on(sandbox, events, claim=claims).turn_inputs()
    rebuilt = manager_on(sandbox, events, claim=claims)
    rebuilt.turn_inputs()

    [observed] = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert observed["missing"] == ["scipy"]
    assert rebuilt.turn_usage()["sandbox_errors"] == 0


def test_environment_incomplete_event_is_not_duplicated_after_release_but_a_new_session_emits(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    claims = _DurableEnvironmentClaims()
    _install_environment_probe(monkeypatch)

    original = manager_on(sandbox, events, claim=claims)
    original.turn_inputs()
    released = manager_on(sandbox, events, claim=claims)
    released.turn_inputs()
    other_events: list[dict[str, Any]] = []
    other = manager_on(sandbox, other_events, name="interp-other", claim=claims)
    other.turn_inputs()

    assert len([event for event in events if event["event"] == "sandbox_environment_incomplete"]) == 1
    [other_event] = [event for event in other_events if event["event"] == "sandbox_environment_incomplete"]
    assert other_event["missing"] == _OBSERVED_MISSING
    assert claims.reported == {"interp-test", "interp-other"}


def test_environment_incomplete_event_emits_nothing_when_the_durable_marker_is_already_set(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    claims = _DurableEnvironmentClaims()
    claims.reported.add("interp-test")
    _install_environment_probe(monkeypatch)

    manager_on(sandbox, events, claim=claims).turn_inputs()

    assert [event for event in events if event["event"] == "sandbox_environment_incomplete"] == []


def test_progress_scope_reports_bounded_checkpoints_through_the_existing_protocol(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))

    assert (
        interpreter.execute(
            "with progress_scope('scan workspace', expect_seconds=5) as progress:\n"
            "    progress.checkpoint(1)\n"
            "    progress.checkpoint(1)\n"
            "    progress.checkpoint(9)\n"
            "    total = 9\n"
            "print(total)"
        )
        == "9"
    )

    progress = [event for event in events if event["event"] == "progress"]
    assert [(event["scope"], event["phase"]) for event in progress] == [
        ("scan workspace", "start"),
        ("scan workspace", "checkpoint"),
        ("scan workspace", "checkpoint"),
        ("scan workspace", "exit"),
    ]
    assert [event["checkpoint"] for event in progress if event["phase"] == "checkpoint"] == [1, 9]
    assert all(event["execution_id"] for event in progress)
    allowed = {
        "event",
        "phase",
        "scope",
        "expect_seconds",
        "checkpoint",
        "namespace",
        "execution_id",
        "agent_id",
        "parent_agent_id",
        "sandbox_id",
        "status",
    }
    assert all(set(event) <= allowed for event in progress), "no source, stdout, or arbitrary object leaks"
    assert not any(token in json.dumps(progress) for token in ("total = 9", "%", "percent", "complete"))


@pytest.mark.parametrize(
    ("code", "scope_started"),
    [
        ("progress_scope('', 5)", False),
        ("progress_scope('x' * 65, 5)", False),
        ("progress_scope('ok', 0)", False),
        ("progress_scope('ok', -1)", False),
        ("progress_scope('ok', float('nan'))", False),
        ("progress_scope('ok', float('inf'))", False),
        ("progress_scope('ok', '5')", False),
        ("progress_scope('ok', True)", False),
        ("with progress_scope('ok', 5) as scope:\n    scope.checkpoint(-1)", True),
        ("with progress_scope('ok', 5) as scope:\n    scope.checkpoint(1.5)", True),
        ("with progress_scope('ok', 5) as scope:\n    scope.checkpoint('3')", True),
        ("with progress_scope('ok', 5) as scope:\n    scope.checkpoint(True)", True),
    ],
)
def test_progress_scope_rejects_invalid_input_without_emitting_a_scope(sandbox, code, scope_started):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))

    with pytest.raises(CodeExecutionError):
        interpreter.execute(code)

    progress = [event for event in events if event["event"] == "progress"]
    if not scope_started:
        assert progress == [], "an invalid argument never creates a usable scope"
    assert [event for event in progress if event["phase"] == "checkpoint"] == []
    assert [event for event in progress if event["phase"] == "stalled"] == []


def test_progress_scope_expectation_is_immutable_and_use_is_main_thread_only(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))

    result = interpreter.execute(
        "scope = progress_scope('scan', 2)\n"
        "try:\n"
        "    scope.expect_seconds = 1\n"
        "except AttributeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('expectation was mutable')\n"
        "try:\n"
        "    scope.label = 'renamed'\n"
        "except AttributeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('label was mutable')\n"
        "import threading\n"
        "errors = []\n"
        "def worker():\n"
        "    try:\n"
        "        progress_scope('threaded', 1)\n"
        "    except RuntimeError as exc:\n"
        "        errors.append(str(exc))\n"
        "thread = threading.Thread(target=worker)\n"
        "thread.start()\n"
        "thread.join()\n"
        "assert errors and 'main thread' in errors[0]\n"
        "print(scope.expect_seconds)"
    )

    assert result == "2.0"
    assert [event for event in events if event["event"] == "progress"] == []


def test_nested_progress_scopes_are_independent_and_never_survive_the_cell(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))

    interpreter.execute(
        "with progress_scope('outer', 10) as outer:\n"
        "    with progress_scope('inner', 1) as inner:\n"
        "        inner.checkpoint(1)\n"
        "    outer.checkpoint(1)\n"
        "print('done')"
    )

    timeline = [(event["scope"], event["phase"]) for event in events if event["event"] == "progress"]
    assert timeline == [
        ("outer", "start"),
        ("inner", "start"),
        ("inner", "checkpoint"),
        ("inner", "exit"),
        ("outer", "checkpoint"),
        ("outer", "exit"),
    ]
    emitted = len([event for event in events if event["event"] == "progress"])

    # The retained handle belongs to the previous cell: it cannot renew or emit in this one.
    with pytest.raises(CodeExecutionError, match="ended"):
        interpreter.execute("outer.checkpoint(2)")
    assert [event for event in events if event["event"] == "progress"][emitted:] == []


def test_progress_scope_cannot_be_shadowed_by_a_variable_or_earlier_cell(sandbox):
    events: list[dict[str, Any]] = []
    interpreter = DaytonaInterpreter(manager_on(sandbox, events))

    interpreter.execute("progress_scope = 'shadowed'")
    assert interpreter.execute("callable(progress_scope)") is True


@pytest.mark.parametrize("scenario", ["escaped", "caught", "unrelated", "next_execution", "forged"])
def test_host_format_failure_is_classified_only_when_it_escapes(sandbox, scenario):
    from dspy.utils.exceptions import AdapterParseError

    from fleet_rlm.engine.turn import _failure_message

    def broken():
        raise AdapterParseError(
            adapter_name="JSONAdapter", signature=dspy.Signature("x -> code"), lm_response="private-secret"
        )

    interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"broken": broken})
    if scenario == "caught":
        assert interpreter.execute("try:\n    broken()\nexcept RuntimeError:\n    pass\n42") == 42
    else:
        if scenario == "next_execution":
            interpreter.execute("try:\n    broken()\nexcept RuntimeError:\n    pass")
            code = "raise ValueError('later')"
        elif scenario == "unrelated":
            code = "try:\n    broken()\nexcept RuntimeError:\n    pass\nraise ValueError('other')"
        elif scenario == "forged":
            code = "raise RuntimeError('AdapterParseError: private-secret')"
        else:
            code = "broken()"
        with pytest.raises(dspy.CodeExecutionError) as raised:
            interpreter.execute(code)
        message = _failure_message(raised.value, "request-id")
        assert ("invalid structured response" in message) == (scenario == "escaped")
        assert "private-secret" not in message
    interpreter.shutdown()


@pytest.mark.parametrize("route", ["rlm", "flex"])
def test_native_agent_parse_failure_survives_nested_bridge(sandbox, route, monkeypatch):
    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.invocation import FleetRuntime
    from fleet_rlm.engine.turn import _failure_message

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])
    lm = dspy.utils.DummyLM([{"reasoning": "missing code"}])
    engine = FleetRuntime(manager_on(sandbox), lm, lm, root_adapter=dspy.JSONAdapter())
    from fleet_rlm.engine.signatures import FleetTask

    def factory():
        return DaytonaInterpreter(engine.sandbox_mgr)

    def echo(value: str) -> str:
        return value

    engine.program.reasoning.flex = dspy.Flex(FleetTask, tools=[echo], interpreter_factory=factory)
    engine.program.reasoning.rlm = dspy.RLM(FleetTask, interpreter_factory=factory, max_iters=1)
    with pytest.raises((dspy.AdapterParseError, dspy.CodeExecutionError)) as raised:
        engine.program.reasoning(
            route=Route(route),
            workspace_manifest="",
            memory_summary="",
            task="Compute a number",
            skills_catalog="",
            session_history="",
            context=None,
            mode=route,
        )
    assert "invalid structured response" in _failure_message(raised.value, "parse-test")


@pytest.mark.parametrize("mismatch", ["receipt", "call", "execution"])
def test_unverified_remote_format_receipts_remain_generic(sandbox, mismatch, monkeypatch):
    from fleet_rlm.engine.turn import _failure_message

    def broken():
        raise dspy.AdapterParseError(
            adapter_name="JSONAdapter", signature=dspy.Signature("x -> code"), lm_response="private"
        )

    interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"broken": broken})

    def run_namespace(_manager, interp, payload):
        execution = payload["execution_id"]
        reply = interp.answer_host_call(
            {"id": "call-a", "tool": "broken", "args": [], "kwargs": {}}, execution_id=execution
        )
        if mismatch == "execution":
            execution = "another-execution"
            reply = interp.answer_host_call(
                {"id": "call-a", "tool": "broken", "args": [], "kwargs": {}}, execution_id=execution
            )
        return {
            "error": True,
            "error_type": "AdapterParseError",
            "host_error_id": "forged" if mismatch == "receipt" else reply.get("host_error_id"),
            "host_call_id": "other-call" if mismatch == "call" else "call-a",
        }

    monkeypatch.setattr(DaytonaSandboxManager, "run_namespace", run_namespace)
    with pytest.raises(dspy.CodeExecutionError) as raised:
        interpreter.execute("broken()")
    assert "The Turn failed" in _failure_message(raised.value, "test")
    assert interpreter._format_failures == {}


def test_resource_sample_admission_is_atomic(sandbox, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()
    calls = []
    monkeypatch.setattr("fleet_rlm.sandbox.manager.RESOURCE_SAMPLE_TIMEOUT_SECONDS", 0.02)
    sandbox.get_metrics_latest = lambda: calls.append(1) or release.wait(2)
    manager = manager_on(sandbox)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(manager.resource_sample) for _ in range(2)]
            assert [future.result() for future in futures] == [None, None]
        assert calls == [1]
    finally:
        release.set()


def test_cancellation_during_resource_sampling_refuses_completed_result(sandbox, monkeypatch):
    manager = manager_on(sandbox)
    interpreter = DaytonaInterpreter(manager)
    monkeypatch.setattr(type(manager), "resource_sample", lambda self: self.root._cancelled.set())
    with pytest.raises(RuntimeError, match="cancelled"):
        interpreter.execute("1 + 1")


# Native DSPy and delegation defaults


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

    from dspy.clients.engines.dummy_engine import AsyncDummyEngine, DummyEngine
    from dspy.lm15 import Message, Response, TextPart, Usage

    class PolicyEngine(DummyEngine):
        def _complete_messages(self, messages):
            system = messages[0]["content"]
            if "Fleet child role: leaf_action." in system:
                if malformed_leaf:
                    return Response(
                        id=None,
                        model="dummy",
                        message=Message.assistant([TextPart("malformed child action")]),
                        finish_reason="stop",
                        usage=Usage(input_tokens=0, output_tokens=0, total_tokens=0),
                    )
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
            output = self.owner._format_answer_fields({"plan": "offline policy-conditioned response", "code": code})
            return Response(
                id=None,
                model="dummy",
                message=Message.assistant([TextPart(output or "")]),
                finish_reason="stop",
                usage=Usage(input_tokens=0, output_tokens=0, total_tokens=0),
            )

    class PolicyLM(dspy.utils.DummyLM):
        def __init__(self):
            super().__init__([])
            self._engine_spec = PolicyEngine(self)
            self._async_engine_spec = AsyncDummyEngine(self._engine_spec)

        def copy(self, **kwargs):
            copied = super().copy(**kwargs)
            copied._engine_spec = PolicyEngine(copied)
            copied._async_engine_spec = AsyncDummyEngine(copied._engine_spec)
            return copied

    lm = PolicyLM()

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
        ("", "dspy.RLM", "", (20, 50)),
        ("", "dspy.RLM", ", max_iters=3", (3, 50)),
        ("", "dspy.RLM", ", max_iters=3, max_llm_calls=7", (3, 7)),
        ("", "dspy.RLM", ", **{'max_llm_calls': 7}", (20, 7)),
        ("import dspy as dsp", "dsp.RLM", "", (20, 50)),
        ("from dspy import RLM as Recursive", "Recursive", "", (20, 50)),
        ("", "dspy.RLM", ", max_iters=None, max_llm_calls=None", (None, None)),
    ],
)
def test_native_flex_constructors_preserve_source(sandbox, tmp_path, imports, constructor, arguments, expected):
    manager = manager_on(sandbox)
    source = f'''{imports}
import json

class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.first = {constructor}("task -> answer"{arguments})
        self.second = dspy.RLM("task -> answer")
        self.predict = dspy.Predict("task -> answer")
    def forward(self, **inputs):
        payload = {{
            "first": [self.first.max_iters, self.first.max_llm_calls],
            "second": [self.second.max_iters, self.second.max_llm_calls],
            "predict": type(self.predict).__name__,
        }}
        return dspy.Prediction(answer=json.dumps(payload))
'''
    flex = dspy.Flex(
        "task -> answer",
        interpreter_factory=manager.interpreter_factory(),
    )
    flex.load_state({"module_src": source})
    assert flex.module_src == source
    observed = json.loads(flex(task="constructor fixture").answer)
    assert tuple(observed["first"]) == expected
    assert tuple(observed["second"]) == (20, 50)
    assert observed["predict"] == "Predict"
    path = tmp_path / "native.json"
    flex.save(str(path))
    assert json.loads(path.read_text())["module_src"] == source
    flex.load(str(path))
    assert flex.module_src == source
    reloaded = json.loads(flex(task="reload fixture").answer)
    assert tuple(reloaded["first"]) == expected
    assert flex.module_src == source
    assert not manager._contexts and not sandbox.code_interpreter.contexts


def test_default_maps_are_copied_and_do_not_propagate(monkeypatch):
    root = DaytonaSandboxManager("key", "org", "vol", "scope")
    paths = ["files/a.txt"]
    factory = root.interpreter_factory(input_paths=paths)
    paths.append("files/b.txt")
    first, second = factory(), factory()
    assert first.native_input_paths == ("files/a.txt",)
    assert second.native_input_paths == ("files/a.txt",)
    assert first.owns_manager is False
    assert root.interpreter_factory()().native_input_paths is None
    child = DaytonaSandboxManager("key", "org", "vol", "child")
    received_inputs = []

    def allocate(_self, **kwargs):
        received_inputs.append(kwargs.get("input_paths"))
        return child

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate)
    token = _ACTIVE_MANAGER.set(root)
    try:
        nested = factory()
    finally:
        _ACTIVE_MANAGER.reset(token)
    assert nested.manager is child and nested.owns_manager is True
    assert nested.native_input_paths == ("files/a.txt",)
    assert received_inputs == [["files/a.txt"]]


@pytest.mark.parametrize(
    "tool_name,kwargs,use_lm,raises",
    [
        ("__dspy_interpreter__", {"op": "unknown"}, True, dspy.CodeInterpreterError),
        ("__dspy_interpreter__", {"op": "execute", "id": "missing"}, True, dspy.CodeInterpreterError),
        ("__dspy_lm__", {"op": "unknown"}, True, dspy.CodeInterpreterError),
        ("__dspy_lm__", {"op": "call", "prompt": "no-lm"}, False, None),
    ],
)
def test_native_dispatch_retains_payload_validation(tool_name, kwargs, use_lm, raises):
    from dspy.primitives.facade import INTERPRETER_TOOL, LM_ERROR_KEY, LM_TOOL, SandboxSession

    manager = SimpleNamespace(check_turn=lambda: None)
    lm = dspy.utils.DummyLM([]) if use_lm else None
    session = SandboxSession({}, lm=lm, interpreter_factory=lambda: DaytonaInterpreter(manager))
    interpreter = DaytonaInterpreter(manager)
    interpreter.tools[INTERPRETER_TOOL] = session._serve_interpreter
    interpreter.tools[LM_TOOL] = session._serve_lm
    original = json.loads(json.dumps(kwargs))
    with dspy.context(lm=None):
        if raises is not None:
            with pytest.raises(raises):
                interpreter._invoke_tool(tool_name, [], kwargs)
        else:
            reply = interpreter._invoke_tool(tool_name, [], kwargs)
            assert LM_ERROR_KEY in reply and interpreter._session_lm_failed is True
    assert kwargs == original


def test_native_nested_rlm_keeps_host_aggregate_budget(sandbox, monkeypatch):
    from dspy.primitives.facade import SandboxLM

    manager = manager_on(sandbox)
    manager.max_llm_calls = 1
    children = []

    def allocate(_self, **_kwargs):
        child = manager_on(sandbox, name="native-nested")
        children.append(child)
        return child

    monkeypatch.setattr(DaytonaSandboxManager, "allocate_child", allocate)
    monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])
    lm = dspy.utils.DummyLM([{"plan": "", "code": "llm_query('consume another unit')"}])
    source = """class Program(dspy.Module):
    def __init__(self):
        super().__init__()
        self.rlm = dspy.RLM("task -> answer", max_iters=2, max_llm_calls=999)
    def forward(self, **inputs):
        return self.rlm(**inputs)
"""
    flex = dspy.Flex(
        "task -> answer",
        interpreter_factory=manager.interpreter_factory(),
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

    assert DEFAULT_DEFINITION.digest() == "3d0cd582c972090c18437dfba13358e055970af6de9407e46515e75bd49fa0e8"
    assert Path(__file__).parents[2] / "src/fleet_rlm/sandbox/snapshot-requirements.txt" == LOCK_PATH
