"""Process supervision for the backend-backed pi-tui development client."""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, cast

from fleet_rlm.config import active_profile, load_runtime_settings
from fleet_rlm.persistence.database import ensure_database_compatible

if TYPE_CHECKING:
    from fleet_rlm.config import Settings

SignalHandler = int | Callable[[int, FrameType | None], object] | None


class SupervisorError(RuntimeError):
    """A user-actionable local process supervision failure."""


# Daytona lifespan creates ephemeral Volume I/O sandboxes during orphan cleanup;
# cold provider create/delete routinely exceeds a 30s local readiness budget.
_READY_TIMEOUT_SECONDS = {
    "daytona": 90.0,
}
_MLFLOW_READY_TIMEOUT_SECONDS = 30.0
_LOCAL_MLFLOW_URI = "http://127.0.0.1:5001"
_LOCAL_MLFLOW_HOST = "127.0.0.1"
_LOCAL_MLFLOW_PORT = 5001
_RUNTIME_PROFILES = {
    "daytona": "daytona",
}


def _profile_for_run_environment(run_environment: str) -> str:
    """Return the stable default profile for one public run environment."""
    try:
        return _RUNTIME_PROFILES[run_environment]
    except KeyError as exc:
        raise SupervisorError(f"unsupported Fleet run environment: {run_environment}") from exc


def _selected_runtime_policy(run_environment: str) -> Settings:
    """Load the selected policy once and require it to match the launcher."""
    recommended_profile = _profile_for_run_environment(run_environment)
    try:
        settings = load_runtime_settings()
    except Exception as exc:
        raise SupervisorError(f"Fleet runtime policy could not be loaded: {exc}") from exc
    if settings.run_environment != run_environment:
        selected_profile = active_profile(settings) or "unknown"
        raise SupervisorError(
            f"selected Fleet profile {selected_profile!r} uses "
            f"run environment {settings.run_environment!r}, but this command requires "
            f"{run_environment!r}. Set [config] default_profile = {recommended_profile!r} "
            "or use /profiles, then restart Fleet."
        )
    return settings


def _validate_prerequisites(repo_root: Path) -> tuple[Path, str]:
    workspace = repo_root / "tools" / "fleet-tui"
    if (
        not workspace.is_dir()
        or not (workspace / "package.json").is_file()
        or not (workspace / "src" / "cli.ts").is_file()
    ):
        raise SupervisorError(f"Fleet TUI workspace is missing: {workspace}")

    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise SupervisorError("pnpm is required to launch the Fleet TUI")
    node = shutil.which("node")
    if node is None:
        raise SupervisorError("Node.js 22.19 or newer is required to launch the Fleet TUI")
    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SupervisorError("Could not execute Node.js to validate version 22.19 or newer") from exc
    version = result.stdout.strip().removeprefix("v")
    try:
        parts = version.split(".")
        installed = tuple(int(part) for part in parts[:3])
    except ValueError as exc:
        raise SupervisorError(f"Could not determine the installed Node.js version: {version or 'unknown'}") from exc
    if result.returncode != 0 or installed < (22, 19, 0):
        raise SupervisorError(f"Node.js 22.19 or newer is required; found {version or 'unknown'}")
    return workspace, pnpm


def _require_available_port(host: str, port: int) -> None:
    if not 1 <= port <= 65_535:
        raise SupervisorError("port must be between 1 and 65535")
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((host, port))
    except OSError as exc:
        raise SupervisorError(f"port {port} is already in use on {host}") from exc


def _api_url(host: str, port: int) -> str:
    client_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in client_host and not client_host.startswith("["):
        client_host = f"[{client_host}]"
    return f"http://{client_host}:{port}"


def _validate_daytona_database(repo_root: Path, *, settings: Settings | None = None) -> None:
    if settings is None:
        try:
            settings = load_runtime_settings()
        except Exception as exc:
            raise SupervisorError("Fleet database preflight failed; verify FLEET_DATABASE_URL") from exc
    database_url = (settings.database_url or "").strip()
    if not database_url:
        raise SupervisorError("Fleet database preflight failed; verify FLEET_DATABASE_URL")
    try:
        asyncio.run(ensure_database_compatible(database_url, repo_root=repo_root))
    except Exception as exc:
        raise SupervisorError(str(exc)) from exc


