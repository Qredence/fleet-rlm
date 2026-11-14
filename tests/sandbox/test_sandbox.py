"""Offline domain regression coverage."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import dspy
import pytest
from daytona import DaytonaNotFoundError
from daytona.common.errors import DaytonaConnectionError, DaytonaTimeoutError
from dspy import CodeExecutionError, FinalOutput

import fleet_rlm.sandbox.interpreter as interpreter_module
from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox import manager as manager_module
from fleet_rlm.sandbox import repl, workspace_io
from fleet_rlm.sandbox import repl as repl_module
from fleet_rlm.sandbox import snapshots as operations
from fleet_rlm.sandbox.interpreter import DaytonaInterpreter
from fleet_rlm.sandbox.manager import _ACTIVE_MANAGER, DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError
from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME, SnapshotUnavailableError
from fleet_rlm.sandbox.workspace_io import WorkspaceIOMixin
from fleet_rlm.workspace import WorkspaceDataset, _workspace_relative_path, session_volume_subpath, validate_session_id

# Local test doubles


_DRIVER = r"""
import json, os, sys, traceback
channel = os.fdopen(os.dup(1), "w", buffering=1)
scope = {"__name__": "__main__"}

class Stream:
    def __init__(self, kind):
        self.kind = kind
    def write(self, text):
        if text:
            channel.write(json.dumps({"t": self.kind, "d": text}) + "\n")
            channel.flush()
        return len(text)
    def flush(self):
        pass
    def isatty(self):
        return False

for line in sys.stdin:
    request = json.loads(line)
    sys.stdout, sys.stderr = Stream("o"), Stream("e")
    error = None
    try:
        exec(compile(request["code"], "<cell>", "exec"), scope)
    except BaseException as exc:
        error = {"name": type(exc).__name__, "value": str(exc), "traceback": traceback.format_exc()}
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    channel.write(json.dumps({"t": "done", "error": error}) + "\n")
    channel.flush()
"""


class _Context:
    def __init__(self) -> None:
        self.id = uuid.uuid4().hex
        self.process = subprocess.Popen(
            [sys.executable, "-c", _DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)


class EmulatedInterpreter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.contexts: dict[str, _Context] = {}
        self.run_calls = 0
        self.stdout_messages = 0

    def translate(self, text: str) -> str:
        return text.replace("/workspace", str(self.root / "workspace")).replace(
            "/tmp/fleet/replies", str(self.root / "replies")
        )

    def create_context(self) -> Any:
        context = _Context()
        self.contexts[context.id] = context
        return SimpleNamespace(id=context.id)

    def list_contexts(self) -> list[Any]:
        return [SimpleNamespace(id=key) for key, value in self.contexts.items() if value.process.poll() is None]

    def delete_context(self, context: Any) -> None:
        live = self.contexts.pop(context.id, None)
        if live is not None:
            live.process.kill()

    def run_code(
        self,
        code: str,
        *,
        context: Any = None,
        on_stdout: Any = None,
        on_stderr: Any = None,
        timeout: int | None = None,
        envs: Any = None,  # noqa: ARG002
    ) -> Any:
        self.run_calls += 1
        live = self.contexts[context.id]
        result = SimpleNamespace(stdout="", stderr="", error=None)
        with live.lock:
            assert live.process.stdin is not None
            live.process.stdin.write(json.dumps({"code": self.translate(code)}) + "\n")
            live.process.stdin.flush()
            deadline = time.monotonic() + timeout if timeout else None
            timed_out = False
            while True:
                wait = None if deadline is None else max(0.01, deadline - time.monotonic())
                try:
                    line = live.lines.get(timeout=wait)
                except queue.Empty:
                    os.kill(live.process.pid, signal.SIGINT)  # Daytona interrupts the cell and keeps the context
                    timed_out, deadline = True, None
                    continue
                if line is None:
                    raise DaytonaConnectionError("Failed to run code: worker process ended (close code 1001)")
                message = json.loads(line)
                if message["t"] == "o":
                    self.stdout_messages += 1
                    result.stdout += message["d"]
                    if on_stdout:
                        on_stdout(SimpleNamespace(output=message["d"]))
                elif message["t"] == "e":
                    result.stderr += message["d"]
                    if on_stderr:
                        on_stderr(SimpleNamespace(output=message["d"]))
                else:
                    if timed_out:
                        raise DaytonaTimeoutError("Execution timed out")
                    if message["error"]:
                        result.error = SimpleNamespace(**message["error"])
                    return result

    def close(self) -> None:
        for live in self.contexts.values():
            live.process.kill()


class EmulatedFs:
    def __init__(self, interpreter: EmulatedInterpreter) -> None:
        self.interpreter = interpreter
        self.uploads: list[str] = []

    def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
        target = Path(self.interpreter.translate(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.uploads.append(path)


def emulated_sandbox(root: Path) -> SimpleNamespace:
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    interpreter = EmulatedInterpreter(root)
    return SimpleNamespace(
        id="emulated-sandbox", state="started", code_interpreter=interpreter, fs=EmulatedFs(interpreter)
    )


# Consolidated from sandbox/test_interpreter.py


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


def test_sandbox_skill_receipts_survive_later_cell_failure(sandbox, monkeypatch):
    from fleet_rlm.observability import sanitize_trace_span, trace_object

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
    activity = [trace_object(span.outputs)["skill_activity"] for span in spans]
    assert [receipts[0]["helper"] for receipts in activity] == ["save_skill", "use_skill"]
    assert all(receipts[0]["completed"] is True for receipts in activity)


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

    fixture = json.loads((Path(__file__).parents[1] / "fixtures.json").read_text())["pi_digit_trace"]
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


def test_native_flex_constructors_preserve_source(sandbox, tmp_path):
    # Constructor syntax is independent of guest process state. Exercise the
    # complete matrix in one native program, then reload that same source once.
    cases = [
        ("dspy.RLM", "", (20, 50)),
        ("dspy.RLM", ", max_iters=3", (3, 50)),
        ("dspy.RLM", ", max_iters=3, max_llm_calls=7", (3, 7)),
        ("dspy.RLM", ", **{'max_llm_calls': 7}", (20, 7)),
        ("dsp.RLM", "", (20, 50)),
        ("Recursive", "", (20, 50)),
        ("dspy.RLM", ", max_iters=None, max_llm_calls=None", (None, None)),
    ]
    constructors = "\n".join(
        f'        self.first_{index} = {constructor}("task -> answer"{arguments})'
        for index, (constructor, arguments, _expected) in enumerate(cases)
    )
    limits = ", ".join(
        f"[self.first_{index}.max_iters, self.first_{index}.max_llm_calls]" for index in range(len(cases))
    )
    manager = manager_on(sandbox)
    source = f"""import dspy as dsp
from dspy import RLM as Recursive
import json

class Program(dspy.Module):
    def __init__(self):
        super().__init__()
{constructors}
        self.second = dspy.RLM("task -> answer")
        self.predict = dspy.Predict("task -> answer")
    def forward(self, **inputs):
        payload = {{
            "first": [{limits}],
            "second": [self.second.max_iters, self.second.max_llm_calls],
            "predict": type(self.predict).__name__,
        }}
        return dspy.Prediction(answer=json.dumps(payload))
