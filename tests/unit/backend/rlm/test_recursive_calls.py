from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Callable
from concurrent.futures import Future

import dspy
import pytest
from dspy.predict.rlm import RLM

from fleet_rlm.chat.run_authority import RunAuthority
from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.child_runtime import ChildRuntimeCleanupError
from fleet_rlm.rlm.events import Status, ToolCompleted, ToolFailed, ToolStarted
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.rlm.recursive_calls import (
    RLM_NATIVE_CHILD_DEPTH,
    RecursiveBatchError,
    RecursiveRLMExecutor,
    RecursiveRLMOptions,
)
from tests.unit.backend.rlm.fakes import FakeChildRuntimeFactory


def _executor(
    root_actions: list[dict[str, str]],
    *,
    sub_actions: list[dict[str, str]] | None = None,
    options: RecursiveRLMOptions | None = None,
    factory_calls: list[DaytonaCodeInterpreter] | None = None,
    observer=None,
    is_authorized: Callable[[], bool] | None = None,
) -> RecursiveRLMExecutor:
    """
    Construct a recursive executor backed by dummy root and sub-models for tests.

    Parameters:
        root_actions (list[dict[str, str]]): Responses supplied by the root model.
        sub_actions (list[dict[str, str]] | None): Responses supplied by the sub-model.
        options (RecursiveRLMOptions | None): Executor configuration.
        factory_calls (list[DaytonaCodeInterpreter] | None): Collection to receive created child interpreters.
        observer: Optional event observer.
        is_authorized (Callable[[], bool] | None): Optional callback used to authorize recursive calls.

    Returns:
        RecursiveRLMExecutor: A configured executor with an in-process child-runtime factory.
    """
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(root_actions, adapter=adapter)
    sub = dspy.utils.DummyLM(sub_actions or [{"answer": "fallback"}], adapter=adapter)

    def factory(call_index: int) -> ChildRuntimeLease:
        """Create a child runtime lease backed by an in-process interpreter.

        Parameters:
                call_index (int): Index used to identify the child runtime.

        Returns:
                ChildRuntimeLease: A lease for the newly created child runtime.
        """
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        if factory_calls is not None:
            factory_calls.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )

    return RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=options or RecursiveRLMOptions(),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 30,
        observer=observer,
        is_authorized=is_authorized,
    )


def test_native_child_depth_is_a_fixed_invariant_not_an_options_surface() -> None:
    import inspect

    parameters = set(inspect.signature(RecursiveRLMOptions).parameters)
    assert "max_depth" not in parameters
    assert RLM_NATIVE_CHILD_DEPTH == 1
    assert not hasattr(RecursiveRLMOptions(), "max_depth")
    with pytest.raises(TypeError, match="max_depth"):
        RecursiveRLMOptions(max_depth=2)  # type: ignore[call-arg]


def test_recursive_tool_runs_fresh_native_child_and_redacts_observation() -> None:
    events = []
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-ok')"}],
        factory_calls=created,
        observer=events.append,
    )

    result = executor.tool(prompt="classify selected row")

    assert result == "child-ok"
    assert len(created) == 1
    assert created[0]._shutdown
    assert executor.summary().call_count == 1
    assert executor.summary().child_iterations == 1
    started = next(event for event in events if isinstance(event, ToolStarted))
    completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert started.input == {"prompt_count": 1, "prompt_chars": len("classify selected row")}
    assert completed.output == {
        "status": "completed",
        "call_index": 1,
        "recursive_depth": 1,
        "child_iterations": 1,
        "termination_mode": "typed_submit",
    }
    statuses = [event for event in events if isinstance(event, Status)]
    assert [event.status for event in statuses] == ["child_started", "child_completed"]
    assert statuses[0].message == "call_index=1 recursive_depth=1"
    assert statuses[1].message is not None
    assert "duration_ms=" in statuses[1].message
    assert "cleanup_status=completed" in statuses[1].message
    assert "classify selected row" not in repr(events)
    assert "child-ok" not in repr(events)


