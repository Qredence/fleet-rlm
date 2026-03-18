from __future__ import annotations

from fleet_rlm.infrastructure.providers.daytona.config import DaytonaConfigError
from fleet_rlm.infrastructure.providers.daytona.diagnostics import (
    DaytonaDiagnosticError,
)
from fleet_rlm.infrastructure.providers.daytona.smoke import run_daytona_smoke
from fleet_rlm.infrastructure.providers.daytona.types import (
    PromptHandle,
    PromptManifest,
    PromptSliceRef,
)


class _FakeSession:
    def __init__(self):
        self.sandbox_id = "sbx-123"
        self.workspace_path = "/workspace/repo"
        self.phase_timings_ms = {"sandbox_create": 2, "repo_clone": 4}
        self.driver_started = False
        self.deleted = False
        self.counter = 0
        self.execute_calls = 0
        self.prompt_counter = 0
        self.prompt_store: dict[str, str] = {}

    def start_driver(self, *, timeout: float = 30.0) -> None:
        del timeout
        self.driver_started = True

    def execute_code(
        self, *, code: str, callback_handler, timeout: float, submit_schema=None
    ):
        del code, callback_handler, timeout, submit_schema
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
                    "value": {"output": self.counter},
                    "finalization_mode": "SUBMIT",
                },
                "duration_ms": 1,
                "callback_count": 0,
            },
        )()

    def store_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ) -> PromptHandle:
        del timeout
        self.prompt_counter += 1
        handle_id = f"prompt-{self.prompt_counter}"
        self.prompt_store[handle_id] = text
        return PromptHandle(
            handle_id=handle_id,
            kind=kind,
            label=label,
            path=f".fleet-rlm/prompts/{handle_id}.txt",
            char_count=len(text),
            line_count=len(text.splitlines()),
            preview=text[:240],
        )

    def list_prompts(self, *, timeout: float = 30.0) -> PromptManifest:
        del timeout
        return PromptManifest(
            handles=[
                PromptHandle(
                    handle_id=handle_id,
                    kind="smoke",
                    label="smoke-prompt",
                    path=f".fleet-rlm/prompts/{handle_id}.txt",
                    char_count=len(text),
                    line_count=len(text.splitlines()),
                    preview=text[:240],
                )
                for handle_id, text in self.prompt_store.items()
            ]
        )

    def read_prompt_slice(
        self,
        *,
        handle_id: str,
        start_line: int = 1,
        num_lines: int = 120,
        start_char: int | None = None,
        char_count: int | None = None,
        timeout: float = 30.0,
    ) -> tuple[PromptSliceRef, str]:
        del timeout
        text = self.prompt_store[handle_id]
        if start_char is not None:
            start_idx = max(0, start_char)
            end_idx = start_idx + max(0, char_count or 4000)
            slice_text = text[start_idx:end_idx]
            return (
                PromptSliceRef(
                    handle_id=handle_id,
                    start_char=start_idx,
                    end_char=start_idx + len(slice_text),
                    preview=slice_text[:240],
                ),
                slice_text,
            )

        lines = text.splitlines()
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), start_idx + max(0, num_lines))
        slice_text = "\n".join(lines[start_idx:end_idx])
        return (
            PromptSliceRef(
                handle_id=handle_id,
                start_line=start_idx + 1 if slice_text else start_line,
                end_line=start_idx + len(lines[start_idx:end_idx])
                if slice_text
                else start_line,
                preview=slice_text[:240],
            ),
            slice_text,
        )

    def delete(self) -> None:
        self.deleted = True


class _FakeRuntime:
    def __init__(self, session: _FakeSession | None = None):
        self.session = session or _FakeSession()

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None = None,
    ):
        del context_paths
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
    assert result.finalization_mode == "SUBMIT"
    assert result.termination_phase == "completed"
    assert result.error_category is None
    assert result.error_message is None
    assert result.phase_timings_ms["sandbox_create"] == 2
    assert result.phase_timings_ms["repo_clone"] == 4
    assert result.workspace_path == "/workspace/repo"
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
        def create_workspace_session(
            self,
            *,
            repo_url: str | None,
            ref: str | None,
            context_paths: list[str] | None = None,
        ):
            del repo_url, ref, context_paths
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
        def execute_code(
            self, *, code: str, callback_handler, timeout: float, submit_schema=None
        ):
            del code, callback_handler, timeout, submit_schema
            raise RuntimeError("driver broke")

    runtime = _FakeRuntime(session=_BrokenSession())
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

    runtime = _FakeRuntime(session=_StartBrokenSession())
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

    runtime = _FakeRuntime(session=_CleanupBrokenSession())
    result = run_daytona_smoke(
        repo="https://github.com/example/repo.git",
        runtime=runtime,
    )

    assert result.termination_phase == "cleanup"
    assert result.error_category == "cleanup_error"
    assert "cleanup broke" in str(result.error_message)
    assert runtime.session.deleted is True