"""
    flex = dspy.Flex("task -> answer", interpreter_factory=manager.interpreter_factory())
    flex.load_state({"module_src": source})
    assert flex.module_src == source
    observed = json.loads(flex(task="constructor fixture").answer)
    assert [tuple(value) for value in observed["first"]] == [expected for _, _, expected in cases]
    assert tuple(observed["second"]) == (20, 50)
    assert observed["predict"] == "Predict"
    path = tmp_path / "native.json"
    flex.save(str(path))
    assert json.loads(path.read_text())["module_src"] == source
    flex.load(str(path))
    assert flex.module_src == source
    reloaded = json.loads(flex(task="reload fixture").answer)
    assert reloaded == observed
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


# Remote execution transport


@pytest.mark.parametrize("transient", [True, False])
def test_retry_metrics_survive_exhaustion_without_recording_exception_values(monkeypatch, transient):
    attributes = {}
    delays = []
    calls = []
    span = SimpleNamespace(set_attributes=attributes.update, set_attribute=attributes.__setitem__)
    monkeypatch.setattr(repl.time, "sleep", delays.append)
    monkeypatch.setattr(repl.random, "random", lambda: 0.5)
    error = DaytonaConnectionError("private provider response") if transient else ValueError("private input")

    def fail():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        repl.with_retries(fail, span=span)
    assert attributes == {"attempts": 3 if transient else 1, "retry_delay_seconds": 1.5 if transient else 0.0}
    assert len(calls) == (3 if transient else 1)
    assert delays == ([0.5, 1.0] if transient else [])


def no_calls(call: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"unexpected host call {call}")


def run(sandbox, context, code, *, on_call=no_calls, timeout=30, fence=None, on_fence=None, **payload):
    return repl.run(
        sandbox,
        context,
        {"code": code, **payload},
        on_call=on_call,
        timeout=timeout,
        fence=fence,
        on_fence=on_fence,
    )


def test_state_persists_and_the_last_expression_is_the_value(sandbox):
    context = repl.create_context(sandbox)
    reply_dir = Path(sandbox.code_interpreter.translate(repl.REPLY_DIR))
    assert reply_dir.is_dir() and reply_dir.stat().st_mode & 0o777 == 0o700
    assert run(sandbox, context, "x = 41")["has_value"] is False
    result = run(
        sandbox, context, "import json\nprint('captured')\njson.dumps({'answer': x + value})", variables={"value": 1}
    )
    assert result["stdout"] == "captured\n"
    assert result["value"] == '{"answer": 42}'


def test_namespaces_are_isolated_and_run_in_parallel(sandbox):
    left, right = repl.create_context(sandbox), repl.create_context(sandbox)
    results: dict[str, Any] = {}

    def execute(context, marker):
        results[marker] = run(sandbox, context, f"import time\ntime.sleep(0.2)\nprint({marker!r})\nmarker = {marker!r}")

    threads = [
        threading.Thread(target=execute, args=(left, "alpha")),
        threading.Thread(target=execute, args=(right, "beta")),
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert time.monotonic() - started < 0.4 + 1.0
    assert results["alpha"]["stdout"] == "alpha\n" and results["beta"]["stdout"] == "beta\n"
    assert run(sandbox, left, "marker")["value"] == "alpha"


def test_output_is_one_message_however_many_lines_are_printed(sandbox):
    # Daytona drops the websocket on bursts of ~2,000 writes; the runtime must coalesce.
    context = repl.create_context(sandbox)
    before = sandbox.code_interpreter.stdout_messages
    result = run(sandbox, context, "for i in range(3000):\n    print(i)")
    assert result["stdout"].split()[-1] == "2999"
    assert sandbox.code_interpreter.stdout_messages - before == 1


def test_output_is_bounded(sandbox):
    result = run(sandbox, repl.create_context(sandbox), "print('x' * 150000)")
    assert result["truncated"] is True and len(result["stdout"]) == 100_000


def test_errors_and_submit_are_reported(sandbox):
    context = repl.create_context(sandbox)
    error = run(sandbox, context, "raise ValueError('broken')")
    assert error["error"] is True and error["error_type"] == "ValueError" and "broken" in error["stderr"]
    final = run(sandbox, context, "SUBMIT(answer=42, note='done')")
    assert final["final"] == {"answer": 42, "note": "done"}


def test_skill_receipts_follow_actual_helper_calls_and_survive_later_errors(sandbox):
    context = repl.create_context(sandbox)
    created = run(
        sandbox,
        context,
        "save_skill('example', 'Use for example tasks', 'Read the reference', "
        "scripts={'check.py': 'print(17)'}, references={'notes.md': 'example reference'})",
    )
    assert created["skill_activity"] == [{"helper": "save_skill", "skill": "example", "completed": True}]
    # Aliases and keyword arguments still call the instrumented helper; source text alone is not evidence.
    used = run(
        sandbox, context, "activate = use_skill\nactivate(name='example')\nrun_skill_script('example', 'check.py')"
    )
    assert used["skill_activity"] == [
        {"helper": "use_skill", "skill": "example", "completed": True},
        {"helper": "run_skill_script", "skill": "example", "completed": True},
    ]
    failed = run(sandbox, context, "read_skill_reference('example', 'references/notes.md')\nuse_skill('missing')")
    assert failed["error"] is True
    assert failed["skill_activity"] == [
        {"helper": "read_skill_reference", "skill": "example", "completed": True},
        {"helper": "use_skill", "skill": "missing", "completed": False},
    ]
    empty = run(sandbox, context, "# use_skill('example')\n'run_skill_script'\nif False: use_skill('example')")
    assert empty["skill_activity"] == []
    assert not empty["skill_activity_truncated"]
    discovery = run(sandbox, context, "for _ in range(25): list_skills()\nuse_skill('example')")
    assert discovery["skill_activity"] == [{"helper": "use_skill", "skill": "example", "completed": True}]
    assert not discovery["skill_activity_truncated"]


def test_skill_receipts_are_bounded_and_preserved_by_submit(sandbox):
    context = repl.create_context(sandbox)
    run(sandbox, context, "save_skill('example', 'Use for examples', 'Example instructions')")
    result = run(sandbox, context, "for _ in range(25): use_skill('example')\nSUBMIT(answer='done')")
    assert result["final"] == {"answer": "done"}
    assert len(result["skill_activity"]) == 20
    assert result["skill_activity_truncated"] is True


@pytest.mark.parametrize(
    "code,expected",
    [
        ("SUBMIT('found', 'note')", {"findings": "found", "learned": "note"}),
        ("SUBMIT(findings='found', learned='note')", {"findings": "found", "learned": "note"}),
        ("SUBMIT('found', learned='note')", None),
        ("SUBMIT(answer='found')", None),
        ("SUBMIT('a', 'b', 'c')", None),
    ],
)
def test_submit_takes_the_run_output_fields_positionally_or_by_keyword(sandbox, code, expected):
    result = run(sandbox, repl.create_context(sandbox), code, outputs=["findings", "learned"])
    if expected is None:
        assert result["error"] is True and result["error_type"] == "TypeError"
        assert "SUBMIT" in result["stderr"]
    else:
        assert result["final"] == expected


def test_tool_bindings_survive_variables_and_round_trip_through_the_host(sandbox):
    calls: list[dict[str, Any]] = []

    def on_call(call):
        calls.append(call)
        time.sleep(0.1)  # the sandbox must wait for a slow host
        return {"value": f"bridged {call['args'][0]}"}

    result = run(
        sandbox,
        repl.create_context(sandbox),
        "print(callable(sample_tool))\nprint(SUBMIT is not None)\n"
        "print(sample_tool.__name__)\nprint(sample_tool('arg'))",
        on_call=on_call,
        tools=["sample_tool"],
        variables={"sample_tool": "overwritten", "SUBMIT": "overwritten"},
    )
    assert result["stdout"] == "True\nTrue\nsample_tool\nbridged arg\n"
    assert [(call["tool"], call["args"]) for call in calls] == [("sample_tool", ["arg"])]


def test_parallel_host_calls_are_not_interleaved(sandbox):
    code = (
        "from concurrent.futures import ThreadPoolExecutor\n"
        "with ThreadPoolExecutor(8) as pool:\n"
        "    out = list(pool.map(lambda i: echo(i), range(8)))\n"
        "out"
    )
    result = run(
        sandbox, repl.create_context(sandbox), code, tools=["echo"], on_call=lambda call: {"value": call["args"][0]}
    )
    assert result["value"] == list(range(8))


def test_a_cancelled_turn_fences_a_late_successful_host_result(sandbox, caplog):
    fence = repl.ExecutionFence()
    fenced: list[bool] = []
    seen: dict[str, Any] = {}

    def on_call(call):
        seen.update(call)
        fence.close()
        return {"value": "late answer"}

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        result = run(
            sandbox,
            repl.create_context(sandbox),
            "lookup()",
            tools=["lookup"],
            on_call=on_call,
            host_call_timeout=0.3,
            fence=fence,
            on_fence=lambda: fenced.append(True),
        )

    assert sandbox.fs.uploads == []
    reply_path = Path(sandbox.code_interpreter.translate(f"/tmp/fleet/replies/{seen['id']}.json"))
    assert not reply_path.exists()
    assert fenced == [True]
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "Fenced a successful host tool result" in warnings[0].getMessage()
    assert "late answer" not in caplog.text
    # The fence publishes nothing, so the sandbox reports its own host-call bound.
    assert result["error"] is True


def test_a_cancelled_turn_still_delivers_cancellation_and_error_replies(sandbox, caplog):
    fence = repl.ExecutionFence()
    fence.close()

    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        result = run(
            sandbox,
            repl.create_context(sandbox),
            "lookup()",
            tools=["lookup"],
            on_call=lambda _call: {"error": "Turn cancelled"},
            timeout=5,
            fence=fence,
        )

    assert result["error"] is True and "Turn cancelled" in result["stderr"]
    [path] = sandbox.fs.uploads
    assert path.startswith("/tmp/fleet/replies/") and path.endswith(".json")
    assert not [record for record in caplog.records if "Fenced" in record.getMessage()]


def test_cancellation_between_retry_attempts_refuses_the_successful_upload(monkeypatch, caplog):
    # A transient upload failure must not reopen publication: the retry checks the same token.
    monkeypatch.setattr(repl, "_RETRY_BASE_SECONDS", 0.0)
    fence = repl.ExecutionFence()
    attempts: list[str] = []

    class FlakyFs:
        def upload_file(self, _data, path, timeout=None):  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                fence.close()  # the originating Turn is cancelled while the first attempt is in flight
                raise DaytonaConnectionError("upload lost")

    fenced: list[bool] = []
    protocol = repl._Protocol(
        SimpleNamespace(fs=FlakyFs()),
        lambda _call: {"value": "late answer"},
        fence=fence,
        on_fence=lambda: fenced.append(True),
    )
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-retry"})

    assert len(attempts) == 1, "the retry after cancellation must not attempt an upload"
    assert fenced == [True]
    refusals = [record for record in caplog.records if "Fenced" in record.getMessage()]
    assert len(refusals) == 1, "exactly one bounded warning per refused publication"
    assert "late answer" not in caplog.text


def test_an_error_reply_is_retried_and_delivered_when_the_execution_is_fenced(monkeypatch, caplog):
    monkeypatch.setattr(repl, "_RETRY_BASE_SECONDS", 0.0)
    fence = repl.ExecutionFence()
    fence.close()
    attempts: list[str] = []

    class FlakyFs:
        def __init__(self) -> None:
            self.delivered = 0

        def upload_file(self, _data, path, timeout=None):  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                raise DaytonaConnectionError("upload lost")
            self.delivered += 1

    fs = FlakyFs()
    protocol = repl._Protocol(SimpleNamespace(fs=fs), lambda _call: {"error": "Turn cancelled"}, fence=fence)
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-error"})

    assert len(attempts) == 2, "an error reply goes through the retry path"
    assert fs.delivered == 1
    assert not [record for record in caplog.records if "Fenced" in record.getMessage()]


def test_a_permanently_failed_upload_never_escapes_the_worker(caplog):
    class RefusingFs:
        def upload_file(self, *_args, **_kwargs):
            raise RuntimeError("no route to the sandbox")

    protocol = repl._Protocol(
        SimpleNamespace(fs=RefusingFs()),
        lambda _call: {"value": 1},
        fence=repl.ExecutionFence(),
    )
    with caplog.at_level(logging.WARNING, logger="fleet_rlm.sandbox.repl"):
        protocol._answer({"id": "call-failed-upload"})

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "Could not return a host tool result" in warnings[0].getMessage()


def test_an_execution_token_is_closed_once_and_never_reopened():
    fence = repl.ExecutionFence()
    assert fence.is_open()
    fence.close()
    fence.close()
    assert not fence.is_open()


def test_a_host_call_worker_converts_an_unexpected_failure_into_an_error_reply(sandbox):
    def on_call(_call):
        raise RuntimeError("bridge exploded")

    protocol = repl._Protocol(sandbox, on_call)
    protocol._answer({"id": "call-1"})

    [path] = sandbox.fs.uploads
    reply = json.loads(Path(sandbox.code_interpreter.translate(path)).read_bytes())
    assert reply == {"error": "Host tool call failed"}


def test_host_errors_raise_in_the_sandbox(sandbox):
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "lookup()",
        tools=["lookup"],
        on_call=lambda _call: {"error": "no such row"},
    )
    assert result["error"] is True and "no such row" in result["stderr"]


def test_registered_dspy_builtin_tools_keep_their_bridge(sandbox):
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "all(callable(tool) for tool in (sub_rlm, llm_query, llm_query_batched))",
        tools=["sub_rlm", "llm_query", "llm_query_batched"],
    )
    assert result["value"] is True


def test_invalid_bindings_are_refused_before_running(sandbox):
    result = run(sandbox, repl.create_context(sandbox), "1", tools=["SUBMIT"])
    assert result["error"] is True and "invalid execution bindings" in result["stderr"]


def test_namespace_imports_stay_bound_to_their_own_facade(sandbox):
    left, right = repl.create_context(sandbox), repl.create_context(sandbox)
    for context, marker in ((left, "left"), (right, "right")):
        setup = (
            f"import sys, types\ndspy = types.ModuleType('dspy')\ndspy.marker = {marker!r}\nsys.modules['dspy'] = dspy"
        )
        assert not run(sandbox, context, setup).get("error")
    assert run(sandbox, left, "import dspy\ndspy.marker")["value"] == "left"


def test_workspace_dataset_revisions_and_symlink_escape(sandbox):
    context = repl.create_context(sandbox)
    setup = run(
        sandbox,
        context,
        "workspace.write('files/data.csv', 'id,value\\n1,-7\\n2,11\\n3,5\\n')\n"
        "view = WorkspaceDatasetView(workspace.stat('files/data.csv'))\n"
        "[len(list(view.lines())), view.search('-7'), len(view.preview(4))]",
    )
    assert not setup.get("error") and setup["value"] == [4, [[2, "1,-7\n"]], 4]
    changed = run(
        sandbox,
        context,
        "metadata = workspace.stat('files/data.csv')\n"
        "workspace.write('files/data.csv', 'changed')\nWorkspaceDatasetView(metadata)",
    )
    assert changed["error"] is True and "dataset source changed" in changed["stderr"]
    escaped = run(
        sandbox,
        context,
        "import os\nos.symlink('/etc/hostname', workspace.resolve('files/link'))\nworkspace.read_text('files/link')",
    )
    assert escaped["error"] is True and "symbolic links" in escaped["stderr"]


def test_deleting_a_context_ends_an_execution_waiting_on_the_host(sandbox):
    context = repl.create_context(sandbox)
    seen = threading.Event()
    outcome: dict[str, Any] = {}
    release = threading.Event()

    def on_call(_call):
        seen.set()
        assert release.wait(10), "Host handler was not released"
        return {"value": None}

    def execute():
        try:
            run(sandbox, context, "llm_query('pending')", on_call=on_call)
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=execute)
    worker.start()
    try:
        assert seen.wait(10)
        repl.delete_context(sandbox, context)
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert "worker process ended" in str(outcome["error"])
    finally:
        release.set()
        worker.join(timeout=10)


def test_a_timeout_interrupts_the_cell_and_keeps_the_context(sandbox):
    from daytona.common.errors import DaytonaTimeoutError

    context = repl.create_context(sandbox)
    run(sandbox, context, "x = 41")
    with pytest.raises(DaytonaTimeoutError):
        run(sandbox, context, "import time\nwhile True:\n    time.sleep(0.05)", timeout=1)
    assert run(sandbox, context, "x + 1")["value"] == 42


def test_a_context_that_reports_nothing_is_an_unknown_outcome(sandbox):
    context = repl.create_context(sandbox)
    sandbox.code_interpreter.run_code("def _fleet_exec(payload): pass", context=context, timeout=10)
    with pytest.raises(ExecutionOutcomeUnknownError):
        run(sandbox, context, "x = 1")


def test_a_broken_context_is_reported_as_an_error_not_a_hang(sandbox):
    context = repl.create_context(sandbox)
    sandbox.code_interpreter.run_code("del _fleet_exec", context=context, timeout=10)
    result = run(sandbox, context, "x = 1")
    assert result["error"] is True and result["error_type"] == "NameError"


def test_a_host_call_whose_reply_never_arrives_times_out_in_the_sandbox(sandbox, monkeypatch):
    from fleet_rlm.sandbox import repl as repl_module

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)

    def lost_upload(*_args, **_kwargs):
        raise RuntimeError("upload failed")

    monkeypatch.setattr(sandbox.fs, "upload_file", lost_upload)
    started = time.monotonic()
    result = run(
        sandbox,
        repl.create_context(sandbox),
        "lookup()",
        tools=["lookup"],
        on_call=lambda _call: {"value": 1},
        host_call_timeout=0.3,
    )
    assert result["error"] is True and result["error_type"] == "TimeoutError"
    assert "did not reply within" in result["stderr"]
    assert time.monotonic() - started < 5


def test_the_host_call_deadline_is_bounded_by_the_execution_timeout(sandbox):
    captured: dict[str, Any] = {}
    original = sandbox.code_interpreter.run_code

    def spy(code, **kwargs):
        captured["code"] = code
        return original(code, **kwargs)

    sandbox.code_interpreter.run_code = spy
    run(sandbox, repl.create_context(sandbox), "1", timeout=7)
    assert '"host_call_timeout": 7' in captured["code"] or '"host_call_timeout":7' in captured["code"]


def _capture_execution(sandbox) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    original = sandbox.code_interpreter.run_code

    def spy(code, **kwargs):
        captured["code"] = code
        captured["kwargs"] = kwargs
        return original(code, **kwargs)

    sandbox.code_interpreter.run_code = spy
    return captured


def _executed_payload(captured: dict[str, Any]) -> dict[str, Any]:
    argument = captured["code"].split("_fleet_exec(", 1)[1].rsplit(")", 1)[0]
    return json.loads(ast.literal_eval(argument))


@pytest.mark.parametrize("remaining", [650, 240, 120])
def test_the_host_call_bound_follows_the_remaining_turn_deadline(sandbox, remaining):
    # A fixed 600 s bridge cutoff must never outrank a longer Turn deadline.
    captured = _capture_execution(sandbox)
    run(sandbox, repl.create_context(sandbox), "1", timeout=remaining)
    assert _executed_payload(captured)["host_call_timeout"] == remaining


def test_a_deadline_free_execution_sends_timeout_zero_and_no_host_call_bound(sandbox):
    captured = _capture_execution(sandbox)
    result = run(sandbox, repl.create_context(sandbox), "x = 1\nx + 1", timeout=None)
    assert result["value"] == 2
    payload = _executed_payload(captured)
    assert "host_call_timeout" in payload, "the bound must be explicit, never omitted"
    assert payload["host_call_timeout"] is None
    assert captured["kwargs"]["timeout"] == 0, "Daytona reads 0 as no execution timeout"


def test_the_runtime_reads_zero_and_none_as_no_bound_without_a_fallback():
    # The runtime is sandbox code, not an importable host module, so exercise just its pure selector.
    source = (Path(__file__).resolve().parents[2] / "src" / "fleet_rlm" / "sandbox" / "runtime" / "repl.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    selection = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_host_call_limit"
    )
    namespace: dict[str, Any] = {}
    exec(compile(ast.Module(body=[selection], type_ignores=[]), "<runtime>", "exec"), namespace)
    limit = namespace["_host_call_limit"]

    assert limit(None) is None, "no Turn deadline means no host-call bound"
    assert limit(0) is None, "0 must not be replaced by a fixed fallback"
    assert limit(120) == 120.0, "a supplied remaining deadline is used as given"


# Durable workspace files


@pytest.mark.parametrize("case", ["no_artifacts", "unchanged_input", "new_empty_file"])
def test_empty_child_promotion_preserves_success_without_parent_io(monkeypatch, case):
    events = []
    parent = DaytonaSandboxManager("key", "org", "volume", "empty-promotion", event_callback=events.append)
    child = DaytonaSandboxManager("key", "org", "volume", "empty-promotion", parent=parent)
    path = "artifacts/result.txt"
    raw = b"copied input" if case == "unchanged_input" else b""
    inputs = {path: hashlib.sha256(raw).hexdigest()} if case == "unchanged_input" else {}
    parent._child_receipts[child.agent_id] = {
        "agent_id": child.agent_id,
        "inputs": inputs,
        "artifacts": [],
        "status": "running",
    }
    published = {}

    def list_files(manager):
        if manager is child:
            return ["skills/check/SKILL.md", *([] if case == "no_artifacts" else [path])]
        assert case == "new_empty_file", "empty promotion must not inspect the parent workspace"
        return []

    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", list_files)
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, _path: raw)
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda _self, paths, **_kwargs: {item: raw for item in paths},
    )
    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", lambda _self, files: published.update(files))

    result = child.collect_child_artifacts()

    target = f"artifacts/{child.agent_id}/result.txt"
    expected = (
        [{"path": target, "sha256": hashlib.sha256(b"").hexdigest(), "agent_id": child.agent_id}]
        if case == "new_empty_file"
        else []
    )
    assert result == expected
    assert published == ({target: b""} if case == "new_empty_file" else {})
    receipt = parent.execution_evidence()["children"][0]
    assert receipt["status"] == "succeeded"
    assert receipt["artifacts"] == [{**row, "root_path": row["path"]} for row in expected]
    assert parent.execution_evidence()["children_complete"] is True
    assert events[-1]["event"] == "agent_result"
    assert events[-1]["status"] == "succeeded"
    assert events[-1]["artifacts"] == expected


def test_child_promotion_quota_preflight_performs_no_public_writes() -> None:
    writes = []
    child_files = [f"artifacts/{index:03}.txt" for index in range(101)]
    root = SimpleNamespace(
        _budget_lock=Lock(),
        _child_receipts={"child": {"inputs": {}}},
        _children={},
    )
    parent = SimpleNamespace(
        list_workspace_files=lambda: [],
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: child_files,
        read_workspace_file=lambda _path: b"x",
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match="quota"):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == []


@pytest.mark.parametrize(
    ("child_files", "content_size", "existing", "reason"),
    [
        (["artifacts/a.txt"], 1, ["artifacts/child/a.txt"], "already exists"),
        (["artifacts/a.txt", "artifacts/b.txt"], 13_000_001, [], "quota"),
        (["artifacts/a.txt", "artifacts/b.txt"], 12_500_001, [], "quota"),
    ],
)
def test_child_promotion_collision_and_byte_quota_preflight_have_zero_writes(
    child_files: list[str], content_size: int, existing: list[str], reason: str
) -> None:
    writes = []
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        list_workspace_files=lambda: existing,
        # An occupied destination whose bytes differ is incompatible, never a completed retry.
        stat_workspace_file=lambda _path: {"sha256": hashlib.sha256(b"someone else's bytes").hexdigest()},
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: child_files,
        read_workspace_file=lambda _path: b"x" * content_size,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match=reason):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == []


def test_child_promotion_retry_skips_an_already_published_identical_destination() -> None:
    """A destination holding the same bytes is a completed publication, so a retry is idempotent."""
    writes: list[dict[str, bytes]] = []
    raw = b"promoted"
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        parent=None,
        list_workspace_files=lambda: ["artifacts/child/a.txt", "artifacts/child/b.txt"],
        stat_workspace_file=lambda _path: {"sha256": hashlib.sha256(raw).hexdigest()},
        write_workspace_files=lambda files: writes.append(files),
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: ["artifacts/a.txt", "artifacts/b.txt"],
        read_workspace_file=lambda _path: raw,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    results = WorkspaceIOMixin.collect_child_artifacts(child)

    assert writes == [{}]
    assert [row["path"] for row in results] == ["artifacts/child/a.txt", "artifacts/child/b.txt"]


def test_child_promotion_allows_exact_quota_followed_by_unchanged_input() -> None:
    writes = []
    paths = [f"artifacts/{index:03}.txt" for index in range(100)]
    unchanged = "artifacts/z-input.txt"
    root = SimpleNamespace(
        _budget_lock=Lock(),
        _child_receipts={"child": {"inputs": {unchanged: hashlib.sha256(b"x").hexdigest()}}},
        _children={},
    )
    parent = SimpleNamespace(
        parent=None,
        list_workspace_files=lambda: [],
        write_workspace_files=writes.append,
    )
    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: [*paths, unchanged],
        read_workspace_file=lambda _path: b"x",
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )
    receipts = WorkspaceIOMixin.collect_child_artifacts(child)
    assert len(receipts) == len(writes[0]) == 100
    assert all(not receipt["path"].endswith("z-input.txt") for receipt in receipts)


def test_child_promotion_stops_reading_once_the_file_quota_is_exceeded() -> None:
    """A runaway child is refused without buffering its whole output on the host."""
    reads: list[str] = []
    root = SimpleNamespace(_budget_lock=Lock(), _child_receipts={"child": {"inputs": {}}}, _children={})
    parent = SimpleNamespace(
        list_workspace_files=lambda: [],
        stat_workspace_file=lambda _path: {},
        write_workspace_files=lambda _files: pytest.fail("no write may happen after a quota refusal"),
    )

    def read(path: str) -> bytes:
        reads.append(path)
        return b"x"

    child = SimpleNamespace(
        parent=parent,
        root=root,
        agent_id="child",
        check_turn=lambda: None,
        list_workspace_files=lambda: [f"artifacts/{index:03}.txt" for index in range(500)],
        read_workspace_file=read,
        emit=lambda *_args, **_kwargs: None,
        record_child_outcome=lambda *_args: None,
    )

    with pytest.raises(ValueError, match="quota"):
        WorkspaceIOMixin.collect_child_artifacts(child)

    assert len(reads) == 100


def test_failed_download_span_omits_private_exception_details(monkeypatch, tmp_path):
    import mlflow
    from mlflow.entities import SpanStatusCode
    from mlflow.entities.trace_location import MlflowExperimentLocation

    experiment_id = mlflow.create_experiment(f"private-transfer-failure-{tmp_path.name}")
    destination = MlflowExperimentLocation(experiment_id=experiment_id)
    sandbox = SimpleNamespace(fs=SimpleNamespace(download_files=lambda *_args: []))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    monkeypatch.setattr(DaytonaSandboxManager, "_workspace_files_metadata", lambda *_args, **_kwargs: [{"size": 1}])
    manager = DaytonaSandboxManager("key", "org", "volume", "private-transfer")
    with (
        mlflow.start_span("parent", trace_destination=destination) as parent,
        pytest.raises(RuntimeError, match=r"files/private-name\.txt"),
    ):
        manager.read_workspace_files(["files/private-name.txt"])
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(parent.trace_id)
    assert trace is not None
    span = next(span for span in trace.data.spans if span.name == "sandbox.workspace_download")
    assert span.status.status_code == SpanStatusCode.ERROR
    assert span.outputs == {"completed": False}
    assert span.attributes["attempts"] == 1
    assert span.events == []
    assert "private-name.txt" not in json.dumps(span.to_dict())


@pytest.mark.parametrize("retry_stage", [None, "metadata", "download", "prepare", "upload"])
def test_transfer_spans_separate_remote_checks_and_io_without_payloads(monkeypatch, retry_stage):
    import mlflow
    from daytona.common.errors import DaytonaConnectionError

    from fleet_rlm.sandbox import repl

    rows, stack, delays = [], [], []
    attempts = Counter()
    content = b"private payload"

    @contextmanager
    def start_span(name, span_type):
        assert span_type == "TOOL"
        row = {"name": name, "parent": stack[-1] if stack else None, "attributes": {}}
        rows.append(row)
        stack.append(name)
        try:
            yield SimpleNamespace(
                set_attributes=row["attributes"].update,
                set_attribute=lambda k, v: row["attributes"].__setitem__(k, v),
                set_inputs=lambda v: row.__setitem__("inputs", v),
                set_outputs=lambda v: row.__setitem__("outputs", v),
            )
        finally:
            stack.pop()

    def attempt(stage):
        attempts[stage] += 1
        if stage == retry_stage and attempts[stage] == 1:
            raise DaytonaConnectionError("transient")

    def execute(_command, *, timeout):
        assert timeout is None
        stage = "prepare" if stack[-1] == "sandbox.workspace_prepare" else "metadata"
        attempt(stage)
        return SimpleNamespace(exit_code=0, result=json.dumps([{"size": len(content)}]))

    def download(requests, timeout):
        assert timeout == 1800
        assert stack[-1] == "sandbox.workspace_download"
        attempt("download")
        return [SimpleNamespace(source=r.source, result=content, error=None) for r in requests]

    def upload(files, *, timeout):
        assert timeout == 1800
        assert stack[-1] == "sandbox.workspace_upload"
        assert files[0].source == content
        attempt("upload")

    sandbox = SimpleNamespace(
        process=SimpleNamespace(exec=execute),
        fs=SimpleNamespace(
            download_files=download,
            upload_files=upload,
        ),
    )
    monkeypatch.setattr(mlflow, "get_current_active_span", object)
    monkeypatch.setattr(mlflow, "start_span", start_span)
    monkeypatch.setattr(repl.time, "sleep", delays.append)
    monkeypatch.setattr(repl.random, "random", lambda: 0.5)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    manager = DaytonaSandboxManager("private-key", "org", "volume", "transfer")

    assert manager.read_workspace_files(["files/private.txt"]) == {"files/private.txt": content}
    manager.write_workspace_files({"files/private.txt": content})

    assert [row["name"] for row in rows] == [
        "sandbox.workspace_metadata",
        "sandbox.workspace_download",
        "sandbox.workspace_prepare",
        "sandbox.workspace_upload",
    ]
    for stage, row in zip(["metadata", "download", "prepare", "upload"], rows, strict=True):
        assert row["outputs"] == {"completed": True}
        assert row["attributes"]["files_requested"] == 1
        assert row["attributes"]["attempts"] == (2 if retry_stage == stage else 1)
        assert row["attributes"]["retry_delay_seconds"] == (0.5 if retry_stage == stage else 0)
    assert rows[1]["attributes"]["bytes_requested"] == len(content)
    assert rows[3]["attributes"]["bytes_requested"] == len(content)
    assert delays == ([0.5] if retry_stage else [])
    recorded = json.dumps(rows)
    assert all(secret not in recorded for secret in ["private-key", "private payload", "files/private.txt"])


@pytest.mark.parametrize("case", ["file", "empty", "missing", "symlink", "parent_symlink", "oversized", "changed"])
def test_remote_stat_checks_size_digest_and_paths_without_downloads(monkeypatch, tmp_path, case):
    import contextlib
    import hashlib
    import io
    import os

    root = tmp_path / "workspace"
    (root / "files").mkdir(parents=True)
    target = root / "files/data.txt"
    raw = "selected café\n".encode() if case != "empty" else b""
    target.write_bytes(raw)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    if case == "missing":
        target.unlink()
    elif case == "symlink":
        target.unlink()
        target.symlink_to(outside)
    elif case == "parent_symlink":
        (root / "skills").symlink_to(tmp_path, target_is_directory=True)
    elif case == "oversized":
        with target.open("wb") as handle:
            handle.truncate(25_000_001)
    calls = []
    fstats = []

    def stat(descriptor):
        actual = os.fstat(descriptor)
        fstats.append(actual)
        if case == "changed" and len(fstats) == 2:
            return SimpleNamespace(st_mtime_ns=actual.st_mtime_ns + 1, st_ctime_ns=actual.st_ctime_ns)
        return actual

    remote_os = SimpleNamespace(**{**vars(os), "fstat": stat})

    def execute(_self, _sandbox, code, **_kwargs):
        calls.append(code)
        out = io.StringIO()
        code = code.replace("import base64,hashlib,json,os", "import base64,hashlib,json")
        code = code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(root)!r})")
        with contextlib.redirect_stdout(out):
            exec(code, {"os": remote_os})
        return out.getvalue()

    manager = DaytonaSandboxManager("key", "org", "volume", "stat-test")
    # No file-transfer methods exist on this double: metadata must stay remote.
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: SimpleNamespace(fs=object()))
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", execute)
    if case in {"file", "empty"}:
        assert manager.stat_workspace_file("files/data.txt") == {
            "path": "files/data.txt",
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    elif case == "missing":
        with pytest.raises(FileNotFoundError):
            manager.stat_workspace_file("files/data.txt")
    else:
        message = "changed" if case == "changed" else "size limit" if case == "oversized" else "symbolic link"
        with pytest.raises(ValueError, match=message):
            manager.stat_workspace_file("skills/outside.txt" if case == "parent_symlink" else "files/data.txt")
    if case == "file":
        (root / "files/other.txt").write_bytes(b"other")
        metadata = manager.stat_workspace_files(["files/data.txt", "files/other.txt"])
        assert metadata == {
            "files/data.txt": {
                "path": "files/data.txt",
                "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            "files/other.txt": {
                "path": "files/other.txt",
                "size_bytes": 5,
                "sha256": hashlib.sha256(b"other").hexdigest(),
            },
        }
    expected_calls = 2 if case == "file" else 1
    assert len(calls) == expected_calls
    with pytest.raises(ValueError):
        manager.stat_workspace_file("../outside.txt")
    assert len(calls) == expected_calls


@pytest.mark.parametrize(
    "metadata",
    [
        {"size": 1, "sha256": "invalid"},
        {"size": True, "sha256": "a" * 64},
        {"size": -1, "sha256": "a" * 64},
        {"size": None},
    ],
)
def test_remote_stat_rejects_invalid_metadata(monkeypatch, metadata):
    import json

    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_args, **_kwargs: json.dumps([metadata]))
    with pytest.raises(RuntimeError, match="Invalid workspace file metadata"):
        DaytonaSandboxManager("key", "org", "volume", "stat-test").stat_workspace_file("files/data.txt")


def test_daytona_path_transfer_overloads_and_25mb_provider_limits(tmp_path, monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, ...]] = []

    class FileSystemDouble:
        def upload_file(self, source, destination, timeout):
            calls.append(("upload", source, destination, str(timeout)))

        def download_file(self, source, destination, timeout):
            calls.append(("download", source, destination, str(timeout)))
            Path(destination).write_bytes(b"data")

    sandbox = SimpleNamespace(fs=FileSystemDouble())
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, _path, *, max_bytes=None, **_kwargs):
        if max_bytes is not None and max_bytes < 4:
            raise ValueError("workspace file exceeds size limit")
        return {"size": 4}

    monkeypatch.setattr(
        DaytonaSandboxManager,
        "_workspace_file_metadata",
        workspace_metadata,
    )
    local_source = tmp_path / "source"
    local_source.write_bytes(b"data")
    local_destination = tmp_path / "destination"

    assert manager.write_workspace_file_from_path("files/input.bin", local_source) == "/workspace/files/input.bin"
    assert manager.read_workspace_file_to_path("files/input.bin", local_destination) == 4
    assert calls == [
        ("upload", str(local_source), "/workspace/files/input.bin", "1800"),
        ("download", "/workspace/files/input.bin", str(local_destination), "1800"),
    ]
    with local_source.open("wb") as oversized_source:
        oversized_source.truncate(25_000_001)
    with pytest.raises(ValueError, match="25 MB limit"):
        manager.write_workspace_file_from_path("files/too-large.bin", local_source)
    with pytest.raises(ValueError, match="size limit"):
        manager.read_workspace_file_to_path("files/too-large.bin", local_destination, max_bytes=3)
    assert len(calls) == 2


def test_delete_checks_the_path_remotely_before_using_the_file_api(monkeypatch) -> None:
    manager = DaytonaSandboxManager(api_key="key", org_id="org", volume_id="volume", session_id="session-a")
    calls: list[tuple[str, str]] = []
    sandbox = SimpleNamespace(fs=SimpleNamespace(delete_file=lambda path: calls.append(("delete", path))))
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    def workspace_metadata(_self, _sandbox, path, **_kwargs):
        calls.append(("check", path))
        if path.endswith("link.txt"):
            raise ValueError("workspace target may not be a symbolic link")
        return {"size": 1}

    monkeypatch.setattr(DaytonaSandboxManager, "_workspace_file_metadata", workspace_metadata)
    manager.delete_workspace_file("files/a.txt")
    with pytest.raises(ValueError, match="symbolic link"):
        manager.delete_workspace_file("files/link.txt")
    with pytest.raises(ValueError):
        manager.delete_workspace_file("../escape")
    assert calls == [("check", "files/a.txt"), ("delete", "/workspace/files/a.txt"), ("check", "files/link.txt")]


@pytest.mark.parametrize("value", [-1, 25_000_001, True, 1.5, "1000"])
def test_byte_bounds_are_rejected_before_any_provider_transfer(value):
    """The shared 25 MB ceiling rejects a malformed bound without touching Daytona."""
    from fleet_rlm.sandbox.workspace_io import _checked_max_bytes
    from fleet_rlm.workspace import MAX_WORKSPACE_FILE_BYTES

    with pytest.raises(ValueError, match=f"between 0 and {MAX_WORKSPACE_FILE_BYTES}"):
        _checked_max_bytes(value)


@pytest.mark.parametrize("value", [0, 1, 1024, 25_000_000])
def test_byte_bounds_accept_every_integer_in_range(value):
    from fleet_rlm.sandbox.workspace_io import _checked_max_bytes

    assert _checked_max_bytes(value) == value


# Consolidated from sandbox/test_manager.py


# Lifecycle and shared budgets


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_persisted_sandbox_ownership_reconciles_known_provider_id(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = SimpleNamespace(id="sandbox-old")
    actions = []
    client = SimpleNamespace(
        get=lambda provider_id: sandbox if provider_id == sandbox.id else None,
        stop=lambda observed, timeout: actions.append(("stop", observed.id, timeout)),
    )
    writes = []
    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: client)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 7
    manager.ownership_resource_mutate = lambda agent_id, resource: writes.append((agent_id, resource)) or True

    manager.reconcile_sandbox_ownership(
        [
            {
                "agent_id": "root-agent",
                "allocation_nonce": "root-agent",
                "provider_id": "sandbox-old",
                "action": "stop",
                "intended_action": "stop",
            }
        ]
    )

    assert actions == [("stop", "sandbox-old", 60)]
    assert writes == [("root-agent", None)]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_provider_confirmed_absence_reconciles_as_confirmed_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resource the provider has forgotten cannot still write, so a 404 must not fence admission."""
    from daytona.common.errors import DaytonaNotFoundError

    writes: list[tuple[str, dict[str, Any] | None]] = []

    def not_found(*_args: Any, **_kwargs: Any) -> Any:
        raise DaytonaNotFoundError("gone")

    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: SimpleNamespace(get=not_found))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 3
    manager.ownership_resource_mutate = lambda agent_id, resource: writes.append((agent_id, resource)) or True

    manager.reconcile_sandbox_ownership(
        [
            {
                "agent_id": "root-agent",
                "allocation_nonce": "root-agent",
                "provider_id": "sandbox-vanished",
                "action": "stop",
                "intended_action": "stop",
            }
        ]
    )

    assert writes == [("root-agent", None)]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_other_provider_failures_still_fence_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona.common.errors import DaytonaError

    class ServerError(DaytonaError):
        status_code = 500

    def failing(*_args: Any, **_kwargs: Any) -> Any:
        raise ServerError("provider unavailable")

    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: SimpleNamespace(get=failing))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 3
    manager.ownership_resource_mutate = lambda *_args: pytest.fail("a failed cleanup must stay recorded")

    with pytest.raises(ServerError):
        manager.reconcile_sandbox_ownership(
            [
                {
                    "agent_id": "root-agent",
                    "allocation_nonce": "root-agent",
                    "provider_id": "sandbox-unknown",
                    "action": "stop",
                    "intended_action": "stop",
                }
            ]
        )


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_empty_ownership_never_builds_a_provider_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission with nothing outstanding must not depend on a provider round trip."""
    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: pytest.fail("no client may be built"))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 1
    manager.ownership_resource_mutate = lambda *_args: pytest.fail("nothing to mutate")

    manager.reconcile_sandbox_ownership([])


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_persisted_ambiguous_create_stays_fenced_when_labels_do_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SimpleNamespace(list=lambda _query: [])
    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: client)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 8
    manager.ownership_resource_mutate = lambda *_args: pytest.fail("ambiguous resource must remain recorded")

    with pytest.raises(RuntimeError, match="outcome is uncertain"):
        manager.reconcile_sandbox_ownership(
            [
                {
                    "agent_id": "root-agent",
                    "allocation_nonce": "root-agent",
                    "provider_id": None,
                    "action": "create",
                    "intended_action": "stop",
                }
            ]
        )


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_reconciliation_uses_unique_allocation_label_and_keeps_stale_owner_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = SimpleNamespace(id="sandbox-created-before-ack")
    actions: list[tuple[str, str]] = []
    client = SimpleNamespace(
        list=lambda query: (
            [sandbox] if query.labels == {"fleet_session": "session-a", "fleet_agent": "agent-a"} else []
        ),
        stop=lambda observed, **_kwargs: actions.append(("stop", observed.id)),
    )
    writes: list[tuple[str, dict[str, Any] | None]] = []

    def fenced_write(agent_id: str, resource: dict[str, Any] | None) -> bool:
        writes.append((agent_id, resource))
        return False  # a takeover advanced the ownership epoch during the provider operation

    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: client)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 12
    manager.ownership_resource_mutate = fenced_write

    with pytest.raises(RuntimeError, match="epoch changed during sandbox reconciliation"):
        manager.reconcile_sandbox_ownership(
            [
                {
                    "agent_id": "agent-a",
                    "allocation_nonce": "agent-a",
                    "provider_id": None,
                    "action": "create",
                    "intended_action": "stop",
                }
            ]
        )

    assert actions == [("stop", "sandbox-created-before-ack")]
    assert writes == [("agent-a", None)]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_ownership_lease_loss_prevents_remote_create(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Provider:
        snapshot = object()

        def create(self, _params: Any) -> None:
            events.append("create")

    monkeypatch.setattr(manager_module, "preflight", lambda *_args, **_kwargs: object())
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager.ownership_epoch = 4
    manager.ownership_resource_mutate = lambda *_args: events.append("lease_lost") or False
    manager._client = Provider()

    with pytest.raises(RuntimeError, match="ownership epoch changed"):
        manager._ensure_sandbox()

    assert events == ["lease_lost"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_stale_create_response_cannot_replace_new_owner_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = SimpleNamespace(id="sandbox-late-create", state="started")
    current_epoch = [1]
    ownership: dict[str, Any] = {"epoch": 1, "resources": []}
    events: list[str] = []

    def mutate(epoch: int, agent_id: str, resource: dict[str, Any] | None) -> bool:
        if epoch != current_epoch[0]:
            return False
        ownership["resources"] = [item for item in ownership["resources"] if item["agent_id"] != agent_id]
        if resource is not None:
            ownership["resources"].append({"agent_id": agent_id, **resource})
        return True

    class Provider:
        snapshot = object()

        def create(self, _params: Any) -> Any:
            events.append("provider_created")
            current_epoch[0] = 2
            return sandbox

        def list(self, _query: Any) -> list[Any]:
            return [sandbox]

        def stop(self, observed: Any, **_kwargs: Any) -> None:
            events.append(f"stopped:{observed.id}")

    monkeypatch.setattr(manager_module, "preflight", lambda *_args, **_kwargs: object())
    provider = Provider()
    old_owner = DaytonaSandboxManager("key", "org", "volume", "session-a", agent_id="root-agent")
    old_owner.ownership_epoch = 1
    old_owner.ownership_resource_mutate = lambda agent_id, resource: mutate(1, agent_id, resource)
    old_owner._client = provider

    with pytest.raises(RuntimeError, match="ownership epoch changed"):
        old_owner._ensure_sandbox()

    assert events == ["provider_created"]
    assert ownership["resources"] == [
        {
            "agent_id": "root-agent",
            "allocation_nonce": "root-agent",
            "provider_id": None,
            "parent_agent_id": None,
            "action": "create",
            "intended_action": "stop",
            "confirmed": False,
        }
    ]

    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: provider)
    new_owner = DaytonaSandboxManager("key", "org", "volume", "session-a")
    new_owner.ownership_epoch = 2
    new_owner.ownership_resource_mutate = lambda agent_id, resource: mutate(2, agent_id, resource)
    new_owner.reconcile_sandbox_ownership(ownership["resources"])

    assert events == ["provider_created", "stopped:sandbox-late-create"]
    assert ownership["resources"] == []


@pytest.mark.parametrize("crash_point", ["create_before_ack", "before_create_call", "stop_before_confirmation"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_subprocess_crash_leaves_durable_intent_for_the_next_owner(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, crash_point: str
) -> None:
    ownership_path = tmp_path / "ownership.json"
    provider_path = tmp_path / "provider.json"
    ownership_path.write_text(json.dumps({"epoch": 1, "resources": []}), encoding="utf-8")
    if crash_point == "stop_before_confirmation":
        provider_path.write_text(json.dumps({"id": "sandbox-before-stop", "stop_count": 0}), encoding="utf-8")

    worker = r"""
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from fleet_rlm.sandbox import manager as manager_module
from fleet_rlm.sandbox.manager import DaytonaSandboxManager

