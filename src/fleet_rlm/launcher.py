"""Start and stop the local services `fleet cli` runs against: the API server and, optionally, a local MLflow server."""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

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
    api_env: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


def plan_launch(
    *,
    url: str | None,
    host: str,
    port: int,
    mlflow_mode: str,
    mlflow_port: int,
    mlflow_experiment: str,
    api_healthy: Callable[[str], bool] | None = None,
    mlflow_healthy: Callable[[str], bool] | None = None,
) -> LaunchPlan:
    """Decide what to start. An explicit URL, or an API already running at host:port, is never replaced."""
    if url:
        return LaunchPlan(api_url=url.rstrip("/"))
    api_healthy = api_healthy or api_is_healthy
    mlflow_healthy = mlflow_healthy or mlflow_is_healthy
    api_url = f"http://{host}:{port}"
    if api_healthy(api_url):
        notes = [f"Reusing the API server already running at {api_url}."]
        if mlflow_mode == "local":
            notes.append("Local MLflow settings apply only to servers `fleet cli` starts itself.")
        return LaunchPlan(api_url=api_url, notes=tuple(notes))
    if mlflow_mode != "local":
        return LaunchPlan(api_url=api_url, start_api=True)
    mlflow_url = f"http://{MLFLOW_HOST}:{mlflow_port}"
    reuse = mlflow_healthy(mlflow_url)
    return LaunchPlan(
        api_url=api_url,
        start_api=True,
        start_mlflow=not reuse,
        mlflow_url=mlflow_url,
        api_env={"FLEET_MLFLOW_TRACKING_URI": mlflow_url, "FLEET_MLFLOW_EXPERIMENT_NAME": mlflow_experiment},
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
        [sys.executable, "-m", "fleet_rlm.cli", "serve", "--host", host, "--port", str(port)],
        STATE_DIR / "logs" / "server.log",
        env=plan.api_env,
    )


def _mlflow_process(mlflow_port: int) -> ManagedProcess:
    data = (STATE_DIR / "mlflow").resolve()
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
        while started:
            started.pop().stop()

    atexit.register(cleanup)
    previous_handler = None
    if threading.current_thread() is threading.main_thread():
        previous_handler = signal.signal(signal.SIGTERM, lambda _signum, _frame: sys.exit(143))
    try:
        for note in plan.notes:
            log(note)
        if plan.start_mlflow and plan.mlflow_url:
            mlflow_process = _mlflow_process(mlflow_port)
            log(f"Starting MLflow server at {plan.mlflow_url} (data in {STATE_DIR / 'mlflow'}) ...")
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
        cleanup()
        atexit.unregister(cleanup)
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
