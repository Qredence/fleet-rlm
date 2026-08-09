from __future__ import annotations

import socket
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_rlm.cli import supervisor
from fleet_rlm.persistence.database import DatabaseCompatibilityError, DatabaseConnectionError

_VALIDATE_DAYTONA_DATABASE = supervisor._validate_daytona_database
_LOCAL_MLFLOW_SERVER = supervisor._local_mlflow_server
_SELECTED_RUNTIME_POLICY = supervisor._selected_runtime_policy


@pytest.fixture(autouse=True)
def _compatible_daytona_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervisor, "_validate_daytona_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        supervisor,
        "_selected_runtime_policy",
        lambda run_environment: SimpleNamespace(
            run_environment=run_environment,
            mlflow_tracing_enabled=False,
            mlflow_tracking_uri="",
            data_root=".fleet_rlm",
        ),
    )

    @contextmanager
    def disabled_local_mlflow(*_args: object, **_kwargs: object):
        yield None

    monkeypatch.setattr(supervisor, "_local_mlflow_server", disabled_local_mlflow)


class _ReadyResponse:
    status = 200

    def __enter__(self) -> _ReadyResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _VersionResponse(_ReadyResponse):
    def __init__(self, version: str) -> None:
        self._version = version

    def read(self) -> bytes:
        return self._version.encode()


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

    with pytest.raises(supervisor.SupervisorError, match=r"Node.js 22.19 or newer"):
        supervisor.supervise(
            host="127.0.0.1",
            port=8123,
            reload=False,
            run_environment="daytona",
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
        supervisor._require_available_port("127.0.0.1", 0)


def _local_mlflow_settings(**overrides: object) -> SimpleNamespace:
    values = {
        "mlflow_tracing_enabled": True,
        "mlflow_tracking_uri": "http://127.0.0.1:5001",
        "data_root": ".fleet_rlm",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_local_mlflow_server_starts_with_durable_storage_and_stops_owned_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    logs = tmp_path / ".fleet_rlm" / "logs"
    logs.mkdir(parents=True)
    versions = iter((None, "3.15.0"))
    monkeypatch.setattr(supervisor, "_mlflow_server_version", lambda *_args, **_kwargs: next(versions))
    monkeypatch.setattr(supervisor.importlib.metadata, "version", lambda _name: "3.15.0")
    monkeypatch.setattr(supervisor, "_require_available_port", lambda *_args: None)
    process = _Process(pid=5001)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(command: list[str], **kwargs: object) -> _Process:
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(supervisor.subprocess, "Popen", popen)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    with _LOCAL_MLFLOW_SERVER(
        _local_mlflow_settings(),
        repo_root=tmp_path,
        logs=logs,
        timestamp="stamp",
    ) as owned:
        assert owned is process
        assert signals == []

    command, options = calls[0]
    assert command[:4] == [supervisor.sys.executable, "-m", "mlflow", "server"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--port") + 1] == "5001"
    assert command[command.index("--workers") + 1] == "1"
    assert command[command.index("--backend-store-uri") + 1].endswith("/.fleet_rlm/mlflow/mlflow.db")
    assert command[command.index("--artifacts-destination") + 1].endswith("/.fleet_rlm/mlflow/artifacts")
    assert options["cwd"] == tmp_path
    assert options["start_new_session"] is True
    assert signals == [(5001, supervisor.signal.SIGTERM)]
    assert (logs / "mlflow-stamp.log").is_file()
    assert (logs / "mlflow-latest.log").resolve() == (logs / "mlflow-stamp.log").resolve()


def test_local_mlflow_server_reuses_compatible_external_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(supervisor, "_mlflow_server_version", lambda *_args, **_kwargs: "3.15.0")
    monkeypatch.setattr(supervisor.importlib.metadata, "version", lambda _name: "3.15.0")
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("compatible external MLflow must be reused"),
    )
    monkeypatch.setattr(
        supervisor,
        "_stop_process_group",
        lambda *_args: pytest.fail("reused external MLflow must not be stopped"),
    )

    with _LOCAL_MLFLOW_SERVER(
        _local_mlflow_settings(),
        repo_root=tmp_path,
        logs=tmp_path,
        timestamp="stamp",
    ) as owned:
        assert owned is None


def test_local_mlflow_server_rejects_incompatible_external_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(supervisor, "_mlflow_server_version", lambda *_args, **_kwargs: "3.13.0")
    monkeypatch.setattr(supervisor.importlib.metadata, "version", lambda _name: "3.15.0")

    with (
        pytest.raises(supervisor.SupervisorError, match=r"reports version 3\.13\.0.*requires 3\.15\.0"),
        _LOCAL_MLFLOW_SERVER(
            _local_mlflow_settings(),
            repo_root=tmp_path,
            logs=tmp_path,
            timestamp="stamp",
        ),
    ):
        pass


def test_local_mlflow_server_rejects_non_mlflow_port_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(supervisor, "_mlflow_server_version", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(supervisor.importlib.metadata, "version", lambda _name: "3.15.0")
    monkeypatch.setattr(
        supervisor,
        "_require_available_port",
        lambda *_args: (_ for _ in ()).throw(supervisor.SupervisorError("occupied")),
    )

    with (
        pytest.raises(supervisor.SupervisorError, match="not compatible MLflow"),
        _LOCAL_MLFLOW_SERVER(
            _local_mlflow_settings(),
            repo_root=tmp_path,
            logs=tmp_path,
            timestamp="stamp",
        ),
    ):
        pass


def test_local_mlflow_readiness_reports_early_exit(tmp_path: Path) -> None:
    log_path = tmp_path / "mlflow.log"

    with pytest.raises(supervisor.SupervisorError, match=r"MLflow exited with status 3.*mlflow\.log"):
        supervisor._wait_until_mlflow_ready(
            _ExitedProcess(pid=5001, returncode=3),
            tracking_uri="http://127.0.0.1:5001",
            expected_version="3.15.0",
            log_path=log_path,
        )


def test_local_mlflow_readiness_reports_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clock = iter((100.0, 131.0))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(supervisor, "_mlflow_server_version", lambda *_args, **_kwargs: None)

    with pytest.raises(supervisor.SupervisorError, match=r"MLflow was not ready within 30s.*mlflow\.log"):
        supervisor._wait_until_mlflow_ready(
            _Process(pid=5001),
            tracking_uri="http://127.0.0.1:5001",
            expected_version="3.15.0",
            log_path=tmp_path / "mlflow.log",
        )


@pytest.mark.parametrize(
    "settings",
    (
        None,
        _local_mlflow_settings(mlflow_tracing_enabled=False),
        _local_mlflow_settings(mlflow_tracking_uri="databricks"),
    ),
)
def test_local_mlflow_server_skips_unmanaged_policies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    settings: SimpleNamespace | None,
) -> None:
    monkeypatch.setattr(
        supervisor.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("unmanaged policy must not start MLflow"),
    )

    with _LOCAL_MLFLOW_SERVER(settings, repo_root=tmp_path, logs=tmp_path, timestamp="stamp") as owned:
        assert owned is None


def test_daytona_startup_cleanup_recovery_leaves_readiness_margin() -> None:
    from fleet_rlm.composition.daytona import (
        _ORPHAN_CLEANUP_TIMEOUT_SECONDS,
        _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS,
    )

    readiness_timeout = supervisor._READY_TIMEOUT_SECONDS["daytona"]
    assert readiness_timeout - 15 >= _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS
    assert _STARTUP_CLEANUP_RECOVERY_BUDGET_SECONDS >= _ORPHAN_CLEANUP_TIMEOUT_SECONDS


@pytest.mark.parametrize("profile", ("daytona", "daytona-bench"))
def test_selected_runtime_policy_accepts_any_compatible_daytona_profile(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    settings = SimpleNamespace(
        run_environment="daytona",
        _active_profile=profile,
    )
    monkeypatch.setattr(supervisor, "active_profile", lambda _settings: profile)
    monkeypatch.setattr(
        supervisor,
        "load_runtime_settings",
        lambda: settings,
    )

    assert _SELECTED_RUNTIME_POLICY("daytona") is settings


def test_selected_runtime_policy_reports_removed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        supervisor,
        "load_runtime_settings",
        lambda: (_ for _ in ()).throw(RuntimeError("configured profile does not exist: databricks-daytona")),
    )

    with pytest.raises(
        supervisor.SupervisorError,
        match="configured profile does not exist: databricks-daytona",
    ):
        _SELECTED_RUNTIME_POLICY("daytona")


def test_supervisor_reuses_one_daytona_settings_object_and_stops_mlflow_last(
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
    settings = SimpleNamespace(
        run_environment="daytona",
        _active_profile="daytona",
    )
    load_calls = 0

    def load_settings() -> SimpleNamespace:
        nonlocal load_calls
        load_calls += 1
        return settings

    monkeypatch.setattr(supervisor, "active_profile", lambda _settings: "daytona")
    monkeypatch.setattr(supervisor, "load_runtime_settings", load_settings)
    monkeypatch.setattr(supervisor, "_selected_runtime_policy", _SELECTED_RUNTIME_POLICY)
    database_settings: list[object] = []
    monkeypatch.setattr(
        supervisor,
        "_validate_daytona_database",
        lambda _root, *, settings: database_settings.append(settings),
    )
    order: list[str] = []

    @contextmanager
    def local_mlflow(selected: object, **_kwargs: object):
        assert selected is settings
        order.append("mlflow-started")
        yield None
        order.append("mlflow-stopped")

    monkeypatch.setattr(supervisor, "_local_mlflow_server", local_mlflow)

    def run_backend_and_tui(**options: object) -> None:
        assert "FLEET_CONFIG_PROFILE" not in options["backend_env"]  # type: ignore[index]
        order.append("backend-and-tui-stopped")
        return None

    monkeypatch.setattr(supervisor, "_run_backend_and_tui", run_backend_and_tui)

    supervisor.supervise(
        host="127.0.0.1",
        port=8123,
        reload=False,
        run_environment="daytona",
        repo_root=tmp_path,
    )

    assert load_calls == 1
    assert database_settings == [settings]
    assert order == ["mlflow-started", "backend-and-tui-stopped", "mlflow-stopped"]


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

    def reject_database(_repo_root: Path, **_kwargs: object) -> None:
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
        "load_runtime_settings",
        lambda: SimpleNamespace(database_url="sqlite+aiosqlite:///private/database.sqlite3"),
    )

    async def reject_database(*_args: object, **_kwargs: object) -> None:
        raise DatabaseCompatibilityError("Fleet database is not at Alembic head; run uv run python scripts/db_init.py")

    monkeypatch.setattr(supervisor, "ensure_database_compatible", reject_database)

    with pytest.raises(
        supervisor.SupervisorError,
        match=r"Fleet database is not at Alembic head; run uv run python scripts/db_init\.py",
    ):
        _VALIDATE_DAYTONA_DATABASE(tmp_path)


def test_daytona_database_preflight_sanitizes_connectivity_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Sanitization lives in the shared ensure_database_compatible helper; the
    # supervisor surfaces its already-sanitized message. Here the helper has
    # already scrubbed the secret-bearing cause.
    secret_url = "postgresql+asyncpg://user:top-secret@private-db/fleet"
    monkeypatch.setattr(supervisor, "load_runtime_settings", lambda: SimpleNamespace(database_url=secret_url))

    async def reject_database(*_args: object, **_kwargs: object) -> None:
        raise DatabaseConnectionError("Fleet database compatibility could not be verified")

    monkeypatch.setattr(supervisor, "ensure_database_compatible", reject_database)

    with pytest.raises(supervisor.SupervisorError) as error:
        _VALIDATE_DAYTONA_DATABASE(tmp_path)

    assert str(error.value) == "Fleet database compatibility could not be verified"
    assert "top-secret" not in str(error.value)


def test_supervisor_runs_pi_tui_against_ready_backend_and_terminates_backend_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _tui_workspace(tmp_path)
    database_calls: list[Path] = []

    def validate_daytona_database(root: Path, *, settings: object) -> None:
        del settings
        database_calls.append(root)

    monkeypatch.setattr(supervisor, "_validate_daytona_database", validate_daytona_database)
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
        run_environment="daytona",
        tui_args=("--session", "session-id"),
        repo_root=tmp_path,
    )

    backend_command, backend_options = popen_calls[0]
    assert database_calls == [tmp_path]
    assert backend_command[-6:] == [
        "fleet_rlm.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
        "--reload",
    ]
    assert backend_options["start_new_session"] is True
    assert "FLEET_CONFIG_PROFILE" not in backend_options["env"]  # type: ignore[index]
    assert "FLEET_RUN_ENVIRONMENT" not in backend_options["env"]  # type: ignore[index]
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
            run_environment="daytona",
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


def test_supervisor_uses_longer_daytona_readiness_timeout(
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
    backend = _Process(pid=9)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *_args, **_kwargs: backend)
    # deadline = 100 + 150; second monotonic sample exceeds it without a ready probe.
    clock = iter((100.0, 251.0))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(supervisor.os, "killpg", lambda *_args: None)
    monkeypatch.setattr(supervisor, "_validate_daytona_database", lambda *_args, **_kwargs: None)

    with pytest.raises(supervisor.SupervisorError, match="not ready within 150s") as error:
        supervisor.supervise(
            host="127.0.0.1",
            port=8126,
            reload=False,
            run_environment="daytona",
            repo_root=tmp_path,
        )

    assert str(tmp_path / ".fleet_rlm" / "logs") in str(error.value)
    assert backend.wait_timeouts == [5.0]