def test_recursive_tool_uses_sub_lm_at_depth_cap_without_new_interpreter() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [
            {"reasoning": "delegate deeper", "code": "inner = rlm_query(prompt='inner slice')"},
            {"reasoning": "submit child", "code": "SUBMIT(answer=inner)"},
        ],
        sub_actions=[{"answer": "fallback-answer"}],
        factory_calls=created,
    )

    assert executor.tool(prompt="outer slice") == "fallback-answer"
    assert len(created) == 1
    assert executor.summary().call_count == 2
    assert executor.summary().depth_fallback_count == 1
    assert "depth_fallback" in executor.summary().termination_modes


@pytest.mark.parametrize(
    ("prompt", "message"),
    [("", "must not be empty"), ("x" * 11, "character bound")],
)
def test_recursive_tool_rejects_invalid_prompt_before_child_creation(prompt: str, message: str) -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_prompt_chars=10),
        factory_calls=created,
    )

    with pytest.raises(ValueError, match=message):
        executor.tool(prompt=prompt)
    assert created == []


def test_recursive_tool_enforces_shared_call_budget() -> None:
    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='ok')"}],
        options=RecursiveRLMOptions(max_calls=1),
    )

    assert executor.tool(prompt="first") == "ok"
    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.tool(prompt="second")


def test_recursive_batched_tool_preserves_order_and_bounds_child_concurrency() -> None:
    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM(
        {
            "FANOUT-A": {"reasoning": "a", "code": "SUBMIT(answer='A')"},
            "FANOUT-B": {"reasoning": "b", "code": "SUBMIT(answer='B')"},
            "FANOUT-C": {"reasoning": "c", "code": "SUBMIT(answer='C')"},
        },
        adapter=adapter,
    )
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        created.append(interpreter)
        return ChildRuntimeLease(
            interpreter,
            f"batch-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=2),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 30,
        observer=events.append,
    )

    assert executor.batched_tool(prompts=["FANOUT-A", "FANOUT-B", "FANOUT-C"]) == ["A", "B", "C"]
    summary = executor.summary()
    assert summary.recursive_batch_calls == 1
    assert summary.recursive_children_started == 3
    assert summary.recursive_children_completed == 3
    assert 1 <= summary.peak_child_concurrency <= 2
    assert summary.delegation_metrics.child_root_lm_calls_depth_1 == 3
    assert all(interpreter._shutdown for interpreter in created)
    batch_completed = next(event for event in events if isinstance(event, ToolCompleted))
    assert batch_completed.output["answer_count"] == 3
    assert 1 <= batch_completed.output["peak_child_concurrency"] <= 2


def test_recursive_batch_starts_each_trace_span_in_its_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    import fleet_rlm.rlm.recursive_calls as recursive_calls

    parent_thread = threading.get_ident()
    span_threads: list[int] = []

    class Span:
        def finish(self, **_kwargs: object) -> None:
            return None

    def start_span(_name: str, *, inputs: object) -> Span:
        del inputs
        span_threads.append(threading.get_ident())
        return Span()

    monkeypatch.setattr(recursive_calls, "start_turn_span", start_span)
    executor = _executor(
        {
            "first": {"reasoning": "first", "code": "SUBMIT(answer='first')"},
            "second": {"reasoning": "second", "code": "SUBMIT(answer='second')"},
        },  # type: ignore[arg-type]
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
    )

    assert executor.batched_tool(prompts=["first", "second"]) == ["first", "second"]
    assert len(span_threads) == 2
    assert all(thread_id != parent_thread for thread_id in span_threads)


def test_recursive_batch_join_stops_at_turn_deadline_and_worker_retains_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    import fleet_rlm.rlm.recursive_calls as recursive_calls

    release = threading.Event()
    started = threading.Event()
    closed = threading.Event()

    class Child:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            release.wait(1)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"deadline-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=1, max_parallel_children=1),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 0.05,
    )

    began = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="batch deadline exceeded"):
            executor.batched_tool(prompts=["blocked"])
        assert time.monotonic() - began < 0.5
        assert started.is_set()
        assert not closed.is_set()
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            executor.raise_if_cleanup_failed()
    finally:
        release.set()

    assert closed.wait(1)
    executor.raise_if_cleanup_failed()


