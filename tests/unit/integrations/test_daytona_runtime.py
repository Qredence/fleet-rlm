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
async def test_sandbox_create_reconciles_stale_slots_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    sandbox = SimpleNamespace(delete=MagicMock(), stop=MagicMock())
    acquire_calls = 0
    reconciled_counts: list[int] = []

    async def fake_acquire(*, timeout: float | None = None) -> bool:
        nonlocal acquire_calls
        _ = timeout
        acquire_calls += 1
        if acquire_calls == 1:
            raise asyncio.TimeoutError
        return True

    async def fake_provider_count(self: DaytonaSandboxRuntime) -> int:
        _ = self
        return 0

    def fake_reconcile(provider_active_count: int):
        reconciled_counts.append(provider_active_count)
        return concurrency.SandboxUsageStats(limit=5, available_slots=5, active_count=0)

    def fake_create(self: DaytonaSandboxRuntime, spec: SandboxSpec):
        _ = (self, spec)
        return sandbox

    monkeypatch.setattr(runtime_module, "acquire_sandbox_slot", fake_acquire)
    monkeypatch.setattr(
        runtime_module,
        "get_current_sandbox_usage",
        lambda: concurrency.SandboxUsageStats(limit=5, available_slots=0, active_count=5),
    )
    monkeypatch.setattr(runtime_module, "reconcile_sandbox_slots", fake_reconcile)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_count_provider_fleet_sandboxes", fake_provider_count)
    monkeypatch.setattr(runtime_module, "aresolve_sandbox_spec_snapshot", lambda spec, config: spec)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_create_sandbox_from_spec_impl", fake_create)

    result = await _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="custom-sandbox"))

    assert result is sandbox
    assert acquire_calls == 2
    assert reconciled_counts == [0]


@pytest.mark.asyncio
async def test_sandbox_create_keeps_busy_error_when_provider_reconcile_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.daytona import runtime as runtime_module
    from fleet_rlm.integrations.daytona.errors import DaytonaDiagnosticError
    from fleet_rlm.integrations.daytona.models import SandboxSpec
    from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

    async def fake_acquire(*, timeout: float | None = None) -> bool:
        _ = timeout
        raise asyncio.TimeoutError

    async def fake_provider_count(self: DaytonaSandboxRuntime) -> int:
        _ = self
        raise RuntimeError("provider unavailable")

    reconcile = MagicMock()

    monkeypatch.setattr(runtime_module, "acquire_sandbox_slot", fake_acquire)
    monkeypatch.setattr(runtime_module, "reconcile_sandbox_slots", reconcile)
    monkeypatch.setattr(DaytonaSandboxRuntime, "_count_provider_fleet_sandboxes", fake_provider_count)

    with pytest.raises(DaytonaDiagnosticError, match="Sandbox concurrency limit reached") as exc_info:
        await _sandbox_runtime().acreate_sandbox(spec=SandboxSpec(name="custom-sandbox"))

    assert exc_info.value.category == "sandbox_concurrency_busy"
    reconcile.assert_not_called()


def test_provider_fleet_sandbox_count_filters_labels_and_inactive_states() -> None:
    class FakeClient:
        def list(self, *, labels: dict[str, str]):
            assert labels == {"managed-by": "fleet-rlm"}
            return [
                SimpleNamespace(labels={"managed-by": "fleet-rlm"}, state="started"),
                SimpleNamespace(labels={"managed-by": "fleet-rlm"}, state="archived"),
                SimpleNamespace(labels={"managedBy": "fleet-pi"}, state="started"),
            ]

    runtime = _sandbox_runtime()
    runtime._client = FakeClient()

    assert runtime._count_provider_fleet_sandboxes_sync() == 1


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


def test_daytona_volume_browser_reports_all_canonical_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.integrations.daytona import file_browser
    from fleet_rlm.integrations.daytona.volumes import VFS_CANONICAL_ROOTS

    class _Sandbox:
        fs = SimpleNamespace(list_files=lambda path: [])

    class _MountedVolume:
        def __enter__(self) -> _Sandbox:
            return _Sandbox()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(file_browser, "_mounted_daytona_volume", lambda volume_name: _MountedVolume())

    tree = file_browser.list_daytona_volume_tree("volume", root_path="/", max_depth=1)

    assert tree["allowed_roots"] == sorted(VFS_CANONICAL_ROOTS)


