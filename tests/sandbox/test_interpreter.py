"""DaytonaInterpreter and the manager over the run_code transport (emulated sandbox)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from dspy.primitives.code_interpreter import CodeExecutionError, FinalOutput

import fleet_rlm.sandbox.interpreter as interpreter_module
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError

STOPPED: list[str] = []


def manager_on(sandbox, events=None, name="interp-test") -> DaytonaSandboxManager:
    manager = DaytonaSandboxManager(
        "key", "org", "volume", name, event_callback=events.append if events is not None else None
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


def test_exec_span_records_the_execution_and_its_host_calls(sandbox, monkeypatch):
    from mlflow.entities import SpanType

    spans: list[dict[str, Any]] = []
    monkeypatch.setattr(
        interpreter_module.mlflow, "start_span", lambda name, span_type: recording_span(spans, name, span_type)
    )
    interpreter = DaytonaInterpreter(manager_on(sandbox), tools={"lookup": lambda: 17})

    assert interpreter.execute("lookup() + lookup()") == 34

    [span] = spans
    assert (span["name"], span["span_type"], span["open"]) == ("sandbox.exec", SpanType.TOOL, False)
    assert span["attributes"]["host_calls"] == 2
    assert span["attributes"]["phase"] == "task" and span["attributes"]["namespace"] == interpreter.ns_id
    assert span["attributes"]["outcome"] == "completed"


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


def test_a_lost_transport_stops_the_turn_without_replay(sandbox, monkeypatch):
    manager = manager_on(sandbox)
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


def test_an_expired_deadline_prevents_execution(sandbox):
    manager = manager_on(sandbox)
    manager._deadline = time.monotonic() - 1
    runs = sandbox.code_interpreter.run_calls
    with pytest.raises(TimeoutError, match="deadline"):
        DaytonaInterpreter(manager).execute("42")
    assert sandbox.code_interpreter.run_calls == runs
    assert manager._cancelled.is_set()


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
from dspy.primitives.code_interpreter import CodeExecutionError
mlflow.set_experiment('execution-test')
manager = SimpleNamespace(run_namespace=lambda *_a: {
    'error': True, 'error_type': 'ValueError', 'stderr': 'ValueError: verification_probe'})
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