ownership_path, provider_path = map(Path, sys.argv[1:3])
crash_point = sys.argv[3]

def persist(agent_id, resource):
    record = json.loads(ownership_path.read_text(encoding="utf-8"))
    record["resources"] = [item for item in record["resources"] if item["agent_id"] != agent_id]
    if resource is not None:
        record["resources"].append({"agent_id": agent_id, **resource})
    temporary = ownership_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    os.replace(temporary, ownership_path)
    return True

class Provider:
    snapshot = object()

    def create(self, _params):
        if crash_point == "before_create_call":
            # Intent is already durable and no provider call happened: nothing to reconcile but
            # the recorded reservation, which must not be mistaken for a created sandbox.
            os._exit(75)
        provider_path.write_text(json.dumps({
            "id": "sandbox-created-before-ack",
            "labels": {"fleet_session": "session-a", "fleet_agent": manager.agent_id},
            "stop_count": 0,
        }), encoding="utf-8")
        os._exit(73)

    def stop(self, sandbox, **_kwargs):
        record = json.loads(provider_path.read_text(encoding="utf-8"))
        record["stop_count"] += 1
        provider_path.write_text(json.dumps(record), encoding="utf-8")
        os._exit(74)

manager_module.preflight = lambda *_args, **_kwargs: object()
manager = DaytonaSandboxManager("key", "org", "volume", "session-a", agent_id="root-agent")
manager.ownership_epoch = 1
manager.ownership_resource_mutate = persist
manager._client = Provider()
if crash_point == "stop_before_confirmation":
    manager._sandbox = SimpleNamespace(id="sandbox-before-stop", state="started")
    manager.shutdown()
else:
    manager._ensure_sandbox()
