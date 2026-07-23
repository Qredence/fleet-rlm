"""Process supervision for the backend-backed pi-tui development client."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from fleet_rlm.config import load_runtime_settings
from fleet_rlm.persistence.database import DatabaseCompatibilityError, check_database_compatibility

SignalHandler = int | Callable[[int, FrameType | None], object] | None


class SupervisorError(RuntimeError):
    """A user-actionable local process supervision failure."""


# Daytona lifespan creates ephemeral Volume I/O sandboxes during orphan cleanup;
# cold provider create/delete routinely exceeds a 30s local readiness budget.
_READY_TIMEOUT_SECONDS = {
    "daytona": 90.0,
    "deno": 30.0,
}
_RUNTIME_PROFILES = {
    "daytona": "daytona",
    "deno": "local-deno",
}


def _profile_for_run_environment(run_environment: str) -> str:
    try:
        return _RUNTIME_PROFILES[run_environment]
    except KeyError as exc:
        raise SupervisorError(f"unsupported Fleet run environment: {run_environment}") from exc


@contextmanager
def _selected_runtime_profile(profile: str, run_environment: str):
    """Temporarily align parent-process config while validating a forced launcher."""
    previous_profile = os.environ.get("FLEET_CONFIG_PROFILE")
    previous_environment = os.environ.get("FLEET_RUN_ENVIRONMENT")
    os.environ["FLEET_CONFIG_PROFILE"] = profile
    os.environ["FLEET_RUN_ENVIRONMENT"] = run_environment
    try:
        yield
    finally:
        if previous_profile is None:
            os.environ.pop("FLEET_CONFIG_PROFILE", None)
        else:
            os.environ["FLEET_CONFIG_PROFILE"] = previous_profile
        if previous_environment is None:
            os.environ.pop("FLEET_RUN_ENVIRONMENT", None)
        else:
            os.environ["FLEET_RUN_ENVIRONMENT"] = previous_environment


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


def _validate_daytona_database(repo_root: Path) -> None:
    try:
        settings = load_runtime_settings()
    except Exception as exc:  # noqa: BLE001 - CLI configuration failures must remain secret-free
        raise SupervisorError("Fleet database preflight failed; verify FLEET_DATABASE_URL") from exc
    database_url = (settings.database_url or "").strip()
    if not database_url:
        raise SupervisorError("Fleet database preflight failed; verify FLEET_DATABASE_URL")
    try:
        asyncio.run(check_database_compatibility(database_url, repo_root=repo_root))
    except DatabaseCompatibilityError as exc:
        raise SupervisorError("Fleet database is not at Alembic head; run uv run python scripts/db_init.py") from exc
    except Exception as exc:  # noqa: BLE001 - connectivity errors must not expose database credentials
        raise SupervisorError("Fleet database preflight failed; verify FLEET_DATABASE_URL") from exc


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
    profile = _profile_for_run_environment(run_environment)
    root = repo_root or Path(__file__).resolve().parents[3]
    workspace, pnpm = _validate_prerequisites(root)
    _require_available_port(host, port)
    if run_environment == "daytona":
        with _selected_runtime_profile(profile, run_environment):
            _validate_daytona_database(root)
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
    backend_env = {
        **os.environ,
        "FLEET_CONFIG_PROFILE": profile,
        "FLEET_RUN_ENVIRONMENT": run_environment,
    }
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
            backend: subprocess.Popen[bytes] | None = None
            tui: subprocess.Popen[bytes] | None = None
            try:
                backend = subprocess.Popen(
                    backend_command,
                    cwd=root,
                    env=backend_env,
                    stdout=backend_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
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
                        raise SupervisorError("Could not start the Fleet pi-tui TUI") from exc

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

    if received_signal is not None:
        if received_signal != signal.SIGINT:
            raise SystemExit(128 + received_signal)
        return
