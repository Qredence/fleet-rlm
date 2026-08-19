"""P22 contract tests: versioned installed Workspace Agent protocol.

Proves once-per-Sandbox install, checksum handshake, fail-closed mismatch
behavior, compact operation wire payloads, per-Sandbox reuse/replacement
semantics, transport metrics, and parity with the hardened remote dispatch.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fleet_rlm.daytona import workspace_agent as wa

_FULL_SOURCE_MARKERS = ("def respond(payload):", "O_NOFOLLOW", "fcntl.flock")


class _InstallFs:
    """Fake Daytona fs surface that materializes uploads for local execution."""

    def __init__(self) -> None:
        self.writes: list[tuple[bytes, str]] = []
        self.fail_upload = False
        self.write_garbage = False

    def _write(self, data: bytes, path: str) -> None:
        if self.fail_upload:
            raise OSError("upload failed")
        payload = b"garbage-remote-source" if self.write_garbage else bytes(data)
        self.writes.append((bytes(data), path))
        Path(path).write_bytes(payload)

    def upload_file(self, data: bytes, path: str) -> None:
        self._write(data, path)

    async def upload_file_async(self, data: bytes, path: str) -> None:  # pragma: no cover - helper
        self._write(data, path)


class _AsyncFsFacade:
    def __init__(self, fs: _InstallFs) -> None:
        self._fs = fs

    async def upload_file(self, data: bytes, path: str) -> None:
        self._fs.upload_file(data, path)


class _ExecProcess:
    """Fake Daytona process surface that really executes submitted code."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.timeouts: list[Any] = []

    def code_run(self, code: str, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(code)
        self.timeouts.append(kwargs.get("timeout"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                exec(code, {})
            except SystemExit:
                pass
            except Exception as exc:  # loading a missing/tampered module fails
                return SimpleNamespace(exit_code=1, result=str(exc))
        return SimpleNamespace(exit_code=0, result=buf.getvalue().strip())


class _AsyncProcessFacade:
    def __init__(self, process: _ExecProcess) -> None:
        self._process = process

    async def code_run(self, code: str, **kwargs: Any) -> SimpleNamespace:
        return self._process.code_run(code, **kwargs)


def _sandbox(sandbox_id: str = "sandbox-1") -> tuple[SimpleNamespace, _ExecProcess, _InstallFs]:
    process = _ExecProcess()
    fs = _InstallFs()
    return SimpleNamespace(id=sandbox_id, process=process, fs=fs), process, fs


def _stat_args(volume: Path, root: Path) -> dict[str, object]:
    return {
        "volume_root": str(volume),
        "root": str(root),
        "operation": "stat",
        "relative": "note.txt",
        "allow_missing": True,
        "max_bytes": 1024,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }


@pytest.fixture(autouse=True)
def _isolated_install_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wa, "WORKSPACE_AGENT_INSTALL_PATH", str(tmp_path / "agent.py"))
    wa._AGENT_SESSIONS.clear()
    yield
    wa._AGENT_SESSIONS.clear()


def test_install_once_then_compact_requests_with_metrics(tmp_path: Path) -> None:
    sandbox, process, fs = _sandbox()
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)

    args = _stat_args(volume, root)
    first = wa.run_workspace_agent(sandbox, **args)
    assert first["ok"] is True
    second = wa.run_workspace_agent(sandbox, **args)
    assert second["ok"] is True

    # 2 handshakes (probe + post-install verify), then 2 compact operations.
    assert len(process.calls) == 4
    assert len(fs.writes) == 1
    handshake_call = process.calls[0]
    op_call, second_op_call = process.calls[2], process.calls[3]
    assert "__handshake__" in handshake_call
    assert "__handshake__" in process.calls[1]
    for call in (op_call, second_op_call):
        for marker in _FULL_SOURCE_MARKERS:
            assert marker not in call
        assert len(call.encode("utf-8")) < 4096

    metrics = wa.workspace_agent_metrics(sandbox)
    assert metrics.bootstrap_count == 1
    assert metrics.handshake_calls == 2
    assert metrics.operation_calls == 2
    expected_bytes = len(wa.build_installed_workspace_agent_source().encode("utf-8"))
    assert metrics.source_transfer_bytes == expected_bytes
    assert fs.writes[0][0] == wa.build_installed_workspace_agent_source().encode("utf-8")
    assert metrics.latency_ms_total >= 0.0


def test_tampered_remote_agent_is_restored_and_verified(tmp_path: Path) -> None:
    sandbox, _process, fs = _sandbox()
    Path(wa.WORKSPACE_AGENT_INSTALL_PATH).write_text("garbage-remote-source")
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)

    payload = wa.run_workspace_agent(sandbox, **_stat_args(volume, root))
    assert payload["ok"] is True
    assert len(fs.writes) == 1
    assert sandbox_id_verified(sandbox)
    installed_bytes = wa.build_installed_workspace_agent_source().encode("utf-8")
    assert Path(wa.WORKSPACE_AGENT_INSTALL_PATH).read_bytes() == installed_bytes