def test_recursive_batched_tool_reserves_the_shared_budget_atomically() -> None:
    created: list[DaytonaCodeInterpreter] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        options=RecursiveRLMOptions(max_calls=2),
        factory_calls=created,
    )

    with pytest.raises(RuntimeError, match="budget exhausted"):
        executor.batched_tool(prompts=["first", "second", "third"])
    assert created == []
    assert executor.summary().call_count == 0


def test_recursive_tool_rejects_revoked_authority_before_child_creation() -> None:
    authority = RunAuthority()
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []
    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=lambda: not authority.revoked,
    )
    authority.revoke()

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="late child request")

    assert created == []
    assert [type(event) for event in events] == [ToolStarted, ToolFailed]


def test_recursive_tool_rechecks_authority_before_child_allocation() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether an authorization check is permitted.

        Returns:
                bool: `true` for the first check, `false` for subsequent checks.
        """
        nonlocal checks
        checks += 1
        return checks == 1

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked before allocation")

    assert created == []
    assert [event.status for event in events if isinstance(event, Status)] == []


def test_recursive_tool_closes_lease_when_authority_is_revoked_after_acquisition() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determine whether authorization remains available for the current check.

        Returns:
            bool: `True` for the first four checks, and `False` thereafter.
        """
        nonlocal checks
        checks += 1
        return checks < 5

    executor = _executor(
        [{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked after acquisition")

    assert len(created) == 1
    assert created[0]._shutdown
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message
    assert "cleanup_status=completed" in failed[0].message


def test_recursive_tool_discards_result_when_authority_is_revoked_after_execution() -> None:
    checks = 0
    created: list[DaytonaCodeInterpreter] = []
    events: list[object] = []

    def is_authorized() -> bool:
        """
        Determines whether authorization remains valid for the next check.

        Returns:
                bool: `True` for the first six checks and `False` thereafter.
        """
        nonlocal checks
        checks += 1
        return checks < 7

    executor = _executor(
        [{"reasoning": "submit", "code": "SUBMIT(answer='child-secret')"}],
        factory_calls=created,
        observer=events.append,
        is_authorized=is_authorized,
    )

    with pytest.raises(RuntimeError, match="no longer authorized"):
        executor.tool(prompt="revoked after execution")

    assert len(created) == 1
    assert created[0]._shutdown
    assert "child-secret" not in repr(events)
    failed = [event for event in events if isinstance(event, Status) and event.status == "child_failed"]
    assert len(failed) == 1
    assert failed[0].message is not None
    assert "failure_category=unauthorized" in failed[0].message


def test_rlm_query_wrapper_forwards_kwargs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """RC-1: the recursive ``rlm_query`` sandbox wrapper forwards prompts by name."""
    executor = _executor([{"reasoning": "submit", "code": "SUBMIT(answer='ok')"}])
    tool = executor.tool
    rlm = RLM("prompt -> answer", max_iters=1)
    invoke = rlm._make_interpreter_tool(tool)

    # The pinned DSPy interpreter tool accepts keyword arguments only.
    with pytest.raises(TypeError, match="positional"):
        invoke("delegate this slice")

    broker = DaytonaHttpToolBroker(sandbox=object())
    namespace: dict[str, object] = {}
    exec(broker._tool_wrapper_source("rlm_query", invoke), namespace, namespace)
    wrapper = namespace["rlm_query"]

    class _StubbedResponse:
        def read(self) -> bytes:
            return json.dumps({"result": "child-ok"}).encode("utf-8")

        def __enter__(self) -> _StubbedResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    captured: list[dict[str, object]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _StubbedResponse:
        del timeout
        captured.append(json.loads(bytes(request.data).decode("utf-8")))
        return _StubbedResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # Sandbox-side ergonomics stay positional, exactly as model code writes them.
    assert wrapper("delegate this slice") == "child-ok"

    assert len(captured) == 1
    payload = captured[0]
    assert payload["tool_name"] == "rlm_query"
    assert payload["args"] == []
    assert payload["kwargs"] == {"prompt": "delegate this slice"}


def test_recursive_child_receives_only_rlm_query_again(monkeypatch: pytest.MonkeyPatch) -> None:
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    captured: list[dict[str, object]] = []

    class Child:
        def __call__(self, _interpreter, *, prompt):
            del prompt
            return dspy.Prediction(answer="child-ok", trajectory=[])

    def capture_build(**kwargs):
        captured.append(dict(kwargs))
        return Child()

    monkeypatch.setattr(recursive_calls, "build_native_rlm", capture_build)
    executor = _executor([{"reasoning": "unused", "code": "SUBMIT(answer='unused')"}])

    assert executor.tool(prompt="memory tools stay in the root") == "child-ok"
    assert len(captured) == 1
    tools = tuple(str(tool.name) for tool in captured[0]["tools"])
    assert tools == ("rlm_query",)


def test_recursive_batch_preserves_order_when_workers_finish_out_of_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    finish_order: list[str] = []
    created: list[ChildRuntimeLease] = []
    delays = {"A": 0.10, "B": 0.01, "C": 0.05}

    class Child:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            time.sleep(delays[prompt])
            finish_order.append(prompt)
            return dspy.Prediction(answer=prompt, trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=3),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 5,
    )

    assert executor.batched_tool(prompts=["A", "B", "C"]) == ["A", "B", "C"]
    assert finish_order == ["B", "C", "A"]
    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().recursive_children_completed == 3


def test_recursive_batch_wraps_failure_when_all_children_are_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_batch as recursive_batch
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    created: list[ChildRuntimeLease] = []

    class Child:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            if prompt == "fail":
                raise ValueError("provider failure")
            return dspy.Prediction(answer="ok", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())
    real_wait = recursive_batch.wait

    def wait_for_all(futures, *, timeout=None, **_kwargs):
        return real_wait(futures, timeout=timeout)

    monkeypatch.setattr(recursive_batch, "wait", wait_for_all)

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 5,
    )

    with pytest.raises(RecursiveBatchError) as raised:
        executor.batched_tool(prompts=["fail", "ok"])
    assert isinstance(raised.value.__cause__, ValueError)
    executor.wait_owned()
    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().recursive_children_completed == 2


def test_recursive_batch_failure_returns_before_running_sibling_and_cleanup_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    a_started = threading.Event()
    b_started = threading.Event()
    release_b = threading.Event()
    created: list[ChildRuntimeLease] = []

    class Child:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            if prompt == "A":
                a_started.set()
                assert b_started.wait(1)
                raise ValueError("A failed")
            b_started.set()
            release_b.wait(2)
            return dspy.Prediction(answer="B", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: Child())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=2),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 5,
    )
    result: dict[str, BaseException] = {}

    def run_batch() -> None:
        try:
            executor.batched_tool(prompts=["A", "B"])
        except BaseException as exc:
            result["error"] = exc

    worker = threading.Thread(target=run_batch)
    worker.start()
    assert a_started.wait(1)
    assert b_started.wait(1)
    worker.join(1)
    assert not worker.is_alive()
    assert isinstance(result.get("error"), RecursiveBatchError)
    assert any(not lease.interpreter._shutdown for lease in created)
    with pytest.raises(RuntimeError, match="cleanup is still pending"):
        executor.raise_if_cleanup_failed()

    release_b.set()
    executor.wait_owned()
    assert all(lease.interpreter._shutdown for lease in created)
    executor.raise_if_cleanup_failed()


