from __future__ import annotations

import hashlib
import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox.digests import DIGEST_CACHE_PATH
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
            return json.dumps([{"size": len(content)}])
        return json.dumps([{"size": None}])

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
    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", lambda *_a, **_k: json.dumps([{"size": None}]))
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
            )
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


def _run_remote_locally(tmp_path, calls):
    """Execute generated remote code with /workspace and /learned redirected to local stand-ins."""
    import contextlib
    import io

    def run(_self, code: str) -> str:
        calls.append(code)
        local = (
            code.replace("'/workspace'", repr(str(tmp_path / "workspace")))
            .replace("'/learned'", repr(str(tmp_path / "learned")))
            .replace(repr(DIGEST_CACHE_PATH), repr(str(tmp_path / "cache" / "digests.json")))
        )
        # A temporary directory is not a mount point; treat the stand-in as mounted.
        local = local.replace("os.path.ismount(", "os.path.isdir(")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(local, {})
        return out.getvalue()

    return run


def test_turn_inputs_gathers_digests_notes_and_environment_in_one_call(monkeypatch, tmp_path) -> None:
    import hashlib

    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files" / "a.txt").write_bytes(b"alpha")
    (workspace / "outside").mkdir()
    (workspace / "outside" / "secret.txt").write_bytes(b"secret")
    (workspace / "files" / "link.txt").symlink_to(workspace / "outside" / "secret.txt")
    (workspace / "linked-dir").symlink_to(workspace / "outside")
    (tmp_path / "learned").mkdir()
    (tmp_path / "learned" / "s-1.md").write_text("---\nsummary: Use Chudnovsky\n---\nbody\n")
    calls: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _run_remote_locally(tmp_path, calls))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/learned-notes")

    inputs = manager.turn_inputs()

    assert len(calls) == 1
    # Symbolic links are skipped and never traversed.
    assert inputs["digests"] == {
        "files/a.txt": hashlib.sha256(b"alpha").hexdigest(),
        "outside/secret.txt": hashlib.sha256(b"secret").hexdigest(),
    }
    assert inputs["notes"] == [{"file": "s-1.md", "summary": "Use Chudnovsky"}]
    assert inputs["environment"].startswith("Sandbox environment: Python ")
    assert "installed: " in inputs["environment"] and "not installed: " in inputs["environment"]
    assert "set_int_max_str_digits(0)" in inputs["environment"]


def test_turn_inputs_skips_notes_without_the_shared_mount(monkeypatch, tmp_path) -> None:
    (tmp_path / "workspace").mkdir()
    calls: list[str] = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _run_remote_locally(tmp_path, calls))

    inputs = DaytonaSandboxManager("key", "org", "volume", "session-a").turn_inputs()

    assert inputs["notes"] == [] and inputs["digests"] == {}
    assert "/learned" not in calls[0]


def test_turn_inputs_returns_only_the_newest_bounded_shared_notes(monkeypatch, tmp_path) -> None:
    import os

    (tmp_path / "workspace").mkdir()
    learned = tmp_path / "learned"
    learned.mkdir()
    for index in range(23):
        path = learned / f"note-{index}.md"
        path.write_text(f"---\nsummary: {'x' * 150}\n---\n")
        os.utime(path, (index + 1, index + 1))
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    notes = manager.turn_inputs()["notes"]

    assert [row["file"] for row in notes] == [f"note-{index}.md" for index in range(22, 2, -1)]
    assert all(row["summary"] == "x" * 120 for row in notes)


def test_turn_inputs_preserves_required_inputs_when_optional_notes_are_unreadable(monkeypatch, tmp_path, caplog):
    workspace = tmp_path / "workspace"
    (workspace / "files").mkdir(parents=True)
    (workspace / "files/data.txt").write_bytes(b"source")
    # A directory with a note suffix cannot be opened as a note file.
    (tmp_path / "learned/unreadable.md").mkdir(parents=True)
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _run_remote_locally(tmp_path, []))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    inputs = manager.turn_inputs()

    assert inputs["notes"] == []
    assert inputs["digests"] == {"files/data.txt": hashlib.sha256(b"source").hexdigest()}
    assert inputs["environment"].startswith("Sandbox environment: Python ")
    assert "Shared learned notes were unavailable" in caplog.text


@pytest.mark.parametrize("notes_limit", [0, 51])
def test_turn_inputs_skips_notes_outside_supported_limits(monkeypatch, tmp_path, notes_limit):
    (tmp_path / "workspace").mkdir()
    calls = []
    monkeypatch.setattr(DaytonaSandboxManager, "exec_remote_python_watched", _run_remote_locally(tmp_path, calls))
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a", shared_notes_subpath="shared/notes")

    assert manager.turn_inputs(notes_limit=notes_limit)["notes"] == []
    assert "/learned" not in calls[0]


def test_turn_inputs_reject_a_malformed_digest_listing(monkeypatch) -> None:
    monkeypatch.setattr(
        DaytonaSandboxManager,
        "exec_remote_python_watched",
        lambda _self, _code: '{"digests": {"files/a.txt": 3}, "notes": [], "environment": ""}',
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

    def remote_exec(_self, _sandbox, code):
        checks.append(code)
        return json.dumps([{"size": size} for size in sizes])

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", remote_exec)
    return manager, checks


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


def test_batched_read_rejects_a_file_that_changed_after_its_check(monkeypatch) -> None:
    filesystem = _BatchFileSystem({"/workspace/files/a.txt": b"grown content"})
    manager, _ = _batch_manager(monkeypatch, filesystem, [3])

    with pytest.raises(RuntimeError, match=r"changed or could not be downloaded: files/a\.txt"):
        manager.read_workspace_files(["files/a.txt"])


def test_batched_read_refuses_the_quota_before_downloading(monkeypatch) -> None:
    filesystem = _BatchFileSystem({})
    manager, _ = _batch_manager(monkeypatch, filesystem, [60, 60])

    with pytest.raises(ValueError, match="transfer quota"):
        manager.read_workspace_files(["files/a", "files/b"], max_total_bytes=100)
    assert filesystem.downloads == []


@pytest.mark.parametrize("bad", ["files/link.txt", "linked-dir/secret.txt"])
def test_batched_metadata_check_refuses_symbolic_links(monkeypatch, tmp_path, bad) -> None:
    import contextlib
    import io

    (tmp_path / "files").mkdir()
    (tmp_path / "files" / "ok.txt").write_bytes(b"ok")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_bytes(b"secret")
    (tmp_path / "files" / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (tmp_path / "linked-dir").symlink_to(tmp_path / "outside")

    def run_locally(_self, _sandbox, code):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code.replace("os.path.realpath('/workspace')", f"os.path.realpath({str(tmp_path)!r})"), {})
        return out.getvalue()

    monkeypatch.setattr(DaytonaSandboxManager, "_remote_exec", run_locally)
    manager = DaytonaSandboxManager("key", "org", "volume", "session-a")

    assert manager._workspace_files_metadata(object(), ["files/ok.txt"], create_parents=False) == [{"size": 2}]
    with pytest.raises(ValueError, match="symbolic link"):
        manager._workspace_files_metadata(object(), ["files/ok.txt", bad], create_parents=False)


def test_empty_batched_write_makes_no_remote_call(monkeypatch) -> None:
    monkeypatch.setattr(DaytonaSandboxManager, "ensure_sandbox", lambda _self: pytest.fail("no sandbox call"))

    DaytonaSandboxManager("key", "org", "volume", "session-a").write_workspace_files({})