def _mlflow_server_version(tracking_uri: str, *, timeout: float = 0.5) -> str | None:
    """Return the version exposed by a reachable MLflow server."""
    try:
        with urllib.request.urlopen(f"{tracking_uri.rstrip('/')}/version", timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return None
            return response.read().decode("utf-8").strip() or None
    except (OSError, TimeoutError, UnicodeDecodeError, urllib.error.URLError):
        return None


def _wait_until_mlflow_ready(
    process: subprocess.Popen[bytes],
    *,
    tracking_uri: str,
    expected_version: str,
    log_path: Path,
    timeout: float = _MLFLOW_READY_TIMEOUT_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise SupervisorError(f"MLflow exited with status {returncode}; see {log_path}")
        actual_version = _mlflow_server_version(tracking_uri)
        if actual_version is not None:
            if actual_version != expected_version:
                raise SupervisorError(
                    f"MLflow at {tracking_uri} reports version {actual_version}, "
                    f"but Fleet requires {expected_version}; see {log_path}"
                )
            return
        time.sleep(0.1)
    raise SupervisorError(f"MLflow was not ready within {timeout:g}s; see {log_path}")


@contextmanager
def _local_mlflow_server(
    settings: Settings | None,
    *,
    repo_root: Path,
    logs: Path,
    timestamp: str,
) -> Iterator[subprocess.Popen[bytes] | None]:
    """Reuse or supervise the standard loopback MLflow tracking server."""
    if (
        settings is None
        or not settings.mlflow_tracing_enabled
        or settings.mlflow_tracking_uri.rstrip("/") != _LOCAL_MLFLOW_URI
    ):
        yield None
        return

    expected_version = importlib.metadata.version("mlflow")
    actual_version = _mlflow_server_version(_LOCAL_MLFLOW_URI)
    if actual_version is not None:
        if actual_version != expected_version:
            raise SupervisorError(
                f"MLflow at {_LOCAL_MLFLOW_URI} reports version {actual_version}, but Fleet requires {expected_version}"
            )
        yield None
        return

    try:
        _require_available_port(_LOCAL_MLFLOW_HOST, _LOCAL_MLFLOW_PORT)
    except SupervisorError as exc:
        raise SupervisorError(
            f"port {_LOCAL_MLFLOW_PORT} is occupied by a service that is not compatible MLflow"
        ) from exc

    store = repo_root / settings.data_root / "mlflow"
    artifacts = store / "artifacts"
    store.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    database = (store / "mlflow.db").resolve()
    log_path = logs / f"mlflow-{timestamp}.log"
    latest_log_path = logs / "mlflow-latest.log"
    command = [
        sys.executable,
        "-m",
        "mlflow",
        "server",
        "--host",
        _LOCAL_MLFLOW_HOST,
        "--port",
        str(_LOCAL_MLFLOW_PORT),
        "--workers",
        "1",
        "--backend-store-uri",
        f"sqlite:///{database.as_posix()}",
        "--artifacts-destination",
        artifacts.resolve().as_uri(),
    ]

    with log_path.open("wb") as mlflow_log:
        latest_log_path.unlink(missing_ok=True)
        latest_log_path.symlink_to(log_path.name)
        try:
            process = subprocess.Popen(
                command,
                cwd=repo_root,
                stdout=mlflow_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=False,
            )
        except OSError as exc:
            raise SupervisorError(f"Could not start MLflow; see {log_path}") from exc
        try:
            _wait_until_mlflow_ready(
                process,
                tracking_uri=_LOCAL_MLFLOW_URI,
                expected_version=expected_version,
                log_path=log_path,
            )
            yield process
        finally:
            _stop_process_group(process)


def _wait_until_ready(
    backend: subprocess.Popen[bytes],
    *,
    api_url: str,
    log_path: Path,
    timeout: float,
    shutdown_requested: Callable[[], bool],
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if shutdown_requested():
            return
        returncode = backend.poll()
        if returncode is not None:
            raise SupervisorError(f"Fleet backend exited with status {returncode}; see {log_path}")
        try:
            with urllib.request.urlopen(f"{api_url}/openapi.json", timeout=0.5) as response:
                if 200 <= response.status < 300:
                    return
        except (OSError, TimeoutError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    if shutdown_requested():
        return
    raise SupervisorError(f"Fleet backend was not ready within {timeout:g}s; see {log_path}")


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop one owned child process group, escalating after a bounded wait."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()


def _run_backend_and_tui(
    *,
    root: Path,
    workspace: Path,
    pnpm: str,
    api_url: str,
    backend_command: list[str],
    backend_env: dict[str, str],
    log_path: Path,
    latest_log_path: Path,
    run_environment: str,
    tui_args: Sequence[str],
) -> int | None:
    """Supervise the backend and terminal client, returning a received signal."""
    previous_handlers: dict[int, SignalHandler] = {}
    received_signal: int | None = None

    def request_shutdown(signum: int, _frame: FrameType | None) -> None:
        nonlocal received_signal
        if received_signal is None:
            received_signal = signum

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            previous_handlers[signum] = signal.signal(signum, request_shutdown)
        except ValueError:
            continue
    try:
        with log_path.open("wb") as backend_log:
            latest_log_path.unlink(missing_ok=True)
            latest_log_path.symlink_to(log_path.name)
            tui: subprocess.Popen[bytes] | None = None
            popen_bytes = cast("Callable[..., subprocess.Popen[bytes]]", subprocess.Popen)
            try:
                backend = popen_bytes(
                    backend_command,
                    cwd=root,
                    env=backend_env,
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=False,
                )
            except OSError as exc:
                raise SupervisorError(f"Could not start the Fleet backend; see {log_path}") from exc
            try:
                _wait_until_ready(
                    backend,
                    api_url=api_url,
                    log_path=log_path,
                    timeout=_READY_TIMEOUT_SECONDS.get(run_environment, 30.0),
                    shutdown_requested=lambda: received_signal is not None,
                )
                if received_signal is None:
                    try:
                        tui = subprocess.Popen(
                            [pnpm, "start", "--", "--api-url", api_url, *tui_args],
                            cwd=workspace,
                            start_new_session=True,
                        )
                    except OSError as exc:
                        raise SupervisorError("Could not start Fleet pi-tui TUI") from exc

                tui_returncode: int | None = None
                while received_signal is None and tui is not None:
                    backend_returncode = backend.poll()
                    if backend_returncode is not None:
                        raise SupervisorError(f"Fleet backend exited with status {backend_returncode}; see {log_path}")
                    tui_returncode = tui.poll()
                    if tui_returncode is not None:
                        break
                    time.sleep(0.1)
                if received_signal is None and tui_returncode not in {
                    0,
                    128 + signal.SIGINT,
                    -signal.SIGINT,
                }:
                    raise SupervisorError(f"Fleet pi-tui TUI exited with status {tui_returncode}")
            finally:
                if tui is not None:
                    _stop_process_group(tui)
                _stop_process_group(backend)
    finally:
        for signum, handler in previous_handlers.items():
            try:
                signal.signal(signum, handler)
            except ValueError:
                continue
    return received_signal


def supervise(
    *,
    host: str,
    port: int,
    reload: bool,
    run_environment: str,
    tui_args: Sequence[str] = (),
    repo_root: Path | None = None,
) -> None:
    """Run the selected backend and repository pi-tui client together."""
    root = repo_root or Path(__file__).resolve().parents[3]
    workspace, pnpm = _validate_prerequisites(root)
    _require_available_port(host, port)
    runtime_settings = _selected_runtime_policy(run_environment)
    if run_environment == "daytona":
        _validate_daytona_database(root, settings=runtime_settings)
    logs = root / ".fleet_rlm" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    log_path = logs / f"backend-{timestamp}.log"
    latest_log_path = logs / "latest.log"
    api_url = _api_url(host, port)
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "fleet_rlm.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        backend_command.append("--reload")
    backend_env = dict(os.environ)
    # The backend resolves the committed TOML policy itself; do not pin an
    # ambient profile override into the child process environment.
    backend_env.pop("FLEET_CONFIG_PROFILE", None)
    backend_env.pop("FLEET_RUN_ENVIRONMENT", None)
    with _local_mlflow_server(
        runtime_settings if run_environment == "daytona" else None,
        repo_root=root,
        logs=logs,
        timestamp=timestamp,
    ):
        received_signal = _run_backend_and_tui(
            root=root,
            workspace=workspace,
            pnpm=pnpm,
            api_url=api_url,
            backend_command=backend_command,
            backend_env=backend_env,
            log_path=log_path,
            latest_log_path=latest_log_path,
            run_environment=run_environment,
            tui_args=tui_args,
        )

    if received_signal is not None:
        if received_signal != signal.SIGINT:
            raise SystemExit(128 + received_signal)
        return
