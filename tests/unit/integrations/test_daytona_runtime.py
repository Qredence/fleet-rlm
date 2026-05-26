from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_daytona_interpreter_session_lifecycle_uses_fake_runtime(daytona_runtime, daytona_session) -> None:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    interpreter = DaytonaInterpreter(runtime=daytona_runtime)

    await interpreter.astart()

    assert daytona_runtime.create_calls == [(None, None, [], None)]
    assert daytona_session.driver_started == 1

    await interpreter.ashutdown()

    assert daytona_session.deleted == 1
    assert daytona_runtime.close_calls == 0


@pytest.mark.asyncio
async def test_workspace_manager_exports_state_and_resumes_with_fake_runtime(daytona_runtime, daytona_session) -> None:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    interpreter = DaytonaInterpreter(
        runtime=daytona_runtime,
        repo_url="https://github.com/example/repo.git",
        repo_ref="main",
        context_paths=["src"],
        volume_name="tenant-volume",
    )
    await interpreter.aget_session()

    exported = interpreter.export_session_state()

    assert exported["daytona"]["repo_url"] == "https://github.com/example/repo.git"
    assert exported["daytona"]["repo_ref"] == "main"
    assert exported["daytona"]["context_paths"] == ["src"]
    assert exported["daytona"]["volume_name"] == "tenant-volume"
    assert exported["daytona"]["sandbox_id"] == daytona_session.sandbox_id

    resumed = DaytonaInterpreter(runtime=daytona_runtime)
    await resumed.aimport_session_state(exported)
    session = await resumed.aget_session()

    assert session is daytona_session
    assert daytona_runtime.resume_calls == [
        (
            daytona_session.sandbox_id,
            "https://github.com/example/repo.git",
            "main",
            daytona_session.workspace_path,
        )
    ]


def test_daytona_interpreter_default_execution_profile_switches_executor(daytona_runtime) -> None:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
    from fleet_rlm.runtime.execution.interpreter_protocol import ExecutionProfile

    interpreter = DaytonaInterpreter(runtime=daytona_runtime)

    interpreter.default_execution_profile = ExecutionProfile.ROOT_INTERLOCUTOR

    assert interpreter._active_executor.default_execution_profile is ExecutionProfile.ROOT_INTERLOCUTOR


def test_daytona_interpreter_applies_delegate_timeout_and_broker_settings(daytona_runtime) -> None:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    interpreter = DaytonaInterpreter(
        runtime=daytona_runtime,
        timeout=900,
        execute_timeout=900,
        delegate_execution_timeout=45,
        broker_health_timeout=7.5,
        broker_start_retries=2,
    )

    assert interpreter.delegate_execution_timeout == 45
    assert interpreter.broker_health_timeout == 7.5
    assert interpreter.broker_start_retries == 2
    assert interpreter._active_executor.broker_health_timeout == 7.5
    assert interpreter._active_executor.broker_start_retries == 2


def test_daytona_volume_layout_matches_phase_one_skeleton() -> None:
    from fleet_rlm.integrations.daytona.sdk_ops import ensure_daytona_volume_layout

    created: list[str] = []
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(create_folder=lambda directory, mode: created.append(directory)),
        process=SimpleNamespace(exec=lambda cmd: None),
    )

    ensure_daytona_volume_layout(sandbox=sandbox, mounted_root="/data")

    assert {
        "/data/memory",
        "/data/artifacts",
        "/data/buffers",
        "/data/meta",
        "/data/memories",
        "/data/knowledge/ingested",
        "/data/knowledge/summaries",
        "/data/skills/system",
        "/data/skills/user",
        "/data/sessions",
        "/data/logs",
        "/data/uploads",
    } <= set(created)


def test_store_evidence_redacts_credentials_from_bridge_errors() -> None:
    from fleet_rlm.integrations.daytona.isolation import store_evidence

    identity = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    interpreter = MagicMock()
    interpreter._host_repository = MagicMock()
    interpreter._host_identity = identity
    interpreter._host_run_id = uuid.uuid4()

    leaking_error = RuntimeError(
        "failed DATABASE_URL=postgresql://user:secret@db.example/app DAYTONA_API_KEY=topsecret"
    )

    with patch(
        "fleet_rlm.integrations.daytona.isolation._run_async_compat",
        side_effect=leaking_error,
    ):
        result = store_evidence(interpreter, key="child-result", content="payload")

    assert result["status"] == "error"
    assert "postgresql://user:secret@db.example/app" not in result["error"]
    assert "DAYTONA_API_KEY" not in result["error"]
    assert "[REDACTED]" in result["error"]
