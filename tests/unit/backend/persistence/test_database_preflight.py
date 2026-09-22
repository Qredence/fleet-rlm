"""Database preflight and compatibility-gate contracts.

* ``test_ensure_database_compatible.py``: Unit tests for the shared fail-closed compatibility gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm.persistence import database
from fleet_rlm.persistence.database import (
    DatabaseCompatibilityError,
    DatabaseConnectionError,
    ensure_database_compatible,
)
from fleet_rlm.persistence.preflight import (
    _REQUIRED_DML_PRIVILEGES,
    _REQUIRED_SELECT_TABLES,
    ManagedDatabasePreflight,
    ManagedDatabasePreflightError,
    inspect_managed_postgres,
    storage_is_separate,
)


# --- from test_database_preflight.py ----------------------------------
def test_storage_separation_compares_hosts_without_credentials() -> None:
    url = "postgresql://fleet_app:secret@lakebase.example/fleet?sslmode=require"
    assert storage_is_separate(url, "http://127.0.0.1:5001") is True
    assert storage_is_separate(url, "https://lakebase.example/mlflow") is False


@pytest.mark.parametrize("uri", ["databricks", "mlflow", "file", None])
def test_logical_or_missing_mlflow_backend_is_unknown(uri: str | None) -> None:
    assert storage_is_separate("postgresql://fleet_app:p@lakebase.example/fleet?sslmode=require", uri) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_privilege",
    [
        *[(table, "SELECT") for table in _REQUIRED_SELECT_TABLES],
        ("fleet_sessions", "INSERT"),
        ("fleet_users", "INSERT"),
        ("fleet_workspaces", "INSERT"),
    ],
)
async def test_managed_preflight_rejects_role_without_runtime_dml(
    monkeypatch: pytest.MonkeyPatch,
    missing_privilege: tuple[str, str],
) -> None:
    class Result:
        def __init__(self, value: tuple[object, ...]) -> None:
            self.value = value

        def one(self) -> tuple[object, ...]:
            return self.value

    class Connection:
        async def execute(self, statement):
            if "server_version_num" in str(statement):
                return Result(("fleet_app", "fleet", "public", "160000"))
            values = [
                True,
                True,
                True,
                *([True] * len(_REQUIRED_SELECT_TABLES)),
                *([True] * len(_REQUIRED_DML_PRIVILEGES)),
            ]
            if missing_privilege[1] == "SELECT":
                values[3 + _REQUIRED_SELECT_TABLES.index(missing_privilege[0])] = False
            else:
                values[3 + len(_REQUIRED_SELECT_TABLES) + _REQUIRED_DML_PRIVILEGES.index(missing_privilege)] = False
            return Result(tuple(values))

    class ConnectContext:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_args):
            return None

    class Engine:
        def connect(self):
            return ConnectContext()

        async def dispose(self):
            return None

    async def compatible(*_args, **_kwargs):
        return None

    monkeypatch.setattr("fleet_rlm.persistence.preflight.validate_managed_postgres_url", lambda _url: None)
    monkeypatch.setattr("fleet_rlm.persistence.preflight.ensure_database_compatible", compatible)
    monkeypatch.setattr("fleet_rlm.persistence.preflight.create_async_engine_from_url", lambda _url: Engine())

    with pytest.raises(ManagedDatabasePreflightError, match="privileges"):
        await inspect_managed_postgres("postgresql://fleet_app:p@lakebase.example/fleet?sslmode=require")


def test_preflight_receipt_defaults_missing_dml_privileges_to_false() -> None:
    observed = ManagedDatabasePreflight(
        backend="postgresql",
        server_version_num="160000",
        current_user="fleet_app",
        database="fleet",
        schema="public",
        schema_usage=True,
        schema_create=True,
        alembic_select=True,
        fleet_sessions_select=True,
        alembic_head=True,
        mlflow_storage_separate=None,
    )

    fleet_dml = observed.as_dict()["privileges"]["fleet_dml"]
    assert fleet_dml["fleet_users:insert"] is False
    assert fleet_dml["fleet_workspaces:insert"] is False

    fleet_select = observed.as_dict()["privileges"]["fleet_select"]
    assert fleet_select["fleet_sessions"] is True
    assert fleet_select["fleet_runs"] is False
    assert fleet_select["fleet_memory_promotion_intents"] is False


# --- from test_ensure_database_compatible.py --------------------------
@pytest.mark.asyncio
async def test_revision_mismatch_is_wrapped_with_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise DatabaseCompatibilityError("database revision does not match Alembic head")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseCompatibilityError) as error:
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))

    assert str(error.value) == "Fleet database is not at Alembic head; run `uv run python scripts/db_init.py`"


@pytest.mark.asyncio
async def test_connection_error_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise DatabaseConnectionError("fleet database compatibility could not be verified")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseConnectionError, match="could not be verified"):
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))


@pytest.mark.asyncio
async def test_unexpected_exception_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    async def reject(*_args: object, **_kwargs: object) -> None:
        raise OSError("could not read /secret/path/alembic.ini")

    monkeypatch.setattr(database, "check_database_compatibility", reject)

    with pytest.raises(DatabaseConnectionError) as error:
        await ensure_database_compatible("postgresql+asyncpg://u:p@h/db", repo_root=Path("/repo"))

    assert str(error.value) == "Fleet database compatibility could not be verified"
    assert "/secret/path" not in str(error.value)
    assert isinstance(error.value.__cause__, OSError)
