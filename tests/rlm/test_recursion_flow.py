from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import dspy
import pytest

import fleet_rlm.rlm.execution as runtime_module
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.runtime import ChildRuntimeLease
from fleet_rlm.rlm.events import (
    ChildProgress,
    ObservationSession,
    Status,
    ToolCompleted,
    ToolFailed,
    ToolStarted,
    _latest_lm_telemetry,
    _RLMTraceCallback,
)
from fleet_rlm.rlm.execution import (
    DelegationPolicy,
    ExecutionRuntime,
    RLMExecutionContext,
    RLMRunner,
    RunIdentity,
    SessionView,
    WorkerOwnership,
    materialize_child_inputs,
)
from fleet_rlm.rlm.program import RLMModelBundle, RLMOptions
from fleet_rlm.rlm.recursion import (
    ChildRequest,
    ChildRuntimeAuthorizationError,
    DelegationMetrics,
    RecursiveRLMOptions,
    normalize_lm_token_usage,
)
from fleet_rlm.sessions.context import SessionContextManifest
from fleet_rlm.sessions.models import TurnAccess
from fleet_rlm.sessions.run_state import RunAuthority
from tests.rlm.fakes import EmptyCapabilities
from tests.support.native_rlm import build_native_rlm_for_test


@pytest.mark.asyncio
async def test_root_child_root_flow_preserves_parent_repl_and_typed_submit() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "prepare selected data", "code": "root_marker = 'root-only'"},
            {
                "reasoning": "delegate selected row",
                "code": "child = rlm_query(task='classify selected row', inputs=[])['answer']",
            },
            {
                "reasoning": "check child scope",
                "code": (
                    "\ntry:\n    root_marker\n    child_cannot_see_root = False\n"
                    "except NameError:\n    child_cannot_see_root = True\n"
                    "SUBMIT(answer=str(child_cannot_see_root), evidence=[], gaps=[], result_files=[])"
                ),
            },
            {
                "reasoning": "integrate child answer",
                "code": "assert root_marker == 'root-only'\nassert child == 'True'\nSUBMIT(answer='root-complete')",
            },
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="classify the selected row",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2),
            child_runtime_factory=lambda call_index, *, profile="semantic-child": _child_lease(
                call_index, profile=profile
            ),
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "root-complete"
    assert stream.outcome.usage["iterations"] == 3
    tool_started = [event for event in events if event.kind == "tool.started"]
    tool_completed = [event for event in events if event.kind == "tool.completed"]
    assert len(tool_started) == len(tool_completed) == 1
    assert isinstance(tool_started[0].detail, ToolStarted)
    assert isinstance(tool_completed[0].detail, ToolCompleted)
    assert tool_started[0].detail.input == {"input_count": 0}
    assert tool_completed[0].detail.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event.detail, Status) and event.detail.phase == "recursive"]
    assert [event.detail.status for event in statuses] == ["child_started", "child_completed"]
    child_events = [event for event in events if isinstance(event.detail, ChildProgress)]
    assert len(child_events) == 2
    assert all(event.detail.parent_run_id == str(context.identity.run_id) for event in child_events)
    assert all(event.run_id == context.identity.run_id for event in child_events)
    assert all("classify selected row" not in (event.detail.message or "") for event in statuses)
    assert all("root-complete" not in (event.detail.message or "") for event in statuses)
    assert all(not isinstance(detail, Status) for detail in stream.outcome.execution_details)