def sandbox_id_verified(sandbox: Any) -> bool:
    return wa._agent_session(sandbox).verified


def test_install_failure_fails_closed_without_source_fallback(tmp_path: Path) -> None:
    sandbox, process, fs = _sandbox()
    fs.write_garbage = True
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)

    with pytest.raises(wa.WorkspaceAgentProtocolError):
        wa.run_workspace_agent(sandbox, **_stat_args(volume, root))
    # Only handshake probes ran; no per-operation full-source transmission.
    assert len(process.calls) == 2
    for call in process.calls:
        for marker in _FULL_SOURCE_MARKERS:
            assert marker not in call


def test_protocol_mismatch_maps_to_typed_error(tmp_path: Path) -> None:
    sandbox, _process, _fs = _sandbox()

    class _MismatchProcess(_ExecProcess):
        def code_run(self, code: str, **kwargs: Any) -> SimpleNamespace:
            if "__handshake__" in code:
                return super().code_run(code, **kwargs)
            if code.startswith("import importlib"):
                return SimpleNamespace(exit_code=0, result='{"ok": false, "error": "protocol_mismatch"}')
            return super().code_run(code, **kwargs)

    mismatch = _MismatchProcess()
    sandbox.process = mismatch
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)
    with pytest.raises(wa.WorkspaceAgentProtocolError):
        wa.run_workspace_agent(sandbox, **_stat_args(volume, root))


def test_request_bound_enforced_before_operation(tmp_path: Path) -> None:
    sandbox, process, _fs = _sandbox()
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)
    args = _stat_args(volume, root)
    args.update({"operation": "write", "overwrite": True, "content_b64": "eA" * (17 * 1024 * 1024)})
    with pytest.raises(wa.WorkspaceAgentProtocolError, match="bound"):
        wa.run_workspace_agent(sandbox, **args)
    # Install handshake ran; the oversized operation never reached the provider.
    assert sum(1 for call in process.calls if "__handshake__" not in call) == 0


def test_replacement_sandbox_installs_independently(tmp_path: Path) -> None:
    process_a, fs_a = _ExecProcess(), _InstallFs()
    process_b, fs_b = _ExecProcess(), _InstallFs()
    first = SimpleNamespace(id="sandbox-a", process=process_a, fs=fs_a)
    second = SimpleNamespace(id="sandbox-b", process=process_b, fs=fs_b)
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)

    wa.run_workspace_agent(first, **_stat_args(volume, root))
    assert len(fs_a.writes) == 1
    # A replacement starts with a fresh Sandbox filesystem.
    Path(wa.WORKSPACE_AGENT_INSTALL_PATH).unlink()
    wa.run_workspace_agent(second, **_stat_args(volume, root))
    assert len(fs_b.writes) == 1
    # Retained identity: a second call on the first sandbox stays installed.
    wa.run_workspace_agent(first, **_stat_args(volume, root))
    assert len(fs_a.writes) == 1
    assert wa.workspace_agent_metrics(first).bootstrap_count == 1
    assert wa.workspace_agent_metrics(second).bootstrap_count == 1


def test_drop_session_forces_rehandshake(tmp_path: Path) -> None:
    sandbox, process, fs = _sandbox()
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)
    wa.run_workspace_agent(sandbox, **_stat_args(volume, root))
    calls_after_install = len(process.calls)

    wa.drop_workspace_agent_session(sandbox)
    wa.run_workspace_agent(sandbox, **_stat_args(volume, root))
    # Re-handshake observed the still-valid installed digest: no re-upload.
    assert len(fs.writes) == 1
    assert len(process.calls) == calls_after_install + 1 + 1


@pytest.mark.asyncio
async def test_async_install_single_flight_and_compact_ops(tmp_path: Path) -> None:
    base_process = _ExecProcess()
    base_fs = _InstallFs()
    sandbox = SimpleNamespace(id="async-1", process=_AsyncProcessFacade(base_process), fs=_AsyncFsFacade(base_fs))
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)

    first, second = await asyncio.gather(
        wa.run_workspace_agent_async(sandbox, **_stat_args(volume, root)),
        wa.run_workspace_agent_async(sandbox, **_stat_args(volume, root)),
    )
    assert first["ok"] is True and second["ok"] is True
    assert len(base_fs.writes) == 1
    metrics = wa.workspace_agent_metrics(sandbox)
    assert metrics.bootstrap_count == 1
    assert metrics.operation_calls == 2
    for call in base_process.calls:
        if "__handshake__" in call:
            continue
        for marker in _FULL_SOURCE_MARKERS:
            assert marker not in call


