from __future__ import annotations

import asyncio
import datetime
import re
import time
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.integrations.daytona import concurrency


@pytest.fixture(autouse=True)
def _reset_sandbox_semaphore():
    """Reset Daytona sandbox slot state between runtime tests."""
    concurrency._GLOBAL_SEMAPHORE = None
    concurrency._INITIALIZED_CONFIG = None
    yield
    concurrency._GLOBAL_SEMAPHORE = None
    concurrency._INITIALIZED_CONFIG = None


def test_default_sandbox_name_keeps_timestamp_prefix_and_adds_unique_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.daytona import models

    fixed = datetime.datetime(2026, 5, 29, 4, 11, 13, tzinfo=datetime.timezone.utc)
    ids = iter(
        [
            SimpleNamespace(hex="11111111aaaaaaaaaaaaaaaaaaaaaaaa"),
            SimpleNamespace(hex="22222222bbbbbbbbbbbbbbbbbbbbbbbb"),
        ]
    )
    monkeypatch.setattr(models.uuid, "uuid4", lambda: next(ids))

    first = models.default_sandbox_name(now=fixed)
    second = models.default_sandbox_name(now=fixed)

    assert first == "fleet-rlm-20260529-041113-11111111"
    assert second == "fleet-rlm-20260529-041113-22222222"
    assert first != second
    assert re.fullmatch(r"fleet-rlm-\d{8}-\d{6}-[0-9a-f]{8}", first)


class DaytonaConflictError(Exception):
    pass


def _daytona_name_conflict(name: str) -> DaytonaConflictError:
    return DaytonaConflictError(
        f"Daytona provider failure (HTTP 409): Failed to create sandbox: Sandbox with name {name} already exists"
    )


def _sandbox_runtime():
    from fleet_rlm.integrations.daytona.config import ResolvedDaytonaConfig
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    return DaytonaSandboxRuntime(config=ResolvedDaytonaConfig(api_key="key", api_url="https://daytona.local"))


@pytest.mark.asyncio
async def test_sandbox_create_retries_generated_name_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    sandbox = SimpleNamespace(delete=MagicMock(), stop=MagicMock())
    calls: list[str | None] = []
    fresh_names = iter(["fleet-rlm-20260529-041114-deadbeef"])

    def fake_create(self: DaytonaSandboxRuntime, spec: SandboxSpec):
        _ = self
        calls.append(spec.name)
        if len(calls) == 1:
            raise _daytona_name_conflict(str(spec.name))
        return sandbox

    monkeypatch.setattr(runtime_module, "aresolve_sandbox_spec_snapshot", lambda spec, config: spec)
    monkeypatch.setattr(runtime_module, "_default_sandbox_name_helper", lambda: next(fresh_names))
    monkeypatch.setattr(DaytonaSandboxRuntime, "_create_sandbox_from_spec_impl", fake_create)

    result = await _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="fleet-rlm-20260529-041113-11111111"))

    assert result is sandbox
    assert calls == [
        "fleet-rlm-20260529-041113-11111111",
        "fleet-rlm-20260529-041114-deadbeef",
    ]
    assert concurrency.get_current_sandbox_usage().active_count == 1

    result.delete()

    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_sandbox_create_does_not_retry_explicit_non_generated_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    calls: list[str | None] = []

    def fake_create(self: DaytonaSandboxRuntime, spec: SandboxSpec):
        _ = self
        calls.append(spec.name)
        raise _daytona_name_conflict(str(spec.name))

    monkeypatch.setattr(runtime_module, "aresolve_sandbox_spec_snapshot", lambda spec, config: spec)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_create_sandbox_from_spec_impl", fake_create)

    with pytest.raises(DaytonaDiagnosticError, match="already exists"):
        await _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="custom-sandbox"))

    assert calls == ["custom-sandbox"]
    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_sandbox_create_releases_slot_after_retry_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    calls: list[str | None] = []
    retry_count = 0

    def fake_fresh_name() -> str:
        nonlocal retry_count
        retry_count += 1
        return f"fleet-rlm-20260529-04111{retry_count}-deadbee{retry_count}"

    def fake_create(self: DaytonaSandboxRuntime, spec: SandboxSpec):
        _ = self
        calls.append(spec.name)
        raise _daytona_name_conflict(str(spec.name))

    monkeypatch.setattr(runtime_module, "aresolve_sandbox_spec_snapshot", lambda spec, config: spec)
    monkeypatch.setattr(runtime_module, "_default_sandbox_name_helper", fake_fresh_name)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_create_sandbox_from_spec_impl", fake_create)

    with pytest.raises(DaytonaDiagnosticError, match="already exists"):
        await _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="fleet-rlm-20260529-041113-11111111"))

    assert len(calls) == 4
    assert concurrency.get_current_sandbox_usage().active_count == 0


@pytest.mark.asyncio
async def test_async_sandbox_create_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    events: list[str] = []
    sandbox = SimpleNamespace(delete=MagicMock(), stop=MagicMock())

    def fake_create(self: DaytonaSandboxRuntime, spec: SandboxSpec):
        _ = (self, spec)
        events.append("create_start")
        time.sleep(0.05)
        events.append("create_end")
        return sandbox

    async def probe_loop() -> None:
        await asyncio.sleep(0.01)
        events.append("probe")

    monkeypatch.setattr(runtime_module, "aresolve_sandbox_spec_snapshot", lambda spec, config: spec)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_create_sandbox_from_spec_impl", fake_create)

    result, _ = await asyncio.gather(
        _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="custom-sandbox")),
        probe_loop(),
    )

    assert result is sandbox
    assert events == ["create_start", "probe", "create_end"]

    result.delete()


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
async def test_daytona_interpreter_release_idle_session_deletes_session_without_closing_runtime(
    daytona_runtime,
    daytona_session,
) -> None:
    from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter

    interpreter = DaytonaInterpreter(runtime=daytona_runtime)

    first = await interpreter.aget_session()
    await interpreter.arelease_idle_session()
    second = await interpreter.aget_session()

    assert first is daytona_session
    assert second is daytona_session
    assert daytona_session.deleted == 1
    assert daytona_runtime.close_calls == 0
    assert len(daytona_runtime.create_calls) == 2


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
    code_runs: list[str] = []
    uploads: list[tuple[bytes, str]] = []
    sandbox = SimpleNamespace(
        fs=SimpleNamespace(
            create_folder=lambda directory, mode: created.append(directory),
            list_files=lambda directory: [],
            upload_file=lambda payload, path: uploads.append((payload, path)),
        ),
        process=SimpleNamespace(
            code_run=lambda code, **kwargs: code_runs.append(code) or SimpleNamespace(exit_code=0, stdout="")
        ),
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
    assert any("sqlite3" in code and "core.db" in code for code in code_runs)
    assert any(path.startswith("/data/skills/system/") and path.endswith(".md") for _, path in uploads)


def test_daytona_volume_browser_allows_durable_phase_roots() -> None:
    from fleet_rlm.integrations.daytona.volumes import VFS_CANONICAL_ROOTS

    assert {
        "/memory",
        "/artifacts",
        "/buffers",
        "/meta",
        "/memories",
        "/knowledge",
        "/skills",
        "/sessions",
        "/logs",
        "/uploads",
    } <= VFS_CANONICAL_ROOTS


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
