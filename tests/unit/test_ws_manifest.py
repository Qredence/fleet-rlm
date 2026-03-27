from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from fleet_rlm.api.routers.ws import manifest as ws_manifest


class _RecordingInterpreter:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def aexecute(
        self,
        code: str,
        variables: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> object:
        self.calls.append(
            {
                "code": code,
                "variables": variables,
                "kwargs": kwargs,
            }
        )
        return self.result


class _FakeDaytonaSession:
    def __init__(self) -> None:
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []
        self.file_contents: dict[str, str] = {}

    async def aread_file(self, path: str) -> str:
        self.read_calls.append(path)
        return self.file_contents[path]

    async def awrite_file(self, path: str, content: str) -> str:
        self.write_calls.append((path, content))
        self.file_contents[path] = content
        return path


def test_manifest_path_uses_default_session_id_when_missing() -> None:
    assert (
        ws_manifest._manifest_path("workspace-123", "user-456", "")
        == "meta/workspaces/workspace-123/users/user-456/react-session-default-session.json"
    )


def test_load_manifest_from_volume_returns_empty_without_interpreter() -> None:
    agent = cast(Any, SimpleNamespace(interpreter=None))

    manifest = asyncio.run(
        ws_manifest.load_manifest_from_volume(agent, "workspaces/test/session.json")
    )

    assert manifest == {}


def test_load_manifest_from_volume_returns_empty_on_invalid_json(
    monkeypatch,
) -> None:
    interpreter = _RecordingInterpreter(SimpleNamespace(output={"text": "{oops"}))
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(ws_manifest, "_is_final_output", lambda result: True)

    manifest = asyncio.run(
        ws_manifest.load_manifest_from_volume(agent, "workspaces/test/session.json")
    )

    assert manifest == {}


def test_load_manifest_from_volume_parses_json_payload(monkeypatch) -> None:
    interpreter = _RecordingInterpreter(SimpleNamespace(output={"text": '{"rev": 2}'}))
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(ws_manifest, "_is_final_output", lambda result: True)

    manifest = asyncio.run(
        ws_manifest.load_manifest_from_volume(agent, "workspaces/test/session.json")
    )

    assert manifest == {"rev": 2}
    assert interpreter.calls[0]["variables"] == {"path": "workspaces/test/session.json"}


def test_load_manifest_from_volume_uses_daytona_session(monkeypatch) -> None:
    session = _FakeDaytonaSession()
    session.file_contents["/home/daytona/memory/meta/workspaces/test/session.json"] = (
        '{"rev": 3, "state": {"ok": true}}'
    )
    agent = cast(
        Any,
        SimpleNamespace(
            interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")
        ),
    )

    async def _fake_get_daytona_session(_agent) -> _FakeDaytonaSession:
        return session

    monkeypatch.setattr(ws_manifest, "_aget_daytona_session", _fake_get_daytona_session)

    manifest = asyncio.run(
        ws_manifest.load_manifest_from_volume(
            agent, "meta/workspaces/test/session.json"
        )
    )

    assert manifest == {"rev": 3, "state": {"ok": True}}
    assert session.read_calls == [
        "/home/daytona/memory/meta/workspaces/test/session.json"
    ]


def test_load_manifest_from_volume_falls_back_to_legacy_path(monkeypatch) -> None:
    session = _FakeDaytonaSession()
    session.file_contents["/home/daytona/memory/workspaces/test/session.json"] = (
        '{"rev": 5}'
    )
    agent = cast(
        Any,
        SimpleNamespace(
            interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")
        ),
    )

    async def _fake_get_daytona_session(_agent) -> _FakeDaytonaSession:
        return session

    monkeypatch.setattr(ws_manifest, "_aget_daytona_session", _fake_get_daytona_session)

    manifest = asyncio.run(
        ws_manifest.load_manifest_from_volume(
            agent, "meta/workspaces/test/session.json"
        )
    )

    assert manifest == {"rev": 5}
    assert session.read_calls == [
        "/home/daytona/memory/meta/workspaces/test/session.json",
        "/home/daytona/memory/workspaces/test/session.json",
    ]


def test_save_manifest_to_volume_returns_none_without_interpreter() -> None:
    agent = cast(Any, SimpleNamespace(interpreter=None))

    saved_path = asyncio.run(
        ws_manifest.save_manifest_to_volume(
            agent,
            "workspaces/test/session.json",
            {"rev": 1},
        )
    )

    assert saved_path is None


def test_save_manifest_to_volume_returns_saved_path(monkeypatch) -> None:
    interpreter = _RecordingInterpreter(
        SimpleNamespace(output={"saved_path": "workspaces/test/session.json"})
    )
    agent = cast(Any, SimpleNamespace(interpreter=interpreter))

    monkeypatch.setattr(ws_manifest, "_is_final_output", lambda result: True)

    saved_path = asyncio.run(
        ws_manifest.save_manifest_to_volume(
            agent,
            "workspaces/test/session.json",
            {"rev": 1},
        )
    )

    assert saved_path == "workspaces/test/session.json"
    assert interpreter.calls[0]["variables"] == {
        "path": "workspaces/test/session.json",
        "payload": '{"rev": 1}',
    }


def test_save_manifest_to_volume_uses_daytona_session(monkeypatch) -> None:
    session = _FakeDaytonaSession()
    agent = cast(
        Any,
        SimpleNamespace(
            interpreter=SimpleNamespace(volume_mount_path="/home/daytona/memory")
        ),
    )

    async def _fake_get_daytona_session(_agent) -> _FakeDaytonaSession:
        return session

    monkeypatch.setattr(ws_manifest, "_aget_daytona_session", _fake_get_daytona_session)

    saved_path = asyncio.run(
        ws_manifest.save_manifest_to_volume(
            agent,
            "meta/workspaces/test/session.json",
            {"rev": 4},
        )
    )

    assert saved_path == "/home/daytona/memory/meta/workspaces/test/session.json"
    assert session.write_calls == [
        (
            "/home/daytona/memory/meta/workspaces/test/session.json",
            '{"rev": 4}',
        )
    ]
