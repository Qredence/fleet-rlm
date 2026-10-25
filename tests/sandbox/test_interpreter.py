"""DaytonaInterpreter and the manager over the run_code transport (emulated sandbox)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy import CodeExecutionError, FinalOutput

import fleet_rlm.sandbox.interpreter as interpreter_module
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.workspace import WorkspaceDataset

STOPPED: list[str] = []


@pytest.mark.parametrize("route", ["rlm", "flex"])
def test_dataset_native_roundtrip_loads_once_and_keeps_iteration_state(sandbox, route, monkeypatch):
    """Exercise the real Fleet dispatch, native DSPy bridge, and remote runtime without providers."""
    import hashlib

    from pydantic_core import to_jsonable_python

    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
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
    lm = dspy.utils.DummyLM(
        [
            {
                "reasoning": "Read the sandbox file",
                "code": "assert 'view_id' not in globals()\nview_id = id(context)\n"
                "subtotal = sum(int(line.split(',')[1]) for line in list(context.lines())[1:])\nprint(subtotal)",
            },
            {
                "reasoning": "Reuse the computed value",
                "code": "assert id(context) == view_id\nSUBMIT(answer=str(subtotal), learned_skill_note='')",
            },
        ]
        * 2
    )
    engine = AdaptiveFleetEngine(manager, lm, lm)
    if route == "flex":
        engine.flex_agent = dspy.Flex(FleetTask, tools=[echo], interpreter_factory=factory)
    else:
        engine.primary_rlm = dspy.RLM(FleetTask, interpreter_factory=factory, max_iters=3)
    with dspy.context(disable_history=True):
        for _ in range(2):
            prediction = engine._execute(Route(route), "files/selected.csv", "", "Sum the values", "", "", dataset)
            assert prediction.answer == "28" and prediction.route == route
            assert not manager._contexts and not manager._interpreters and not sandbox.code_interpreter.contexts
    assert serialized == [dataset, dataset]
    assert assignments == [(dataset, "context"), (dataset, "context")]
    assert not any("__FILE_BODY_ONLY__" in payload for payload in transported)
    assert len(dataset.rlm_preview(40)) <= 40
    assert manager.turn_usage()["submit_rejections"] == manager.turn_usage()["sandbox_errors"] == 0
    assert lm.history == []


@pytest.mark.parametrize("route", ["rlm", "flex"])
@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_dataset_initialization_failure_closes_native_interpreters(sandbox, route, failure, monkeypatch):
    from fleet_rlm.contracts import Route
    from fleet_rlm.engine.programs import AdaptiveFleetEngine
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
    engine = AdaptiveFleetEngine(manager, lm, lm)

    def echo(value: str) -> str:
        return value

    factory = lambda: DaytonaInterpreter(manager)  # noqa: E731
    if route == "flex":
        engine.flex_agent = dspy.Flex(FleetTask, tools=[echo], interpreter_factory=factory)
    else:
        engine.primary_rlm = dspy.RLM(FleetTask, interpreter_factory=factory)
    with pytest.raises(dspy.CodeInterpreterError, match=r"source changed|FileNotFoundError"):
        engine._execute(Route(route), "", "", "Read the selected file", "", "", dataset)
    assert not manager._contexts and not manager._interpreters and not sandbox.code_interpreter.contexts
    assert lm.history == []


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
    manager = manager_on(sandbox)
    paths = ["files/source.txt", "skills/check/SKILL.md", "artifacts/prior.txt", "memory/private.txt"]
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: paths)
    received = []
    interpreter = DaytonaInterpreter(manager, native_input_paths=selected)
    interpreter.tools.update(
        {
            "__dspy_construct__": lambda **kwargs: kwargs["attr_name"],
            "__dspy_call__": lambda **kwargs: received.append(kwargs) or {},
        }
    )
    interpreter._invoke_tool("__dspy_construct__", [], {"kind": "RLM", "attr_name": "agent"})
    inputs = {"workspace_manifest": ", ".join(f"/workspace/{p}" for p in paths), "task": "read inputs"}
    original = dict(inputs)
    interpreter._invoke_tool("__dspy_call__", [], {"handle": "agent", "inputs": inputs})
    expected = (
        ["files/source.txt", "skills/check/SKILL.md"]
        if selected is None
        else ["artifacts/prior.txt", "skills/check/SKILL.md"]
    )
    assert received[-1]["inputs"]["workspace_manifest"] == ", ".join(f"/workspace/{p}" for p in expected)
    assert inputs == original
    assert received[-1]["inputs"]["task"] == "read inputs"
    # Reusing a handle for a host-only predictor must not rewrite its inputs.
    interpreter._invoke_tool("__dspy_construct__", [], {"kind": "Predict", "attr_name": "agent"})
    interpreter._invoke_tool("__dspy_call__", [], {"handle": "agent", "inputs": inputs})
    assert received[-1]["inputs"] == original


def test_exec_span_records_the_execution_and_its_host_calls(sandbox, monkeypatch):
    from mlflow.entities import SpanType

    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(spans, name, span_type)
    )
    interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"lookup": lambda: 17})

    assert interpreter.execute("lookup() + lookup()") == 34

    [span] = [span for span in spans if span["name"] == "sandbox.exec"]
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
    from dspy.primitives.facade import SHIM_SETUP

    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events, "activity-test")
    interpreter = DaytonaInterpreter(manager, tools={"lookup": lambda: {"value": 17, "password": "hidden"}})
    interpreter.execute("lookup()")
    with pytest.raises(CodeExecutionError):
        interpreter.execute("missing_tool()")
    interpreter.execute(SHIM_SETUP)

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

    def answer(_call: dict[str, Any]) -> dict[str, Any]:
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
    from fleet_rlm.contracts import STOPPING_RULES
    from fleet_rlm.metering import MeteredLM

    fixture = json.loads((Path(__file__).parents[1] / "fixtures/pi_digit_trace.json").read_text())
    events = []
    manager = manager_on(sandbox, events)
    # The scripted observation is captured evidence, not a fresh computation of pi.
    lm = dspy.utils.DummyLM(
        [
            {"reasoning": "Use the captured execution output", "code": f"print({fixture['captured_stdout']!r})"},
            {
                "reasoning": "Submit the observed result",
                "code": f"SUBMIT(answer='5', learned_skill_note={fixture['learned_note']!r})",
            },
        ]
    )
    signature = dspy.Signature("task -> answer, learned_skill_note").with_instructions(STOPPING_RULES)
    rlm = dspy.RLM(signature, interpreter_factory=lambda: DaytonaInterpreter(manager), max_iters=3)
    example = dspy.Example(task=fixture["task"], answer=fixture["answer"]).with_inputs("task")
    with dspy.context(lm=MeteredLM(lm, manager.consume_budget), disable_history=True):
        result = dspy.Evaluate(devset=[example], metric=dspy.evaluate.answer_exact_match, num_threads=1)(rlm)
    assert result.score == 100
    prediction = result.results[0][1]
    assert len(prediction.trajectory) == 2
    assert manager.turn_usage()["lm_calls"] == 2
    assert manager.turn_usage()["submit_rejections"] == manager.turn_usage()["sandbox_errors"] == 0
    assert any(event["event"] == "learned_note_warning" for event in events)
    assert lm.history == []
    assert fixture["neighborhood"][10] == fixture["answer"]


_OBSERVED_ENVIRONMENT = (
    "Sandbox environment: Python 3.13.13; installed: numpy 2.5.1, pandas 3.0.5, mpmath 1.4.1; "
    "not installed: scipy, sympy, gmpy2, polars, pyarrow, sklearn; call "
    "sys.set_int_max_str_digits(0) before converting integers over 4300 digits to str."
)
_OBSERVED_MISSING = ["scipy", "sympy", "gmpy2", "polars", "pyarrow", "sklearn"]


def _environment_probe(missing: list[str], environment: str = _OBSERVED_ENVIRONMENT) -> str:
    return json.dumps({"digests": {}, "notes": [], "environment": environment, "missing": missing}, sort_keys=True)


def test_environment_incomplete_event_is_emitted_once_per_session(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(_OBSERVED_MISSING),
    )

    turns = [manager.turn_inputs() for _ in range(3)]

    observed = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert len(observed) == 1
    assert observed[0]["missing"] == _OBSERVED_MISSING
    assert all(turn["environment"] == _OBSERVED_ENVIRONMENT for turn in turns)
    assert manager.turn_usage()["sandbox_errors"] == 0


def test_environment_incomplete_event_is_absent_when_nothing_is_missing(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe([]),
    )

    inputs = manager.turn_inputs()

    assert [event for event in events if event["event"] == "sandbox_environment_incomplete"] == []
    assert inputs["environment"] == _OBSERVED_ENVIRONMENT


def test_environment_incomplete_event_reports_a_single_absent_module_without_failing(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    manager = manager_on(sandbox, events)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(["scipy"]),
    )

    inputs = manager.turn_inputs()

    [observed] = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert observed["missing"] == ["scipy"]
    assert inputs["environment"] == _OBSERVED_ENVIRONMENT
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
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(_OBSERVED_MISSING),
    )

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
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(["scipy"]),
    )

    manager_on(sandbox, events, claim=claims).turn_inputs()
    rebuilt = manager_on(sandbox, events, claim=claims)
    rebuilt.turn_inputs()

    [observed] = [event for event in events if event["event"] == "sandbox_environment_incomplete"]
    assert observed["missing"] == ["scipy"]
    assert rebuilt.turn_usage()["sandbox_errors"] == 0


def test_environment_incomplete_event_is_not_duplicated_after_release_but_a_new_session_emits(sandbox, monkeypatch):
    events: list[dict[str, Any]] = []
    claims = _DurableEnvironmentClaims()
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(_OBSERVED_MISSING),
    )

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
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: _environment_probe(_OBSERVED_MISSING),
    )

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
