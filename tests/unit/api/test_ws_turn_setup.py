from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_build_trace_context_includes_attempt_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.api.routers.ws import turn_setup
    from fleet_rlm.integrations.observability import mlflow_runtime

    monkeypatch.setattr(mlflow_runtime, "new_client_request_id", lambda *, prefix: f"{prefix}-fixed")

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
