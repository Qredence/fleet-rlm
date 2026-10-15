from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import fleet_rlm.cli as cli
from fleet_rlm import launcher
from fleet_rlm.launcher import LaunchError, LaunchPlan, ManagedProcess, plan_launch


def _plan(**overrides: Any) -> LaunchPlan:
    options: dict[str, Any] = {
        "url": None,
        "host": "127.0.0.1",
        "port": 8000,
        "mlflow_mode": "local",
        "mlflow_port": 5001,
        "mlflow_experiment": "fleet-rlm-dev",
        "api_healthy": lambda _url: False,
        "mlflow_healthy": lambda _url: False,
    }
    return plan_launch(**{**options, **overrides})


def test_explicit_url_starts_nothing() -> None:
    plan = _plan(url="http://example.test:9000/")
    assert (plan.api_url, plan.start_api, plan.start_mlflow) == ("http://example.test:9000", False, False)


def test_a_healthy_api_is_reused_and_nothing_is_started() -> None:
    plan = _plan(api_healthy=lambda _url: True)
    assert not plan.start_api and not plan.start_mlflow and plan.api_env == {}
    assert any("Reusing the API server" in note for note in plan.notes)
    assert any("only to servers" in note for note in plan.notes)


def test_default_starts_local_mlflow_then_the_api_pointed_at_it() -> None:
    plan = _plan()
    assert plan.start_mlflow and plan.start_api and plan.mlflow_url == "http://127.0.0.1:5001"
    assert plan.api_env == {
        "FLEET_MLFLOW_TRACKING_URI": "http://127.0.0.1:5001",
        "FLEET_MLFLOW_EXPERIMENT_NAME": "fleet-rlm-dev",
    }


def test_a_healthy_mlflow_is_reused_but_the_api_still_points_at_it() -> None:
    plan = _plan(mlflow_healthy=lambda _url: True, mlflow_port=5002)
    assert plan.start_api and not plan.start_mlflow
    assert plan.api_env["FLEET_MLFLOW_TRACKING_URI"] == "http://127.0.0.1:5002"
    assert any("Reusing the MLflow server" in note for note in plan.notes)


def test_env_mode_leaves_the_configured_tracking_alone() -> None:
    plan = _plan(mlflow_mode="env")
    assert plan.start_api and not plan.start_mlflow and plan.api_env == {} and plan.mlflow_url is None


def test_wait_ready_reports_a_process_that_exits_with_its_log(tmp_path: Path) -> None:
    process = ManagedProcess(
        "Demo", [sys.executable, "-c", "print('boom: bad config'); raise SystemExit(3)"], tmp_path / "demo.log"
    )
    process.start()
    with pytest.raises(LaunchError, match=r"exited during startup \(code 3\)[\s\S]*boom: bad config"):
        process.wait_ready(lambda: False, timeout=10, interval=0.05)


def test_wait_ready_times_out_and_stop_terminates_the_process(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    with pytest.raises(LaunchError, match="not ready after"):
        process.wait_ready(lambda: False, timeout=0.3, interval=0.05)
    assert process.running
    process.stop(grace=5)
    assert not process.running


def test_wait_ready_returns_once_the_check_passes(tmp_path: Path) -> None:
    process = ManagedProcess("Demo", [sys.executable, "-c", "import time; time.sleep(60)"], tmp_path / "demo.log")
    process.start()
    try:
        calls = iter([False, False, True])
        assert process.wait_ready(lambda: next(calls), timeout=10, interval=0.01) >= 0
    finally:
        process.stop(grace=5)


def test_stop_kills_the_whole_process_group_including_grandchildren(tmp_path: Path) -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    process = ManagedProcess("Demo", [sys.executable, "-c", script], tmp_path / "demo.log")
    process.start()
    deadline = time.monotonic() + 10
    while not process.tail().strip().isdigit() and time.monotonic() < deadline:
        time.sleep(0.05)
    grandchild = int(process.tail().strip())
    process.stop(grace=5)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("grandchild process survived stop()")


class _FakeProcess:
    def __init__(self, name: str, events: list[str], log_path: Path) -> None:
        self.name, self.events, self.log_path = name, events, log_path

    def start(self) -> None:
        self.events.append(f"start {self.name}")

    def wait_ready(self, _check: Any, _timeout: float) -> float:
        self.events.append(f"ready {self.name}")
        return 0.0

    def stop(self) -> None:
        self.events.append(f"stop {self.name}")


def _patch_fake_processes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(launcher, "_mlflow_process", lambda _port: _FakeProcess("mlflow", events, Path("m.log")))
    monkeypatch.setattr(
        launcher, "_api_process", lambda _plan, _host, _port: _FakeProcess("api", events, Path("a.log"))
    )
    return events


def _run_services(plan: LaunchPlan, body: Any = None) -> list[str]:
    messages: list[str] = []
    with launcher.running_services(
        plan, host="127.0.0.1", port=8000, mlflow_port=5001, startup_timeout=5, log=messages.append
    ):
        if body:
            body()
    return messages


def test_services_start_mlflow_first_and_stop_the_api_first(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)
    _run_services(_plan())
    assert events == ["start mlflow", "ready mlflow", "start api", "ready api", "stop api", "stop mlflow"]


def test_services_are_stopped_when_the_body_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    def boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run_services(_plan(), boom)
    assert events[-2:] == ["stop api", "stop mlflow"]


def test_a_failed_api_start_still_stops_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_fake_processes(monkeypatch)

    class FailingApi(_FakeProcess):
        def wait_ready(self, _check: Any, _timeout: float) -> float:
            raise LaunchError("API server exited during startup")

    monkeypatch.setattr(launcher, "_api_process", lambda _plan, _host, _port: FailingApi("api", events, Path("a.log")))
    with pytest.raises(LaunchError):
        _run_services(_plan())
    assert events[-2:] == ["stop api", "stop mlflow"]


def _stub_cli(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class Services:
        def __init__(self, plan: LaunchPlan, **kwargs: Any) -> None:
            seen["plan"], seen["kwargs"] = plan, kwargs

        def __enter__(self) -> LaunchPlan:
            return seen["plan"]

        def __exit__(self, *_exc: Any) -> None:
            seen["exited"] = True

    monkeypatch.setattr(cli.launcher, "running_services", Services)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **kw: seen.setdefault("ran", kw))
    return seen


def test_fleet_cli_starts_services_then_runs_the_client_against_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1"])
    plan = seen["plan"]
    assert plan.start_api and plan.start_mlflow and seen["exited"]
    assert seen["ran"] == {"base_url": plan.api_url, "session_id": "s1"}


def test_fleet_cli_with_an_explicit_url_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _stub_cli(monkeypatch)
    cli.main(["cli", "--session", "s1", "--url", "http://remote.test:9000"])
    assert not seen["plan"].start_api and seen["ran"]["base_url"] == "http://remote.test:9000"


def test_fleet_cli_reports_a_startup_failure_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    class Failing:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> None:
            raise LaunchError("API server exited during startup (code 1).")

        def __exit__(self, *_exc: Any) -> None:
            return None

    monkeypatch.setattr(cli.launcher, "running_services", Failing)
    monkeypatch.setattr(cli.launcher, "api_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli.launcher, "mlflow_is_healthy", lambda _url: False)
    monkeypatch.setattr(cli, "run_interactive_cli", lambda **_kw: pytest.fail("client must not start"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["cli"])
    assert exit_info.value.code == 1
