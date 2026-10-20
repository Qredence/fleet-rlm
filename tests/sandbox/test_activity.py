"""The sandbox busy/alive signal: local state only, fed by run_code, liveness polls, and lifecycle events."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest  # noqa: F401
from daytona.common.errors import DaytonaConnectionError

from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.manager import DaytonaSandboxManager
from fleet_rlm.sandbox.repl import ExecutionTracker, SandboxActivity, SandboxLostError


class _Sandbox(SimpleNamespace):
    """A started sandbox whose run_code writes one stdout chunk, then blocks until released."""

    def __init__(self, sandbox_id: str, *, die_after_start: bool = False) -> None:
        super().__init__(id=sandbox_id, state="started", refreshes=0)
        self.die_after_start = die_after_start
        self.running = threading.Event()
        self.release = threading.Event()
        self.code_interpreter = SimpleNamespace(run_code=self._run_code)

    def refresh_data(self) -> None:
        self.refreshes += 1
        if self.die_after_start and self.running.is_set():
            self.state = "destroyed"

    def _run_code(self, _code: str, *, context: Any, on_stdout: Any, timeout: int) -> Any:  # noqa: ARG002
        on_stdout(SimpleNamespace(output="working\n"))
        self.running.set()
        assert self.release.wait(30)
        if self.die_after_start:
            raise DaytonaConnectionError("websocket closed")
        on_stdout(SimpleNamespace(output=repl.RESULT_MARK + '{"stdout": "done"}\n'))
        return SimpleNamespace(error=None)


def _manager(sandbox: _Sandbox, *, parent: DaytonaSandboxManager | None = None) -> DaytonaSandboxManager:
    manager = DaytonaSandboxManager("key", "org", "volume", "activity", parent=parent)
    manager._client = SimpleNamespace(stop=lambda *_a, **_k: None)
    manager._sandbox = sandbox
    manager._contexts["ns"] = object()
    if parent is not None:
        parent._children[manager.agent_id] = manager
    return manager


def _start(manager: DaytonaSandboxManager, outcome: dict[str, Any]) -> threading.Thread:
    interpreter = SimpleNamespace(ns_id="ns", answer_host_call=lambda _call: {})

    def target() -> None:
        try:
            outcome["result"] = manager.run_namespace(interpreter, {"code": "work()"})  # type: ignore[arg-type]
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def test_an_idle_manager_reports_nothing_running_and_unknown_liveness() -> None:
    activity = DaytonaSandboxManager("key", "org", "volume", "activity").sandbox_activity()
    assert activity == SandboxActivity(
        busy=False,
        executions=0,
        busy_since=None,
        busy_since_monotonic=None,
        alive=None,
        alive_checked_at=None,
        alive_checked_monotonic=None,
        last_output_at=None,
        last_output_monotonic=None,
    )


def test_a_running_execution_is_busy_with_its_start_and_output_times(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    sandbox = _Sandbox("root-sb")
    root = _manager(sandbox)
    root.reset_turn_budget()
    outcome: dict[str, Any] = {}
    before_wall, before_mono = time.time(), time.monotonic()
    thread = _start(root, outcome)
    try:
        assert sandbox.running.wait(5)
        activity = root.sandbox_activity()
        assert activity.busy and activity.executions == 1
        assert before_wall <= (activity.busy_since or 0) <= time.time()
        assert before_mono <= (activity.busy_since_monotonic or 0) <= time.monotonic()
        assert activity.alive is True and activity.alive_checked_at is not None  # from ensure_sandbox
        assert activity.last_output_monotonic is not None and activity.last_output_monotonic >= before_mono
        # The liveness poll keeps refreshing the verdict while the execution runs.
        checked = activity.alive_checked_at
        deadline = time.monotonic() + 5
        while root.sandbox_activity().alive_checked_at == checked and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (root.sandbox_activity().alive_checked_at or 0) > (checked or 0)
        assert (root.sandbox_activity().alive_checked_monotonic or 0) > (activity.alive_checked_monotonic or 0)
    finally:
        sandbox.release.set()
        thread.join(5)
        root.end_turn()
    assert outcome["result"]["stdout"] == "done"
    after = root.sandbox_activity()
    assert not after.busy and after.executions == 0 and after.busy_since is None
    assert after.alive is True and after.last_output_monotonic is not None


def test_querying_never_calls_daytona_and_never_takes_the_manager_lock(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 60)  # no poll during the test window
    sandbox = _Sandbox("root-sb")
    root = _manager(sandbox)
    root.reset_turn_budget()
    outcome: dict[str, Any] = {}
    thread = _start(root, outcome)
    try:
        assert sandbox.running.wait(5)
        refreshes = sandbox.refreshes
        holder_has_lock, release_lock = threading.Event(), threading.Event()

        def hold_lock() -> None:
            with root._lock:
                holder_has_lock.set()
                release_lock.wait(5)

        holder = threading.Thread(target=hold_lock, daemon=True)
        holder.start()
        assert holder_has_lock.wait(5)
        started = time.monotonic()
        for _ in range(1000):
            assert root.sandbox_activity().busy
        assert time.monotonic() - started < 1.0
        assert sandbox.refreshes == refreshes  # no provider call per query
        release_lock.set()
        holder.join(5)
    finally:
        sandbox.release.set()
        thread.join(5)
        root.end_turn()


def test_a_sandbox_lost_mid_execution_is_reported_not_busy_and_not_alive(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    root = _manager(_Sandbox("root-sb"))
    sandbox = _Sandbox("child-sb", die_after_start=True)
    child = _manager(sandbox, parent=root)
    root.reset_turn_budget()
    outcome: dict[str, Any] = {}
    thread = _start(child, outcome)
    try:
        thread.join(5)
        assert isinstance(outcome.get("error"), SandboxLostError)
        activity = child.sandbox_activity()
        assert not activity.busy and activity.alive is False
    finally:
        sandbox.release.set()
        root.end_turn()


def test_child_executions_count_toward_the_root_but_liveness_stays_the_roots(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    root = _manager(_Sandbox("root-sb"))
    root._activity.observe_alive(True)
    sandbox = _Sandbox("child-sb")
    child = _manager(sandbox, parent=root)
    root.reset_turn_budget()
    outcome: dict[str, Any] = {}
    thread = _start(child, outcome)
    try:
        assert sandbox.running.wait(5)
        child._activity.observe_alive(False)  # a child verdict never becomes the root's
        combined, own = root.sandbox_activity(), root.sandbox_activity(include_children=False)
        assert combined.busy and combined.executions == 1 and combined.alive is True
        assert combined.busy_since == child.sandbox_activity().busy_since
        assert combined.last_output_at == child.sandbox_activity().last_output_at
        assert not own.busy and own.executions == 0 and own.last_output_at is None
    finally:
        sandbox.release.set()
        thread.join(5)
        root.end_turn()
    assert not root.sandbox_activity().busy


def test_a_stop_marks_the_sandbox_not_alive_until_it_is_resumed() -> None:
    root = _manager(_Sandbox("root-sb"))
    root.ensure_sandbox()
    assert root.sandbox_activity().alive is True
    root.cancel_turn()
    assert root.sandbox_activity().alive is False
    root.reset_turn_budget()
    root.ensure_sandbox()
    assert root.sandbox_activity().alive is True
    root.end_turn()
    root.shutdown()
    assert root.sandbox_activity().alive is False


def test_overlapping_executions_report_the_oldest_start() -> None:
    tracker = ExecutionTracker()
    first = tracker.begin()
    time.sleep(0.01)
    second = tracker.begin()
    oldest = tracker.snapshot()
    assert oldest.executions == 2
    tracker.end(first)
    newer = tracker.snapshot()
    assert newer.executions == 1 and (newer.busy_since_monotonic or 0) > (oldest.busy_since_monotonic or 0)
    tracker.end(second)
    tracker.end(second)  # ending twice is harmless
    assert tracker.snapshot().executions == 0


def test_the_tracker_is_thread_safe() -> None:
    tracker = ExecutionTracker()
    errors: list[BaseException] = []

    def churn() -> None:
        try:
            for _ in range(2000):
                token = tracker.begin()
                tracker.observe_output()
                tracker.observe_alive(True)
                tracker.snapshot()
                tracker.end(token)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == [] and tracker.snapshot().executions == 0


def test_run_without_a_tracker_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)

    def run_code(_code: str, *, context: Any, on_stdout: Any, timeout: int) -> Any:  # noqa: ARG001
        on_stdout(SimpleNamespace(output=repl.RESULT_MARK + '{"stdout": "ok"}\n'))
        return SimpleNamespace(error=None)

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=run_code))
    result = repl.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5, alive=lambda: True)
    assert result == {"stdout": "ok"}