"""
    completed = subprocess.run(
        [sys.executable, "-c", worker, str(ownership_path), str(provider_path), crash_point],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    expected_exit = {"create_before_ack": 73, "before_create_call": 75, "stop_before_confirmation": 74}[crash_point]
    assert completed.returncode == expected_exit, completed.stderr

    ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
    assert ownership["epoch"] == 1
    assert len(ownership["resources"]) == 1
    intent = ownership["resources"][0]
    assert intent["action"] == ("stop" if crash_point == "stop_before_confirmation" else "create")
    assert intent["provider_id"] == ("sandbox-before-stop" if crash_point == "stop_before_confirmation" else None)

    # A new owner takes over, confirms cleanup by labels or provider ID, then clears the intent.
    ownership["epoch"] = 2
    ownership_path.write_text(json.dumps(ownership), encoding="utf-8")
    if crash_point == "before_create_call":
        # No sandbox was ever created: an empty label lookup is still not proof, so the new owner
        # must stay fenced rather than admit against an unverified reservation.
        provider_record = {"id": "sandbox-created-before-ack"}
    else:
        provider_record = json.loads(provider_path.read_text(encoding="utf-8"))
    sandbox = SimpleNamespace(id=provider_record["id"])
    stop_calls: list[str] = []

    def mutate(agent_id: str, resource: dict[str, Any] | None) -> bool:
        record = json.loads(ownership_path.read_text(encoding="utf-8"))
        if record["epoch"] != 2:
            return False
        record["resources"] = [item for item in record["resources"] if item["agent_id"] != agent_id]
        if resource is not None:
            record["resources"].append({"agent_id": agent_id, **resource})
        ownership_path.write_text(json.dumps(record), encoding="utf-8")
        return True

    client = SimpleNamespace(
        get=lambda provider_id: sandbox if provider_id == sandbox.id else None,
        list=lambda query: (
            []
            if crash_point == "before_create_call"
            else (
                [sandbox]
                if query.labels.get("fleet_session") == "session-a"
                and query.labels.get("fleet_agent") == intent["allocation_nonce"]
                else []
            )
        ),
        stop=lambda observed, **_kwargs: stop_calls.append(observed.id),
    )
    monkeypatch.setattr(manager_module, "_shared_daytona_client", lambda *_args: client)
    owner = DaytonaSandboxManager("key", "org", "volume", "session-a")
    owner.ownership_epoch = 2
    owner.ownership_resource_mutate = mutate

    if crash_point == "before_create_call":
        # An empty label lookup never proves the reservation produced nothing, so admission stays fenced.
        with pytest.raises(RuntimeError, match="outcome is uncertain"):
            owner.reconcile_sandbox_ownership(ownership["resources"])
        assert json.loads(ownership_path.read_text(encoding="utf-8"))["resources"] == ownership["resources"]
        return

    owner.reconcile_sandbox_ownership(ownership["resources"])

    assert stop_calls == [sandbox.id]
    assert json.loads(ownership_path.read_text(encoding="utf-8"))["resources"] == []


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_inventory_and_artifact_collection_record_only_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    spans: dict[str, Any] = {}

    @contextmanager
    def phase(name: str):
        yield SimpleNamespace(set_outputs=lambda value: spans.__setitem__(name, value))

    monkeypatch.setattr(workspace_io, "phase_span", phase)
    monkeypatch.setattr(workspace_io.mlflow, "get_current_active_span", lambda: object())
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: json.dumps(["artifacts/private.txt"])
    )
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda _self, _path, **_kwargs: b"secret")
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._sandbox = object()

    evidence = manager.execution_evidence()

    assert evidence["artifacts"]["complete"] is True
    assert spans == {
        "sandbox.workspace_inventory": {"files": 1},
        "sandbox.artifact_collection": {"files": 1, "complete": True},
    }
    spans.clear()
    monkeypatch.setattr(workspace_io.mlflow, "get_current_active_span", lambda: None)
    assert manager.list_workspace_files() == ["artifacts/private.txt"]
    assert manager.execution_evidence()["artifacts"]["complete"] is True
    assert not spans


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_file_transfer_handles_uploads_larger_than_exec_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"fleet-data" * 250_000

    class FileSystem:
        uploaded: tuple[bytes, str, int] | None = None

        def upload_file(self, data: bytes, path: str, *, timeout: int) -> None:
            self.uploaded = (data, path, timeout)

        def download_file(self, path: str, timeout: int) -> bytes:
            assert path == "/workspace/files/large.csv"
            assert timeout == 1800
            return content

    filesystem = FileSystem()
    sandbox = SimpleNamespace(state=None, fs=filesystem)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._client = object()
    manager._sandbox = sandbox

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str, **_kwargs) -> str:
        assert sandbox_arg is sandbox
        assert len(code.encode("utf-8")) < 10_000
        if "if not os.path.isfile(target)" in code:
            return json.dumps([{"size": len(content)}])
        return json.dumps([{"size": None}])

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.write_workspace_file("files/large.csv", content) == "/workspace/files/large.csv"
    assert filesystem.uploaded == (content, "/workspace/files/large.csv", 1800)
    assert manager.read_workspace_file("files/large.csv", max_bytes=len(content)) == content


@pytest.mark.parametrize("subpath", ["/shared", "shared/../x", "shared//x", "shared/"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shared_notes_subpath_must_be_a_relative_volume_prefix(subpath: str) -> None:
    with pytest.raises(ValueError):
        FleetSettings(fleet_shared_notes_subpath=subpath)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shared_notes_subpath_defaults_and_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SHARED_NOTES_SUBPATH", raising=False)
    assert FleetSettings().fleet_shared_notes_subpath == "shared/learned-notes"
    assert FleetSettings(fleet_shared_notes_subpath="").fleet_shared_notes_subpath == ""


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_root_sandbox_mounts_shared_notes_beside_its_session_subtree() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    mounts = root.volume_mounts(child=False)
    assert [(mount.mount_path, mount.subpath) for mount in mounts] == [
        ("/workspace", "sessions/session-a"),
        ("/learned", "shared/learned-notes"),
    ]
    assert {mount.volume_id for mount in mounts} == {"volume"}
    child_mounts = root.volume_mounts(child=True)
    assert [(mount.mount_path, mount.subpath) for mount in child_mounts] == [
        ("/workspace", f"agents/session-a/{root.agent_id}")
    ]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shared_notes_mount_is_optional_and_never_reaches_child_sandboxes() -> None:
    plain = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert [mount.mount_path for mount in plain.volume_mounts(child=False)] == ["/workspace"]
    shared = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    assert [mount.mount_path for mount in shared.volume_mounts(child=True)] == ["/workspace"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_learned_note_lands_as_an_unreviewed_redacted_file_on_the_shared_mount(monkeypatch) -> None:
    uploads: list[tuple[bytes, str]] = []
    commands: list[str] = []
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(upload_file=lambda content, path, **_kwargs: uploads.append((content, path)))
    )
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, code: commands.append(code) or "True\n"
    )
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)

    path = manager.write_learned_note("turn-0123456789", "Use api_key=hidden\n\nAlways verify digits.", "tr-1")

    assert path == "/learned/session-a-turn0123.md"
    assert uploads[0][1] == path
    assert uploads[0][0].decode("utf-8") == (
        "---\nsession: session-a\nturn: turn0123\ntrace_id: tr-1\nstatus: unreviewed\n"
        "summary: Use api_key=[redacted]\n---\nUse api_key=[redacted]\n\nAlways verify digits.\n"
    )
    assert "/learned" in commands[0]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_learned_note_is_skipped_when_the_sandbox_predates_the_mount(monkeypatch) -> None:
    requested: list[bool] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "False\n")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: requested.append(True))

    assert manager.write_learned_note("turn-1", "A note.") is None
    assert requested == []


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_learned_note_write_is_skipped_for_empty_notes_and_disabled_mounts(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: pytest.fail("no sandbox call expected")
    )
    enabled = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    disabled = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert enabled.write_learned_note("turn-1", "  \n") is None
    assert disabled.write_learned_note("turn-1", "A note.") is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_cancelled_manager_cannot_restart_remote_execution(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, client = _provision(monkeypatch, tmp_path, service)
    first = manager.ensure_sandbox()
    manager.cancel_turn()
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.ensure_sandbox()
    manager.reset_turn_budget()
    manager.end_turn()
    # The stopped root is discarded, so the next Turn cold-resumes into a fresh sandbox.
    second = manager.ensure_sandbox()
    assert second.id != first.id
    assert len(client.created) == 2


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_an_unconfirmed_stop_retains_the_root_and_fences_writable_reuse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, client = _provision(monkeypatch, tmp_path, service)
    assert manager.ensure_sandbox().id == "sandbox-1"
    stops: list[str] = []

    def refuse(sandbox: Any, **_kwargs: Any) -> None:
        stops.append(sandbox.id)
        raise RuntimeError("provider refused the stop")

    client.stop = refuse
    manager.cancel_turn()
    assert manager.cleanup_outcome == "unconfirmed"
    assert manager.live_sandbox_id == "sandbox-1", "retain the unresolved provider identity"

    manager.reset_turn_budget()
    manager.end_turn()
    for operation in (
        manager.ensure_sandbox,
        lambda: manager.write_workspace_file("files/new.txt", b"unsafe"),
        manager.allocate_child,
    ):
        with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
            operation()
    assert len(client.created) == 1
    assert stops == ["sandbox-1"]
    client.stop = lambda sandbox, **_kwargs: stops.append(sandbox.id)
    assert manager.shutdown() is True
    manager.reset_turn_budget()
    manager.end_turn()
    assert manager.ensure_sandbox().id == "sandbox-2"
    assert stops == ["sandbox-1", "sandbox-1"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_terminal_teardown_stops_the_root_once_and_records_the_outcome() -> None:
    sandbox = SimpleNamespace(id="sandbox-teardown", state="started")
    manager, calls = _manager_with(sandbox)
    events: list[dict[str, Any]] = []
    manager.event_callback = events.append

    manager.cancel_turn()
    assert calls == ["stop"]
    assert manager.cleanup_outcome == "stopped"
    assert manager._contexts == {}, "interpreter contexts are invalid after the root stops"

    # The watchdog, a client disconnect, and the terminal Turn branch may all reach the teardown.
    manager.cancel_turn()
    assert calls == ["stop"], "a second caller must not issue another provider stop"
    assert [event["event"] for event in events] == ["cancellation_requested"]

    # A fresh Turn owns a fresh teardown outcome.
    manager.reset_turn_budget()
    manager.end_turn()
    assert manager.cleanup_outcome is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_raising_provider_stop_records_unconfirmed(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    manager = DaytonaSandboxManager("key", "org", "volume", "stop-failure")

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("provider refused the stop")

    manager._sandbox = SimpleNamespace(id="sandbox-stop-failure", state="started")
    manager._client = SimpleNamespace(stop=refuse)
    # Teardown is a finalization seam: no exception escapes, callers read the recorded outcome.
    manager.cancel_turn()
    assert manager.cleanup_outcome == "unconfirmed"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_raising_notification_never_skips_child_or_root_teardown(caplog: pytest.LogCaptureFixture) -> None:
    sandbox = SimpleNamespace(id="sandbox-notify-failure", state="started")
    manager, calls = _manager_with(sandbox)
    manager._children["child-1"] = SimpleNamespace(shutdown=lambda: calls.append("child_shutdown") or True)

    def refuse_notification(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream closed; payload=secret")

    manager.event_callback = refuse_notification

    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()

    # The notification is a side channel: every other teardown step still ran, in order.
    assert calls == ["stop", "child_shutdown"]
    assert manager.cleanup_outcome == "stopped"
    assert manager._contexts == {}
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Lifecycle notification cancellation_requested failed" in warnings[0].getMessage()
    assert "secret" not in warnings[0].getMessage()

    # A later caller sees the completed teardown and issues neither a stop nor a second warning.
    caplog.clear()
    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()
    assert calls == ["stop", "child_shutdown"]
    assert manager.cleanup_outcome == "stopped"
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_cleanup_stopped_is_cached_only_after_teardown_attempts_have_run() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "cache-order")
    manager._sandbox = SimpleNamespace(id="sandbox-cache-order", state="started")
    observed_at_notification: list[str | None] = []
    observed_at_stop: list[str | None] = []

    manager.event_callback = lambda _event: observed_at_notification.append(manager.cleanup_outcome)

    def stop(*_args: Any, **_kwargs: Any) -> None:
        observed_at_stop.append(manager.cleanup_outcome)

    manager._client = SimpleNamespace(stop=stop)

    manager.cancel_turn()

    assert observed_at_notification == [None], "the notification must not run after a cached outcome"
    assert observed_at_stop == [None], "the outcome is cached only once the stop returned"
    assert manager.cleanup_outcome == "stopped"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_raising_child_shutdown_still_attempts_the_root_stop() -> None:
    sandbox = SimpleNamespace(id="sandbox-child-failure", state="started")
    manager, calls = _manager_with(sandbox)

    def refuse_shutdown() -> None:
        raise RuntimeError("child shutdown failed")

    manager._children["child-1"] = SimpleNamespace(shutdown=refuse_shutdown)

    manager.cancel_turn()

    assert calls == ["stop"], "the root stop is attempted even when a child refused to shut down"
    assert manager.cleanup_outcome == "unconfirmed"


@pytest.mark.parametrize("cancel", [False, True], ids=["shutdown", "cancel"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_root_interruption_precedes_blocked_child_cleanup(cancel: bool) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="root-first", state="started"))
    child_started, release = threading.Event(), threading.Event()
    outcomes: list[Any] = []

    def blocked_child() -> bool:
        child_started.set()
        assert release.wait(timeout=5)
        return True

    manager._children["blocked"] = SimpleNamespace(shutdown=blocked_child)
    cleanup = threading.Thread(target=lambda: outcomes.append(manager.cancel_turn() if cancel else manager.shutdown()))
    cleanup.start()
    try:
        assert child_started.wait(timeout=2)
        assert calls == ["stop"], "interrupt the root before waiting for child deletion"
        assert not outcomes, "acknowledgment still waits for all required cleanup"
        with pytest.raises(RuntimeError, match="cancelled"):
            manager.ensure_sandbox()
    finally:
        release.set()
        cleanup.join(timeout=5)
        manager.end_turn()
    assert not cleanup.is_alive()
    assert outcomes == ([None] if cancel else [True])


@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shutdown_aggregates_child_failures_and_keeps_admission_fenced(raises: bool) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="root-aggregate", state="started"))

    def failed_child() -> bool:
        calls.append("failed-child")
        if raises:
            raise RuntimeError("delete refused")
        return False

    manager._children["failed"] = SimpleNamespace(shutdown=failed_child)
    manager._children["healthy"] = SimpleNamespace(shutdown=lambda: calls.append("healthy-child") or True)
    assert manager.shutdown() is False
    assert calls == ["stop", "failed-child", "healthy-child"]
    manager.reset_turn_budget()
    manager.end_turn()
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        manager.write_workspace_file("files/unsafe.txt", b"unsafe")
    manager._children["failed"].shutdown = lambda: True
    assert manager.shutdown() is True


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shutdown_hands_stale_epoch_resources_to_new_owner(pending: bool) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="taken-over", state="started"))
    child = DaytonaSandboxManager("key", "org", "volume", manager.session_id, parent=manager)
    child._creation_pending = True
    manager._children[child.agent_id] = child
    manager.ownership_epoch = 1
    writes = []
    manager.ownership_resource_mutate = lambda agent, resource: writes.append((agent, resource)) or False
    manager._creation_pending = pending
    assert manager.shutdown() is True
    assert calls == []
    assert len(writes) == 1 and writes[0][1] is not None
    assert manager._sandbox is None and not manager._children
    assert child._ownership_lost is True
    assert manager.shutdown() is True and len(writes) == 1
    manager.reset_turn_budget()
    manager.end_turn()
    with pytest.raises(manager_module.SandboxOwnershipLostError):
        manager.ensure_sandbox()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 408, 422, 429, 500, None])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_create_rejection_clears_only_definitive_allocation_intent(monkeypatch, status):
    from daytona.common.errors import DaytonaError

    error = DaytonaError("rejected", status_code=status)
    writes = []
    monkeypatch.setattr(manager_module, "preflight", lambda *_args, **_kwargs: object())

    def reject(_params):
        raise error

    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._client = SimpleNamespace(snapshot=object(), create=reject)
    manager.ownership_epoch = 1
    manager.ownership_resource_mutate = lambda agent, resource: writes.append((agent, resource)) or True
    with pytest.raises(DaytonaError) as caught:
        manager._ensure_sandbox()
    assert caught.value is error
    definitive = status in {400, 401, 403, 404, 422}
    assert manager._creation_pending is not definitive
    assert (writes[-1][1] is None) is definitive


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_shutdown_preserves_ambiguous_create_until_a_resource_can_be_stopped() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "uncertain-create")
    manager._creation_pending = True
    matches: list[Any] = []
    stopped: list[str] = []
    manager._client = SimpleNamespace(
        list=lambda _query: matches,
        stop=lambda sandbox, **_kwargs: stopped.append(sandbox.id),
    )
    assert manager.shutdown() is False, "an empty lookup is not confirmed absence"
    assert manager._creation_pending is True
    manager.reset_turn_budget()
    manager.end_turn()
    with pytest.raises(RuntimeError, match="cleanup is unconfirmed"):
        manager.ensure_sandbox()
    matches.append(SimpleNamespace(id="allocated-before-ack"))
    assert manager.shutdown() is True
    assert stopped == ["allocated-before-ack"]
    assert manager._creation_pending is False
    assert manager._sandbox is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_concurrent_root_shutdowns_cannot_report_confirmation_before_stop() -> None:
    manager, _calls = _manager_with(SimpleNamespace(id="root-concurrent", state="started"))
    started, release = threading.Event(), threading.Event()
    stopped: list[str] = []

    def stop(sandbox: Any, **_kwargs: Any) -> None:
        stopped.append(sandbox.id)
        started.set()
        assert release.wait(timeout=5)

    manager._client.stop = stop
    outcomes: list[bool] = []
    first = threading.Thread(target=lambda: outcomes.append(manager.shutdown()))
    second = threading.Thread(target=lambda: outcomes.append(manager.shutdown()))
    first.start()
    assert started.wait(timeout=2)
    second.start()
    try:
        second.join(timeout=0.1)
        assert not outcomes
    finally:
        release.set()
        first.join(timeout=5)
        second.join(timeout=5)
    assert outcomes == [True, True]
    assert stopped == ["root-concurrent"]


def _child_with_provider(root: DaytonaSandboxManager, index: int, deleted: list[str]) -> DaytonaSandboxManager:
    """Build a real child manager whose provider deletion is observable and never contacts Daytona."""
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id=f"child-{index}",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )
    child._client = SimpleNamespace(delete=lambda sandbox, **_kwargs: deleted.append(sandbox.id))
    root._children[child.agent_id] = child
    return child


def _child_with_blocking_delete(
    root: DaytonaSandboxManager,
    *,
    started: threading.Event,
    release: threading.Event,
    deleted: list[str],
) -> DaytonaSandboxManager:
    """Build a child whose provider delete blocks until the test releases it."""
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-blocking",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def delete(sandbox: Any, **_kwargs: Any) -> None:
        deleted.append(sandbox.id)
        started.set()
        release.wait(timeout=10)

    child._client = SimpleNamespace(delete=delete)
    root._children[child.agent_id] = child
    return child


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_concurrent_child_shutdowns_join_the_same_provider_cleanup() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    deleted: list[str] = []
    child = _child_with_blocking_delete(root, started=started, release=release, deleted=deleted)

    first_outcome: list[bool] = []
    first = threading.Thread(target=lambda: first_outcome.append(child.shutdown()), name="normal-child-shutdown")
    first.start()
    assert started.wait(timeout=5), "the first caller must reach provider deletion"

    joined_outcome: list[bool] = []
    second = threading.Thread(target=lambda: joined_outcome.append(child.shutdown()), name="racing-child-shutdown")
    second.start()
    time.sleep(0.1)
    assert joined_outcome == [], "a concurrent caller must not report confirmed before the provider delete returns"

    release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert first_outcome == [True]
    assert joined_outcome == [True], "every caller observes the completed provider result"
    assert deleted == ["child-blocking"], "the provider delete runs exactly once"
    assert root._children == {}


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_cancellation_cannot_acknowledge_while_a_child_cleanup_is_in_flight() -> None:
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    deleted: list[str] = []
    child = _child_with_blocking_delete(root, started=started, release=release, deleted=deleted)

    normal = threading.Thread(target=child.shutdown, name="normal-child-shutdown")
    normal.start()
    assert started.wait(timeout=5), "normal child completion must be inside provider deletion"

    acknowledged: list[None] = []

    def cancel() -> None:
        root.cancel_turn()
        acknowledged.append(None)

    cancelled = threading.Thread(target=cancel, name="cancel-turn")
    cancelled.start()
    time.sleep(0.1)
    assert acknowledged == [], "cancel_turn must not return before the child's provider cleanup resolves"
    assert root.cleanup_outcome is None

    release.set()
    normal.join(timeout=5)
    cancelled.join(timeout=5)

    assert root.cleanup_outcome == "stopped", "the cancelled Turn observes the joined provider result"
    assert deleted == ["child-blocking"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_cancellation_observes_an_unconfirmed_join_rather_than_an_early_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # A failed provider delete must surface as unconfirmed; the join must not be skipped or retried
    # outside the caller that observes it.
    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    root = DaytonaSandboxManager("key", "org", "volume", "state-test")
    started, release = threading.Event(), threading.Event()
    attempts: list[str] = []
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-failing",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def refuse_delete(sandbox: Any, **_kwargs: Any) -> None:
        attempts.append(sandbox.id)
        started.set()
        release.wait(timeout=10)
        raise RuntimeError("provider delete failed")

    child._client = SimpleNamespace(delete=refuse_delete)
    root._children[child.agent_id] = child

    normal = threading.Thread(target=child.shutdown, name="normal-child-shutdown")
    normal.start()
    assert started.wait(timeout=5)

    acknowledged: list[None] = []

    def cancel() -> None:
        root.cancel_turn()
        acknowledged.append(None)

    cancelled = threading.Thread(target=cancel, name="cancel-turn")
    cancelled.start()
    time.sleep(0.1)
    assert acknowledged == []
    assert root.cleanup_outcome is None

    release.set()
    normal.join(timeout=5)
    cancelled.join(timeout=5)

    assert root.cleanup_outcome == "unconfirmed"
    assert root._children[child.agent_id] is child, "the failed child stays registered for a later attempt"
    assert child._sandbox is not None
    # The normal completion failed and restored the handle, so the joining cancellation makes its
    # own provider attempt rather than reusing the failed result. Both attempts failed.
    assert attempts == ["child-failing", "child-failing"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_failed_child_provider_delete_keeps_the_child_registered_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setattr(manager_module, "with_retries", lambda call, **_kwargs: call())
    root, calls = _manager_with(SimpleNamespace(id="sandbox-retry-root", state="started"))
    attempts: list[str] = []
    child = DaytonaSandboxManager("key", "org", "volume", "state-test", parent=root)
    child._sandbox = SimpleNamespace(
        id="child-retry",
        state="started",
        process=SimpleNamespace(exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=0, result="")),
    )

    def refuse(_sandbox: Any, **_kwargs: Any) -> None:
        attempts.append("refused")
        raise RuntimeError("provider delete failed")

    child._client = SimpleNamespace(delete=refuse)
    root._children[child.agent_id] = child

    root.cancel_turn()

    assert root.cleanup_outcome == "unconfirmed"
    assert calls == ["stop"]
    assert root._children[child.agent_id] is child, "a genuinely failed cleanup stays registered"
    assert child._sandbox is not None, "the handle is retained for a later attempt"
    assert attempts == ["refused"]

    child._client = SimpleNamespace(delete=lambda sandbox, **_kwargs: attempts.append(sandbox.id))
    assert child.shutdown() is True
    assert root._children == {}


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_raising_shared_callback_never_skips_real_child_provider_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="sandbox-notify-children", state="started"))
    deleted: list[str] = []
    for index in (1, 2):
        _child_with_provider(manager, index, deleted)

    def refuse_notification(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream closed; payload=secret")

    manager.event_callback = refuse_notification

    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager.cancel_turn()

    # Delivery is a side channel: both children reached provider deletion and the root still stopped.
    assert deleted == ["child-1", "child-2"]
    assert calls == ["stop"]
    assert manager._children == {}
    assert manager.cleanup_outcome == "stopped", "a notification failure is not a provider failure"
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 5, "each refused lifecycle notification logs exactly one warning"
    assert all("Lifecycle notification" in record.getMessage() for record in warnings)
    assert "secret" not in caplog.text


@pytest.mark.parametrize("raises", [False, True], ids=["returns-unconfirmed", "raises"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_failing_child_never_skips_its_sibling_or_the_root_stop(raises: bool) -> None:
    manager, calls = _manager_with(SimpleNamespace(id="sandbox-child-failure", state="started"))
    observed: list[str] = []

    def refuse_shutdown() -> bool:
        observed.append("failed-child")
        if raises:
            raise RuntimeError("provider delete failed")
        return False

    def succeed_shutdown() -> bool:
        observed.append("healthy-child")
        return True

    manager._children["child-1"] = SimpleNamespace(shutdown=refuse_shutdown)
    manager._children["child-2"] = SimpleNamespace(shutdown=succeed_shutdown)

    manager.cancel_turn()

    assert observed == ["failed-child", "healthy-child"]
    assert calls == ["stop"], "a failed child must not skip the root stop"
    assert manager.cleanup_outcome == "unconfirmed"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_successful_turn_keeps_one_warm_root_for_the_next_turn() -> None:
    sandbox = _RemoteSandbox("started")
    manager, calls = _manager_with(sandbox)

    for _turn in range(2):
        manager.reset_turn_budget()
        manager.end_turn()
        assert manager.ensure_sandbox() is sandbox
        assert manager.cleanup_outcome is None

    assert calls == [], "success never starts, stops, or re-provisions the root"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_managers_with_the_same_credentials_share_one_daytona_client(monkeypatch: pytest.MonkeyPatch) -> None:

    created: list[tuple[str, str]] = []

    class FakeDaytona:
        def __init__(self, config: Any) -> None:
            created.append((config.api_key, config.organization_id))

    monkeypatch.setattr(manager_module, "Daytona", FakeDaytona)
    monkeypatch.setattr(manager_module, "_DAYTONA_CLIENTS", {})

    first = manager_module._shared_daytona_client("key", "org")
    assert manager_module._shared_daytona_client("key", "org") is first
    assert manager_module._shared_daytona_client("key", "other-org") is not first
    assert created == [("key", "org"), ("key", "other-org")]


class _RemoteSandbox(SimpleNamespace):
    """A sandbox handle whose cached `state` lags the provider until `refresh_data()` is called."""

    def __init__(self, cached: str, *remote: str) -> None:
        super().__init__(id="sandbox-remote", state=cached, refreshes=0)
        self._remote = list(remote)

    def refresh_data(self) -> None:
        self.refreshes += 1
        if self._remote:
            self.state = self._remote.pop(0)


def _manager_with(sandbox: Any, **client_methods: Any) -> tuple[DaytonaSandboxManager, list[str]]:
    calls: list[str] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "state-test")
    manager._sandbox = sandbox
    manager._contexts["main"] = object()
    manager._client = SimpleNamespace(
        start=lambda *_args, **_kwargs: calls.append("start"),
        stop=lambda *_args, **_kwargs: calls.append("stop"),
        **client_methods,
    )
    return manager, calls


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_ensure_sandbox_refreshes_remote_state_before_deciding_to_restart() -> None:
    # Daytona auto-stops an idle sandbox; the cached handle still says "started".
    sandbox = _RemoteSandbox("started", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 1 and calls == ["start"]
    assert manager._contexts == {}, "interpreter contexts are invalid after a stop"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_ensure_sandbox_waits_for_a_settling_sandbox_before_starting_it(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(manager_module, "_STATE_POLL_SECONDS", 0.0)
    sandbox = _RemoteSandbox("started", "stopping", "stopping", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 3 and calls == ["start"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_ensure_sandbox_refuses_a_failed_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "error")
    manager, calls = _manager_with(sandbox)
    with pytest.raises(RuntimeError, match="error"):
        manager.ensure_sandbox()
    assert calls == []


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_ensure_sandbox_does_not_touch_a_running_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "started")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert calls == [] and manager._contexts != {}


@pytest.mark.parametrize("transient", [True, False])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_idempotent_daytona_calls_retry_only_transient_failures(
    monkeypatch: pytest.MonkeyPatch, transient: bool
) -> None:
    from daytona.common.errors import DaytonaConnectionError, DaytonaError

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)
    attempts: list[str] = []

    class FileSystem:
        def upload_file(self, _data: bytes, path: str, *, timeout: int) -> None:  # noqa: ARG002
            attempts.append(path)
            if len(attempts) == 1:
                if transient:
                    raise DaytonaConnectionError("websocket closed")
                raise DaytonaError("no such volume", status_code=400)

    manager = DaytonaSandboxManager("key", "org", "volume", "retry-test")
    manager._client = object()
    manager._sandbox = SimpleNamespace(state=None, fs=FileSystem())
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_a, **_k: json.dumps([{"size": None}]))
    if transient:
        assert manager.write_workspace_file("files/a.txt", b"x") == "/workspace/files/a.txt"
        assert len(attempts) == 2
    else:
        with pytest.raises(DaytonaError, match="no such volume"):
            manager.write_workspace_file("files/a.txt", b"x")
        assert len(attempts) == 1


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_run_code_transport_failures_are_never_retried() -> None:
    from daytona.common.errors import DaytonaTimeoutError

    attempts: list[int] = []

    def never(*_args: Any, **_kwargs: Any) -> None:
        attempts.append(1)
        raise DaytonaTimeoutError("Execution timed out")

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=never), fs=SimpleNamespace())
    with pytest.raises(DaytonaTimeoutError):
        repl_module.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5)
    assert attempts == [1]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_should_share_budget_categories_and_recoveries_across_children():
    root = DaytonaSandboxManager("key", "org", "volume", "usage", max_llm_calls=10)
    child = DaytonaSandboxManager("key", "org", "volume", "usage", parent=root)
    root.consume_budget(category="routing")
    root.consume_budget(3, category="skill_selection")
    child.consume_budget(6)
    child.record_sandbox_error()
    child.record_submit_rejection()
    usage = root.turn_usage()
    assert usage == child.turn_usage()
    assert usage == {
        "lm_calls": 10,
        "sandbox_errors": 1,
        "submit_rejections": 1,
        "budget_breakdown": {"generation_query": 6, "routing": 1, "skill_selection": 3, "other_decisions": 0},
    }
    with pytest.raises(RuntimeError, match="budget exhausted"):
        child.consume_budget()
    assert root.turn_usage() == usage
    usage["budget_breakdown"]["routing"] = 99
    assert root.turn_usage()["budget_breakdown"]["routing"] == 1
    root.reset_turn_budget()
    root.end_turn()
    assert root.turn_usage()["submit_rejections"] == root.turn_usage()["lm_calls"] == 0
    assert sum(root.turn_usage()["budget_breakdown"].values()) == 0


@pytest.mark.parametrize("fails", [False, True], ids=["copied", "copy_failure"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_should_trace_child_copy_lineage_and_preserve_failure_cleanup(monkeypatch, fails):
    from contextlib import contextmanager

    import mlflow

    spans = []

    @contextmanager
    def start_span(name, span_type):
        row = {"name": name, "span_type": span_type, "attributes": {}, "status": "running"}
        spans.append(row)
        try:
            yield SimpleNamespace(
                set_attributes=row["attributes"].update,
                set_attribute=lambda k, v: row["attributes"].__setitem__(k, v),
                set_inputs=lambda v: row.__setitem__("inputs", v),
                set_outputs=lambda v: row.__setitem__("outputs", v),
                set_status=lambda v: row.__setitem__("status", v.lower()),
            )
            if row["status"] == "running":
                row["status"] = "ok"
        except BaseException:
            row["status"] = "error"
            raise

    monkeypatch.setattr(mlflow, "start_span", start_span)
    closed = []
    root = DaytonaSandboxManager("secret-api-key", "org", "volume", "copy")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: object())
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: ["files/data.txt"])
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda _self, paths: {path: b"private file content" for path in paths},
    )

    def write(*_args):
        if fails:
            raise ValueError("copy failed")

    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", write)
    monkeypatch.setattr(DaytonaSandboxManager, "shutdown", lambda self: closed.append(self.agent_id))
    if fails:
        with pytest.raises(ValueError, match="copy failed"):
            root.allocate_child()
    else:
        child = root.allocate_child()
        assert spans[0]["attributes"]["child_agent_id"] == child.agent_id
    copy = next(row for row in spans if row["name"] == "sandbox.input_copy")
    assert copy["attributes"]["parent_agent_id"] == root.agent_id
    assert copy["attributes"]["bytes_read"] == len(b"private file content")
    read = next(row for row in spans if row["name"] == "sandbox.input_read")
    write_span = next(row for row in spans if row["name"] == "sandbox.input_write")
    assert read["outputs"]["completed"] is True
    assert read["attributes"]["files_read"] == 1
    assert read["attributes"]["bytes_read"] == len(b"private file content")
    assert write_span["outputs"]["completed"] is not fails
    assert write_span["attributes"]["bytes_requested"] == len(b"private file content")
    assert copy["outputs"]["completed"] is not fails
    assert copy["status"] == ("error" if fails else "ok")
    assert bool(closed) == fails
    # A failed batch never leaves a partial input receipt behind.
    assert root._child_receipts[next(iter(root._child_receipts))]["inputs"] == (
        {} if fails else {"files/data.txt": hashlib.sha256(b"private file content").hexdigest()}
    )
    assert "private file content" not in json.dumps(spans) and "secret-api-key" not in json.dumps(spans)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_artifact_execution_is_not_replayed_after_losing_its_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona.common.errors import DaytonaConnectionError

    monkeypatch.setattr(repl_module, "_RETRY_BASE_SECONDS", 0.0)
    executions: list[str] = []
    cancelled = threading.Event()

    def execute(command: str, *, timeout: int | None) -> None:
        assert timeout is not None and 1 <= timeout <= 31
        executions.append(command)
        raise DaytonaConnectionError("response lost after execution")

    manager = DaytonaSandboxManager("key", "org", "volume", "artifact-replay")
    manager._client = object()
    manager._sandbox = SimpleNamespace(state=None, process=SimpleNamespace(exec=execute))
    manager._deadline = time.monotonic() + 60
    monkeypatch.setattr(DaytonaSandboxManager, "read_workspace_file", lambda *_args, **_kwargs: b"print(42)")
    monkeypatch.setattr(DaytonaSandboxManager, "cancel_turn", lambda _self: cancelled.set())

    with pytest.raises(repl_module.ExecutionOutcomeUnknownError, match="verification outcome unknown"):
        manager.verify_python_artifact("artifacts/result.py", 30)

    assert len(executions) == 1
    assert cancelled.is_set()


def _run_remote_locally(tmp_path, calls):
    """Execute generated remote code with /workspace and /learned redirected to local stand-ins."""
    import contextlib
    import io

    def run(_self, code: str) -> str:
        calls.append(code)
        local = code.replace("'/workspace'", repr(str(tmp_path / "workspace"))).replace(
            "'/learned'", repr(str(tmp_path / "learned"))
        )
        # A temporary directory is not a mount point; treat the stand-in as mounted.
        local = local.replace("os.path.ismount(", "os.path.isdir(")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(local, {})
        return out.getvalue()

    return run


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_turn_inputs_gathers_digests_and_environment_in_one_call(monkeypatch, tmp_path) -> None:
    import hashlib

    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files" / "a.txt").write_bytes(b"alpha")
    (workspace / "outside").mkdir()
    (workspace / "outside" / "secret.txt").write_bytes(b"secret")
    (workspace / "files" / "link.txt").symlink_to(workspace / "outside" / "secret.txt")
    (workspace / "linked-dir").symlink_to(workspace / "outside")
    calls: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, calls))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    inputs = manager.turn_inputs()

    assert len(calls) == 1
    # Learned notes are written for review, never read back into Turns.
    assert "notes" not in inputs and "/learned" not in calls[0]
    # Symbolic links are skipped and never traversed.
    assert inputs["digests"] == {"files/a.txt": hashlib.sha256(b"alpha").hexdigest()}
    assert inputs["environment"].startswith("Sandbox environment: Python ")
    assert "installed: " in inputs["environment"] and "not installed: " in inputs["environment"]
    assert "set_int_max_str_digits(0)" in inputs["environment"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_listing_and_digests_share_managed_roots_without_touching_scratch(monkeypatch, tmp_path):
    from fleet_rlm.workspace import _ALLOWED_WORKSPACE_ROOTS

    workspace = tmp_path / "workspace"
    expected = {}
    for root in _ALLOWED_WORKSPACE_ROOTS:
        directory = workspace / root / "nested"
        directory.mkdir(parents=True)
        (directory / "source.txt").write_bytes(b"source")
        expected[f"{root}/nested/source.txt"] = hashlib.sha256(b"source").hexdigest()
    (workspace / "checkout").mkdir()
    scratch = workspace / "checkout" / "db.py"
    scratch.write_bytes(b"unmanaged edits")
    (workspace / "bundle.txt").write_bytes(b"bundle")
    (workspace / "files" / "link").symlink_to(workspace / "checkout", target_is_directory=True)
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager.list_workspace_files() == sorted(expected)
    assert manager.turn_inputs()["digests"] == expected
    assert scratch.read_bytes() == b"unmanaged edits"
    assert (workspace / "bundle.txt").read_bytes() == b"bundle"


@pytest.mark.parametrize("method", ["list_workspace_files", "turn_inputs"])
@pytest.mark.parametrize("unsafe", ["root-symlink", "backslash", "control"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_inventory_rejects_unsafe_managed_entries(monkeypatch, tmp_path, method, unsafe):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if unsafe == "root-symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_bytes(b"secret")
        (workspace / "files").symlink_to(outside, target_is_directory=True)
    else:
        (workspace / "files").mkdir()
        name = "bad\\name" if unsafe == "backslash" else "bad\nname"
        (workspace / "files" / name).write_bytes(b"source")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    with pytest.raises(ValueError, match=r"workspace|Workspace"):
        getattr(manager, method)()


@pytest.mark.parametrize("response", ["", "{}", "null", '"files/a"', "[1]", '{"files/a": 1}'])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_listing_rejects_malformed_responses(monkeypatch, response):
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda *_args: response)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    with pytest.raises((ValueError, RuntimeError)):
        manager.list_workspace_files()


@pytest.mark.parametrize(
    "response",
    ["", "{}", "null", '{"digests": {"files/a": "short"}}', '{"digests": {"outside/a": "' + "a" * 64 + '"}}'],
)
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_digests_reject_malformed_responses(monkeypatch, response):
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda *_args: response)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    with pytest.raises((ValueError, RuntimeError)):
        manager.turn_inputs()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_turn_inputs_reject_a_malformed_digest_listing(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python",
        lambda _self, _code: '{"digests": {"files/a.txt": 3}, "environment": ""}',
    )

    with pytest.raises(RuntimeError, match="Invalid workspace digest"):
        DaytonaSandboxManager("key", "org", "volume", "session-a").turn_inputs()


class _BatchFileSystem:
    """Records Daytona batch transfers; `download_files` serves `contents` keyed by absolute path."""

    def __init__(self, contents: dict[str, bytes]) -> None:
        self.contents = contents
        self.downloads: list[list[str]] = []
        self.uploads: list[list[tuple[str, bytes]]] = []

    def download_files(self, requests, timeout):
        assert timeout == 1800
        self.downloads.append([request.source for request in requests])
        return [SimpleNamespace(source=r.source, result=self.contents.get(r.source), error=None) for r in requests]

    def upload_files(self, uploads, *, timeout):
        assert timeout == 1800
        self.uploads.append([(upload.destination, upload.source) for upload in uploads])


def _batch_manager(monkeypatch, filesystem, sizes):
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    sandbox = SimpleNamespace(fs=filesystem)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: sandbox)
    checks: list[str] = []

    def remote_exec(_self, _sandbox, code, **_kwargs):
        checks.append(code)
        return json.dumps([{"size": size} for size in sizes])

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)
    return manager, checks


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_batched_transfer_uses_one_check_and_one_transfer_per_side(monkeypatch) -> None:
    files = {f"files/{index}.txt": f"row {index}".encode() for index in range(5)}
    filesystem = _BatchFileSystem({f"/workspace/{path}": data for path, data in files.items()})
    manager, checks = _batch_manager(monkeypatch, filesystem, [len(data) for data in files.values()])

    contents = manager.read_workspace_files(list(files))
    manager.write_workspace_files(contents)

    assert contents == files
    assert len(checks) == 2 and len(filesystem.downloads) == 1 and len(filesystem.uploads) == 1
    assert filesystem.uploads[0] == [(f"/workspace/{path}", data) for path, data in files.items()]
    assert "os.makedirs(parent,exist_ok=True)" in checks[1] and "os.makedirs" not in checks[0]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_batched_read_rejects_a_file_that_changed_after_its_check(monkeypatch) -> None:
    filesystem = _BatchFileSystem({"/workspace/files/a.txt": b"grown content"})
    manager, _ = _batch_manager(monkeypatch, filesystem, [3])

    with pytest.raises(RuntimeError, match=r"changed or could not be downloaded: files/a\.txt"):
        manager.read_workspace_files(["files/a.txt"])


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_batched_read_refuses_the_quota_before_downloading(monkeypatch) -> None:
    filesystem = _BatchFileSystem({})
    manager, _ = _batch_manager(monkeypatch, filesystem, [60, 60])

    with pytest.raises(ValueError, match="transfer quota"):
        manager.read_workspace_files(["files/a", "files/b"], max_total_bytes=100)
    assert filesystem.downloads == []


@pytest.mark.parametrize("bad", ["files/link.txt", "linked-dir/secret.txt"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_batched_metadata_check_refuses_symbolic_links(monkeypatch, tmp_path, bad) -> None:
    import contextlib
    import io

    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "ok.txt").write_bytes(b"ok")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_bytes(b"secret")
    (tmp_path / "files" / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (tmp_path / "linked-dir").symlink_to(tmp_path / "outside")

    def run_locally(_self, _sandbox, code, **_kwargs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(tmp_path)!r})"), {})
        return out.getvalue()

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", run_locally)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager._workspace_files_metadata(object(), ["files/ok.txt"], create_parents=False) == [{"size": 2}]
    with pytest.raises(ValueError, match="symbolic link"):
        manager._workspace_files_metadata(object(), ["files/ok.txt", bad], create_parents=False)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_empty_batched_write_makes_no_remote_call(monkeypatch) -> None:
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: pytest.fail("no sandbox call"))

    DaytonaSandboxManager("key", "org", "volume", "session-a").write_workspace_files({})


# --- Turn snapshot preflight -------------------------------------------------


class _SnapshotDTO(SimpleNamespace):
    """The subset of the provider snapshot DTO that preflight reads."""


class _RecordingSnapshotService:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def get(self, name: str) -> Any:
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return self.result


class _RecordingClient:
    """A Daytona client stub that records snapshot lookups and sandbox creates."""

    def __init__(self, service: _RecordingSnapshotService) -> None:
        self.snapshot = service
        self.created: list[Any] = []

    def create(self, params: Any) -> Any:
        self.created.append(params)
        return SimpleNamespace(id=f"sandbox-{len(self.created)}", state="started")

    def list(self, _query: Any) -> list[Any]:
        return []

    def stop(self, _sandbox: Any, **_kwargs: Any) -> None:
        return None


@pytest.fixture
def clear_snapshot_preflight_cache():

    from fleet_rlm.config import get_settings

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    snapshot_module.clear_preflight_cache()
    get_settings.cache_clear()
    yield
    snapshot_module.clear_preflight_cache()
    get_settings.cache_clear()


def _provision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, service: _RecordingSnapshotService, **manager_values: Any
) -> tuple[DaytonaSandboxManager, _RecordingClient]:
    monkeypatch.chdir(tmp_path)
    client = _RecordingClient(service)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", **manager_values)
    manager._client = client
    return manager, client


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_manager_snapshot_defaults_to_repo_owned_name() -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, DEFAULT_DEFINITION, SNAPSHOT_NAME

    assert SNAPSHOT_NAME == "fleet-rlm-python313-v15"
    resources = DEFAULT_DEFINITION.resources()
    assert (resources.cpu, resources.memory, resources.disk) == (4, 8, 8)
    assert DEFAULT_DEFINITION.python_version == "3.13"
    packages = dict(DEFAULT_DEFINITION.packages)
    assert packages["dspy"] == "3.5.0"
    assert packages["numpy"] == "2.5.1"
    assert packages["pandas"] == "3.0.5"
    assert {"matplotlib", "pillow", "openpyxl", "pypdf", "uv", "pytest", "ruff"} <= packages.keys()
    assert not {"markitdown", "scipy", "scikit-learn", "pyarrow"} & packages.keys()
    assert "fleet snapshot" in BUILD_COMMAND
    root = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert root.snapshot == SNAPSHOT_NAME
    explicit = DaytonaSandboxManager("key", "org", "volume", "session-a", snapshot="fleet-rlm-python313-v12")
    assert explicit.snapshot == "fleet-rlm-python313-v12"


@pytest.mark.parametrize(
    "change",
    [
        {"name": "fleet-rlm-python313-v11"},
        {"cpu": 2},
        {"memory": 4},
        {"disk": 8, "memory": 8, "cpu": 4, "python_version": "3.12"},
        {"packages": (("numpy", "2.5.0"), ("pandas", "3.0.5"), ("mpmath", "1.4.1"))},
    ],
)
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_snapshot_digest_changes_per_field_and_ignores_package_order(change: dict[str, Any]) -> None:
    import dataclasses

    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION

    baseline = DEFAULT_DEFINITION.digest()
    assert dataclasses.replace(DEFAULT_DEFINITION, **change).digest() != baseline
    reordered = dataclasses.replace(DEFAULT_DEFINITION, packages=tuple(reversed(DEFAULT_DEFINITION.packages)))
    assert reordered.digest() == baseline


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_accept_and_cache_once_per_credentials(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    first, first_client = _provision(monkeypatch, tmp_path, service)
    root_sandbox = first.ensure_sandbox()
    assert root_sandbox.id == "sandbox-1"
    assert first_client.created[0].snapshot == SNAPSHOT_NAME
    assert service.calls == [SNAPSHOT_NAME]
    assert first.snapshot_identity is not None
    assert first.snapshot_identity.as_dict() == {
        "name": SNAPSHOT_NAME,
        "id": "snap-1",
        "cpu": 4,
        "mem": 8,
        "disk": 8,
        "definition_digest": first.snapshot_identity.definition_digest,
    }

    second, second_client = _provision(monkeypatch, tmp_path, service)
    assert second.ensure_sandbox().id == "sandbox-1"
    assert len(second_client.created) == 1
    assert service.calls == [SNAPSHOT_NAME], "the process cache must reuse the resolution"


@pytest.mark.parametrize("state", ["missing", "inactive", "pending", "building", "error"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_unusable_snapshot_refuses_before_creation(monkeypatch, tmp_path, state) -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, SNAPSHOT_NAME, SnapshotUnavailableError

    service = _RecordingSnapshotService(
        None if state == "missing" else _SnapshotDTO(id="snap-1", state=state, cpu=4, mem=8, disk=8)
    )
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError) as error:
        manager.ensure_sandbox()
    message = str(error.value)
    assert SNAPSHOT_NAME in message
    assert f"state={state}" in message
    if state == "inactive":
        assert "fleet snapshot activate" in message
    elif state in {"pending", "building"}:
        assert "wait for it to become active" in message
    else:
        assert BUILD_COMMAND in message
    assert snapshot_error_is_single_error(message)
    assert client.created == []
    assert manager._sandbox is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_unusable_snapshot_treats_provider_error_as_missing(monkeypatch, tmp_path) -> None:
    from daytona import DaytonaNotFoundError

    from fleet_rlm.sandbox.snapshots import SnapshotUnavailableError

    service = _RecordingSnapshotService(error=DaytonaNotFoundError("fleet-rlm-python313-v10"))
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError, match="state=missing"):
        manager.ensure_sandbox()
    assert client.created == []


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_surfaces_provider_failures_and_refresh_evicts_stale_cache() -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    active = _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)

    class Service:
        result: Any = active
        error: Exception | None = None

        def get(self, _name: str) -> Any:
            if self.error is not None:
                raise self.error
            return self.result

    service = Service()
    first = snapshot_module.preflight(service, api_key="key", org_id="org")
    assert first.id == "snap-1"
    service.error = TimeoutError("Daytona temporarily unavailable")
    with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
        snapshot_module.preflight(service, api_key="key", org_id="org", refresh=True)
    with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
        snapshot_module.preflight(service, api_key="key", org_id="org")


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_custom_snapshot_does_not_claim_default_recipe() -> None:
    from fleet_rlm.sandbox.snapshots import preflight

    identity = preflight(
        _RecordingSnapshotService(_SnapshotDTO(id="custom-id", state="active", cpu=4, mem=8, disk=8)),
        name="team-custom-image",
        api_key="custom-key",
        org_id="org",
    )
    assert identity.definition_digest is None
    assert identity.provenance()["snapshot_definition_digest"] is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_provider_build_metadata_is_hashed_and_not_copied_to_provenance() -> None:
    from datetime import UTC, datetime

    from daytona_api_client import BuildInfo

    from fleet_rlm.sandbox.snapshots import preflight

    dto = _SnapshotDTO(
        id="snap-1",
        state="active",
        cpu=4,
        mem=8,
        disk=8,
        image_name="registry.example/fleet@sha256:abc",
        build_info=BuildInfo(
            dockerfile_content="FROM python:3.13",
            context_hashes=["sha256:def"],
            snapshot_ref="provider-build-1",
            created_at=datetime(2026, 10, 23, tzinfo=UTC),
            updated_at=datetime(2026, 10, 23, tzinfo=UTC),
        ),
    )
    identity = preflight(_RecordingSnapshotService(dto), api_key="build-key", org_id="org")
    assert identity.build_digest is not None
    assert "dockerfile_content" not in identity.provenance()
    dto.build_info.dockerfile_content = "FROM python:3.12"
    changed = preflight(_RecordingSnapshotService(dto), api_key="build-key", org_id="org", refresh=True)
    assert changed.build_digest != identity.build_digest


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_expires_success_after_sixty_seconds(monkeypatch) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    now = [100.0]
    monkeypatch.setattr(snapshot_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    service = _RecordingSnapshotService(_SnapshotDTO(id="old", state="active", cpu=4, mem=8, disk=8))
    first = snapshot_module.preflight(service, api_key="key", org_id="org")
    service.result = _SnapshotDTO(id="new", state="active", cpu=4, mem=8, disk=8)
    now[0] += 59
    assert snapshot_module.preflight(service, api_key="key", org_id="org") is first
    now[0] += 1
    assert snapshot_module.preflight(service, api_key="key", org_id="org").id == "new"
    assert len(service.calls) == 2


@pytest.mark.parametrize("operation", ["clear", "refresh"])
@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_new_preflight_does_not_join_or_get_overwritten_by_old_flight(operation, old_failure) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class Service:
        def get(self, name: str) -> Any:
            calls.append(name)
            if len(calls) == 1:
                started.set()
                assert release.wait(5)
                if old_failure:
                    raise TimeoutError("old lookup failed")
                return _SnapshotDTO(id="old", state="active", cpu=4, mem=8, disk=8)
            return _SnapshotDTO(id="new", state="active", cpu=4, mem=8, disk=8)

    service = Service()
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        assert started.wait(5)
        try:
            if operation == "clear":
                snapshot_module.clear_preflight_cache()
            current = snapshot_module.preflight(service, api_key="key", org_id="org", refresh=operation == "refresh")
            assert current.id == "new"
        finally:
            release.set()
        if old_failure:
            with pytest.raises(snapshot_module.SnapshotUnavailableError):
                first.result(timeout=5)
        else:
            assert first.result(timeout=5).id == "old"
    assert snapshot_module.preflight(service, api_key="key", org_id="org") is current
    assert len(calls) == 2


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_concurrent_failed_preflight_releases_waiters_and_can_retry(monkeypatch) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    started = threading.Event()
    joined = threading.Event()
    release = threading.Event()

    class Service:
        calls = 0

        def get(self, _name: str) -> Any:
            self.calls += 1
            if self.calls == 1:
                started.set()
                assert release.wait(5)
                raise TimeoutError("provider unavailable")
            return _SnapshotDTO(id="retry", state="active", cpu=4, mem=8, disk=8)

    service = Service()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        assert started.wait(5)
        flight = next(iter(snapshot_module._INFLIGHT.values()))
        original_wait = flight.event.wait

        def observe_wait(timeout=None):
            joined.set()
            return original_wait(timeout)

        monkeypatch.setattr(flight.event, "wait", observe_wait)
        second = pool.submit(snapshot_module.preflight, service, api_key="key", org_id="org")
        try:
            assert joined.wait(5)
        finally:
            release.set()
        for future in (first, second):
            with pytest.raises(snapshot_module.SnapshotUnavailableError, match="Check Daytona access"):
                future.result(timeout=5)
    assert service.calls == 1
    assert snapshot_module.preflight(service, api_key="key", org_id="org").id == "retry"


@pytest.mark.parametrize("status", [401, 404, 500])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_classifies_daytona_errors_without_exposing_the_provider_message(status) -> None:

    from daytona import DaytonaError, DaytonaNotFoundError

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    error_class = DaytonaNotFoundError if status == 404 else DaytonaError
    service = _RecordingSnapshotService(error=error_class("private provider detail", status_code=status))
    with pytest.raises(snapshot_module.SnapshotUnavailableError) as raised:
        snapshot_module.preflight(service, api_key="key", org_id="org")
    assert "private provider detail" not in str(raised.value)
    assert ("state=missing" in str(raised.value)) is (status == 404)


@pytest.mark.parametrize(("cpu", "mem", "disk"), [(3, 8, 8), (4, 6, 8), (4, 8, 7)])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_preflight_resources_enforce_floors(monkeypatch, tmp_path, cpu, mem, disk) -> None:
    from fleet_rlm.sandbox.snapshots import BUILD_COMMAND, SNAPSHOT_NAME, SnapshotUnavailableError

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=cpu, mem=mem, disk=disk))
    manager, client = _provision(monkeypatch, tmp_path, service)
    with pytest.raises(SnapshotUnavailableError) as error:
        manager.ensure_sandbox()
    message = str(error.value)
    assert SNAPSHOT_NAME in message
    assert f"cpu={cpu}" in message and f"mem={mem}" in message and f"disk={disk}" in message
    assert "cpu>=4" in message and "mem>=8" in message and "disk>=8" in message
    assert BUILD_COMMAND in message
    assert client.created == []

    boundary, boundary_client = _provision(
        monkeypatch,
        tmp_path,
        _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)),
    )
    assert boundary.ensure_sandbox().id == "sandbox-1"
    assert len(boundary_client.created) == 1


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_concurrent_preflight_shares_single_inflight_lookup() -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    first_started = threading.Event()
    second_arrived = threading.Event()
    calls: list[str] = []
    dto = _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)

    class BlockingService:
        def get(self, name: str):
            calls.append(name)
            if len(calls) == 1:
                first_started.set()
                assert second_arrived.wait(5)
            return dto

    service = BlockingService()

    def second_racer():
        assert first_started.wait(5)
        second_arrived.set()
        return snapshot_module.preflight(service, name=snapshot_module.SNAPSHOT_NAME, api_key="key", org_id="org")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            snapshot_module.preflight, service, name=snapshot_module.SNAPSHOT_NAME, api_key="key", org_id="org"
        )
        second = pool.submit(second_racer)
        assert first.result(timeout=5) is second.result(timeout=5)
    assert calls == [snapshot_module.SNAPSHOT_NAME]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_build_and_verify_snapshot_credential_resolution(monkeypatch, tmp_path) -> None:

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    monkeypatch.chdir(tmp_path)
    for name in ("FLEET_DAYTONA_API_KEY", "FLEET_DAYTONA_ORG_ID", "DAYTONA_API_KEY", "DAYTONA_ORG_ID", "FLEET_CONFIG"):
        monkeypatch.delenv(name, raising=False)

    instances: list[Any] = []
    creations: list[tuple[Any, dict[str, Any]]] = []

    class FakeDaytona:
        def __init__(self, config: Any) -> None:
            self.config = config
            self._before = len(creations)
            self.snapshot = SimpleNamespace(
                get=lambda _name: (
                    _SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8)
                    if len(creations) > self._before
                    else None
                ),
                create=lambda params, **kwargs: creations.append((params, kwargs)),
            )
            instances.append(self)

    monkeypatch.setattr(snapshot_module, "Daytona", FakeDaytona)

    with pytest.raises(ValueError, match="FLEET_DAYTONA_API_KEY"):
        snapshot_module.build_snapshot()

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "fleet-key")
    monkeypatch.setenv("FLEET_DAYTONA_ORG_ID", "fleet-org")
    identity = snapshot_module.build_snapshot(api_key="  explicit-key  ", org_id="  explicit-org  ")
    assert identity.name == snapshot_module.SNAPSHOT_NAME
    assert instances[-1].config.api_key == "explicit-key"
    assert instances[-1].config.organization_id == "explicit-org"
    assert creations[-1][1]["timeout"] == 1200
    assert callable(creations[-1][1]["on_logs"])
    recipe = creations[-1][0].image.dockerfile()
    assert "@sha256:" in recipe and "--require-hashes" in recipe
    assert "pnpm@" in recipe and "typescript@" in recipe

    snapshot_module.build_snapshot(api_key="explicit-key", org_id="explicit-org", timeout_seconds=45, on_logs=None)
    assert creations[-1][1] == {"timeout": 45, "on_logs": None}

    with pytest.raises(ValueError, match="FLEET_DAYTONA_API_KEY"):
        snapshot_module.build_snapshot(api_key="   ", org_id="explicit-org")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_build_snapshot_rejects_unbounded_or_invalid_timeouts_before_contacting_daytona(timeout, monkeypatch):

    snapshot_module = importlib.import_module("fleet_rlm.sandbox.snapshots")

    monkeypatch.setattr(snapshot_module, "Daytona", lambda *_args: pytest.fail("No provider call expected"))
    with pytest.raises(ValueError, match="finite and positive"):
        snapshot_module.build_snapshot(timeout_seconds=timeout)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_and_snapshot_provisioning_skips_turn_preflight(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()

    child_service = _RecordingSnapshotService(_SnapshotDTO(id="child-snap", state="active", cpu=2, mem=4, disk=8))
    child_client = _RecordingClient(child_service)
    child = DaytonaSandboxManager(
        "key",
        "org",
        "volume",
        "session-a",
        parent=root,
        snapshot="fleet-rlm-python313-child-v5",
    )
    child._client = child_client

    assert child.ensure_sandbox().id == "sandbox-1"
    assert [params.snapshot for params in child_client.created] == ["fleet-rlm-python313-child-v5"]
    assert child.snapshot_identity is None
    assert child_service.calls == []
    assert service.calls == [SNAPSHOT_NAME]
    assert root_client.created[0].snapshot == SNAPSHOT_NAME


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_snapshot_provenance_is_the_preflight_identity(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.snapshots import DEFAULT_DEFINITION, SNAPSHOT_NAME

    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    manager, _client = _provision(monkeypatch, tmp_path, service)
    assert manager.snapshot_provenance() == {}, "nothing is recorded before the provider resolves the snapshot"

    manager.ensure_sandbox()

    assert manager.snapshot_provenance() == {
        "snapshot_name": SNAPSHOT_NAME,
        "snapshot_id": "snap-1",
        "snapshot_cpu": 4,
        "snapshot_mem": 8,
        "snapshot_disk": 8,
        "snapshot_definition_digest": DEFAULT_DEFINITION.digest(),
    }
    assert service.calls == [SNAPSHOT_NAME]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_snapshot_resolution_callback_fires_before_provisioning(monkeypatch, tmp_path) -> None:
    """A caller records provenance the moment preflight resolves it, before creating anything."""
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    observed: dict[str, Any] = {}

    def record(identity: Any) -> None:
        observed["identity"] = identity
        observed["created_when_resolved"] = list(client.created)
        observed["provenance_when_resolved"] = manager.snapshot_provenance()

    manager, client = _provision(monkeypatch, tmp_path, service, snapshot_resolved_callback=record)
    assert manager.snapshot_provenance() == {}

    manager.ensure_sandbox()

    assert observed["created_when_resolved"] == [], "the identity is published before the provider create"
    assert observed["identity"] is manager.snapshot_identity
    assert observed["provenance_when_resolved"] == manager.snapshot_provenance()
    assert observed["provenance_when_resolved"]["snapshot_id"] == "snap-1"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_snapshot_resolution_never_invokes_the_callback(monkeypatch, tmp_path) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, _root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()
    resolved: list[Any] = []
    child = DaytonaSandboxManager(
        "key",
        "org",
        "volume",
        "session-a",
        parent=root,
        snapshot_resolved_callback=resolved.append,
    )
    child._client = _RecordingClient(_RecordingSnapshotService())

    child.ensure_sandbox()

    assert resolved == [], "a child inherits the Turn snapshot and resolves no identity of its own"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_snapshot_provenance_is_not_invented(monkeypatch, tmp_path) -> None:
    service = _RecordingSnapshotService(_SnapshotDTO(id="snap-1", state="active", cpu=4, mem=8, disk=8))
    root, _root_client = _provision(monkeypatch, tmp_path, service)
    root.ensure_sandbox()
    child = DaytonaSandboxManager("key", "org", "volume", "session-a", parent=root)
    child._client = _RecordingClient(_RecordingSnapshotService())
    child.ensure_sandbox()
    assert child.snapshot_provenance() == {}
    assert root.snapshot_provenance()["snapshot_name"]


def snapshot_error_is_single_error(message: str) -> bool:
    return message.count("Snapshot '") == 1


def _ready_manager(*, deadline: float | None, turn_timeout_seconds: int = 900) -> DaytonaSandboxManager:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-bound", turn_timeout_seconds=turn_timeout_seconds)
    manager._client = object()
    manager._sandbox = SimpleNamespace(id="sandbox-bound", state="started")
    manager._contexts = {"ns": object()}
    manager._deadline = deadline
    return manager


def _ready_interpreter() -> Any:
    return SimpleNamespace(ns_id="ns", answer_host_call=lambda _call, **_kwargs: {"value": 1})


def _record_host_call_bound(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:

    captured: dict[str, Any] = {}

    def fake_run(_sandbox, _context, _payload, **kwargs):
        captured.update(kwargs)
        return {"stdout": "", "stderr": "", "final": None, "has_value": False, "value": None}

    monkeypatch.setattr(repl_module, "run", fake_run)
    return captured


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_the_host_call_bound_is_the_remaining_turn_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert 0 < captured["timeout"] <= 120, "the bound follows the deadline, not a fixed cutoff"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_deadline_free_turn_sends_no_host_call_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=None, turn_timeout_seconds=0)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured["timeout"] is None, "a deadline-free Turn must not invent a positive bound"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_an_expired_deadline_stops_the_turn_instead_of_waiting() -> None:
    manager = _ready_manager(deadline=time.monotonic() - 5)
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_run_namespace_opens_one_execution_token_and_closes_it_when_the_execution_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    captured = _record_host_call_bound(monkeypatch)
    observed: list[bool] = []

    def fake_run(_sandbox, _context, _payload, **kwargs):
        observed.append(kwargs["fence"].is_open())
        captured.update(kwargs)
        return {"stdout": "", "stderr": "", "final": None, "has_value": False, "value": None}

    monkeypatch.setattr(repl_module, "run", fake_run)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert observed == [True], "a live execution may publish"
    assert not captured["fence"].is_open(), "the execution's window ends with the execution"
    assert manager._execution_fences == set()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_cancelled_execution_token_is_never_reopened_by_the_next_turn() -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: None)

    # An execution that is still in flight when the Turn is cancelled keeps its token for good.
    abandoned = manager._open_execution_fence()
    assert abandoned.is_open()

    manager.cancel_turn()
    manager.reset_turn_budget()
    try:
        assert not manager._cancelled.is_set(), "the next Turn clears the shared cancellation Event"
        assert not abandoned.is_open(), "the originating execution stays fenced after the reset"

        live = manager._open_execution_fence()
        assert live is not abandoned and live.is_open(), "a new execution gets its own live token"
        manager._release_execution_fence(live)
    finally:
        manager.end_turn()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_an_abandoned_worker_cannot_publish_after_cancellation_and_the_next_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:

    manager = _ready_manager(deadline=time.monotonic() + 120)
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: None)

    class RecordingFs:
        def __init__(self) -> None:
            self.uploads: list[str] = []

        def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
            self.uploads.append(path)

    sandbox = SimpleNamespace(fs=RecordingFs())
    abandoned = manager._open_execution_fence()
    protocol = repl_module._Protocol(sandbox, lambda _call: {"value": "late answer"}, fence=abandoned)

    # The cancelled Turn acknowledges and the next Turn resets the reusable cancellation Event.
    manager.cancel_turn()
    manager.reset_turn_budget()
    try:
        with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.repl"):
            protocol._answer({"id": "call-abandoned"})
        assert sandbox.fs.uploads == [], "a previous execution cannot publish into the next Turn"
        assert len([record for record in caplog.records if "Fenced" in record.getMessage()]) == 1
        assert "late answer" not in caplog.text
    finally:
        manager.end_turn()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_late_fence_is_attributed_to_the_execution_that_opened_it(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120)
    originating: list[dict[str, Any]] = []
    later: list[dict[str, Any]] = []
    manager.event_callback = originating.append

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    # The next Turn installs its own emitter and clears the shared cancellation state.
    manager.reset_turn_budget()
    manager.event_callback = later.append
    try:
        captured["on_fence"]()
    finally:
        manager.event_callback = None

    assert [event["event"] for event in originating] == ["fenced"]
    assert originating[0]["namespace"] == "ns"
    assert later == [], "a late fence never surfaces on the current Turn's stream"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_fence_whose_channel_is_closed_logs_one_bounded_warning(caplog: pytest.LogCaptureFixture) -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)

    def closed(_event: dict[str, Any]) -> None:
        raise RuntimeError("stream is closed")

    manager.event_callback = closed
    fence = manager._open_execution_fence()
    with caplog.at_level("WARNING", logger="fleet_rlm.sandbox.manager"):
        manager._report_fence(fence, "ns")  # must not raise into the host-call worker

    warnings = [record for record in caplog.records if "could not be delivered" in record.getMessage()]
    assert len(warnings) == 1
    assert "stream is closed" not in caplog.text


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_live_execution_token_allows_a_successful_publication() -> None:

    class RecordingFs:
        def __init__(self) -> None:
            self.uploads: list[str] = []

        def upload_file(self, data: bytes, path: str, timeout: int | None = None) -> None:  # noqa: ARG002
            self.uploads.append(path)

    sandbox = SimpleNamespace(fs=RecordingFs())
    protocol = repl_module._Protocol(sandbox, lambda _call: {"value": 1}, fence=repl_module.ExecutionFence())

    protocol._answer({"id": "call-live"})

    assert sandbox.fs.uploads == ["/tmp/fleet/replies/call-live.json"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_deadline_free_turn_arms_no_watchdog_and_owns_no_deadline() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-deadline-free", turn_timeout_seconds=0)
    manager.reset_turn_budget(deadline_seconds=None, deadline_source="request")
    try:
        assert manager._watchdog is None, "Timer(0) must be impossible"
        assert manager._deadline is None
        assert manager.turn_active is True, "a deadline-free Turn still owns itself"
        assert not manager._cancelled.is_set(), "a deadline-free Turn is not cancelled at start"
        assert manager.deadline_provenance() == {"deadline_source": "request", "effective_seconds": None}
        assert manager.remaining_turn_seconds() is None
    finally:
        manager.end_turn()
    assert manager.turn_active is False


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_deadline_inherits_the_parents_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _ready_manager(deadline=time.monotonic() + 120)
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: None)
    monkeypatch.setattr(DaytonaSandboxManager, "list_workspace_files", lambda _self: [])

    child = manager._allocate_child()
    grandchild = child._allocate_child()

    assert child._deadline == manager._deadline, "a child never receives a longer deadline"
    assert grandchild._deadline == manager._deadline

    manager._deadline = None
    deadline_free_child = manager._allocate_child()
    assert deadline_free_child._deadline is None


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_stale_watchdog_cannot_cancel_a_later_turn() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-stale-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    first_generation = manager._turn_generation
    first_watchdog = manager._watchdog
    assert first_watchdog is not None
    try:
        manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
        assert manager._turn_generation > first_generation
        # Fire the previous Turn's callback exactly as its timer thread would.
        first_watchdog.function()
        assert not manager._cancelled.is_set(), "a stale watchdog is a no-op for the current Turn"
    finally:
        manager.end_turn()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_watchdog_cancellation_cannot_cross_a_turn_reset(monkeypatch: pytest.MonkeyPatch) -> None:

    manager = DaytonaSandboxManager("key", "org", "volume", "session-racing-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    generation = manager._turn_generation
    entered = threading.Event()
    release = threading.Event()
    original = DaytonaSandboxManager.request_cancellation

    def cancel(self: DaytonaSandboxManager) -> bool:
        entered.set()
        assert release.wait(5)
        return original(self)

    monkeypatch.setattr(DaytonaSandboxManager, "request_cancellation", cancel)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            watchdog = pool.submit(manager._expire_generation, generation)
            try:
                assert entered.wait(5)
                reset = pool.submit(manager.reset_turn_budget, deadline_seconds=None)
                with pytest.raises(TimeoutError):
                    reset.result(timeout=0.05)
            finally:
                release.set()
            watchdog.result(timeout=5)
            reset.result(timeout=5)
        assert manager._turn_generation > generation
        assert manager.turn_active
        assert not manager._cancelled.is_set(), "the watchdog belongs only to the preceding Turn"
    finally:
        manager.end_turn()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_watchdog_cannot_cancel_a_turn_that_already_ended() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-ended-watchdog")
    manager.reset_turn_budget(deadline_seconds=900, deadline_source="request")
    watchdog = manager._watchdog
    assert watchdog is not None
    manager.end_turn()

    # Timer.cancel() cannot revoke a callback that already started on its thread.
    watchdog.function()

    assert not manager._cancelled.is_set(), "a finished Turn must keep its warm root"
    assert manager.cleanup_outcome is None


@pytest.mark.parametrize(("remaining", "expected"), [(None, None), (120.9, 120), (1.5, 1)])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_host_call_bounds_truncate_whole_seconds(remaining: float | None, expected: int | None) -> None:
    from fleet_rlm.sandbox.manager import _host_call_bound

    assert _host_call_bound(remaining) == expected


@pytest.mark.parametrize("remaining", [0.4, 0.0, -2.0])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_sub_second_remainder_never_becomes_a_host_call_bound(remaining: float) -> None:
    from fleet_rlm.sandbox.manager import _host_call_bound

    # 0 means "no timeout" on the transport, so it cannot stand in; any whole positive second is
    # wider than the remainder. The helper must refuse both instead of widening the deadline.
    with pytest.raises(ValueError, match="one whole second"):
        _host_call_bound(remaining)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_sub_second_remainder_stops_the_turn_before_any_host_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 0.4)

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured == {}, "no execution and therefore no host call may start"
    assert manager._cancelled.is_set(), "expiry marks the Turn cancelled, so it is stored as cancelled"
    with pytest.raises(RuntimeError, match="Turn cancelled"):
        manager.check_turn()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_whole_second_remainder_still_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=time.monotonic() + 120.9)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured["timeout"] == 120, "whole seconds truncate; the bound never exceeds the remainder"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_threshold_straddling_clock_is_a_deadline_cancellation_not_a_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    # The two former reads: admission sees 1.5 s left and passes, the bound samples 0.4 s
    # later. A single sample must drive both, so the Turn is a deadline cancellation and
    # no execution, host call, ValueError, or transport uncertainty is produced.
    captured = _record_host_call_bound(monkeypatch)
    manager = _ready_manager(deadline=101.5)
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(DaytonaSandboxManager, "remaining_turn_seconds", lambda _self: 0.4)

    with pytest.raises(TimeoutError, match="deadline exceeded"):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert captured == {}, "a Turn without a representable bound starts no execution or host call"
    assert manager._cancelled.is_set(), "expiry marks the Turn cancelled, so it is stored as cancelled"
    with pytest.raises(RuntimeError, match="Turn cancelled"):
        manager.check_turn()


@pytest.mark.parametrize("sampled", [1.0, 1.5])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_one_remainder_sample_drives_both_admission_and_the_bound(
    monkeypatch: pytest.MonkeyPatch, sampled: float
) -> None:
    captured = _record_host_call_bound(monkeypatch)
    samples: list[float] = []

    def sample(_self: DaytonaSandboxManager) -> float:
        samples.append(sampled)
        return sampled

    monkeypatch.setattr(DaytonaSandboxManager, "remaining_turn_seconds", sample)
    manager = _ready_manager(deadline=time.monotonic() + 120)

    manager.run_namespace(_ready_interpreter(), {"code": "1"})

    assert samples == [sampled], "the remainder is sampled once per execution"
    assert captured["timeout"] == 1, "at least one second remains representable and truncates to one"


def _record_terminal_cancellations(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Count every cancel_turn() invocation without changing the teardown it performs."""
    invocations: list[str] = []
    original = DaytonaSandboxManager.cancel_turn

    def recording(self: DaytonaSandboxManager) -> None:
        invocations.append("cancel_turn")
        original(self)

    monkeypatch.setattr(DaytonaSandboxManager, "cancel_turn", recording)
    return invocations