@pytest.mark.asyncio
async def test_root_completion_drains_action_deadline_stragglers(monkeypatch: pytest.MonkeyPatch) -> None:
    """An action-bounded batch returns partial outcomes while a cancelled
    straggler is still stopping; the Root may submit at once, and its outcome
    is accepted only after that straggler settles and its lease closes."""
    import json

    monkeypatch.setattr("fleet_rlm.rlm.recursion._ACTION_RESULT_MARGIN_S", 1.0)
    # The deadline is read once at tool entry: give the calling action 2 s from then.
    monkeypatch.setattr("fleet_rlm.rlm.recursion.current_host_action_deadline", lambda: time.monotonic() + 2.0)
    straggler_settled = threading.Event()

    class Child:
        def __init__(self, interpreter_factory) -> None:
            self._interpreter_factory = interpreter_factory

        def __call__(self, *, prompt: str) -> dspy.Prediction:
            if json.loads(prompt)["task"] == "fast":
                return dspy.Prediction(answer="fast-done", evidence=[], gaps=[], result_files=[], trajectory=[])
            interpreter = self._interpreter_factory()
            try:
                for index in range(1000):
                    interpreter.execute(f"value = {index}")
                    time.sleep(0.02)
            finally:
                interpreter.shutdown()
                # An in-flight LM call outlives the refusal of the next action.
                time.sleep(0.5)
                straggler_settled.set()
            return dspy.Prediction(answer="never", evidence=[], gaps=[], result_files=[], trajectory=[])

    monkeypatch.setattr(
        "fleet_rlm.rlm.recursion.build_native_rlm", lambda **kwargs: Child(kwargs["interpreter_factory"])
    )
    monkeypatch.setattr("fleet_rlm.rlm.recursion.is_native_rlm", lambda _program: True)
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {
                "reasoning": "batch",
                "code": (
                    "answers = rlm_query_batched("
                    "tasks=[{'task': 'fast', 'inputs': []}, {'task': 'slow', 'inputs': []}])"
                ),
            },
            {"reasoning": "submit", "code": "SUBMIT(answer='/'.join(item['status'] for item in answers))"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="cross-check",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2, max_parallel_children=2),
            child_runtime_factory=lambda call_index, *, profile="semantic-child": _child_lease(
                call_index, profile=profile
            ),
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded, events
    assert stream.outcome.prediction is not None
    assert stream.outcome.prediction.answer == "completed/timed_out"
    assert straggler_settled.is_set()


def _child_lease(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
    """Create a child runtime lease for a recursive test invocation.

    Parameters:
        call_index (int): Index used to identify the child runtime and workspace.

    Returns:
                ChildRuntimeLease: A lease backed by an in-process interpreter.
    """
    del profile
    interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
    return ChildRuntimeLease(
        interpreter,
        f"child-{call_index}",
        "test-volume",
        f"recursive/test-workspace/test-run/{call_index}",
        interpreter.shutdown,
    )


@pytest.mark.asyncio
async def test_worker_startup_failure_releases_the_runner_owned_child_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = dspy.utils.DummyLM([], adapter=dspy.JSONAdapter())
    sub = dspy.utils.DummyLM([], adapter=dspy.JSONAdapter())

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="fail during worker startup",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=1, max_llm_calls=1),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
            child_runtime_factory=_child_lease,
        ),
        capabilities=EmptyCapabilities(),
    )
    created = []
    real_executor = runtime_module.RecursiveRLMExecutor

    def track_executor(**kwargs):
        executor = real_executor(**kwargs)
        created.append(executor)
        return executor

    def fail_signature(*_args, **_kwargs):
        raise RuntimeError("worker startup failed")

    monkeypatch.setattr(runtime_module, "RecursiveRLMExecutor", track_executor)
    monkeypatch.setattr(runtime_module, "root_signature_for_recursion", fail_signature)
    ownership = WorkerOwnership()
    observations = ObservationSession(context.identity.run_id, context.identity.session_id)

    try:
        with pytest.raises(RuntimeError, match="worker startup failed"):
            await RLMRunner()._start_worker(context, ownership, observations)
        assert created
        assert created[0]._scheduler._loop is asyncio.get_running_loop()
        assert not created[0]._scheduler._closed
    finally:
        await ownership.wait_owned()

    assert created[0]._scheduler._closed
    assert asyncio.get_running_loop().is_running()


