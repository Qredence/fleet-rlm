from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleet_rlm.tools.artifacts import (
    create_artifact_impl,
    list_artifacts_impl,
    read_artifact_impl,
    update_artifact_impl,
)

VOLUME_ROOT = "/home/daytona/memory"


class _FakeSession:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.file_info: dict[str, SimpleNamespace] = {}
        self.write_calls: list[tuple[str, str]] = []
        self.move_calls: list[tuple[str, str]] = []
        self.delete_calls: list[str] = []
        self.list_calls: list[str] = []
        self.sandbox = SimpleNamespace(fs=_FakeFs(self))

    def _rebind_sandbox_if_needed(self) -> None:
        return None

    def _resolve_sandbox_path(self, path: str) -> str:
        return path

    def write_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        self.files[path] = content
        self.file_info[path] = SimpleNamespace(size=len(content.encode("utf-8")))
        return path

    def read_file(self, path: str) -> str:
        return self.files[path]

    def list_files(self, path: str) -> list[SimpleNamespace]:
        self.list_calls.append(path)
        prefix = path.rstrip("/") + "/"
        children: dict[str, SimpleNamespace] = {}
        for file_path in self.files:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            if not remainder:
                continue
            name = remainder.split("/", 1)[0]
            if name not in children:
                is_dir = any(other.startswith(f"{prefix}{name}/") for other in self.files if other != file_path)
                children[name] = SimpleNamespace(name=name, is_dir=is_dir, size=0)
        return list(children.values())


class _FakeFs:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def get_file_info(self, path: str) -> SimpleNamespace:
        if path not in self._session.files:
            raise FileNotFoundError(path)
        return self._session.file_info[path]

    def move_files(self, source: str, destination: str) -> None:
        self._session.move_calls.append((source, destination))
        content = self._session.files.pop(source)
        self._session.files[destination] = content
        self._session.file_info[destination] = SimpleNamespace(size=len(content.encode("utf-8")))

    def delete_file(self, path: str) -> None:
        self._session.delete_calls.append(path)
        self._session.files.pop(path, None)
        self._session.file_info.pop(path, None)


def _interpreter(session: _FakeSession) -> SimpleNamespace:
    return SimpleNamespace(
        _session=session,
        volume_mount_path=VOLUME_ROOT,
    )


def _artifact_root(session_id: str = "sess-1", category: str = "reports") -> str:
    return f"{VOLUME_ROOT}/artifacts/sessions/{session_id}/{category}"


def test_create_artifact_writes_under_approved_session_root() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)

    payload = create_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="summary.md",
        content="# Summary",
        interpreter=interpreter,
    )

    assert payload["status"] == "ok"
    artifact = payload["artifact"]["ref"]
    assert artifact["path"] == "artifacts/sessions/sess-1/reports/summary.md"
    assert any(path.endswith("/reports/summary.md") for path in session.files)
    assert "/home/daytona/memory" not in artifact["path"]
    assert "/home/daytona/memory" not in artifact["uri"]


@pytest.mark.parametrize(
    "relative_path",
    ["../secret.txt", "%2e%2e/secret.txt", "a%2fb", "a\\b", "/abs.txt"],
)
def test_create_artifact_rejects_unsafe_paths(relative_path: str) -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)

    payload = create_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path=relative_path,
        content="unsafe",
        interpreter=interpreter,
    )

    assert payload["status"] == "error"
    assert session.files == {}


def test_create_artifact_does_not_overwrite_existing() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    existing = f"{_artifact_root()}/summary.md"
    session.files[existing] = "old"
    session.file_info[existing] = SimpleNamespace(size=3)

    payload = create_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="summary.md",
        content="new",
        interpreter=interpreter,
    )

    assert payload["status"] == "error"
    assert session.files[existing] == "old"


def test_update_artifact_updates_existing_safe_artifact() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    existing = f"{_artifact_root()}/summary.md"
    session.files[existing] = "old"
    session.file_info[existing] = SimpleNamespace(size=3)

    payload = update_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="summary.md",
        content="updated",
        interpreter=interpreter,
    )

    assert payload["status"] == "ok"
    assert session.files[existing] == "updated"


def test_update_artifact_rejects_missing_artifact() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)

    payload = update_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="missing.md",
        content="updated",
        interpreter=interpreter,
    )

    assert payload["status"] == "error"


def test_list_artifacts_returns_metadata_only() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    existing = f"{_artifact_root(category='plans')}/phase-5.md"
    session.files[existing] = "# plan"
    session.file_info[existing] = SimpleNamespace(size=6)
    session.list_calls.clear()

    payload = list_artifacts_impl(session_id="sess-1", interpreter=interpreter)

    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert "content" not in payload["artifacts"][0]
    assert payload["artifacts"][0]["ref"]["category"] == "plans"
    dumped = str(payload)
    assert "/home/daytona/memory" not in dumped


def test_read_artifact_returns_bounded_content() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)
    existing = f"{_artifact_root(category='data')}/large.txt"
    session.files[existing] = "x" * 300_000
    session.file_info[existing] = SimpleNamespace(size=300_000)

    payload = read_artifact_impl(
        session_id="sess-1",
        category="data",
        relative_path="large.txt",
        max_bytes=200_000,
        interpreter=interpreter,
    )

    assert payload["status"] == "ok"
    assert payload["truncated"] is True
    assert payload["returned_bytes"] == 200_000
    assert len(payload["content"]) == 200_000
    assert payload["artifact_backed"] is True
    assert payload["artifact"]["ref"]["checksum"]
    assert "/home/daytona/memory" not in str(payload)


def test_public_artifact_responses_do_not_expose_raw_volume_paths() -> None:
    session = _FakeSession()
    interpreter = _interpreter(session)

    payload = create_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="summary.md",
        content="safe",
        interpreter=interpreter,
    )

    dumped = str(payload)
    assert "/home/daytona/memory" not in dumped
    assert "C:" not in dumped


def test_resolve_artifact_by_id_uses_session_index_without_tree_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.artifacts import io as artifact_io

    session = _FakeSession()
    interpreter = _interpreter(session)
    create_payload = create_artifact_impl(
        session_id="sess-1",
        category="reports",
        relative_path="summary.md",
        content="indexed",
        interpreter=interpreter,
    )
    artifact_id = create_payload["artifact"]["ref"]["id"]
    session.list_calls.clear()

    def fail_walk(*args: object, **kwargs: object) -> list[tuple[str, str]]:
        raise AssertionError("resolve_artifact_by_id should not walk the artifact tree when indexed")

    monkeypatch.setattr(artifact_io, "_walk_artifact_files", fail_walk)

    update_payload = update_artifact_impl(
        session_id="sess-1",
        artifact_id=artifact_id,
        content="updated via index",
        interpreter=interpreter,
    )

    assert update_payload["status"] == "ok"
    assert session.list_calls == []