def test_process_only_sandbox_keeps_legacy_wire(tmp_path: Path) -> None:
    class _NoFsProcess(_ExecProcess):
        pass

    process = _NoFsProcess()
    sandbox = SimpleNamespace(process=process)
    volume = tmp_path / "vol"
    root = volume / "root"
    root.mkdir(parents=True)
    (root / "note.txt").write_text("hello")

    payload = wa.run_workspace_agent(sandbox, **_stat_args(volume, root))
    assert payload["ok"] is True
    assert len(process.calls) == 1
    assert "def respond(payload) -> NoReturn:" in process.calls[0]


def _memory_args(volume: Path, root: Path, operation: str, **overrides: object) -> dict[str, object]:
    """
    Build memory-operation arguments with defaults and optional overrides.

    Parameters:
        volume (Path): Root path of the memory volume.
        root (Path): Root path used by the operation.
        operation (str): Memory operation to perform.
        overrides (object): Argument values that replace the defaults.

    Returns:
        dict[str, object]: Arguments for the memory operation.
    """
    args: dict[str, object] = {
        "volume_root": str(volume),
        "root": str(root),
        "operation": operation,
        "relative": "MEMORIES.md",
        "allow_missing": True,
        "max_bytes": 262_144,
        "total_file_bytes": 262_144,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }
    args.update(overrides)
    return args


def test_installed_memory_roundtrip_releases_locks_in_process(tmp_path: Path) -> None:
    """Same-process sequence pins the scope-agnostic lock cleanup: every
    Memory mutator that falls through with a held `.lock` must release it so
    the next op can flock again (real Daytona also gets this at process exit).
    """
    import base64

    sandbox, _process, _fs = _sandbox()
    volume = tmp_path / "vol"
    memory_root = volume / "memory"
    memory_root.mkdir(parents=True)

    first = b"- [2026-08-17T00:00:00Z] **General**: hello\n"
    append = wa.run_workspace_agent(
        sandbox,
        **_memory_args(volume, memory_root, "memory_append", content_b64=base64.b64encode(first).decode("ascii")),
    )
    assert append["ok"] is True
    memory_id = str(append["memory_id"])
    assert (memory_root / "MEMORIES.md.lock").exists()

    edit_body = base64.b64encode(
        b'{"learning": "hello world", "category": null, "updated_at": "2026-08-17T00:00:01Z"}'
    ).decode("ascii")
    edit = wa.run_workspace_agent(
        sandbox,
        **_memory_args(volume, memory_root, "memory_edit", memory_id=memory_id, content_b64=edit_body),
    )
    assert edit["ok"] is True
    assert "hello world" in str(edit["record"])

    tail = wa.run_workspace_agent(sandbox, **_memory_args(volume, memory_root, "tail_read"))
    assert tail["ok"] is True
    assert "hello world" in str(tail["content"])

    delete = wa.run_workspace_agent(sandbox, **_memory_args(volume, memory_root, "memory_delete", memory_id=memory_id))
    assert delete["ok"] is True

    # Idempotent same-record append: no duplicate, lock still cycles cleanly.
    repeat = wa.run_workspace_agent(
        sandbox,
        **_memory_args(volume, memory_root, "memory_append", content_b64=base64.b64encode(first).decode("ascii")),
    )
    assert repeat["ok"] is True
    # Content-derived id is stable across the delete+re-append cycle.
    assert repeat["memory_id"] == memory_id
    final_tail = wa.run_workspace_agent(sandbox, **_memory_args(volume, memory_root, "tail_read"))
    assert str(final_tail["content"]).count("hello") == 1


def test_installed_memory_migrate_roundtrip(tmp_path: Path) -> None:

    sandbox, _process, _fs = _sandbox()
    volume = tmp_path / "vol"
    volume.mkdir()
    (volume / "MEMORIES.md").write_bytes(b"- [2026-08-17T00:00:00Z] **General**: legacy\n")

    probe = wa.run_workspace_agent(sandbox, **_memory_args(volume, volume, "stat", relative="MEMORIES.md"))
    assert probe["ok"] is True and probe["entry"] is not None

    migrated = wa.run_workspace_agent(sandbox, **_memory_args(volume, volume, "memory_migrate"))
    assert migrated["ok"] is True
    memory_root = volume / "memory"
    assert (memory_root / "MEMORIES.md").read_bytes().startswith(b"# Fleet Memory v2\n")
    assert not (volume / "MEMORIES.md").exists()

    tail = wa.run_workspace_agent(sandbox, **_memory_args(volume, memory_root, "tail_read"))
    assert "legacy" in str(tail["content"])
