from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.cli import supervisor
from fleet_rlm.persistence.database import DatabaseCompatibilityError, DatabaseConnectionError

_VALIDATE_DAYTONA_DATABASE = supervisor._validate_daytona_database  # noqa: SLF001


@pytest.fixture(autouse=True)
def _compatible_daytona_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "_validate_daytona_database", lambda _repo_root: None)


class _ReadyResponse:
    status = 200

    def __enter__(self) -> _ReadyResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Process:
    def __init__(
        self,
        *,
        pid: int,
        returncode: int = 0,
        timeout_once: bool = False,
        poll_results: tuple[int | None, ...] = (),
    ) -> None:
        self.pid = pid
        self.returncode = returncode
        self.timeout_once = timeout_once
        self.poll_results = list(poll_results)
        self.last_poll_result: int | None = None
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        if self.poll_results:
            self.last_poll_result = self.poll_results.pop(0)
        return self.last_poll_result

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if timeout is not None and self.timeout_once:
            self.timeout_once = False
            raise supervisor.subprocess.TimeoutExpired("backend", timeout)
        return self.returncode


class _ExitedProcess(_Process):
    def poll(self) -> int | None:
        return self.returncode


def _tui_workspace(repo_root: Path) -> Path:
    workspace = repo_root / "tools" / "fleet-tui"
    (workspace / "src").mkdir(parents=True)
    (workspace / "package.json").write_text('{"engines":{"node":">=22"}}')
    (workspace / "src" / "cli.ts").write_text("// test pi-tui entry point")
    return workspace


def test_supervisor_rejects_node_older_than_22_19(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.18.0", stderr=""),
    )

    with pytest.raises(supervisor.SupervisorError, match="Node.js 22.19 or newer"):
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="deno",
            repo_root=tmp_path,
        )


def test_supervisor_fails_before_spawn_when_port_is_occupied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        with pytest.raises(supervisor.SupervisorError, match=f"port {port} is already in use"):
            supervisor.supervise(
                host="127.0.0.1",
                port=port,
                reload=False,
                run_environment="daytona",
                repo_root=tmp_path,
            )


def test_supervisor_rejects_ephemeral_port() -> None:
    with pytest.raises(supervisor.SupervisorError, match="between 1 and 65535"):
        supervisor._require_available_port("127.0.0.1", 0)  # noqa: SLF001


def test_supervisor_rejects_incompatible_daytona_database_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )

    def reject_database(_repo_root: Path) -> None:
        raise supervisor.SupervisorError("Fleet database is not at Alembic head; run uv run python scripts/db_init.py")

    monkeypatch.setattr(supervisor, "_validate_daytona_database", reject_database)
    popen_calls: list[object] = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    with pytest.raises(
        supervisor.SupervisorError,
        match=r"Fleet database is not at Alembic head; run uv run python scripts/db_init\.py",
    ):
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="daytona",
            repo_root=tmp_path,
        )

    assert popen_calls == []


def test_daytona_database_preflight_maps_revision_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        supervisor,
        "Settings",
        lambda: SimpleNamespace(database_url="sqlite+aiosqlite:///private/database.sqlite3"),
    )

    async def reject_database(*_args: object, **_kwargs: object) -> None:
        raise DatabaseCompatibilityError("database revision does not match Alembic head")

    monkeypatch.setattr(supervisor, "check_database_compatibility", reject_database)

    with pytest.raises(
        supervisor.SupervisorError,
        match=r"Fleet database is not at Alembic head; run uv run python scripts/db_init\.py",
    ):
        _VALIDATE_DAYTONA_DATABASE(tmp_path)


