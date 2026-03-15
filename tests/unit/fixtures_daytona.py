from __future__ import annotations

import uuid
from typing import Any

from fleet_rlm.infrastructure.providers.daytona.protocol import (
    ExecutionEventFrame,
    ExecutionResponse,
    HostCallbackRequest,
)
from fleet_rlm.infrastructure.providers.daytona.types import ContextSource


class FakeLmSequence:
    def __init__(self, responses: list[str]):
        self._responses = responses
        self.prompts: list[str] = []

    def __call__(self, prompt: str):
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("Unexpected extra LM call.")
        return [{"text": self._responses.pop(0)}]


def code_block(body: str) -> str:
    return f"```python\n{body}\n```"


def make_response(
    *,
    final_value: object | None = None,
    error: str | None = None,
    callback_count: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> ExecutionResponse:
    final_artifact = None
    if final_value is not None:
        final_artifact = {
            "kind": "markdown",
            "value": final_value,
            "finalization_mode": "SUBMIT",
        }
    return ExecutionResponse(
        request_id=uuid.uuid4().hex,
        stdout=stdout,
        stderr=stderr,
        error=error,
        final_artifact=final_artifact,
        duration_ms=12,
        callback_count=callback_count,
    )


class FakeStep:
    def __init__(
        self,
        *,
        response: ExecutionResponse | None = None,
        callbacks: list[HostCallbackRequest] | None = None,
        progress_frames: list[ExecutionEventFrame] | None = None,
    ) -> None:
        self.response = response or make_response()
        self.callbacks = list(callbacks or [])
        self.progress_frames = list(progress_frames or [])


class FakeRunSession:
    def __init__(
        self,
        *,
        steps: list[FakeStep] | None = None,
        context_sources: list[ContextSource] | None = None,
    ) -> None:
        self.workspace_path = "/workspace/repo"
        self.sandbox_id = "sbx-root"
        self.context_sources = list(context_sources or [])
        self.steps = list(steps or [])
        self.reset_calls = 0
        self.close_driver_calls = 0
        self.deleted = False
        self.execute_calls: list[dict[str, object]] = []
        self.callback_responses: list[Any] = []
        self.prompt_handles: list[dict[str, Any]] = []
        self.store_prompt_calls: list[dict[str, object]] = []
        self.phase_timings_ms: dict[str, int] = {}

    def reset_for_new_call(self, *, timeout: float = 5.0) -> None:
        _ = timeout
        self.reset_calls += 1

    def close_driver(self, *, timeout: float = 5.0) -> None:
        _ = timeout
        self.close_driver_calls += 1

    def delete(self) -> None:
        self.deleted = True

    def execute_code(
        self,
        *,
        code: str,
        callback_handler,
        timeout: float,
        submit_schema=None,
        cancel_check=None,
        progress_handler=None,
    ) -> ExecutionResponse:
        self.execute_calls.append(
            {
                "code": code,
                "timeout": timeout,
                "submit_schema": submit_schema,
                "cancelled": bool(cancel_check())
                if cancel_check is not None
                else False,
            }
        )
        if not self.steps:
            raise AssertionError("Unexpected execute_code call.")
        step = self.steps.pop(0)
        if progress_handler is not None:
            for frame in step.progress_frames:
                progress_handler(frame)
        for request in step.callbacks:
            response = callback_handler(request)
            self.callback_responses.append(response)
            if not response.ok:
                return make_response(error=response.error, callback_count=1)
        return step.response

    def store_prompt(
        self,
        *,
        text: str,
        kind: str = "manual",
        label: str | None = None,
        timeout: float = 30.0,
    ):
        self.store_prompt_calls.append(
            {"text": text, "kind": kind, "label": label, "timeout": timeout}
        )
        handle = {
            "handle_id": f"prompt-{len(self.store_prompt_calls)}",
            "kind": kind,
            "label": label,
            "path": f".fleet-rlm/prompts/prompt-{len(self.store_prompt_calls)}.txt",
            "char_count": len(text),
            "line_count": len(text.splitlines()),
            "preview": text[:120],
        }
        self.prompt_handles.append(handle)
        from fleet_rlm.infrastructure.providers.daytona.types import PromptHandle

        return PromptHandle.from_raw(handle)

    def list_prompts(self, *, timeout: float = 30.0):
        _ = timeout
        from fleet_rlm.infrastructure.providers.daytona.types import PromptHandle, PromptManifest

        return PromptManifest(
            handles=[PromptHandle.from_raw(item) for item in self.prompt_handles]
        )


class FakeRuntime:
    def __init__(self, session: FakeRunSession | list[FakeRunSession]):
        sessions = session if isinstance(session, list) else [session]
        self._sessions = list(sessions)
        self.workspace_calls: list[tuple[str | None, str | None, list[str] | None]] = []

    def create_workspace_session(
        self,
        *,
        repo_url: str | None,
        ref: str | None,
        context_paths: list[str] | None,
    ):
        self.workspace_calls.append((repo_url, ref, context_paths))
        if not self._sessions:
            raise AssertionError("Unexpected extra workspace session request.")
        return self._sessions.pop(0)


__all__ = [
    "FakeLmSequence",
    "FakeRuntime",
    "FakeRunSession",
    "FakeStep",
    "code_block",
    "make_response",
]