@pytest.mark.asyncio
async def test_runner_rejects_recursive_tool_after_authority_revocation() -> None:
    """A revoked Run authority rejects the recursive tool at the Runner's
    authorization fence: the Run observes ``tool.started`` then ``tool.failed``
    for ``rlm_query`` and the child factory is never called, so no child lease
    is ever created.

    The scripted action sits on a non-final iteration so it actually reaches the
    recursive tool instead of being intercepted by wrap-up, which is now keyed
    to the iteration count. ``max_iters=3`` keeps the subsequent scripted-response
    exhaustion off the final iteration too: ``DummyLM`` answers ``"No more
    responses"`` once its list is spent, and on a final iteration that parse
    failure would be reported as bounded finalization exhaustion (a
    ``TimeoutError``) instead of the parse exhaustion this Turn hits.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "delegate too late", "code": "rlm_query(task='late child request', inputs=[])['answer']"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    authority = RunAuthority()
    authority.revoke()
    created: list[int] = []

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    def child_factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        """
        Create a child runtime lease for the specified recursive call index.

        Parameters:
                call_index (int): Index of the recursive call.

        Returns:
                ChildRuntimeLease: The runtime lease for the child call.
        """
        del profile
        created.append(call_index)
        return _child_lease(call_index)

    context = RLMExecutionContext(
        identity=RunIdentity(
            run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4()), authority=authority
        ),
        session=SessionView(
            request="delegate after claim loss",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=3, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1), child_runtime_factory=child_factory
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"
    # The recursive tool was really attempted and then rejected; the rejection,
    # not the tool never running, is what kept the child factory at zero.
    assert [
        (type(event.detail).__name__, event.detail.tool_name)
        for event in events
        if isinstance(event.detail, (ToolStarted, ToolFailed))
    ] == [("ToolStarted", "rlm_query"), ("ToolFailed", "rlm_query")]
    assert created == []


@pytest.mark.asyncio
async def test_normal_daytona_policy_omits_recursive_tool_and_guidance() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [{"reasoning": "answer directly", "code": "SUBMIT(answer='normal-complete')"}],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    captured: dict[str, object] = {}

    def capturing_builder(**kwargs: object):
        captured.update(kwargs)
        return build_native_rlm_for_test(**kwargs)

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer directly",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=2, max_llm_calls=2),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(recursive_options=RecursiveRLMOptions(enabled=False)),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner(program_builder=capturing_builder).stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None and stream.outcome.succeeded
    assert captured["tools"] is None
    assert "rlm_query" not in captured["signature"].instructions


@pytest.mark.asyncio
async def test_failed_child_cleanup_prevents_successful_root_outcome() -> None:
    """
    Verify that failed child-runtime cleanup causes the root RLM execution to fail.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "delegate", "code": "child = rlm_query(task='small task', inputs=[])['answer']"},
            {"reasoning": "child submit", "code": "SUBMIT(answer='child', evidence=[], gaps=[], result_files=[])"},
            {"reasoning": "submit anyway", "code": "SUBMIT(answer='unexpected')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)

    async def not_cancelled() -> bool:
        """Indicate that cancellation has not been requested.

        Returns:
                bool: `False`, indicating that cancellation has been requested.
        """
        return False

    def failed_lease(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        """Create a child runtime lease whose cleanup raises an error."""
        del profile
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            """Shut down the interpreter and raise an error indicating that child cleanup failed."""
            interpreter.shutdown()
            raise RuntimeError("child cleanup failed")

        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="delegate one task",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
            preparation_notices=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 30,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=2), child_runtime_factory=failed_lease
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "failed"


@pytest.mark.asyncio
async def test_runner_wait_owned_retains_pending_recursive_workers_until_child_lease_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Root batch must not release Run resources before its sibling settles."""
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        [
            {"reasoning": "batch", "code": "answers = rlm_query_batched(tasks=[{'task': 'blocked', 'inputs': []}])"},
            {"reasoning": "submit", "code": "SUBMIT(answer='root')"},
        ],
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    child_started = threading.Event()
    release_child = threading.Event()
    child_closed = threading.Event()

    class BlockingChild:
        def __call__(self, *, prompt: str) -> dspy.Prediction:
            del prompt
            child_started.set()
            release_child.wait(2)
            return dspy.Prediction(answer="late", evidence=[], gaps=[], result_files=[], trajectory=[])

    import fleet_rlm.rlm.recursion as recursive_calls

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    monkeypatch.setattr(recursive_calls, "is_native_rlm", lambda _program: True)

    def child_factory(call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        del profile
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            child_closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    async def not_cancelled() -> bool:
        return False

    context = RLMExecutionContext(
        identity=RunIdentity(run_id=uuid4(), session_id=uuid4(), access=TurnAccess(uuid4(), uuid4())),
        session=SessionView(
            request="answer",
            session_context=SessionContextManifest(uuid4(), 0, 0, ()),
            attachments=(),
        ),
        execution=ExecutionRuntime(
            models=RLMModelBundle(root, sub),
            options=RLMOptions(max_iters=4, max_llm_calls=4),
            deadline=time.monotonic() + 0.5,
            interpreter=DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
            cancellation_requested=not_cancelled,
        ),
        delegation=DelegationPolicy(
            recursive_options=RecursiveRLMOptions(enabled=True, max_calls=1),
            child_runtime_factory=child_factory,
        ),
        capabilities=EmptyCapabilities(),
    )

    stream = RLMRunner().stream(context)
    _events = [event async for event in stream]

    assert stream.outcome is not None
    assert stream.outcome.terminal_status == "timeout"
    assert child_started.wait(2)
    owned = asyncio.create_task(stream.wait_owned())
    await asyncio.sleep(0.05)
    assert not owned.done()
    assert not child_closed.is_set()

    release_child.set()
    await asyncio.wait_for(owned, timeout=2)
    assert child_closed.is_set()


# ==============================================================================
# Child Input Materialization Contracts (from test_child_input_materialization)
# ==============================================================================


def _materialization_tools(source: str, *, change_on_read: bool = False) -> dict[str, dspy.Tool]:
    current_source = [source]
    revision = ["revision-1"]

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path != "selected/evidence.txt":
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(current_source[0].encode("utf-8")),
                "modified_at": revision[0],
            },
        }

    def read_workspace_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        assert path == "selected/evidence.txt"
        offset = int(cursor or 0)
        if change_on_read and offset == 0:
            current_source[0] = source[:-1] + "X"
            revision[0] = "revision-2"
        current = current_source[0]
        end = min(len(current), offset + max_chars)
        return {
            "ok": True,
            "content": current[offset:end],
            "next_cursor": str(end) if end < len(current) else None,
            "eof": end >= len(current),
            "byte_size": len(current.encode("utf-8")),
        }

    return {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }


def test_child_materialization_copies_source_larger_than_two_megabytes() -> None:
    source = "record\n" * 350_000
    source_bytes = source.encode("utf-8")
    request = ChildRequest(task="Inspect all records", inputs=("selected/evidence.txt",))
    staged = materialize_child_inputs(
        request,
        tools=_materialization_tools(source),
        check_authority=lambda: None,
        turn_deadline=time.monotonic() + 10,
        source_reader=lambda _path, _max_bytes: source_bytes,
    )
    assert len(source_bytes) > 2_000_000
    assert staged == {"selected/evidence.txt": source_bytes}


def test_direct_child_reader_rejects_source_metadata_change_during_copy() -> None:
    source = b"same-size"
    revision = ["revision-1"]

    def stat_workspace_file(path: str) -> dict[str, object]:
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(source),
                "modified_at": revision[0],
            },
        }

    def read_source(_path: str, _max_bytes: int) -> bytes:
        revision[0] = "revision-2"
        return source

    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            ChildRequest(task="Inspect selected source", inputs=("selected/evidence.txt",)),
            tools={"stat_workspace_file": dspy.Tool(stat_workspace_file)},
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
            source_reader=read_source,
        )


def test_child_materialization_rejects_a_source_changed_while_copying() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("selected/evidence.txt",))
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            request,
            tools=_materialization_tools("same-size source", change_on_read=True),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_rejects_a_changed_file_in_a_directory_scope() -> None:
    source = {"selected/a.txt": "before", "selected/b.txt": "trigger"}
    revisions = {path: "revision-1" for path in source}

    def stat_workspace_file(path: str) -> dict[str, object]:
        if path == "selected":
            return {"ok": True, "entry": {"path": path, "kind": "directory"}}
        content = source.get(path)
        if content is None:
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": path,
                "kind": "file",
                "byte_size": len(content.encode("utf-8")),
                "modified_at": revisions[path],
            },
        }

    def list_workspace_files(
        path: str,
        limit: int = 100,
        after: str | None = None,
    ) -> dict[str, object]:
        assert path == "selected"
        assert limit == 100
        assert after is None
        return {
            "ok": True,
            "entries": [{"path": name, "kind": "file"} for name in sorted(source)],
            "truncated": False,
            "next_cursor": None,
        }

    def read_workspace_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        current = source[path]
        if path == "selected/b.txt" and cursor is None:
            source["selected/a.txt"] = "changed"
            revisions["selected/a.txt"] = "revision-2"
        return {
            "ok": True,
            "content": current[:max_chars],
            "next_cursor": None,
            "eof": True,
            "byte_size": len(current.encode("utf-8")),
        }

    tools = {
        "stat_workspace_file": dspy.Tool(stat_workspace_file),
        "list_workspace_files": dspy.Tool(list_workspace_files),
        "read_workspace_text": dspy.Tool(read_workspace_text),
    }
    with pytest.raises(ValueError, match="changed during child staging"):
        materialize_child_inputs(
            ChildRequest(task="Inspect both files", inputs=("selected",)),
            tools=tools,
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


def test_child_materialization_limits_project_subtree_to_selected_scope() -> None:
    source = {
        "alpha/src/main.py": "answer = 42\n",
        "beta/private.txt": "unrelated project data",
    }

    def stat_project_file(path: str) -> dict[str, object]:
        normalized = path.removeprefix("projects/")
        if normalized == "alpha":
            return {"ok": True, "entry": {"path": normalized, "kind": "directory"}}
        content = source.get(normalized)
        if content is None:
            return {"ok": False}
        return {
            "ok": True,
            "entry": {
                "path": normalized,
                "kind": "file",
                "byte_size": len(content.encode("utf-8")),
                "modified_at": "revision-1",
            },
        }

    def list_project_files(path: str, limit: int = 100, after: str | None = None) -> dict[str, object]:
        assert path == "projects/alpha"
        assert limit == 100
        assert after is None
        return {
            "ok": True,
            "entries": [{"path": "alpha/src/main.py", "kind": "file"}],
            "truncated": False,
            "next_cursor": None,
        }

    def read_project_text(
        path: str,
        cursor: str | None = None,
        max_chars: int = 10_000,
    ) -> dict[str, object]:
        assert cursor is None
        content = source[path.removeprefix("projects/")]
        return {
            "ok": True,
            "content": content[:max_chars],
            "next_cursor": None,
            "eof": True,
            "byte_size": len(content.encode("utf-8")),
        }

    tools = {
        "stat_project_file": dspy.Tool(stat_project_file),
        "list_project_files": dspy.Tool(list_project_files),
        "read_project_text": dspy.Tool(read_project_text),
    }
    staged = materialize_child_inputs(
        ChildRequest(task="Inspect Alpha", inputs=("projects/alpha",)),
        tools=tools,
        check_authority=lambda: None,
        turn_deadline=time.monotonic() + 10,
    )
    assert staged == {"projects/alpha/src/main.py": b"answer = 42\n"}
    assert all("beta" not in path for path in staged)


def test_child_materialization_never_stages_host_metadata_or_credential_files() -> None:
    for path in (
        ".fleet/task.json",
        "selected/.env.local",
        "selected/id_ed25519",
        "selected/.npmrc",
        "selected/.pypirc",
        "selected/.git-credentials",
        "selected/.docker/config.json",
        "selected/.git/config",
    ):
        with pytest.raises(ChildRuntimeAuthorizationError, match="credential-bearing or host metadata"):
            materialize_child_inputs(
                ChildRequest(task="Inspect source", inputs=(path,)),
                tools=_materialization_tools("irrelevant"),
                check_authority=lambda: None,
                turn_deadline=time.monotonic() + 10,
            )


def test_child_materialization_rejects_unauthorized_reference() -> None:
    request = ChildRequest(task="Inspect selected source", inputs=("unrelated/secret.txt",))
    with pytest.raises(ChildRuntimeAuthorizationError):
        materialize_child_inputs(
            request,
            tools=_materialization_tools("source"),
            check_authority=lambda: None,
            turn_deadline=time.monotonic() + 10,
        )


# ==============================================================================
# Recursion Metrics Contracts (from test_recursion_metrics)
# ==============================================================================


def test_dspy_callback_records_role_and_recursive_depth_without_content() -> None:
    metrics = DelegationMetrics()
    root = SimpleNamespace(model="root-model", history=[])
    sub = SimpleNamespace(model="sub-model", history=[])
    callback = _RLMTraceCallback(root_lm=root, sub_lm=sub, recursive_depth=1, metrics=metrics)

    callback.on_lm_start("root-call", root, {"prompt": "private prompt"})
    callback.on_lm_end("root-call", {"content": "private answer"})
    callback.on_lm_start("sub-call", sub, {"prompt": "private sub-prompt"})
    callback.on_lm_end("sub-call", {"content": "private sub-answer"})

    snapshot = metrics.snapshot()
    assert snapshot.child_root_lm_calls_depth_1 == 1
    assert snapshot.child_sub_lm_calls_depth_1 == 1
    assert snapshot.root_lm_calls_depth_0 == 0
    assert snapshot.sub_lm_calls_depth_0 == 0
    assert "private prompt" not in repr(snapshot)
    assert "private answer" not in repr(snapshot)


def test_metrics_track_recursive_batch_lifecycle_and_peak_width() -> None:
    metrics = DelegationMetrics()
    metrics.record_recursive_batch()
    metrics.record_recursive_call()
    metrics.record_recursive_call()
    metrics.child_started()
    metrics.child_started()
    metrics.child_completed()
    metrics.child_completed()
    metrics.record_delegated_input_bytes(123)

    snapshot = metrics.snapshot()
    assert snapshot.recursive_batch_calls == 1
    assert snapshot.recursive_child_calls == 2
    assert snapshot.recursive_children_started == 2
    assert snapshot.recursive_children_completed == 2
    assert snapshot.peak_child_concurrency == 2
    assert snapshot.delegated_input_bytes == 123
    assert snapshot.as_dict()["delegated_input_bytes"] == 123
    assert snapshot.as_dict()["peak_child_concurrency"] == 2


def test_metrics_count_failed_children_separately_and_release_their_slot() -> None:
    metrics = DelegationMetrics()
    metrics.child_started()
    metrics.child_started()
    metrics.child_failed()
    metrics.child_completed()
    metrics.child_started()

    snapshot = metrics.snapshot()
    assert snapshot.recursive_children_started == 3
    assert snapshot.recursive_children_completed == 1
    assert snapshot.recursive_children_failed == 1
    assert snapshot.peak_child_concurrency == 2
    assert snapshot.as_dict()["recursive_children_failed"] == 1


def test_token_usage_normalizer_accepts_both_supported_alias_families() -> None:
    assert normalize_lm_token_usage({"input_tokens": 11, "output_tokens": 7}) == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }
    assert normalize_lm_token_usage({"prompt_tokens": 5, "completion_tokens": 3}) == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": 8,
    }
    assert normalize_lm_token_usage({"input_tokens": 11, "output_tokens": 7, "total_tokens": 21})["total_tokens"] == 21


def test_metrics_record_input_output_total_per_lm_call() -> None:
    metrics = DelegationMetrics()
    metrics.record_lm_call("sub", 1, usage={"input_tokens": 13, "output_tokens": 4})

    assert metrics.snapshot().lm_token_totals == (("sub", 1, 13, 4, 17),)


def test_metrics_token_totals_partial_usage_is_not_collapsed_to_zero() -> None:
    # A provider that reports only input_tokens must not read as 0 tokens.
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 50})
    metrics.record_lm_call("root", 0, usage={"prompt_tokens": 30, "completion_tokens": 20})

    assert metrics.snapshot().lm_token_totals == (("root", 0, 80, 20, 100),)
    assert metrics.snapshot().as_dict()["lm_token_totals"] == [
        {
            "role": "root",
            "recursive_depth": 0,
            "input_tokens": 80,
            "output_tokens": 20,
            "tokens": 100,
        }
    ]


def test_lm_telemetry_matches_callback_outputs_in_concurrent_history() -> None:
    # P38-RLM-006: the certified DSPy 3.4.0 legacy contract pairs each
    # ``on_lm_end`` payload with its history entry by identity on ``outputs``.
    first_outputs = object()
    second_outputs = object()
    lm = SimpleNamespace(
        history=[
            {"outputs": first_outputs, "usage": {"input_tokens": 3, "output_tokens": 2}},
            {"outputs": second_outputs, "usage": {"input_tokens": 17, "output_tokens": 11}},
        ]
    )

    usage = _latest_lm_telemetry(lm, 0, first_outputs)

    assert usage == {"input_tokens": 3, "output_tokens": 2}


def test_metrics_unobserved_usage_emits_no_zero_token_totals_and_status_unavailable() -> None:
    # A provider that never reports usage must not manufacture an all-zero
    # lm_token_totals entry; call counts/latency still record.
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0)
    metrics.record_lm_call("root", 0, usage=None)
    metrics.record_lm_call("root", 0, usage={})

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 3
    assert snapshot.lm_token_totals == ()
    assert snapshot.token_usage_status == "unavailable"
    assert snapshot.as_dict()["lm_token_totals"] == []
    assert snapshot.as_dict()["token_usage_status"] == "unavailable"


def test_metrics_real_zero_usage_is_observed_not_unavailable() -> None:
    # A provider-reported all-zero usage mapping is a measurement, not
    # "unavailable".
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 0, "output_tokens": 0})

    snapshot = metrics.snapshot()
    assert snapshot.lm_token_totals == (("root", 0, 0, 0, 0),)
    assert snapshot.token_usage_status == "observed"
    assert snapshot.as_dict()["token_usage_status"] == "observed"


def test_metrics_mixed_observed_and_unobserved_calls_only_total_observed() -> None:
    metrics = DelegationMetrics()
    metrics.record_lm_call("root", 0, usage={"input_tokens": 50})
    metrics.record_lm_call("root", 0)  # no usage: call counts, tokens must not
    metrics.record_lm_call("sub", 0)  # entirely unobserved role/depth key

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 2
    assert snapshot.sub_lm_calls_depth_0 == 1
    assert snapshot.lm_token_totals == (("root", 0, 50, 0, 50),)
    assert snapshot.token_usage_status == "observed"


def test_metrics_token_aggregation_is_thread_safe_across_concurrent_recorders() -> None:
    from concurrent.futures import ThreadPoolExecutor

    metrics = DelegationMetrics()
    observed_calls_per_thread = 25

    def record(thread_index: int) -> None:
        for _ in range(observed_calls_per_thread):
            metrics.record_lm_call("root", 0, usage={"prompt_tokens": 10, "completion_tokens": 5})
            metrics.record_lm_call("sub", 0)  # unobserved: counts only
            metrics.record_lm_call("root", 1, usage={"input_tokens": thread_index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(8)))

    snapshot = metrics.snapshot()
    assert snapshot.root_lm_calls_depth_0 == 8 * observed_calls_per_thread
    assert snapshot.sub_lm_calls_depth_0 == 8 * observed_calls_per_thread
    assert snapshot.child_root_lm_calls_depth_1 == 8 * observed_calls_per_thread
    totals = {(role, depth): (i, o, t) for role, depth, i, o, t in snapshot.lm_token_totals}
    root_expected = 8 * observed_calls_per_thread
    assert totals[("root", 0)] == (root_expected * 10, root_expected * 5, root_expected * 15)
    depth1_expected = observed_calls_per_thread * sum(range(8))
    assert totals[("root", 1)] == (depth1_expected, 0, depth1_expected)
    assert ("sub", 0) not in totals
    assert snapshot.token_usage_status == "observed"