def test_daytona_database_preflight_sanitizes_connectivity_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret_url = "postgresql+asyncpg://user:top-secret@private-db/fleet"
    monkeypatch.setattr(supervisor, "Settings", lambda: SimpleNamespace(database_url=secret_url))

    async def reject_database(*_args: object, **_kwargs: object) -> None:
        raise DatabaseConnectionError(f"could not connect to {secret_url}")

    monkeypatch.setattr(supervisor, "check_database_compatibility", reject_database)

    with pytest.raises(supervisor.SupervisorError) as error:
        _VALIDATE_DAYTONA_DATABASE(tmp_path)

    assert str(error.value) == "Fleet database preflight failed; verify FLEET_DATABASE_URL"
    assert "top-secret" not in str(error.value)


def test_supervisor_runs_pi_tui_against_ready_backend_and_terminates_backend_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _tui_workspace(tmp_path)
    monkeypatch.setattr(
        supervisor,
        "_validate_daytona_database",
        lambda _repo_root: pytest.fail("Deno must not run the Daytona database preflight"),
    )
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: _ReadyResponse())
    processes = [
        _Process(pid=4312, timeout_once=True),
        _Process(pid=4313, poll_results=(0,)),
    ]
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **kwargs: object) -> _Process:
        popen_calls.append((command, kwargs))
        return processes[len(popen_calls) - 1]

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    supervisor.supervise(
        host="127.0.0.1",
        port=8123,
        reload=True,
        run_environment="deno",
        tui_args=("--session", "session-id"),
        repo_root=tmp_path,
    )

    backend_command, backend_options = popen_calls[0]
    assert backend_command[-6:] == [
        "fleet_rlm.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--reload",
    ]
    assert backend_options["start_new_session"] is True
    assert backend_options["env"]["FLEET_RUN_ENVIRONMENT"] == "deno"  # type: ignore[index]
    assert Path(backend_options["stdout"].name).parent == tmp_path / ".fleet_rlm" / "logs"  # type: ignore[union-attr]
    latest_log = tmp_path / ".fleet_rlm" / "logs" / "latest.log"
    assert latest_log.is_symlink()
    assert latest_log.resolve() == Path(backend_options["stdout"].name).resolve()  # type: ignore[union-attr]
    assert popen_calls[1] == (
        [
            "/usr/bin/pnpm",
            "start",
            "--",
            "--api-url",
            "http://127.0.0.1:8123",
            "--session",
            "session-id",
        ],
        {"cwd": workspace, "start_new_session": True},
    )
    assert signals == [
        (4312, supervisor.signal.SIGTERM),
        (4312, supervisor.signal.SIGKILL),
    ]
    assert processes[0].wait_timeouts == [5.0, None]


def test_supervisor_reports_backend_early_exit_with_log_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: _ExitedProcess(pid=7, returncode=3))

    with pytest.raises(supervisor.SupervisorError, match="backend exited with status 3") as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8124,
            reload=False,
            run_environment="daytona",
            repo_root=tmp_path,
        )

    log_path = Path(str(error.value).partition("; see ")[2])
    assert log_path.parent == tmp_path / ".fleet_rlm" / "logs"
    assert log_path.is_file()


def test_supervisor_reports_backend_exit_after_readiness_and_stops_tui_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: _ReadyResponse())
    backend = _Process(pid=21, returncode=3, poll_results=(None, 3))
    tui = _Process(pid=22)
    processes = iter((backend, tui))
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: next(processes))
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(supervisor.SupervisorError, match="backend exited with status 3") as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="deno",
            repo_root=tmp_path,
        )

    assert "; see " in str(error.value)
    assert (22, supervisor.signal.SIGTERM) in signals