def _cancellable_manager(*, turn_timeout_seconds: int = 900) -> tuple[DaytonaSandboxManager, list[str]]:
    manager = DaytonaSandboxManager("key", "org", "volume", "single-cancel", turn_timeout_seconds=turn_timeout_seconds)
    stops: list[str] = []
    manager._sandbox = SimpleNamespace(id="sandbox-single-cancel", state="started")
    manager._contexts = {"ns": object()}
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: stops.append("stop"))
    return manager, stops


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_an_unknown_execution_outcome_issues_exactly_one_terminal_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()

    def unknown_outcome(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise repl_module.ExecutionOutcomeUnknownError("transport lost after execution started")

    monkeypatch.setattr(repl_module, "run", unknown_outcome)

    with pytest.raises(repl_module.ExecutionOutcomeUnknownError):
        manager.run_namespace(_ready_interpreter(), {"code": "1"})
    # The engine's terminal branch arrives after the transport path already claimed teardown.
    assert manager.request_cancellation() is False

    assert invocations == ["cancel_turn"], "an unknown outcome invokes cancel_turn exactly once"
    assert stops == ["stop"], "the provider teardown still runs once"
    assert manager.cleanup_outcome == "stopped"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_concurrent_terminal_cancellation_callers_cannot_both_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, _stops = _cancellable_manager()
    stop_entered = threading.Event()
    release_stop = threading.Event()
    stops: list[str] = []

    def slow_stop(*_args: Any, **_kwargs: Any) -> None:
        stop_entered.set()
        assert release_stop.wait(timeout=5)
        stops.append("stop")

    manager._client = SimpleNamespace(stop=slow_stop)
    outcomes: list[bool] = []

    def caller() -> None:
        outcomes.append(manager.request_cancellation())

    owner = threading.Thread(target=caller, name="cancel-owner")
    joiner = threading.Thread(target=caller, name="cancel-joiner")
    owner.start()
    assert stop_entered.wait(timeout=5)
    joiner.start()
    # The joiner must wait for the owner's provider stop rather than start a second teardown.
    assert invocations == ["cancel_turn"]
    release_stop.set()
    owner.join(timeout=5)
    joiner.join(timeout=5)

    assert not owner.is_alive() and not joiner.is_alive()
    assert sorted(outcomes) == [False, True], "exactly one caller owns the teardown"
    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_late_cancellation_caller_after_acknowledgment_does_not_cancel_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()

    assert manager.request_cancellation() is True
    # A watchdog, disconnect, or terminal branch arriving after acknowledgment finds it taken.
    assert manager.request_cancellation() is False

    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]
    assert manager.cleanup_outcome == "stopped"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_the_deadline_watchdog_claims_the_same_single_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()
    manager.reset_turn_budget()
    try:
        watchdog = manager._watchdog
        assert watchdog is not None
        # Fire the armed callback exactly as the timer thread would.
        watchdog.function()
        # The terminal Turn branch joining afterwards must not issue a second cancellation.
        assert manager.request_cancellation() is False
    finally:
        manager.end_turn()

    assert invocations == ["cancel_turn"]
    assert stops == ["stop"]
    assert manager.cleanup_outcome == "stopped"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_a_new_turn_owns_a_new_teardown_after_a_previous_acknowledgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invocations = _record_terminal_cancellations(monkeypatch)
    manager, stops = _cancellable_manager()
    manager.reset_turn_budget()
    manager.end_turn()

    assert manager.request_cancellation() is True
    assert manager.request_cancellation() is False

    manager.reset_turn_budget()
    # The previous teardown discarded its root, so a cold-resumed Turn hands over a new handle.
    manager._sandbox = SimpleNamespace(id="sandbox-next-turn", state="started")
    try:
        assert manager.cleanup_outcome is None
        assert manager.request_cancellation() is True
    finally:
        manager.end_turn()

    assert invocations == ["cancel_turn", "cancel_turn"], "each Turn owns exactly one invocation"
    assert stops == ["stop", "stop"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_provider_bounds_are_reported_as_provider_owned_and_never_rewritten() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")
    manager._sandbox = SimpleNamespace(
        id="sandbox-1",
        ttl_minutes=1440,
        auto_stop_interval=60,
        maximum_lifespan_minutes=2880,
    )

    bounds = manager.provider_bounds()

    assert bounds == {
        "ttl_minutes": 1440,
        "auto_stop_interval": 60,
        "maximum_lifespan_minutes": 2880,
        "provider_owned": True,
    }


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_provider_bounds_report_requested_ttl_and_an_unknown_lifespan_before_provisioning() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager.provider_bounds() == {
        "ttl_minutes": 120,
        "auto_stop_interval": 30,
        "maximum_lifespan_minutes": None,
        "provider_owned": True,
    }
    child = DaytonaSandboxManager("key", "org", "volume", "session-a", parent=manager)
    assert child.provider_bounds()["ttl_minutes"] == 30


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_turn_scope_owns_an_unowned_turn_and_is_inert_inside_one() -> None:
    manager = DaytonaSandboxManager("key", "org", "volume", "session-scope")

    with manager.turn_scope(deadline_seconds=5, deadline_source="request"):
        assert manager.turn_active
        assert manager.deadline_provenance()["effective_seconds"] == 5
    assert not manager.turn_active

    # A Turn an outer owner started stays that owner's: a nested scope neither resets nor ends it.
    manager.reset_turn_budget(deadline_seconds=None, deadline_source="request")
    try:
        with manager.turn_scope(deadline_seconds=5, deadline_source="request"):
            assert manager.turn_active
            assert manager.deadline_provenance()["effective_seconds"] is None
        assert manager.turn_active
    finally:
        manager.end_turn()
    assert not manager.turn_active


# Snapshot operations


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_existing_snapshot_is_never_replaced(monkeypatch):
    service = SimpleNamespace(
        get=lambda _name: SimpleNamespace(id="existing"),
        create=lambda *_a, **_kw: pytest.fail("must not replace existing snapshot"),
    )
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError, match="already exists"):
        operations.build_snapshot(api_key="key", org_id="org")


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_lookup_failure_does_not_turn_into_snapshot_creation(monkeypatch):
    def unavailable(_name):
        raise TimeoutError("private-provider-detail")

    service = SimpleNamespace(get=unavailable, create=lambda *_a, **_kw: pytest.fail("unknown lookup is not missing"))
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError) as error:
        operations.build_snapshot(api_key="key", org_id="org")
    assert "private-provider-detail" not in str(error.value)
    assert error.value.state == "lookup_failed"


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_activation_runs_once_then_verifies_provider_identity(monkeypatch):
    state = ["inactive"]
    calls = []
    timeouts = []

    def get_snapshot(name, *, _request_timeout):
        timeouts.append(_request_timeout)
        return SimpleNamespace(name=name, id="provider-id", state=state[0], cpu=4, mem=8, disk=8)

    def activate(snapshot_id, *, _request_timeout):
        calls.append(snapshot_id)
        timeouts.append(_request_timeout)
        state[0] = "active"
        return SimpleNamespace(name=SNAPSHOT_NAME, id=snapshot_id, state=state[0], cpu=4, mem=8, disk=8)

    service = SimpleNamespace(
        get=lambda name: SimpleNamespace(name=name, id="provider-id", state=state[0], cpu=4, mem=8, disk=8),
        _SnapshotService__snapshots_api=SimpleNamespace(
            get_snapshot=get_snapshot,
            activate_snapshot=activate,
        ),
    )
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    identity = operations.activate_snapshot(api_key="key", org_id="org")
    assert calls == ["provider-id"]
    assert len(timeouts) == 3
    assert timeouts[0] > timeouts[1] > timeouts[2] > 0
    assert identity.id == "provider-id"
    # A second explicit activation only verifies the already active snapshot.
    assert operations.activate_snapshot(api_key="key", org_id="org").id == "provider-id"
    assert calls == ["provider-id"]


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_activation_does_not_create_a_missing_snapshot(monkeypatch):
    def missing(_name):
        raise DaytonaNotFoundError("missing")

    service = SimpleNamespace(get=missing, activate=lambda *_a: pytest.fail("missing snapshot cannot be activated"))
    monkeypatch.setattr(operations, "Daytona", lambda _config: SimpleNamespace(snapshot=service))
    with pytest.raises(SnapshotUnavailableError) as error:
        operations.activate_snapshot(api_key="key", org_id="org")
    assert error.value.state == "missing"


