from __future__ import annotations

import pytest

from fleet_rlm.api import bootstrap_observability


class _FakeProcess:
    def __init__(self, pid: int = 1234, exit_code: int | None = None) -> None:
        self.pid = pid
        self._exit_code = exit_code

    def poll(self) -> int | None:
        return self._exit_code


@pytest.mark.asyncio
async def test_start_mlflow_server_uses_lightweight_local_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []

    monkeypatch.setattr(
        bootstrap_observability,
        "_mlflow_startup_socket_ready",
        lambda *, port: port == 5001 and len(popen_calls) > 0,
    )

    async def _fake_sleep(_seconds: int) -> None:
        return None

    monkeypatch.setattr(bootstrap_observability.asyncio, "sleep", _fake_sleep)

    def _fake_popen(args: list[str], **_kwargs) -> _FakeProcess:
        popen_calls.append(list(args))
        return _FakeProcess()

    monkeypatch.setattr(bootstrap_observability.subprocess, "Popen", _fake_popen)

    proc = await bootstrap_observability.start_mlflow_server(
        app_env="local",
        tracking_uri="http://127.0.0.1:5001",
    )

    assert proc is not None
    assert popen_calls == [
        [
            bootstrap_observability.sys.executable,
            "-m",
            "mlflow",
            "server",
            "--backend-store-uri",
            "sqlite:///mlruns.db",
            "--host",
            "127.0.0.1",
            "--port",
            "5001",
            "--workers",
            "1",
        ]
    ]


@pytest.mark.asyncio
async def test_start_mlflow_server_returns_none_when_process_exits_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bootstrap_observability,
        "_mlflow_startup_socket_ready",
        lambda *, port: False,
    )

    async def _fake_sleep(_seconds: int) -> None:
        return None

    monkeypatch.setattr(bootstrap_observability.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(
        bootstrap_observability.subprocess,
        "Popen",
        lambda args, **kwargs: _FakeProcess(exit_code=2),
    )

    proc = await bootstrap_observability.start_mlflow_server(
        app_env="local",
        tracking_uri="http://127.0.0.1:5001",
    )

    assert proc is None