@pytest.mark.parametrize(
    ("termination_signal", "exit_code"),
    ((supervisor.signal.SIGTERM, 143), (supervisor.signal.SIGHUP, 129)),
)
def test_supervisor_termination_signal_stops_both_groups_and_restores_handlers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    termination_signal: int,
    exit_code: int,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: _ReadyResponse())
    processes = iter((_Process(pid=31), _Process(pid=32)))
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: next(processes))
    handlers: dict[int, object] = {}
    prior_handlers = {
        supervisor.signal.SIGINT: object(),
        supervisor.signal.SIGTERM: object(),
        supervisor.signal.SIGHUP: object(),
    }

    def install_handler(signum: int, handler: object) -> object:
        previous = handlers.get(signum, prior_handlers[signum])
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(supervisor.signal, "signal", install_handler)
    delivered = False

    def deliver_signal(_seconds: float) -> None:
        nonlocal delivered
        if delivered:
            return
        delivered = True
        handler = handlers[termination_signal]
        assert callable(handler)
        handler(termination_signal, None)

    monkeypatch.setattr(supervisor.time, "sleep", deliver_signal)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(SystemExit) as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="daytona",
            repo_root=tmp_path,
        )

    assert error.value.code == exit_code
    assert (31, supervisor.signal.SIGTERM) in signals
    assert (32, supervisor.signal.SIGTERM) in signals
    assert handlers == prior_handlers


def test_supervisor_termination_during_readiness_stops_backend_and_restores_handlers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    backend = _Process(pid=35)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: backend)
    handlers: dict[int, object] = {}
    prior_handlers = {
        supervisor.signal.SIGINT: object(),
        supervisor.signal.SIGTERM: object(),
        supervisor.signal.SIGHUP: object(),
    }

    def install_handler(signum: int, handler: object) -> object:
        previous = handlers.get(signum, prior_handlers[signum])
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(supervisor.signal, "signal", install_handler)

    def interrupt_readiness(*_args: object, **_kwargs: object) -> None:
        handler = handlers.get(supervisor.signal.SIGTERM)
        if callable(handler):
            handler(supervisor.signal.SIGTERM, None)
        raise supervisor.urllib.error.URLError("not ready")

    monkeypatch.setattr(supervisor.urllib.request, "urlopen", interrupt_readiness)
    clock = iter((100.0, 100.1, 131.0))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with pytest.raises(SystemExit) as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="daytona",
            repo_root=tmp_path,
        )

    assert error.value.code == 143
    assert (35, supervisor.signal.SIGTERM) in signals
    assert handlers == prior_handlers


def test_supervisor_sigint_stops_both_groups_and_returns_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    monkeypatch.setattr(supervisor.urllib.request, "urlopen", lambda *_args, **_kwargs: _ReadyResponse())
    processes = iter((_Process(pid=41), _Process(pid=42)))
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: next(processes))
    handlers: dict[int, object] = {}

    def install_handler(signum: int, handler: object) -> object:
        previous = handlers.get(signum, supervisor.signal.SIG_DFL)
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(supervisor.signal, "signal", install_handler)
    delivered = False

    def deliver_sigint(_seconds: float) -> None:
        nonlocal delivered
        if delivered:
            return
        delivered = True
        handler = handlers[supervisor.signal.SIGINT]
        assert callable(handler)
        handler(supervisor.signal.SIGINT, None)

    monkeypatch.setattr(supervisor.time, "sleep", deliver_sigint)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    supervisor.supervise(
        host="127.0.0.1",
        port=8123,
        reload=False,
        run_environment="daytona",
        repo_root=tmp_path,
    )

    assert (41, supervisor.signal.SIGTERM) in signals
    assert (42, supervisor.signal.SIGTERM) in signals


def test_supervisor_reports_readiness_timeout_with_log_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _tui_workspace(tmp_path)
    monkeypatch.setattr(supervisor.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v22.19.0", stderr=""),
    )
    backend = _Process(pid=8)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: backend)
    clock = iter((100.0, 131.0))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(supervisor.os, "killpg", lambda *_args: None)

    with pytest.raises(supervisor.SupervisorError, match="not ready within 30s") as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8125,
            reload=False,
            run_environment="deno",
            repo_root=tmp_path,
        )

    assert str(tmp_path / ".fleet_rlm" / "logs") in str(error.value)
    assert backend.wait_timeouts == [5.0]