# Safe workspace paths


@pytest.mark.parametrize("session_id", ["finance-q4", "session_01", "2e8c98b5-5242-4e3b-91b4-2bf1e8c24f0b"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_session_ids_map_to_isolated_volume_subpaths(session_id: str) -> None:
    validate_session_id(session_id)

    assert session_volume_subpath(session_id) == f"sessions/{session_id}"


@pytest.mark.parametrize("session_id", ["../other", "a/b", "/root", "", ".", "..", "bad id"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_session_ids_reject_volume_path_syntax(session_id: str) -> None:
    with pytest.raises(ValueError):
        validate_session_id(session_id)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_paths_are_rooted_and_have_bounded_names() -> None:
    assert _workspace_relative_path("files/quarter-1.csv") == "files/quarter-1.csv"
    for path in ("/etc/passwd", "files/../../secret", "skills\\escape", "files/a\x00b", "files/" + "a" * 1020):
        with pytest.raises(ValueError):
            _workspace_relative_path(path)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("files/a.log", "files/a.log"),
        ("/workspace/files/a.log", "files/a.log"),
        ("skills/x/SKILL.md", "skills/x/SKILL.md"),
        ("/workspace/artifacts/report.json", "artifacts/report.json"),
    ],
)
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_input_paths_accept_the_sandbox_absolute_form(given: str, expected: str) -> None:
    from fleet_rlm.workspace import _child_input_path

    assert _child_input_path(given) == expected


@pytest.mark.parametrize("bad", ["/workspace/memory/x", "/etc/passwd", "/workspace/files/../memory/x", "files/../x"])
@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_child_input_paths_still_reject_escapes_and_other_roots(bad: str) -> None:
    from fleet_rlm.workspace import _child_input_path

    with pytest.raises(ValueError):
        _child_input_path(bad)


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_generated_remote_scripts_compile():
    """Remote script builders must emit Python, not a string that merely looks like it.

    The transport base64-encodes and `compile`s these, so a literal backslash-n is a
    SyntaxError that only shows up against a real sandbox. Compile them here instead.
    """
    from fleet_rlm.sandbox.lifecycle import CLEAR_WORKSPACE_CODE
    from fleet_rlm.workspace import (
        list_workspace_files_code,
        verify_python_artifact_code,
        workspace_files_metadata_code,
    )

    scripts = {
        "clear": CLEAR_WORKSPACE_CODE,
        "list": list_workspace_files_code(),
        "metadata": workspace_files_metadata_code(["files/a.txt"], create_parents=False),
        "verify": verify_python_artifact_code("artifacts/run.py", digest="0" * 64, timeout=5),
    }
    for name, code in scripts.items():
        compile(code, f"<{name}>", "exec")


def _install_emulated_daytona(monkeypatch, tmp_path: Path):
    """Stub Daytona client and workspace I/O over isolated per-agent emulated sandboxes."""
    from fleet_rlm.sandbox import manager as manager_module

    sandboxes = []

    def create_sandbox(params, **_kwargs):
        agent_id = (params.labels or {}).get("fleet_agent", f"sb-{len(sandboxes)}")
        sb = emulated_sandbox(tmp_path / agent_id)
        sb.id = f"sb-{agent_id}"
        sandboxes.append(sb)
        return sb

    def ws_root(mgr) -> Path:
        return mgr.ensure_sandbox().code_interpreter.root / "workspace"

    monkeypatch.setattr(manager_module, "preflight", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        manager_module,
        "_shared_daytona_client",
        lambda *_args: SimpleNamespace(
            snapshot=SimpleNamespace(),
            create=create_sandbox,
            stop=lambda *_a, **_kw: None,
            delete=lambda *_a, **_kw: None,
        ),
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "list_workspace_files",
        lambda mgr: sorted(p.relative_to(ws_root(mgr)).as_posix() for p in ws_root(mgr).rglob("*") if p.is_file()),
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_files",
        lambda mgr, paths, **_kw: {p: (ws_root(mgr) / p).read_bytes() for p in paths},
    )
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "read_workspace_file",
        lambda mgr, path, **_kw: (ws_root(mgr) / path).read_bytes(),
    )

    def write_files(mgr, files) -> None:
        root = ws_root(mgr)
        for rel, data in files.items():
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

    monkeypatch.setattr(DaytonaSandboxManager, "write_workspace_files", write_files)
    return sandboxes


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_native_flex_nested_child_records_succeeded_even_across_nested_thread_and_skipped_promotion(
    monkeypatch, tmp_path
) -> None:
    from dspy.primitives.facade import SandboxSession

    sandboxes = _install_emulated_daytona(monkeypatch, tmp_path)
    manager = DaytonaSandboxManager("key", "org", "vol", "sess-flex-child", turn_timeout_seconds=0)
    with manager.turn_scope():
        try:
            parent = DaytonaInterpreter(manager, owns_manager=False)
            session = SandboxSession({}, interpreter_factory=manager.interpreter_factory())
            session.install(parent, import_dspy=False)
            parent.start()
            try:
                handle = parent.answer_host_call(
                    {"id": "c1", "tool": "__dspy_interpreter__", "args": ["create"], "kwargs": {}}
                )["value"]
                assert isinstance(handle, str)
                exec_reply = parent.answer_host_call(
                    {
                        "id": "c2",
                        "tool": "__dspy_interpreter__",
                        "args": [],
                        "kwargs": {
                            "op": "execute",
                            "id": handle,
                            "code": "workspace.write_artifact('nested.txt', 'ok')\nSUBMIT(findings='ok')",
                            "variables": {},
                            "tools": {},
                            "output_fields": [{"name": "findings", "type": "str"}],
                        },
                    }
                )["value"]
                assert exec_reply["final"] == {"findings": "ok"}

                parent.answer_host_call(
                    {"id": "c3", "tool": "__dspy_interpreter__", "args": ["shutdown", handle], "kwargs": {}}
                )

                assert len(manager._child_receipts) == 1
                receipt = next(iter(manager._child_receipts.values()))
                assert receipt["status"] == "succeeded"
                assert any(row["path"] == f"artifacts/{receipt['agent_id']}/nested.txt" for row in receipt["artifacts"])

                # Decoupled outcome recording: even if artifact promotion is stubbed/skipped, clean completion succeeds.
                monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])
                handle2 = parent.answer_host_call(
                    {"id": "c4", "tool": "__dspy_interpreter__", "args": ["create"], "kwargs": {}}
                )["value"]
                parent.answer_host_call(
                    {
                        "id": "c5",
                        "tool": "__dspy_interpreter__",
                        "args": [],
                        "kwargs": {
                            "op": "execute",
                            "id": handle2,
                            "code": "SUBMIT(findings='ok2')",
                            "variables": {},
                            "tools": {},
                            "output_fields": [{"name": "findings", "type": "str"}],
                        },
                    }
                )
                parent.answer_host_call(
                    {"id": "c6", "tool": "__dspy_interpreter__", "args": ["shutdown", handle2], "kwargs": {}}
                )
                receipts = list(manager._child_receipts.values())
                assert len(receipts) == 2
                assert receipts[1]["status"] == "succeeded"
            finally:
                session.close()
                parent.shutdown()
        finally:
            manager.shutdown()
            for sb in sandboxes:
                sb.code_interpreter.close()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_native_flex_nested_child_failure_keeps_failed_status_and_never_promotes(monkeypatch, tmp_path) -> None:
    from dspy.primitives.facade import SandboxSession

    sandboxes = _install_emulated_daytona(monkeypatch, tmp_path)
    manager = DaytonaSandboxManager("key", "org", "vol", "sess-flex-fail", turn_timeout_seconds=0)
    with manager.turn_scope():
        try:
            parent = DaytonaInterpreter(manager, owns_manager=False)
            session = SandboxSession({}, interpreter_factory=manager.interpreter_factory())
            session.install(parent, import_dspy=False)
            parent.start()
            try:
                handle = parent.answer_host_call(
                    {"id": "c1", "tool": "__dspy_interpreter__", "args": ["create"], "kwargs": {}}
                )["value"]
                parent.answer_host_call(
                    {
                        "id": "c2",
                        "tool": "__dspy_interpreter__",
                        "args": [],
                        "kwargs": {
                            "op": "execute",
                            "id": handle,
                            "code": "workspace.write_artifact('bad.txt', 'unsafe')\nraise RuntimeError('boom')",
                            "variables": {},
                            "tools": {},
                        },
                    }
                )
                parent.answer_host_call(
                    {"id": "c3", "tool": "__dspy_interpreter__", "args": ["shutdown", handle], "kwargs": {}}
                )

                receipt = next(iter(manager._child_receipts.values()))
                assert receipt["status"] == "failed"
                assert receipt["artifacts"] == []
                assert not any("bad.txt" in path for path in manager.list_workspace_files())

                # Unclosed nested child cleaned up by session.close() outside _invoke_tool also fails without promotion.
                handle2 = parent.answer_host_call(
                    {"id": "c4", "tool": "__dspy_interpreter__", "args": ["create"], "kwargs": {}}
                )["value"]
                parent.answer_host_call(
                    {
                        "id": "c5",
                        "tool": "__dspy_interpreter__",
                        "args": [],
                        "kwargs": {
                            "op": "execute",
                            "id": handle2,
                            "code": "workspace.write_artifact('leaked.txt', 'unsafe')\nSUBMIT(findings='ok')",
                            "variables": {},
                            "tools": {},
                            "output_fields": [{"name": "findings", "type": "str"}],
                        },
                    }
                )
                session.close()
                receipts = list(manager._child_receipts.values())
                assert receipts[1]["status"] == "failed"
                assert not any("leaked.txt" in path for path in manager.list_workspace_files())
            finally:
                parent.shutdown()
        finally:
            manager.shutdown()
            for sb in sandboxes:
                sb.code_interpreter.close()


