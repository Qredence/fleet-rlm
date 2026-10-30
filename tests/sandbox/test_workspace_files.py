"""Workspace file transfer through Daytona's path overloads and the 25 MB provider limit."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.sandbox.manager import DaytonaSandboxManager


def test_failed_download_span_omits_private_exception_details(monkeypatch, tmp_path):
    import mlflow
    from mlflow.entities import SpanStatusCode
    from mlflow.entities.trace_location import MlflowExperimentLocation

    previous_uri = mlflow.get_tracking_uri()
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'traces.db'}")
    try:
        experiment_id = mlflow.create_experiment("private-transfer-failure")
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
    finally:
        mlflow.set_tracking_uri(previous_uri)


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
    assert len(calls) == 1
    with pytest.raises(ValueError):
        manager.stat_workspace_file("../outside.txt")
    assert len(calls) == 1


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
