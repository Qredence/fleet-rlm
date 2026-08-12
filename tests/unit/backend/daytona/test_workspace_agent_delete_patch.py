"""Workspace-agent ``delete`` and ``patch`` operations (WS-7 / PR-G).

WS-7 user-approved deviation: the Session Workspace "append/update-only, no
delete Tool" invariant ended; the agent gained strict ``delete`` (files and
EMPTY directories only) and ``patch`` (bounded unique find-replace) ops.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
from contextlib import redirect_stdout, suppress
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest


class LocalProcess:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def code_run(self, code: str, **_kwargs):
        self.calls.append(code)
        output = StringIO()
        with redirect_stdout(output), suppress(SystemExit):
            exec(code, {})
        return SimpleNamespace(exit_code=0, result=output.getvalue().strip())


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    volume_root = tmp_path / "volume"
    root = volume_root / "sessions" / "session" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return volume_root, root


def _sandbox() -> tuple[SimpleNamespace, LocalProcess]:
    process = LocalProcess()
    return SimpleNamespace(process=process), process


def _run(tmp_path: Path, sandbox: SimpleNamespace, operation: str, relative: str, **overrides: object):
    from fleet_rlm.daytona.workspace_agent import run_workspace_agent

    volume_root, root = _layout(tmp_path)
    arguments = {
        "volume_root": str(volume_root),
        "root": str(root),
        "operation": operation,
        "relative": relative,
        "allow_missing": False,
        "max_bytes": 1024,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
        **overrides,
    }
    return run_workspace_agent(sandbox, **arguments)


def _patch_payload(old: str, new: str) -> str:
    return base64.b64encode(json.dumps({"old": old, "new": new}).encode("utf-8")).decode("ascii")


def _write(tmp_path: Path, relative: str, content: bytes) -> dict[str, object]:
    sandbox, _process = _sandbox()
    return _run(
        tmp_path,
        sandbox,
        "write",
        relative,
        allow_missing=True,
        content_b64=base64.b64encode(content).decode("ascii"),
    )


def test_delete_and_patch_params_are_repr_embedded() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    base = {
        "volume_root": "/home/daytona/fleet",
        "root": "/home/daytona/fleet/sessions/s/workspace",
        "operation": "delete",
        "relative": "note.txt",
        "allow_missing": False,
        "max_bytes": 1024,
        "limit": 0,
        "overwrite": False,
        "content_b64": "",
    }

    assert "expected_sha256 = ''" in build_workspace_agent_code(**base)
    assert "expected_sha256 = 'abc'" in build_workspace_agent_code(**base, expected_sha256="abc")


def test_delete_regular_file_ok(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"bye")
    sandbox, _process = _sandbox()

    payload = _run(tmp_path, sandbox, "delete", "note.txt")

    assert payload == {"ok": True}
    assert not (_layout(tmp_path)[1] / "note.txt").exists()


def test_delete_missing_file_is_not_found(tmp_path: Path) -> None:
    _layout(tmp_path)
    sandbox, _process = _sandbox()

    with pytest.raises(FileNotFoundError):
        _run(tmp_path, sandbox, "delete", "missing.txt")


def test_delete_empty_directory_ok(tmp_path: Path) -> None:
    directory = _layout(tmp_path)[1] / "notes"
    directory.mkdir()
    sandbox, _process = _sandbox()

    payload = _run(tmp_path, sandbox, "delete", "notes")

    assert payload == {"ok": True}
    assert not directory.exists()


def test_delete_non_empty_directory_conflicts(tmp_path: Path) -> None:
    directory = _layout(tmp_path)[1] / "notes"
    directory.mkdir()
    (directory / "kept.txt").write_text("kept", encoding="utf-8")
    sandbox, _process = _sandbox()

    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError) as excinfo:
        _run(tmp_path, sandbox, "delete", "notes")
    assert excinfo.value.detail == "not_empty"
    assert directory.exists()  # strict: no force flag, contents preserved


def test_delete_root_is_refused(tmp_path: Path) -> None:
    _layout(tmp_path)
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "delete", ".")


def test_delete_symlink_fails_closed_and_leaves_it_untouched(tmp_path: Path) -> None:
    _write(tmp_path, "target.txt", b"target")
    link = _layout(tmp_path)[1] / "link.txt"
    link.symlink_to("target.txt")
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "delete", "link.txt")

    assert link.is_symlink()


def test_delete_fifo_fails_closed_without_hanging(tmp_path: Path) -> None:
    pipe = _layout(tmp_path)[1] / "pipe"
    os.mkfifo(pipe)
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "delete", "pipe")

    assert pipe.exists()


def test_delete_with_matching_checksum_succeeds_and_mismatch_conflicts(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"checksum me")
    sandbox, _process = _sandbox()

    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError) as excinfo:
        _run(tmp_path, sandbox, "delete", "note.txt", expected_sha256="0" * 64)
    assert excinfo.value.detail == "checksum_mismatch"
    assert (_layout(tmp_path)[1] / "note.txt").exists()

    good = hashlib.sha256(b"checksum me").hexdigest()
    payload = _run(tmp_path, sandbox, "delete", "note.txt", expected_sha256=good)
    assert payload == {"ok": True}
    assert not (_layout(tmp_path)[1] / "note.txt").exists()


def test_delete_checksum_precondition_on_directory_conflicts(tmp_path: Path) -> None:
    directory = _layout(tmp_path)[1] / "notes"
    directory.mkdir()
    sandbox, _process = _sandbox()

    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError) as excinfo:
        _run(tmp_path, sandbox, "delete", "notes", expected_sha256="f" * 64)
    assert excinfo.value.detail == "checksum_mismatch"


def test_patch_replaces_one_unique_occurrence_and_reports_checksum(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"hello world")
    sandbox, _process = _sandbox()

    payload = _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("world", "fleet"))

    assert payload["ok"] is True
    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert entry["checksum"] == hashlib.sha256(b"hello fleet").hexdigest()
    assert (_layout(tmp_path)[1] / "note.txt").read_bytes() == b"hello fleet"


def test_patch_requires_exactly_one_occurrence(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"dup dup")
    sandbox, _process = _sandbox()

    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError) as ambiguous:
        _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("dup", "x"))
    assert ambiguous.value.detail == "ambiguous"

    with pytest.raises(WorkspaceConflictError) as missing:
        _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("nope", "x"))
    assert missing.value.detail == "missing"

    # Nothing was mutated.
    assert (_layout(tmp_path)[1] / "note.txt").read_bytes() == b"dup dup"


def test_patch_enforces_the_checksum_precondition(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"hello world")
    sandbox, _process = _sandbox()

    from fleet_rlm.files.workspace_models import WorkspaceConflictError

    with pytest.raises(WorkspaceConflictError) as excinfo:
        _run(
            tmp_path,
            sandbox,
            "patch",
            "note.txt",
            content_b64=_patch_payload("world", "fleet"),
            expected_sha256="0" * 64,
        )
    assert excinfo.value.detail == "checksum_mismatch"
    assert (_layout(tmp_path)[1] / "note.txt").read_bytes() == b"hello world"

    good = hashlib.sha256(b"hello world").hexdigest()
    payload = _run(
        tmp_path,
        sandbox,
        "patch",
        "note.txt",
        content_b64=_patch_payload("world", "fleet"),
        expected_sha256=good,
    )
    assert payload["ok"] is True


def test_patch_missing_file_is_not_found(tmp_path: Path) -> None:
    _layout(tmp_path)
    sandbox, _process = _sandbox()

    with pytest.raises(FileNotFoundError):
        _run(tmp_path, sandbox, "patch", "missing.txt", content_b64=_patch_payload("a", "b"))


def test_patch_directory_is_a_directory(tmp_path: Path) -> None:
    directory = _layout(tmp_path)[1] / "notes"
    directory.mkdir()
    sandbox, _process = _sandbox()

    with pytest.raises(IsADirectoryError):
        _run(tmp_path, sandbox, "patch", "notes", content_b64=_patch_payload("a", "b"))


def test_patch_fifo_fails_closed_without_hanging(tmp_path: Path) -> None:
    pipe = _layout(tmp_path)[1] / "pipe"
    os.mkfifo(pipe)
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "patch", "pipe", content_b64=_patch_payload("a", "b"))


def test_patch_symlink_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "target.txt", b"target")
    (_layout(tmp_path)[1] / "link.txt").symlink_to("target.txt")
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "patch", "link.txt", content_b64=_patch_payload("a", "b"))


def test_patch_rejects_invalid_utf8_content(tmp_path: Path) -> None:
    _write(tmp_path, "bin.txt", b"\xff\xfe binary")
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="UTF-8"):
        _run(tmp_path, sandbox, "patch", "bin.txt", content_b64=_patch_payload("binary", "x"))


def test_patch_rejects_malformed_arguments_as_cursor_errors(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"hello")
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="cursor"):
        _run(tmp_path, sandbox, "patch", "note.txt", content_b64=base64.b64encode(b"{not json").decode("ascii"))
    with pytest.raises(ValueError, match="cursor"):
        _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("", "x"))


def test_patch_respects_the_size_bound(tmp_path: Path) -> None:
    _write(tmp_path, "note.txt", b"x" * 64)
    sandbox, _process = _sandbox()

    with pytest.raises(ValueError, match="exceeds maximum size"):
        _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("x" * 64, "y" * 2048), max_bytes=1024)


def test_patch_composes_and_falls_through_the_publish_machinery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the live Volume backend (no rename), patch still mutates via the
    O_TRUNC overwrite fallback owned by the 'write' branch."""
    _write(tmp_path, "note.txt", b"first value")
    monkeypatch.setattr(os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(errno.ENOSYS, "no")))
    sandbox, _process = _sandbox()

    payload = _run(tmp_path, sandbox, "patch", "note.txt", content_b64=_patch_payload("first", "second"))

    assert payload["ok"] is True
    assert payload["warnings"] == [{"code": "non_atomic_overwrite"}]
    entry = payload["entry"]
    assert isinstance(entry, dict)
    assert entry["checksum"] == hashlib.sha256(b"second value").hexdigest()
    assert (_layout(tmp_path)[1] / "note.txt").read_bytes() == b"second value"


