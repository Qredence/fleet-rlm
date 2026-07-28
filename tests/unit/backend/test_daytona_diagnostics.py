from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from daytona import DaytonaError

from fleet_rlm.config import Settings
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.persistence.database import DatabaseCompatibilityError


class FakeDoctorDependencies:
    def __init__(self, *, fail_at: str | None = None, error: Exception | None = None) -> None:
        self.fail_at = fail_at
        self.error = error or RuntimeError("provider api_key=top-secret path=/home/private")
        self.calls: list[str] = []
        self.sandbox = SimpleNamespace(id="sandbox-doctor", snapshot="fleet-test-v1", labels={}, volumes=[])
        self.created: dict[str, Any] | None = None

    def _record(self, operation: str) -> None:
        self.calls.append(operation)
        if self.fail_at == operation:
            raise self.error

    async def check_database(self, settings: Settings) -> None:
        assert settings.database_url
        self._record("database")

    async def resolve_volume(self, settings: Settings) -> str:
        del settings
        self._record("provider")
        return "volume-doctor"

    async def create_sandbox(self, **kwargs: Any) -> Any:
        self._record("create")
        self.created = kwargs
        expected = kwargs["expected_mount"]
        self.sandbox.labels = {"workspace_id": str(expected.workspace_id)}
        self.sandbox.volumes = [
            {
                "volume_id": expected.volume_id,
                "mount_path": expected.mount_path,
                "subpath": expected.volume_subpath,
            }
        ]
        return self.sandbox

    async def verify_mount(self, sandbox: Any, expected_mount: Any) -> None:
        del expected_mount
        assert sandbox is self.sandbox
        self._record("mount")

    async def execute(self, sandbox: Any) -> str:
        assert sandbox is self.sandbox
        self._record("interpreter")
        return "fleet-doctor-ok\n"

    async def delete_sandbox(self, sandbox: Any) -> None:
        assert sandbox is self.sandbox
        self._record("delete")

    async def close(self) -> None:
        self._record("close")


def doctor_settings() -> Settings:
    return Settings(
        daytona_api_key="test-daytona-key",
        daytona_snapshot="fleet-test-v1",
        database_url="sqlite+aiosqlite:///:memory:",
    )


@pytest.mark.asyncio
async def test_daytona_doctor_reports_all_steps_and_deletes_disposable_sandbox() -> None:
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    dependencies = FakeDoctorDependencies()

    result = await run_daytona_doctor(doctor_settings(), dependencies=dependencies)

    assert result.ok is True
    assert [step.name for step in result.steps] == [
        "settings",
        "database",
        "provider",
        "sandbox",
        "interpreter",
        "cleanup",
    ]
    assert all(step.ok for step in result.steps)
    assert dependencies.calls == [
        "database",
        "provider",
        "create",
        "mount",
        "interpreter",
        "delete",
        "close",
    ]
    assert dependencies.created is not None
    assert dependencies.created["ephemeral"] is True
    labels = dependencies.created["labels"]
    assert labels["purpose"] == "fleet-daytona-doctor"
    assert labels["doctor_id"]
    assert labels["workspace_id"]
    assert dependencies.created["expected_mount"].volume_subpath.startswith("workspaces/")


@pytest.mark.asyncio
async def test_daytona_doctor_fails_settings_without_provider_operations() -> None:
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    dependencies = FakeDoctorDependencies()
    result = await run_daytona_doctor(
        Settings(_env_file=None, daytona_api_key=None, database_url=None),
        dependencies=dependencies,
    )

    assert result.ok is False
    assert result.failure_category == "settings"
    assert [(step.name, step.ok) for step in result.steps] == [("settings", False)]
    assert dependencies.calls == []


