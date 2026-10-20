"""The sandbox busy/alive signal: local state only, fed by run_code, liveness polls, and lifecycle events."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
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


def _in_thread(operation: Any, outcome: dict[str, Any]) -> threading.Thread:
    """Run `operation` on a daemon thread, storing its return value or exception in `outcome`."""

    def target() -> None:
        try:
            outcome["result"] = operation()
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _start(manager: DaytonaSandboxManager, outcome: dict[str, Any]) -> threading.Thread:
    interpreter = SimpleNamespace(ns_id="ns", answer_host_call=lambda _call: {})
    return _in_thread(lambda: manager.run_namespace(interpreter, {"code": "work()"}), outcome)  # type: ignore[arg-type]


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


class _HostIOSandbox(_Sandbox):
    """A started sandbox whose remote exec and file transfers block until released."""

    def __init__(self, sandbox_id: str) -> None:
        super().__init__(sandbox_id)
        self.in_host_io = threading.Event()
        self.process = SimpleNamespace(exec=self._exec)
        self.fs = SimpleNamespace(upload_file=self._upload_file)
        self.block_exec = True

    def _exec(self, command: str, timeout: int | None = None) -> Any:  # noqa: ARG002
        if self.block_exec:
            self.in_host_io.set()
            assert self.release.wait(30)
            return SimpleNamespace(exit_code=0, result="ok")
        return SimpleNamespace(exit_code=0, result='[{"size": null}]')  # workspace metadata for one path

    def _upload_file(self, *_args: Any, **_kwargs: Any) -> None:
        self.in_host_io.set()
        assert self.release.wait(30)


def _while_blocked(sandbox: _HostIOSandbox, operation: Any) -> tuple[threading.Thread, dict[str, Any]]:
    outcome: dict[str, Any] = {}
    thread = _in_thread(operation, outcome)
    assert sandbox.in_host_io.wait(5)
    return thread, outcome


def test_a_remote_exec_is_host_io_not_busy_and_starts_no_liveness_poll(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    sandbox = _HostIOSandbox("root-sb")
    root = _manager(sandbox)
    before = time.monotonic()
    thread, outcome = _while_blocked(sandbox, lambda: root.exec_remote_python("print('ok')"))
    try:
        activity = root.sandbox_activity()
        assert activity.host_io_busy is True
        assert (activity.host_io_since_monotonic or 0) >= before
        # busy/executions/busy_since keep meaning "model code is running".
        assert not activity.busy and activity.executions == 0 and activity.busy_since_monotonic is None
        refreshes, checked = sandbox.refreshes, activity.alive_checked_monotonic
        time.sleep(0.2)  # 20 poll intervals: host IO must not start the liveness poll
        assert sandbox.refreshes == refreshes
        assert root.sandbox_activity().alive_checked_monotonic == checked
    finally:
        sandbox.release.set()
        thread.join(5)
    assert outcome == {"result": "ok"}
    after = root.sandbox_activity()
    assert not after.host_io_busy and after.host_io_since_monotonic is None


def test_a_file_upload_is_host_io_and_a_failed_one_still_ends(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    sandbox = _HostIOSandbox("root-sb")
    sandbox.block_exec = False  # the metadata check answers at once; the upload blocks
    root = _manager(sandbox)
    thread, outcome = _while_blocked(sandbox, lambda: root.write_workspace_file("files/a.txt", b"x"))
    try:
        activity = root.sandbox_activity()
        assert activity.host_io_busy and not activity.busy and activity.executions == 0
    finally:
        sandbox.release.set()
        thread.join(5)
    assert outcome == {"result": "/workspace/files/a.txt"}
    assert not root.sandbox_activity().host_io_busy

    def broken_upload(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("upload refused")

    sandbox.fs.upload_file = broken_upload
    with pytest.raises(ValueError, match="upload refused"):
        root.write_workspace_file("files/b.txt", b"y")
    assert not root.sandbox_activity().host_io_busy


def test_artifact_collection_is_host_io(monkeypatch) -> None:
    from fleet_rlm.optimize import evidence

    sandbox = _HostIOSandbox("root-sb")
    root = _manager(sandbox)
    seen: list[repl.SandboxActivity] = []

    def collect(manager: DaytonaSandboxManager) -> dict[str, Any]:
        seen.append(manager.sandbox_activity())
        return {"complete": True, "files": {}}

    monkeypatch.setattr(evidence, "collect_artifacts", collect)
    assert root.execution_evidence()["artifacts"] == {"complete": True, "files": {}}
    assert seen and seen[0].host_io_busy and not seen[0].busy
    assert not root.sandbox_activity().host_io_busy


def test_host_io_is_counted_so_overlapping_operations_work() -> None:
    tracker = ExecutionTracker()
    first = tracker.begin_host_io()
    start = tracker.snapshot().host_io_since_monotonic
    second = tracker.begin_host_io()
    tracker.end_host_io(first)
    activity = tracker.snapshot()
    assert activity.host_io_busy and activity.host_io_since_monotonic is not None
    assert activity.host_io_since_monotonic >= (start or 0)  # the oldest still-open operation
    assert not activity.busy and activity.executions == 0
    tracker.end_host_io(second)
    assert not tracker.snapshot().host_io_busy
    with pytest.raises(RuntimeError), tracker.host_io():
        assert tracker.snapshot().host_io_busy
        raise RuntimeError("boom")
    assert not tracker.snapshot().host_io_busy


def test_host_io_in_a_child_counts_for_the_root_without_making_it_busy() -> None:
    root = _manager(_Sandbox("root-sb"))
    child = _manager(_Sandbox("child-sb"), parent=root)
    token = child._activity.begin_host_io()
    try:
        merged = root.sandbox_activity()
        assert merged.host_io_busy and merged.host_io_since_monotonic is not None
        assert not merged.busy and merged.executions == 0
        assert not root.sandbox_activity(include_children=False).host_io_busy
    finally:
        child._activity.end_host_io(token)
    assert not root.sandbox_activity().host_io_busy