@pytest.mark.parametrize(
    "code, expected",
    [
        ("slow_host()", "host-ok"),
        ("with ThreadPoolExecutor(2) as pool:\n    result = pool.submit(slow_host).result()\nresult", "host-ok"),
        (
            "with ThreadPoolExecutor(2) as pool:\n    result = list(pool.map(lambda _: slow_host(), range(2)))\nresult",
            ["host-ok", "host-ok"],
        ),
        ("parallel([(slow_host, {})])", ["host-ok"]),
    ],
    ids=["direct", "thread-pool", "overlapping-thread-pool", "dspy-parallel"],
)
def test_local_exec_timeout_excludes_threaded_host_waits(sandbox, code, expected) -> None:
    context = repl.create_context(sandbox)
    # Imports and native executor initialization are outside the short local budget.
    prepared = run(
        sandbox,
        context,
        "import dspy\nfrom concurrent.futures import ThreadPoolExecutor\n"
        "parallel = dspy.Parallel(num_threads=2, disable_progress_bar=True)\n"
        "parallel([(lambda: None, {})])",
    )
    assert not prepared.get("error")

    def slow_host_tool(_call):
        time.sleep(0.35)
        return {"value": "host-ok"}

    result = run(sandbox, context, code, on_call=slow_host_tool, timeout=None, tools=["slow_host"], exec_timeout=0.15)
    assert not result.get("error"), result
    assert result["value"] == expected
    assert run(sandbox, context, "1 + 41")["value"] == 42


