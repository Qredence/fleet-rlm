"""Start and stop the local services `fleet cli` runs against: the API server and, optionally, a local MLflow server."""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from fleet_rlm.config import FleetSettings

MLFLOW_HOST = "127.0.0.1"
STATE_DIR = Path(".fleet")


class LaunchError(RuntimeError):
    """A managed service failed to start."""


def api_is_healthy(url: str) -> bool:
    """True when a Fleet API server answers `/health` at `url`."""
    try:
        response = httpx.get(f"{url}/health", timeout=1.5)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def mlflow_is_healthy(url: str) -> bool:
    """True when an MLflow tracking server answers `/health` at `url`."""
    try:
        return httpx.get(f"{url}/health", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


@dataclass(frozen=True)
class LaunchPlan:
    """What `fleet cli` will start, reuse, or leave alone."""

    api_url: str
    start_api: bool = False
    start_mlflow: bool = False
    mlflow_url: str | None = None
    mlflow_mode: str = "local"
    mlflow_data_dir: str = ".fleet/mlflow"
    api_env: dict[str, str] = field(default_factory=dict)
    mlflow_env: dict[str, str] = field(default_factory=dict, repr=False)
    notes: tuple[str, ...] = ()


def mlflow_uses_default_passphrase(url: str) -> bool | None:
    """Return whether a running MLflow server reports using the default Gateway KEK passphrase."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/ajax-api/3.0/mlflow/gateway/secrets/config", timeout=1.5)
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    using_default = payload.get("using_default_passphrase")
    return using_default if isinstance(using_default, bool) else None


def local_mlflow_secret_kek_versions(data_dir: str) -> set[int]:
    """Return distinct Gateway secret `kek_version` integers in a local SQLite MLflow store, read-only."""
    db_path = Path(data_dir) / "mlflow.db"
    if not db_path.is_file():
        return set()
    try:
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as conn:
            rows = conn.execute("SELECT DISTINCT kek_version FROM secrets").fetchall()
        return {int(row[0]) for row in rows if row[0] is not None}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return set()


def local_mlflow_kek_issue(
    settings: FleetSettings,
    *,
    mlflow_url: str | None = None,
    reachable: bool = False,
) -> str | None:
    """Return an actionable diagnostic when local MLflow KEK settings conflict with stored secrets or server state."""
    try:
        mlflow_env = settings.local_mlflow_server_environment()
    except ValueError as exc:
        return str(exc)

    has_passphrase = "MLFLOW_CRYPTO_KEK_PASSPHRASE" in mlflow_env
    configured_version = int(mlflow_env.get("MLFLOW_CRYPTO_KEK_VERSION", "1"))
    kek_versions = local_mlflow_secret_kek_versions(settings.mlflow_local_data_dir)
    if kek_versions:
        versions_text = ", ".join(str(version) for version in sorted(kek_versions))
        if not has_passphrase and any(version > 1 for version in kek_versions):
            return (
                f"Local MLflow database in {settings.mlflow_local_data_dir} contains encrypted Gateway secrets "
                f"(KEK version(s) {versions_text}), but MLFLOW_CRYPTO_KEK_PASSPHRASE is not configured. "
                "Set MLFLOW_CRYPTO_KEK_PASSPHRASE and MLFLOW_CRYPTO_KEK_VERSION in .env or the environment."
            )
        if has_passphrase and configured_version not in kek_versions:
            return (
                f"Local MLflow database in {settings.mlflow_local_data_dir} uses Gateway KEK version(s) "
                f"{versions_text}, but MLFLOW_CRYPTO_KEK_VERSION resolves to {configured_version}. "
                "Set MLFLOW_CRYPTO_KEK_VERSION to match."
            )

    if reachable and mlflow_url and has_passphrase and mlflow_uses_default_passphrase(mlflow_url) is True:
        return (
            f"Running MLflow server at {mlflow_url} is using the default KEK passphrase, but "
            "MLFLOW_CRYPTO_KEK_PASSPHRASE is configured. Restart the MLflow server with .env loaded "
            "(or stop it so `fleet cli` starts it) to decrypt Gateway secrets."
        )
    return None


def verify_api_destination(url: str, expected: dict) -> None:
    """Reject unknown or mismatched tracking destinations without acquiring a Session."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=5.0)
        response.raise_for_status()
        payload = response.json()
        actual = payload.get("mlflow") if isinstance(payload, dict) else None
    except (httpx.HTTPError, ValueError) as exc:
        raise LaunchError(f"Could not verify the API MLflow destination at {url} ({type(exc).__name__}).") from None
    if (
        not isinstance(payload, dict)
        or payload.get("status") != "ok"
        or not isinstance(actual, dict)
        or actual.get("ready") is not True
        or not actual.get("experiment_id")
    ):
        raise LaunchError(
            f"API at {url} has no ready MLflow destination metadata; restart it with the current Fleet version."
        )
    if any(actual.get(key) != value for key, value in expected.items()):
        raise LaunchError(
            f"API MLflow destination mismatch at {url}: requested {expected['mode']} "
            f"({expected['experiment_name']}); server reports {actual.get('mode')} "
            f"({actual.get('experiment_name')}). Stop that server or select another FLEET_PORT. "
            "For evaluation, configure the server runtime experiment and trace prefix for evaluation."
        )


def plan_launch(
    *,
    url: str | None,
    host: str,
    port: int,
    settings: FleetSettings,
    api_healthy: Callable[[str], bool] | None = None,
    mlflow_healthy: Callable[[str], bool] | None = None,
) -> LaunchPlan:
    """Start owned services, or reuse an API only after checking its destination."""
    target = settings.mlflow_target()
    api_healthy = api_healthy or api_is_healthy
    mlflow_healthy = mlflow_healthy or mlflow_is_healthy
    api_url = url.rstrip("/") if url else f"http://{host}:{port}"
    if url or api_healthy(api_url):
        verify_api_destination(api_url, settings.mlflow_descriptor())
        return LaunchPlan(api_url=api_url, notes=(f"Reusing the API server already running at {api_url}.",))
    environment = settings.mlflow_environment()
    if target.mode == "databricks":
        return LaunchPlan(api_url=api_url, start_api=True, api_env=environment, mlflow_mode="databricks")
    mlflow_url = target.tracking_uri
    try:
        mlflow_env = settings.local_mlflow_server_environment()
    except ValueError as exc:
        raise LaunchError(str(exc)) from None
    reuse = mlflow_healthy(mlflow_url)
    notes: list[str] = []
    if reuse:
        notes.append(f"Reusing the MLflow server already running at {mlflow_url}.")
    issue = local_mlflow_kek_issue(settings, mlflow_url=mlflow_url, reachable=reuse)
    if issue:
        notes.append(f"Warning: {issue}")
    return LaunchPlan(
        api_url=api_url,
        start_api=True,
        start_mlflow=not reuse,
        mlflow_url=mlflow_url,
        mlflow_data_dir=settings.mlflow_local_data_dir,
        api_env=environment,
        mlflow_env={} if reuse else mlflow_env,
        notes=tuple(notes),
    )


@dataclass
class ManagedProcess:
    """One subprocess in its own session, logging to a file, stopped as a process group."""

    name: str
    command: list[str]
    log_path: Path
    env: dict[str, str] = field(default_factory=dict, repr=False)
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        """Start the command in its own session, appending its output to log_path."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1", **self.env}
        for key in ("MLFLOW_TRACKING_URI", "MLFLOW_EXPERIMENT_NAME", "MLFLOW_EXPERIMENT_ID"):
            environment.pop(key, None)
        with self.log_path.open("ab") as log:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                start_new_session=True,
            )

    def tail(self, lines: int = 15) -> str:
        """Return the last `lines` lines of the log, or an empty string if it cannot be read."""
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def wait_ready(self, check: Callable[[], bool], timeout: float, interval: float = 0.5) -> float:
        """Block until `check()` passes; fail fast if the process exits. Returns seconds waited."""
        started = time.monotonic()
        while True:
            if self._process is None or self._process.poll() is not None:
                code = None if self._process is None else self._process.returncode
                raise LaunchError(f"{self.name} exited during startup (code {code}).\n{self.tail()}")
            if check():
                return time.monotonic() - started
            if time.monotonic() - started > timeout:
                raise LaunchError(f"{self.name} was not ready after {timeout:.0f}s.\n{self.tail()}")
            time.sleep(interval)

    def stop(self, grace: float | None = 10.0) -> None:
        """Stop the group; API owners may await lifespan cleanup without a force-kill deadline."""
        process = self._process
        if process is None or process.poll() is not None:
            return
        self._signal(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            self._signal(process, signal.SIGKILL)
            process.wait(timeout=5)

    @staticmethod
    def _signal(process: subprocess.Popen[bytes], sig: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            if hasattr(os, "killpg"):
                os.killpg(process.pid, sig)
            else:  # pragma: no cover - non-POSIX platforms
                process.send_signal(sig)


def _api_process(plan: LaunchPlan, host: str, port: int) -> ManagedProcess:
    return ManagedProcess(
        "API server",
        [
            sys.executable,
            "-m",
            "fleet_rlm.cli",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--mlflow",
            plan.mlflow_mode,
        ],
        STATE_DIR / "logs" / "server.log",
        env=plan.api_env,
    )


def _mlflow_process(mlflow_port: int, data_dir: str, env: dict[str, str] | None = None) -> ManagedProcess:
    data = Path(data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    return ManagedProcess(
        "MLflow server",
        [
            sys.executable,
            "-m",
            "mlflow",
            "server",
            "--backend-store-uri",
            f"sqlite:///{data / 'mlflow.db'}",
            "--default-artifact-root",
            str(data / "artifacts"),
            "--host",
            MLFLOW_HOST,
            "--port",
            str(mlflow_port),
        ],
        STATE_DIR / "logs" / "mlflow.log",
        env={**(env or {}), "MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS": "true"},
    )


@contextlib.contextmanager
def running_services(
    plan: LaunchPlan,
    *,
    host: str,
    port: int,
    mlflow_port: int,
    startup_timeout: float,
    log: Callable[[str], None],
) -> Generator[LaunchPlan, None, None]:
    """Start what `plan` asks for, yield, and always stop it: the API server first (it flushes traces), then MLflow."""
    started: list[ManagedProcess] = []
    owned_api: ManagedProcess | None = None
    api_ready = False

    def cleanup() -> None:
        """Stop owned services in reverse startup order.

        Wait for API shutdown without a kill deadline so active experiments and
        traces drain before stopping local MLflow. Temporarily ignore termination
        signals on the main thread.
        """
        nonlocal api_ready
        # A second Ctrl-C must not interrupt wait() and abandon owned services.
        handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, signal.SIG_IGN)
        try:
            if api_ready and owned_api is not None and owned_api in started:
                api_ready = False
                log("Waiting for API graceful shutdown; active experiments finish before owned services stop.")
            while started:
                process = started.pop()
                if process is owned_api:
                    process.stop(grace=None)
                else:
                    process.stop()
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)

    atexit.register(cleanup)
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(143))
    try:
        for note in plan.notes:
            log(note)
        if plan.start_mlflow and plan.mlflow_url:
            mlflow_process = (
                _mlflow_process(mlflow_port, plan.mlflow_data_dir, env=plan.mlflow_env)
                if plan.mlflow_env
                else _mlflow_process(mlflow_port, plan.mlflow_data_dir)
            )
            log(f"Starting MLflow server at {plan.mlflow_url} (data in {plan.mlflow_data_dir}) ...")
            mlflow_process.start()
            started.append(mlflow_process)
            waited = mlflow_process.wait_ready(lambda: mlflow_is_healthy(plan.mlflow_url or ""), startup_timeout)
            log(f"MLflow server ready in {waited:.0f}s (log: {mlflow_process.log_path}).")
        if plan.start_api:
            api_process = _api_process(plan, host, port)
            owned_api = api_process
            log(f"Starting API server at {plan.api_url} ...")
            api_process.start()
            started.append(api_process)
            waited = api_process.wait_ready(lambda: api_is_healthy(plan.api_url), startup_timeout)
            api_ready = True
            log(f"API server ready in {waited:.0f}s (log: {api_process.log_path}).")
        yield plan
    finally:
        try:
            cleanup()
        finally:
            atexit.unregister(cleanup)
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