@pytest.mark.asyncio
async def test_daytona_doctor_reports_dependency_construction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.daytona import diagnostics

    def construct(settings: Settings) -> Any:
        del settings
        raise RuntimeError("provider api_key=private")

    monkeypatch.setattr(diagnostics, "_ProductionDaytonaDoctorDependencies", construct)

    result = await diagnostics.run_daytona_doctor(doctor_settings())

    assert result.ok is False
    assert result.failure_category == "unknown"
    assert [(step.name, step.ok) for step in result.steps] == [
        ("settings", True),
        ("provider", False),
    ]
    assert result.steps[-1].message == "The Daytona diagnostic failed safely."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fail_at", "error", "category", "failed_step", "deleted"),
    [
        ("database", RuntimeError("password=private"), "database", "database", False),
        ("provider", DaytonaError("token=private", status_code=401), "auth", "provider", False),
        ("provider", DaytonaError("quota token=private", status_code=429), "quota", "provider", False),
        (
            "create",
            DaytonaAdapterError(
                "Total disk limit exceeded. Upgrade your organization's Tier.",
                cause_type="DaytonaValidationError",
                status_code=400,
            ),
            "quota",
            "sandbox",
            False,
        ),
        ("create", TimeoutError("api_key=private"), "network_timeout", "sandbox", False),
        ("create", DaytonaError("secret=private", status_code=503), "provider_5xx", "sandbox", False),
        ("create", DaytonaError("secret=private", status_code=422), "request_validation", "sandbox", False),
        (
            "mount",
            DaytonaAdapterError("path=/home/private", cause_type="WorkspaceMountMismatch"),
            "mount_mismatch",
            "sandbox",
            True,
        ),
        ("interpreter", RuntimeError("api_key=private"), "interpreter", "interpreter", True),
        (
            "interpreter",
            DaytonaError("provider unavailable token=private", status_code=503),
            "provider_5xx",
            "interpreter",
            True,
        ),
    ],
)
async def test_daytona_doctor_categorizes_safe_failures_and_cleans_up(
    fail_at: str,
    error: Exception,
    category: str,
    failed_step: str,
    deleted: bool,
) -> None:
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    dependencies = FakeDoctorDependencies(fail_at=fail_at, error=error)
    result = await run_daytona_doctor(doctor_settings(), dependencies=dependencies)

    assert result.ok is False
    assert result.failure_category == category
    assert result.steps[-1].name == ("cleanup" if deleted else failed_step)
    failed = next(step for step in result.steps if not step.ok)
    assert failed.name == failed_step
    assert failed.category == category
    assert "private" not in failed.message
    assert "/home/" not in failed.message
    assert ("delete" in dependencies.calls) is deleted
    assert dependencies.calls[-1] == "close"


@pytest.mark.asyncio
async def test_daytona_doctor_database_step_surfaces_sanitized_remediation() -> None:
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    dependencies = FakeDoctorDependencies(
        fail_at="database",
        error=DatabaseCompatibilityError(
            "Fleet database is not at Alembic head; run `uv run python scripts/db_init.py`"
        ),
    )

    result = await run_daytona_doctor(doctor_settings(), dependencies=dependencies)

    failed = next(step for step in result.steps if not step.ok)
    assert failed.name == "database"
    assert failed.category == "database"
    assert failed.message == (
        "Database connection or Alembic revision validation failed. "
        "Fleet database is not at Alembic head; run `uv run python scripts/db_init.py`"
    )


@pytest.mark.asyncio
async def test_daytona_doctor_reports_cleanup_failure_without_exposing_provider_text() -> None:
    from fleet_rlm.daytona.diagnostics import run_daytona_doctor

    dependencies = FakeDoctorDependencies(
        fail_at="delete",
        error=RuntimeError("delete failed api_key=private path=/home/daytona"),
    )

    result = await run_daytona_doctor(doctor_settings(), dependencies=dependencies)

    assert result.ok is False
    assert result.failure_category == "cleanup"
    assert result.steps[-1].name == "cleanup"
    assert result.steps[-1].ok is False
    assert "private" not in result.steps[-1].message
    assert dependencies.calls[-1] == "close"
