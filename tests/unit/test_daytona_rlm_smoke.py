from __future__ import annotations

from fleet_rlm.daytona_rlm.config import DaytonaConfigError
from fleet_rlm.daytona_rlm.diagnostics import DaytonaDiagnosticError
from fleet_rlm.daytona_rlm.smoke import run_daytona_smoke


class _FakeSession:
    def __init__(self):
        self.sandbox_id = "sbx-123"
        self.repo_path = "/workspace/repo"
        self.driver_started = False
        self.deleted = False
        self.counter = 0
        self.execute_calls = 0

    def start_driver(self, *, timeout: float = 30.0) -> None:
        del timeout
        self.driver_started = True

    def execute_code(self, *, code: str, callback_handler, timeout: float):
        del code, callback_handler, timeout
        self.execute_calls += 1
        if self.execute_calls == 1:
            self.counter = 2
            return type(
                "_ExecResponse",
                (),
                {
                    "stdout": "",
                    "stderr": "",
                    "error": None,
                    "final_artifact": None,
                    "duration_ms": 1,
                    "callback_count": 0,
                },
            )()

        self.counter += 3
        return type(
            "_ExecResponse",
            (),
            {
                "stdout": "",
                "stderr": "",
                "error": None,
                "final_artifact": {
                    "kind": "markdown",
                    "value": self.counter,
                    "finalization_mode": "FINAL_VAR",
                },
                "duration_ms": 1,
                "callback_count": 0,
            },
        )()

    def delete(self) -> None:
        self.deleted = True


class _FakeRuntime:
    def __init__(self):
        self.session = _FakeSession()

    def create_repo_session_with_diagnostics(self, *, repo_url: str, ref: str | None):
        self.session.repo_url = repo_url
        self.session.ref = ref
        return self.session, {"sandbox_create": 2, "repo_clone": 4}

    def create_repo_session(self, *, repo_url: str, ref: str | None):
        self.session.repo_url = repo_url
        self.session.ref = ref
        return self.session


def test_run_daytona_smoke_validates_driver_persistence():
    runtime = _FakeRuntime()

    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        ref="main",
        runtime=runtime,
    )

    assert result.sandbox_id == "sbx-123"
    assert result.persisted_state_value == 5
    assert result.driver_started is True
    assert result.finalization_mode == "FINAL_VAR"
    assert result.termination_phase == "completed"
    assert result.error_category is None
    assert result.error_message is None
    assert result.phase_timings_ms["sandbox_create"] == 2
    assert result.phase_timings_ms["repo_clone"] == 4
    assert "cleanup" in result.phase_timings_ms
    assert runtime.session.deleted is True


def test_run_daytona_smoke_reports_config_errors(monkeypatch):
    def _broken_runtime():
        raise DaytonaConfigError("Missing DAYTONA_API_URL.")

    monkeypatch.setattr(
        "fleet_rlm.daytona_rlm.smoke.DaytonaSandboxRuntime",
        _broken_runtime,
    )

    result = run_daytona_smoke(repo="https://github.com/example/repo.git")

    assert result.termination_phase == "config"
    assert result.error_category == "config_error"
    assert result.error_message == "Missing DAYTONA_API_URL."


def test_run_daytona_smoke_reports_clone_failures():
    class _BrokenRuntime(_FakeRuntime):
        def create_repo_session_with_diagnostics(
            self, *, repo_url: str, ref: str | None
        ):
            del repo_url, ref
            raise DaytonaDiagnosticError(
                "Daytona repo clone failure: bad ref",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            )

    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=_BrokenRuntime(),
    )

    assert result.termination_phase == "repo_clone"
    assert result.error_category == "sandbox_create_clone_error"
    assert "bad ref" in str(result.error_message)


def test_run_daytona_smoke_reports_driver_execution_failures_and_cleans_up():
    class _BrokenSession(_FakeSession):
        def execute_code(self, *, code: str, callback_handler, timeout: float):
            del code, callback_handler, timeout
            raise RuntimeError("driver broke")

    class _BrokenRuntime(_FakeRuntime):
        def __init__(self):
            self.session = _BrokenSession()

    runtime = _BrokenRuntime()
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=runtime,
    )

    assert result.termination_phase == "exec_step_1"
    assert result.error_category == "driver_execution_error"
    assert "driver broke" in str(result.error_message)
    assert runtime.session.deleted is True


def test_run_daytona_smoke_reports_driver_start_failures():
    class _StartBrokenSession(_FakeSession):
        def start_driver(self, *, timeout: float = 30.0) -> None:
            del timeout
            raise DaytonaDiagnosticError(
                "Daytona driver handshake failure: timed out",
                category="driver_handshake_error",
                phase="driver_start",
            )

    class _StartBrokenRuntime(_FakeRuntime):
        def __init__(self):
            self.session = _StartBrokenSession()

    runtime = _StartBrokenRuntime()
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=runtime,
    )

    assert result.termination_phase == "driver_start"
    assert result.error_category == "driver_handshake_error"
    assert "timed out" in str(result.error_message)
    assert runtime.session.deleted is True


def test_run_daytona_smoke_reports_cleanup_failures():
    class _CleanupBrokenSession(_FakeSession):
        def delete(self) -> None:
            self.deleted = True
            raise RuntimeError("cleanup broke")

    class _CleanupBrokenRuntime(_FakeRuntime):
        def __init__(self):
            self.session = _CleanupBrokenSession()

    runtime = _CleanupBrokenRuntime()
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=runtime,
    )

    assert result.termination_phase == "cleanup"
    assert result.error_category == "cleanup_error"
    assert "cleanup broke" in str(result.error_message)
    assert runtime.session.deleted is True
