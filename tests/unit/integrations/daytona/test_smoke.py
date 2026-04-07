from __future__ import annotations

from dspy.primitives import FinalOutput

from fleet_rlm.integrations.daytona.config import DaytonaConfigError
from fleet_rlm.integrations.daytona.diagnostics import DaytonaDiagnosticError
from fleet_rlm.integrations.daytona.diagnostics import run_daytona_smoke
from tests.unit.fixtures_daytona import FakeDaytonaRuntime, FakeDaytonaSession


class _FakeInterpreter:
    def __init__(
        self,
        *,
        runtime: FakeDaytonaRuntime,
        owns_runtime: bool = False,
        repo_url: str | None,
        repo_ref: str | None,
        timeout: int,
        execute_timeout: int,
    ) -> None:
        del repo_url, repo_ref, timeout, execute_timeout
        self.runtime = runtime
        self.owns_runtime = owns_runtime
        self.session = runtime.create_workspace_session(repo_url=None, ref=None)
        self.counter = 0

    def _ensure_session_sync(self) -> FakeDaytonaSession:
        return self.session

    def start(self) -> None:
        self.session.start_driver()

    def execute(self, code: str):
        if "counter = 2" in code:
            self.counter = 2
            return ""
        if "counter += 3" in code:
            self.counter += 3
            return FinalOutput({"output": self.counter})
        raise RuntimeError(f"unexpected smoke code: {code}")

    def shutdown(self) -> None:
        self.session.delete()
        if self.owns_runtime:
            self.runtime.close()


def test_run_daytona_smoke_validates_context_persistence(monkeypatch) -> None:
    runtime = FakeDaytonaRuntime()
    runtime.session.sandbox_id = "sbx-123"
    runtime.session.workspace_path = "/workspace/repo"
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _FakeInterpreter,
    )

    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        ref="main",
        runtime=runtime,
    )

    assert result.sandbox_id == "sbx-123"
    assert result.persisted_state_value == 5
    assert result.driver_started is True
    assert result.finalization_mode == "SUBMIT"
    assert result.termination_phase == "completed"
    assert result.error_category is None
    assert result.error_message is None
    assert result.phase_timings_ms["sandbox_create"] == 2
    assert result.phase_timings_ms["repo_clone"] == 4
    assert result.workspace_path == "/workspace/repo"
    assert "cleanup" in result.phase_timings_ms
    assert runtime.session.deleted == 1


def test_run_daytona_smoke_reports_config_errors(monkeypatch) -> None:
    def _broken_runtime():
        raise DaytonaConfigError("Missing DAYTONA_API_URL.")

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime.DaytonaSandboxRuntime",
        _broken_runtime,
    )

    result = run_daytona_smoke(repo="https://github.com/example/repo.git")

    assert result.termination_phase == "config"
    assert result.error_category == "config_error"
    assert result.error_message == "Missing DAYTONA_API_URL."


def test_run_daytona_smoke_reports_clone_failures(monkeypatch) -> None:
    class _BrokenRuntime(FakeDaytonaRuntime):
        def create_workspace_session(
            self,
            *,
            repo_url: str | None,
            ref: str | None,
            context_paths: list[str] | None = None,
            volume_name: str | None = None,
        ) -> FakeDaytonaSession:
            del repo_url, ref, context_paths, volume_name
            raise DaytonaDiagnosticError(
                "Daytona repo clone failure: bad ref",
                category="sandbox_create_clone_error",
                phase="repo_clone",
            )

    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _FakeInterpreter,
    )
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=_BrokenRuntime(),
    )

    assert result.termination_phase == "repo_clone"
    assert result.error_category == "sandbox_create_clone_error"
    assert "bad ref" in str(result.error_message)


def test_run_daytona_smoke_reports_execution_failures_and_cleans_up(
    monkeypatch,
) -> None:
    class _BrokenInterpreter(_FakeInterpreter):
        def execute(self, code: str):
            del code
            raise RuntimeError("execution broke")

    runtime = FakeDaytonaRuntime()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _BrokenInterpreter,
    )
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=runtime,
    )

    assert result.termination_phase == "exec_step_1"
    assert result.error_category == "driver_execution_error"
    assert "execution broke" in str(result.error_message)
    assert runtime.session.deleted == 1


def test_run_daytona_smoke_closes_owned_runtime_after_startup_failure(
    monkeypatch,
) -> None:
    class _BrokenInterpreter(_FakeInterpreter):
        def _ensure_session_sync(self) -> FakeDaytonaSession:
            raise DaytonaDiagnosticError(
                "Daytona sandbox create failure: invalid credentials",
                category="sandbox_create_clone_error",
                phase="sandbox_create",
            )

    runtime = FakeDaytonaRuntime()
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.runtime.DaytonaSandboxRuntime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        "fleet_rlm.integrations.daytona.interpreter.DaytonaInterpreter",
        _BrokenInterpreter,
    )

    result = run_daytona_smoke(repo="https://github.com/example/repo.git")

    assert result.error_category == "sandbox_create_clone_error"
    assert result.termination_phase == "sandbox_create"
    assert runtime.close_calls == 1
