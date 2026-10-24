"""Start and stop the local services `fleet cli` runs against: the API server and, optionally, a local MLflow server."""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
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
    notes: tuple[str, ...] = ()


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
    reuse = mlflow_healthy(mlflow_url)
    return LaunchPlan(
        api_url=api_url,
        start_api=True,
        start_mlflow=not reuse,
        mlflow_url=mlflow_url,
        mlflow_data_dir=settings.mlflow_local_data_dir,
        api_env=environment,
        notes=(f"Reusing the MLflow server already running at {mlflow_url}.",) if reuse else (),
    )


@dataclass
class ManagedProcess:
    """One subprocess in its own session, logging to a file, stopped as a process group."""

    name: str
    command: list[str]
    log_path: Path
    env: dict[str, str] = field(default_factory=dict)
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

    @property
    def running(self) -> bool:
        """Return True while the started process has not exited."""
        return self._process is not None and self._process.poll() is None

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

    def stop(self, grace: float = 10.0) -> None:
        """Send SIGTERM to the process group, then SIGKILL if it has not exited within grace seconds."""
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


def _mlflow_process(mlflow_port: int, data_dir: str) -> ManagedProcess:
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
    )


@contextlib.contextmanager
def running_setup(
    *,
    backend: str,
    token: str,
    startup_timeout: float,
    log: Callable[[str], None],
    overrides: dict[str, str] | None = None,
) -> Generator[str, None, None]:
    """Run the same FastAPI app on a private loopback port before runtime prerequisites exist."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = ManagedProcess(
        "Setup API",
        [
            sys.executable,
            "-m",
            "fleet_rlm.cli",
            "serve",
            "--setup",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--mlflow",
            backend,
        ],
        STATE_DIR / "logs" / "setup.log",
        env={"FLEET_SETUP_TOKEN": token, "FLEET_SETUP_PROJECT_DIR": str(Path.cwd()), **(overrides or {})},
    )

    def ready() -> bool:
        try:
            response = httpx.get(url + "/health", timeout=1.5)
            return response.status_code == 200 and response.json().get("status") == "setup"
        except (httpx.HTTPError, ValueError):
            return False

    try:
        log("Checking Fleet setup…")
        process.start()
        process.wait_ready(ready, startup_timeout)
        yield url
    finally:
        process.stop()


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

    def cleanup() -> None:
        # A second Ctrl-C must not interrupt wait() and abandon owned services.
        handlers = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, signal.SIG_IGN)
        try:
            while started:
                started.pop().stop()
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
            mlflow_process = _mlflow_process(mlflow_port, plan.mlflow_data_dir)
            log(f"Starting MLflow server at {plan.mlflow_url} (data in {plan.mlflow_data_dir}) ...")
            mlflow_process.start()
            started.append(mlflow_process)
            waited = mlflow_process.wait_ready(lambda: mlflow_is_healthy(plan.mlflow_url or ""), startup_timeout)
            log(f"MLflow server ready in {waited:.0f}s (log: {mlflow_process.log_path}).")
        if plan.start_api:
            api_process = _api_process(plan, host, port)
            log(f"Starting API server at {plan.api_url} ...")
            api_process.start()
            started.append(api_process)
            waited = api_process.wait_ready(lambda: api_is_healthy(plan.api_url), startup_timeout)
            log(f"API server ready in {waited:.0f}s (log: {api_process.log_path}).")
        yield plan
    finally:
        try:
            cleanup()
        finally:
            atexit.unregister(cleanup)
            if previous_handler is not None:
                signal.signal(signal.SIGTERM, previous_handler)