def test_patch_read_is_identity_pinned() -> None:
    """The patch branch reuses the (dev, ino, size) identity pin over an
    O_RDONLY handle, matching the memory_edit/append read discipline."""
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(
        volume_root="/home/daytona/fleet",
        root="/home/daytona/fleet/sessions/s/workspace",
        operation="patch",
        relative="note.txt",
        allow_missing=False,
        max_bytes=1024,
        limit=0,
        overwrite=False,
        content_b64=_patch_payload("a", "b"),
    )
    patch_branch = code[code.index("if operation == 'patch':") : code.index("if operation == 'write':")]
    assert "read_existing(" in patch_branch
    assert "expected_stat=target_stat" in patch_branch
    assert "stat.S_ISREG(target_stat.st_mode)" in patch_branch


def test_guarded_delete_symlink_and_fifo_fail_closed(tmp_path: Path) -> None:
    _write(tmp_path, "target.txt", b"target")
    root = _layout(tmp_path)[1]
    link = root / "link.txt"
    pipe = root / "pipe"
    link.symlink_to("target.txt")
    os.mkfifo(pipe)
    sandbox, _process = _sandbox()
    expected = hashlib.sha256(b"target").hexdigest()

    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "delete", "link.txt", expected_sha256=expected)
    with pytest.raises(ValueError, match="unsafe"):
        _run(tmp_path, sandbox, "delete", "pipe", expected_sha256=expected)
    assert link.is_symlink()
    assert pipe.exists()
    assert (root / "target.txt").read_bytes() == b"target"


def test_guarded_delete_locks_and_revalidates_the_exact_compared_revision() -> None:
    from fleet_rlm.daytona.workspace_agent import build_workspace_agent_code

    code = build_workspace_agent_code(
        volume_root="/home/daytona/fleet",
        root="/home/daytona/fleet/sessions/s/workspace",
        operation="delete",
        relative="note.txt",
        allow_missing=False,
        max_bytes=1024,
        limit=0,
        overwrite=False,
        content_b64="",
        expected_sha256="0" * 64,
    )
    delete_branch = code[code.index("if operation == 'delete':") : code.index("if operation == 'patch':")]
    lock_at = delete_branch.index("lock_existing(parent_fd, relative_parts[-1])")
    compare_at = delete_branch.index("hashlib.sha256(source).hexdigest() != expected_sha256")
    revalidate_at = delete_branch.index("current_stat = os.stat(relative_parts[-1]")
    unlink_at = delete_branch.index("os.unlink(relative_parts[-1], dir_fd=parent_fd)")
    assert lock_at < compare_at < revalidate_at < unlink_at
    assert "(current_stat.st_dev, current_stat.st_ino)" in delete_branch
    assert "if locked_fd is not None:" in delete_branch
