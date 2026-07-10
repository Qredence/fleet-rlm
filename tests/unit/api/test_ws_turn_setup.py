from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_build_trace_context_includes_attempt_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.routers.ws import turn_setup
    from fleet_rlm.integrations.observability import mlflow_context

    monkeypatch.setattr(mlflow_context, "new_client_request_id", lambda *, prefix: f"{prefix}-fixed")

    context = turn_setup._build_trace_context(
        runtime=SimpleNamespace(cfg=SimpleNamespace(app_env="local")),
        workspace_id="workspace-1",
        user_id="user-1",
        sess_id="session-1",
        turn_index=1,
        run_id="workspace-1:user-1:session-1:1",
        message="hello",
        execution_mode="interactive",
    )

    assert context.client_request_id == "chat-fixed"
    assert context.session_id == "workspace-1:user-1:session-1"
    assert context.request_preview == "hello"
    assert context.metadata["fleet_rlm.turn_index"] == "1"
    assert context.metadata["fleet_rlm.predicted_turn_index"] == "1"
    assert context.metadata["fleet_rlm.turn_index_source"] == "agent_history"
    assert context.metadata["fleet_rlm.turn_attempt_id"] == "chat-fixed"
    assert context.metadata["fleet_rlm.client_request_id"] == "chat-fixed"
    assert context.metadata["fleet_rlm.run_id"] == "workspace-1:user-1:session-1:1"


@pytest.mark.asyncio
async def test_prepare_daytona_workspace_warm_starts_interpreter() -> None:
    from fleet_rlm.api.routers.ws.turn_setup import (
        DaytonaChatRequestOptions,
        prepare_daytona_workspace_for_turn,
    )

    class _Interpreter:
        timeout = 30
        started = False

        async def aconfigure_workspace(self, **kwargs: object) -> None:
            self.configured = kwargs

        async def astart(self) -> None:
            self.started = True

    interpreter = _Interpreter()
    agent = SimpleNamespace(interpreter=interpreter, loaded_document_paths=())
    request = DaytonaChatRequestOptions(
        repo_url=None,
        repo_ref=None,
        context_paths=[],
        batch_concurrency=None,
        workspace_id="default",
        sandbox_labels={},
    )

    await prepare_daytona_workspace_for_turn(agent=agent, request=request, docs_path=None)

    assert interpreter.started is True


@pytest.mark.asyncio
async def test_prepare_daytona_workspace_timeout_raises_diagnostic_error() -> None:
    from fleet_rlm.api.routers.ws.turn_setup import (
        DaytonaChatRequestOptions,
        prepare_daytona_workspace_for_turn,
    )
    from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError

    class _Interpreter:
        timeout = 0.01

        async def aconfigure_workspace(self, **kwargs: object) -> None:
            return None

        async def astart(self) -> None:
            import asyncio

            await asyncio.sleep(1)

    interpreter = _Interpreter()
    agent = SimpleNamespace(interpreter=interpreter, loaded_document_paths=())
    request = DaytonaChatRequestOptions(
        repo_url=None,
        repo_ref=None,
        context_paths=[],
        batch_concurrency=None,
        workspace_id="default",
        sandbox_labels={},
    )

    with pytest.raises(DaytonaDiagnosticError) as exc_info:
        await prepare_daytona_workspace_for_turn(agent=agent, request=request, docs_path=None)

    assert exc_info.value.category == "workspace_prep_timeout"


def test_build_prepare_stream_adds_local_snapshot_for_code_review(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.routers.ws import turn_setup
    from fleet_rlm.api.schemas import WSMessage

    monkeypatch.setattr(
        turn_setup,
        "_local_workspace_snapshot_path",
        lambda **kwargs: "/tmp/local-workspace-snapshot.md",
    )
    msg = WSMessage(
        type="message",
        content="Review my recent code changes and suggest improvements",
        execution_mode="rlm_only",
        context_paths=None,
    )

    daytona_request, _prepare = turn_setup._build_prepare_stream(
        agent=SimpleNamespace(),
        msg=msg,
        workspace_id="default",
        owner_tenant_claim="tenant",
        owner_user_claim="user",
        sess_id="session",
    )

    assert daytona_request.repo_url is None
    assert daytona_request.context_paths == ["/tmp/local-workspace-snapshot.md"]


def test_build_prepare_stream_keeps_remote_repo_without_local_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.routers.ws import turn_setup
    from fleet_rlm.api.schemas import WSMessage

    snapshot_calls: list[dict[str, object]] = []

    def _fake_snapshot(**kwargs: object) -> str:
        snapshot_calls.append(kwargs)
        return "/tmp/local-workspace-snapshot.md"

    monkeypatch.setattr(turn_setup, "_local_workspace_snapshot_path", _fake_snapshot)
    msg = WSMessage(
        type="message",
        content="Review my recent code changes and suggest improvements",
        execution_mode="rlm_only",
        repo_url="https://github.com/qredence/fleet-rlm",
        context_paths=None,
    )

    daytona_request, _prepare = turn_setup._build_prepare_stream(
        agent=SimpleNamespace(),
        msg=msg,
        workspace_id="default",
        owner_tenant_claim="tenant",
        owner_user_claim="user",
        sess_id="session",
    )

    assert daytona_request.repo_url == "https://github.com/qredence/fleet-rlm"
    assert daytona_request.context_paths == []
    assert snapshot_calls == []