def test_daytona_volume_browser_filters_root_to_canonical_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from fleet_rlm.integrations.daytona import file_browser

    class _Sandbox:
        def __init__(self) -> None:
            self.fs = SimpleNamespace(list_files=self._list_files)

        def _list_files(self, path: str) -> list[SimpleNamespace]:
            if path == str(file_browser.DAYTONA_PERSISTENT_VOLUME_MOUNT_PATH):
                return [
                    SimpleNamespace(name="artifacts", is_dir=True, size=None, mod_time=None),
                    SimpleNamespace(name="workspace", is_dir=True, size=None, mod_time=None),
                    SimpleNamespace(name="scratch.txt", is_dir=False, size=12, mod_time=None),
                ]
            return []

    class _MountedVolume:
        def __enter__(self) -> _Sandbox:
            return _Sandbox()

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(file_browser, "_mounted_daytona_volume", lambda volume_name: _MountedVolume())

    tree = file_browser.list_daytona_volume_tree("volume", root_path="/", max_depth=1)
    root_children = tree["nodes"][0]["children"]

    assert [child["path"] for child in root_children] == ["/artifacts"]
    assert tree["total_dirs"] == 1
    assert tree["total_files"] == 0


def test_build_browser_snapshot_image_includes_playwright_install() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.integrations.daytona import snapshots

    mock_image = MagicMock()
    mock_image.run_commands.return_value = mock_image

    fake_daytona_image = MagicMock()
    fake_daytona_image.base.return_value = mock_image

    with patch.dict("sys.modules", {"daytona": SimpleNamespace(Image=fake_daytona_image)}):
        result = snapshots.build_browser_snapshot_image(include_vnc=False)

    assert result is mock_image
    run_commands_calls = mock_image.run_commands.call_args_list
    # Should have: system deps, pip install uv, uv pip install packages, playwright install
    assert len(run_commands_calls) == 4
    system_call = run_commands_calls[0][0][0]
    assert "libx11-6" in system_call
    assert "libnss3" in system_call
    assert "xvfb" not in system_call  # VNC excluded
    playwright_call = run_commands_calls[3][0][0]
    assert "playwright install chromium" in playwright_call


def test_build_browser_snapshot_image_includes_vnc_when_enabled() -> None:
    from unittest.mock import MagicMock

    from fleet_rlm.integrations.daytona import snapshots

    mock_image = MagicMock()
    mock_image.run_commands.return_value = mock_image

    fake_daytona_image = MagicMock()
    fake_daytona_image.base.return_value = mock_image

    with patch.dict("sys.modules", {"daytona": SimpleNamespace(Image=fake_daytona_image)}):
        snapshots.build_browser_snapshot_image(include_vnc=True)

    system_call = mock_image.run_commands.call_args_list[0][0][0]
    assert "xvfb" in system_call
    assert "novnc" in system_call


def test_resolve_snapshot_for_skills_returns_browser_snapshot() -> None:
    from fleet_rlm.integrations.daytona.runtime import resolve_snapshot_for_skills
    from fleet_rlm.integrations.daytona.snapshots import BROWSER_SNAPSHOT_NAME, DEFAULT_SNAPSHOT_NAME

    assert resolve_snapshot_for_skills(None) == DEFAULT_SNAPSHOT_NAME
    assert resolve_snapshot_for_skills([]) == DEFAULT_SNAPSHOT_NAME
    assert resolve_snapshot_for_skills(["long-context"]) == DEFAULT_SNAPSHOT_NAME
    assert resolve_snapshot_for_skills(["browser_interaction"]) == BROWSER_SNAPSHOT_NAME
    assert resolve_snapshot_for_skills(["long-context", "browser_interaction"]) == BROWSER_SNAPSHOT_NAME
    assert resolve_snapshot_for_skills(["Playwright automation"]) == BROWSER_SNAPSHOT_NAME


def test_bind_interpreter_tool_generates_valid_store_evidence_wrapper() -> None:
    import ast

    from fleet_rlm.integrations.daytona.bridge import generate_tool_wrapper
    from fleet_rlm.integrations.daytona.bridge_callbacks import _bind_interpreter_tool
    from fleet_rlm.integrations.daytona.isolation import store_evidence

    interpreter = MagicMock()
    wrapper = _bind_interpreter_tool(interpreter, store_evidence)
    generated = generate_tool_wrapper(tool_name="store_evidence", tool_func=wrapper)

    ast.parse(generated)
    assert "def store_evidence(key, content" in generated
    assert "_fn=" not in generated


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
