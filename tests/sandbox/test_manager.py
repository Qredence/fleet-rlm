from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox.manager import DaytonaSandboxManager


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

    def remote_exec(_self: DaytonaSandboxManager, sandbox_arg: Any, code: str) -> str:
        assert sandbox_arg is sandbox
        assert len(code.encode("utf-8")) < 10_000
        if "if not os.path.isfile(target)" in code:
            return json.dumps({"size": len(content)})
        return json.dumps({"size": None})

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)

    assert manager.write_workspace_file("files/large.csv", content) == "/workspace/files/large.csv"
    assert filesystem.uploaded == (content, "/workspace/files/large.csv", 1800)
    assert manager.read_workspace_file("files/large.csv", max_bytes=len(content)) == content


@pytest.mark.parametrize("subpath", ["/shared", "shared/../x", "shared//x", "shared/"])
def test_shared_notes_subpath_must_be_a_relative_volume_prefix(subpath: str) -> None:
    with pytest.raises(ValueError):
        FleetSettings(fleet_shared_notes_subpath=subpath)


def test_shared_notes_subpath_defaults_and_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_SHARED_NOTES_SUBPATH", raising=False)
    assert FleetSettings().fleet_shared_notes_subpath == "shared/learned-notes"
    assert FleetSettings(fleet_shared_notes_subpath="").fleet_shared_notes_subpath == ""


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


def test_shared_notes_mount_is_optional_and_never_reaches_child_sandboxes() -> None:
    plain = DaytonaSandboxManager("key", "org", "volume", "session-a")
    assert [mount.mount_path for mount in plain.volume_mounts(child=False)] == ["/workspace"]
    shared = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    assert [mount.mount_path for mount in shared.volume_mounts(child=True)] == ["/workspace"]


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


def test_learned_note_is_skipped_when_the_sandbox_predates_the_mount(monkeypatch) -> None:
    requested: list[bool] = []
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "False\n")
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: requested.append(True))

    assert manager.write_learned_note("turn-1", "A note.") is None
    assert requested == []


def test_learned_note_write_is_skipped_for_empty_notes_and_disabled_mounts(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: pytest.fail("no sandbox call expected")
    )
    enabled = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    disabled = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert enabled.write_learned_note("turn-1", "  \n") is None
    assert disabled.write_learned_note("turn-1", "A note.") is None


def test_shared_notes_listing_is_sandbox_safe_bounded_and_degrades_to_empty(monkeypatch) -> None:
    codes: list[str] = []
    rows = json.dumps([{"file": "session-a-turn0123.md", "summary": "Use Chudnovsky"}])
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, code: codes.append(code) or rows)

    assert manager.list_learned_notes() == [{"file": "session-a-turn0123.md", "summary": "Use Chudnovsky"}]
    # Generated sandbox source must not contain JSON literals: they raise NameError there.
    compile(codes[0], "<learned-notes>", "exec")
    for literal in ("true", "false", "null"):
        assert literal not in codes[0]

    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: "not json")
    assert manager.list_learned_notes() == []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", lambda _self, _code: '{"file": "x"}')
    assert manager.list_learned_notes() == []

    def unreachable(_self, _code):
        raise RuntimeError("sandbox dropped")

    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python", unreachable)
    assert manager.list_learned_notes() == []
    assert manager.list_learned_notes(limit=99) == []
    assert DaytonaSandboxManager("key", "org", "volume", "session-a").list_learned_notes() == []


def test_cancelled_manager_cannot_restart_remote_execution() -> None:
    stopped = threading.Event()
    sandbox = SimpleNamespace(id="sandbox-cancel", state="started")
    manager = DaytonaSandboxManager("key", "org", "volume", "cancel-test")
    manager._sandbox = sandbox
    manager._client = SimpleNamespace(stop=lambda *_args, **_kwargs: stopped.set())
    manager.cancel_turn()
    assert stopped.is_set()
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.ensure_sandbox()
    manager.reset_turn_budget()
    manager.end_turn()
    assert manager.ensure_sandbox() is sandbox


def test_managers_with_the_same_credentials_share_one_daytona_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.sandbox import manager as manager_module

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


def test_ensure_sandbox_refreshes_remote_state_before_deciding_to_restart() -> None:
    # Daytona auto-stops an idle sandbox; the cached handle still says "started".
    sandbox = _RemoteSandbox("started", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 1 and calls == ["start"]
    assert manager._contexts == {}, "interpreter contexts are invalid after a stop"


def test_ensure_sandbox_waits_for_a_settling_sandbox_before_starting_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.sandbox import manager as manager_module

    monkeypatch.setattr(manager_module, "_STATE_POLL_SECONDS", 0.0)
    sandbox = _RemoteSandbox("started", "stopping", "stopping", "stopped")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert sandbox.refreshes == 3 and calls == ["start"]


def test_ensure_sandbox_refuses_a_failed_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "error")
    manager, calls = _manager_with(sandbox)
    with pytest.raises(RuntimeError, match="error"):
        manager.ensure_sandbox()
    assert calls == []


def test_ensure_sandbox_does_not_touch_a_running_sandbox() -> None:
    sandbox = _RemoteSandbox("started", "started")
    manager, calls = _manager_with(sandbox)
    assert manager.ensure_sandbox() is sandbox
    assert calls == [] and manager._contexts != {}


@pytest.mark.parametrize("transient", [True, False])
def test_idempotent_daytona_calls_retry_only_transient_failures(
    monkeypatch: pytest.MonkeyPatch, transient: bool
) -> None:
    from daytona.common.errors import DaytonaConnectionError, DaytonaError

    from fleet_rlm.sandbox import repl as repl_module

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
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_a, **_k: json.dumps({"size": None}))
    if transient:
        assert manager.write_workspace_file("files/a.txt", b"x") == "/workspace/files/a.txt"
        assert len(attempts) == 2
    else:
        with pytest.raises(DaytonaError, match="no such volume"):
            manager.write_workspace_file("files/a.txt", b"x")
        assert len(attempts) == 1


def test_run_code_transport_failures_are_never_retried() -> None:
    from daytona.common.errors import DaytonaTimeoutError

    from fleet_rlm.sandbox import repl as repl_module

    attempts: list[int] = []

    def never(*_args: Any, **_kwargs: Any) -> None:
        attempts.append(1)
        raise DaytonaTimeoutError("Execution timed out")

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=never), fs=SimpleNamespace())
    with pytest.raises(DaytonaTimeoutError):
        repl_module.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5)
    assert attempts == [1]


def test_artifact_execution_is_not_replayed_after_losing_its_response(monkeypatch: pytest.MonkeyPatch) -> None:
    from daytona.common.errors import DaytonaConnectionError

    from fleet_rlm.sandbox import repl as repl_module

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