def test_recursive_batch_submit_failure_retains_already_submitted_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_batch as recursive_batch
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    release = threading.Event()
    started = threading.Event()
    closed = threading.Event()
    real_pool = recursive_batch.ThreadPoolExecutor(max_workers=1)

    class FailingPool:
        submits = 0

        def submit(self, *args: object, **kwargs: object):
            self.submits += 1
            if self.submits == 1:
                future = real_pool.submit(*args, **kwargs)
                assert started.wait(1)
                return future
            raise RuntimeError("child worker submit failed")

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            real_pool.shutdown(wait=wait, cancel_futures=cancel_futures)

    monkeypatch.setattr(recursive_batch, "ThreadPoolExecutor", lambda **_kwargs: FailingPool())

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())

    def factory(call_index: int) -> ChildRuntimeLease:
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())

        def close() -> None:
            interpreter.shutdown()
            closed.set()

        return ChildRuntimeLease(
            interpreter,
            f"submit-child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=2, max_parallel_children=1),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 10,
    )

    try:
        with pytest.raises(RuntimeError, match="submit failed"):
            executor.batched_tool(prompts=["first", "second"])
        assert started.wait(1)
        assert not closed.is_set()
    finally:
        release.set()
        executor.wait_owned()

    assert closed.is_set()