@pytest.mark.parametrize("worker_owner", [False, True], ids=["signal", "async"])
def test_local_exec_timer_overlapping_waits_resume_and_expire_on_owner(worker_owner) -> None:
    from fleet_rlm.sandbox.runtime.repl import _LocalExecTimer

    results: dict[str, Any] = {}

    def execute():
        timer = _LocalExecTimer(0.15)
        assert timer._use_signal is not worker_owner
        with pytest.raises(TimeoutError), timer:
            # Two workers enter a host wait, then complete in a controlled order.
            with ThreadPoolExecutor(2) as pool:
                assert pool.submit(timer.pause).result(timeout=2)
                assert pool.submit(timer.pause).result(timeout=2)
                pool.submit(timer.resume).result(timeout=2)
                time.sleep(0.3)  # First completion must not rearm the timer.
                pool.submit(timer.resume).result(timeout=2)
            results["resumed"] = True
            while True:
                pass
        results["expired"] = True

    if worker_owner:
        with ThreadPoolExecutor(1) as pool:
            pool.submit(execute).result(timeout=5)
    else:
        execute()
    assert results == {"resumed": True, "expired": True}


def test_local_exec_timer_preserves_budget_and_ignores_stale_callbacks(monkeypatch) -> None:
    from fleet_rlm.sandbox.runtime import repl as runtime_repl

    clock = [0.0]
    monkeypatch.setattr(runtime_repl.time, "monotonic", lambda: clock[0])
    # Keep callbacks under test control, including ones already queued when cancelled.
    monkeypatch.setattr(runtime_repl.threading.Timer, "start", lambda _self: None)
    timer = runtime_repl._LocalExecTimer(10)
    timer._use_signal = False
    deliveries = []
    monkeypatch.setattr(timer, "_set_async_exc", deliveries.append)
    with timer:
        first_generation = timer._generation
        clock[0] = 2
        assert timer.pause()
        clock[0] = 100
        timer.resume()
        assert timer.remaining == 8
        timer._fire_async(first_generation)
        assert deliveries == []
        clock[0] = 103
        assert timer.pause()
        assert timer.remaining == 5
        # Exit while a host call is still pending.
    with runtime_repl._LocalExecTimer(10) as next_timer:
        timer.resume()
        timer._fire_async(timer._generation)
        assert not timer.pause()
        assert not timer._armed
        assert runtime_repl._STATE["exec_timer"] is next_timer
        assert next_timer._armed
    assert deliveries == [None]  # Only the fallback's exit cleanup, never expiry.


@pytest.mark.parametrize("failure", ["reply", "emit", "deadline"])
def test_local_exec_timer_bridge_failure_resumes_budget(monkeypatch, tmp_path, failure) -> None:
    from fleet_rlm.sandbox.runtime import repl as runtime_repl

    monkeypatch.setattr(runtime_repl, "REPLY_DIR", str(tmp_path))
    monkeypatch.setitem(runtime_repl._STATE, "host_call_timeout", 0.01 if failure == "deadline" else None)

    def emit(_mark, request):
        if failure == "emit":
            raise OSError("emit failed")
        if failure == "reply":
            (tmp_path / (request["id"] + ".json")).write_text(json.dumps({"error": "tool failed"}))

    monkeypatch.setattr(runtime_repl, "_emit", emit)
    with runtime_repl._LocalExecTimer(2) as timer:
        with ThreadPoolExecutor(1) as pool, pytest.raises((RuntimeError, OSError, TimeoutError)):
            pool.submit(runtime_repl._bridge_call, "tool", [], {}).result(timeout=3)
        assert timer._host_waits == 0
        assert timer._armed
        assert 0 < timer.remaining <= timer.limit


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_local_exec_timeout_bounds_stalled_cells_and_pauses_for_host_calls(sandbox) -> None:
    context = repl.create_context(sandbox)

    # Deadline-free execution still fails cleanly on a local CPU/loop hang instead of hanging forever.
    stalled = run(
        sandbox,
        context,
        "while True:\n    pass",
        timeout=None,
        exec_timeout=0.15,
    )
    assert stalled["error"] is True
    assert stalled["error_type"] == "TimeoutError"
    assert "local limit" in stalled["stderr"]

    # Context remains intact and reusable after a per-cell local timeout.
    recovered = run(sandbox, context, "1 + 41", timeout=None)
    assert not recovered.get("error")
    assert recovered["value"] == 42

    # Host bridge waits pause the local compute timer so bounded local timeouts do not break host calls.
    def slow_host_tool(_call: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.25)
        return {"value": "host-ok"}

    bridged = run(
        sandbox,
        context,
        "slow_host()",
        on_call=slow_host_tool,
        timeout=None,
        tools=["slow_host"],
        exec_timeout=0.15,
    )
    assert not bridged.get("error")
    assert bridged["value"] == "host-ok"

    # Worker-thread fallback interrupts runaway bytecode too.
    worker_results: dict[str, Any] = {}

    def run_in_worker() -> None:
        worker_results["stalled"] = run(
            sandbox,
            context,
            "while True:\n    pass",
            timeout=None,
            exec_timeout=0.15,
        )

    thread = threading.Thread(target=run_in_worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert worker_results["stalled"]["error"] is True
    assert worker_results["stalled"]["error_type"] == "TimeoutError"


def test_local_exec_timeout_disabled_when_host_timeout_is_within_default_cap(
    monkeypatch: pytest.MonkeyPatch, sandbox
) -> None:
    from daytona.common.errors import DaytonaTimeoutError

    from fleet_rlm.sandbox.runtime import repl as runtime_repl

    monkeypatch.setitem(runtime_repl._STATE, "exec_timeout", None)
    monkeypatch.setitem(runtime_repl._STATE, "host_call_timeout", None)
    assert runtime_repl._exec_limit() == runtime_repl.DEFAULT_EXEC_TIMEOUT_SECONDS

    monkeypatch.setitem(runtime_repl._STATE, "host_call_timeout", runtime_repl.DEFAULT_EXEC_TIMEOUT_SECONDS + 1.0)
    assert runtime_repl._exec_limit() == runtime_repl.DEFAULT_EXEC_TIMEOUT_SECONDS

    monkeypatch.setitem(runtime_repl._STATE, "host_call_timeout", runtime_repl.DEFAULT_EXEC_TIMEOUT_SECONDS)
    assert runtime_repl._exec_limit() is None

    monkeypatch.setitem(runtime_repl._STATE, "host_call_timeout", 1.0)
    assert runtime_repl._exec_limit() is None

    monkeypatch.setitem(runtime_repl._STATE, "exec_timeout", 0.25)
    assert runtime_repl._exec_limit() == 0.25

    context = repl.create_context(sandbox)
    timer_armed = run(sandbox, context, "import signal\nsignal.getitimer(signal.ITIMER_REAL)[0] == 0.0", timeout=1)
    assert timer_armed["value"] is True
    with pytest.raises(DaytonaTimeoutError):
        run(sandbox, context, "import time\nwhile True:\n    time.sleep(0.05)", timeout=1)
    assert run(sandbox, context, "1 + 41", timeout=1)["value"] == 42


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_delegation_bounds_errors_and_docstrings(monkeypatch) -> None:
    from fleet_rlm.sandbox.delegation import ChildDelegation

    manager = DaytonaSandboxManager("key", "org", "vol", "sess-delegation-bounds")
    delegation = ChildDelegation(
        manager,
        metered_sub=None,
        child_signature=dspy.Signature("subtask -> findings"),
        sub_adapter=dspy.ChatAdapter(),
    )

    with pytest.raises(ValueError, match="subtask must be a non-empty string"):
        delegation.spawn_agent("   ")
    with pytest.raises(ValueError, match=r"max_iters=9 is invalid; expected an integer in 1\.\.8"):
        delegation.spawn_agent("check", max_iters=9)
    with pytest.raises(ValueError, match=r"max_iters=True is invalid; expected an integer in 1\.\.8"):
        delegation.spawn_agent("check", max_iters=True)
    with pytest.raises(ValueError, match=r"max_llm_calls=26 is invalid; expected an integer in 1\.\.25"):
        delegation.spawn_agent("check", max_llm_calls=26)

    # Batch validation rejects invalid bounds before allocating any child sandbox.
    monkeypatch.setattr(
        DaytonaSandboxManager, "allocate_child", lambda *_args, **_kwargs: pytest.fail("allocate_child should not run")
    )
    with pytest.raises(ValueError, match=r"max_iters=0 is invalid; expected an integer in 1\.\.8"):
        delegation.spawn_agents(["check"], max_iters=0)
    with pytest.raises(ValueError, match=r"max_llm_calls=30 is invalid; expected an integer in 1\.\.25"):
        delegation.spawn_agents(["check"], max_llm_calls=30)

    doc = ChildDelegation.spawn_agents.__doc__ or ""
    assert "1 <= len(subtasks) <= 8" in doc
    assert "1 <= max_iters <= 8" in doc
    assert "1 <= max_llm_calls <= 25" in doc
    assert "artifacts/" in doc


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_spawn_agent_decouples_child_outcome_from_artifact_collection(monkeypatch, tmp_path) -> None:
    from fleet_rlm.sandbox.delegation import ChildDelegation

    sandboxes = _install_emulated_daytona(monkeypatch, tmp_path)
    monkeypatch.setattr(DaytonaSandboxManager, "collect_child_artifacts", lambda _self: [])

    class FakeRLM:
        def __init__(self, _sig, **kwargs):
            self.interpreter_factory = kwargs["interpreter_factory"]

        def __call__(self, *, subtask: str):
            interp = self.interpreter_factory()
            try:
                if "fail" in subtask:
                    raise RuntimeError("child execution error")
                return SimpleNamespace(findings="ok:" + subtask, interp=interp)
            finally:
                interp.shutdown()

    monkeypatch.setattr(dspy, "RLM", FakeRLM)
    manager = DaytonaSandboxManager("key", "org", "vol", "sess-delegation-outcome", turn_timeout_seconds=0)
    with manager.turn_scope():
        try:
            delegation = ChildDelegation(
                manager,
                metered_sub=dspy.LM("openai/gpt-4o-mini"),
                child_signature=dspy.Signature("subtask -> findings"),
                sub_adapter=dspy.ChatAdapter(),
            )
            sig = dspy.Signature("subtask -> findings")
            state = dspy.Predict(sig).dump_state()
            delegation.bind_policies({"leaf_action": (sig, state), "delegating_action": (sig, state)})
            parent_interp = manager.interpreter_factory(tools={"spawn_agent": delegation.spawn_agent})()
            try:
                ok_call = parent_interp.answer_host_call(
                    {"id": "c1", "tool": "spawn_agent", "args": ["succeed"], "kwargs": {"allowed_tools": []}}
                )
                assert ok_call == {"value": {"findings": "ok:succeed", "artifacts": []}}

                bad_call = parent_interp.answer_host_call(
                    {"id": "c2", "tool": "spawn_agent", "args": ["fail"], "kwargs": {"allowed_tools": []}}
                )
                bad = bad_call["value"]
                assert bad["findings"] == "" and "error" in bad and bad["artifacts"] == []
            finally:
                parent_interp.shutdown()

            receipts = list(manager._child_receipts.values())
            assert [row["status"] for row in receipts] == ["succeeded", "failed"]
        finally:
            manager.shutdown()
            for sb in sandboxes:
                sb.code_interpreter.close()


@pytest.mark.usefixtures("clear_snapshot_preflight_cache")
def test_workspace_skill_reads_emit_bounded_skill_activity_and_docs_note_artifacts_isolation(sandbox) -> None:
    context = repl.create_context(sandbox)
    sandbox.fs.upload_file(b"# Delegation\nRules", "/workspace/skills/delegation/SKILL.md")
    sandbox.fs.upload_file(b"hello", "/workspace/files/notes.txt")

    via_read_text = run(sandbox, context, "workspace.read_text('skills/delegation/SKILL.md')")
    assert "Rules" in via_read_text["value"]
    assert via_read_text["skill_activity"] == [
        {"helper": "workspace.read_text", "skill": "delegation", "completed": True}
    ]

    via_read = run(sandbox, context, "workspace.read('skills/delegation/SKILL.md')")
    assert via_read["skill_activity"] == [{"helper": "workspace.read", "skill": "delegation", "completed": True}]

    failed = run(
        sandbox,
        context,
        "try:\n    workspace.read_text('skills/missing/SKILL.md')\nexcept FileNotFoundError:\n    pass",
    )
    assert failed["skill_activity"] == [{"helper": "workspace.read_text", "skill": "missing", "completed": False}]

    non_skill = run(sandbox, context, "workspace.read_text('files/notes.txt')")
    assert non_skill["skill_activity"] == []

    capped = run(
        sandbox,
        context,
        "for _ in range(25):\n    workspace.read_text('skills/delegation/SKILL.md')",
    )
    assert len(capped["skill_activity"]) == 20
    assert capped["skill_activity_truncated"] is True

    skill_doc = " ".join(
        (Path(__file__).resolve().parents[2] / "src/fleet_rlm/bundled_skills/delegation/SKILL.md")
        .read_text(encoding="utf-8")
        .split()
    )
    assert "artifacts/" in skill_doc and "starts empty inside a child" in skill_doc
    instructions = DaytonaSandboxManager("key", "org", "vol", "sess-doc").interpreter_factory().execution_instructions
    assert "artifacts/ starts empty inside a child" in instructions


@pytest.fixture
def sandbox(tmp_path):
    emulated = emulated_sandbox(tmp_path)
    try:
        yield emulated
    finally:
        emulated.code_interpreter.close()


def test_bootstrap_marker_cannot_hide_a_task_or_side_effect():
    from fleet_rlm.sandbox.interpreter import _is_bootstrap_code

    injection = 'context = __dspy_values.decode(__import__("json").loads(_raw_context))'
    assert _is_bootstrap_code(injection)
    assert not _is_bootstrap_code(injection + '; SUBMIT(answer="unverified")')
    assert not _is_bootstrap_code('x = "__dspy_values.decode(__import__(\\"json\\").loads("; SUBMIT(answer="x")')
    assert not _is_bootstrap_code('context = __dspy_values.decode(__import__("json").loads(side_effect()))')
    assert not _is_bootstrap_code("x = " + "+".join("1" for _ in range(1_500)))