def test_executor_wait_owned_times_out_when_child_worker_never_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    adapter = dspy.JSONAdapter()
    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter),
        ),
        options=RecursiveRLMOptions(max_calls=1, max_parallel_children=1),
        child_runtime_factory=FakeChildRuntimeFactory(
            lambda call_index: ChildRuntimeLease(
                DaytonaCodeInterpreter(backend=InProcessInterpreterBackend()),
                f"child-{call_index}",
                "test-volume",
                f"recursive/test-workspace/test-run/{call_index}",
                lambda: None,
            )
        ),
        deadline=time.monotonic() + 10,
    )
    never_done: Future[str] = Future()
    executor._retain_pending_batch_futures({never_done})
    monkeypatch.setattr(recursive_calls, "_PENDING_BATCH_WAIT_TIMEOUT_S", 0.05)

    started = time.monotonic()
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        executor.wait_owned()
    assert time.monotonic() - started < 2


def test_recursive_batch_cancels_queued_children_before_they_acquire_a_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.rlm.recursive_batch as recursive_batch
    import fleet_rlm.rlm.recursive_calls as recursive_calls

    adapter = dspy.JSONAdapter()
    root = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    sub = dspy.utils.DummyLM([{"answer": "unused"}], adapter=adapter)
    started = threading.Event()
    release = threading.Event()
    call_indexes: list[int] = []
    created: list[ChildRuntimeLease] = []

    class BlockingChild:
        def __call__(self, _interpreter: object, *, prompt: str) -> dspy.Prediction:
            del prompt
            started.set()
            release.wait(2)
            return dspy.Prediction(answer="late", trajectory=[])

    monkeypatch.setattr(recursive_calls, "build_native_rlm", lambda **_kwargs: BlockingChild())
    real_wait = recursive_batch.wait

    def early_wait(futures, *, timeout=None, return_when=None):
        bounded = 0.05 if timeout is None else min(timeout, 0.05)
        if return_when is None:
            return real_wait(futures, timeout=bounded)
        return real_wait(futures, timeout=bounded, return_when=return_when)

    monkeypatch.setattr(recursive_batch, "wait", early_wait)

    def factory(call_index: int) -> ChildRuntimeLease:
        call_indexes.append(call_index)
        interpreter = DaytonaCodeInterpreter(backend=InProcessInterpreterBackend())
        lease = ChildRuntimeLease(
            interpreter,
            f"child-{call_index}",
            "test-volume",
            f"recursive/test-workspace/test-run/{call_index}",
            interpreter.shutdown,
        )
        created.append(lease)
        return lease

    executor = RecursiveRLMExecutor(
        models=RLMModelBundle(root, sub),
        options=RecursiveRLMOptions(max_calls=3, max_parallel_children=1),
        child_runtime_factory=FakeChildRuntimeFactory(factory),
        deadline=time.monotonic() + 1,
    )

    try:
        with pytest.raises(TimeoutError, match="batch deadline exceeded"):
            executor.batched_tool(prompts=["A", "B", "C"])
        assert started.wait(1)
        assert call_indexes == [1]
        assert len(created) == 1
        with pytest.raises(RuntimeError, match="cleanup is still pending"):
            executor.raise_if_cleanup_failed()
        with pytest.raises(ChildRuntimeCleanupError, match="cleanup is still pending"):
            executor.tool(prompt="retry")
    finally:
        release.set()
        executor.wait_owned()

    assert all(lease.interpreter._shutdown for lease in created)
    assert executor.summary().call_count == 3
    assert executor.summary().recursive_children_started == 1
    assert executor.summary().recursive_children_completed == 1
