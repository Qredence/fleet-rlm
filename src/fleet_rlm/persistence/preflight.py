"""Read-only managed PostgreSQL/Lakebase readiness checks.

The preflight is an operator diagnostic, not an application startup migration.
It records only non-secret identity and privilege facts and never changes the
target database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    ensure_database_compatible,
    normalize_database_url,
    validate_managed_postgres_url,
)

# These are the tables mutated by normal Session/Turn settlement, recovery,
# attachment/artifact persistence, and the durable promotion outbox.  The
# operator role must be able to perform the complete write lifecycle; checking
# only SELECT on ``fleet_sessions`` would let a read-only role pass readiness.
_REQUIRED_DML_PRIVILEGES: tuple[tuple[str, str], ...] = (
    *tuple(
        (table, action)
        for table in (
            "fleet_sessions",
            "fleet_runs",
            "fleet_turns",
            "fleet_sandbox_bindings",
            "fleet_attachments",
            "fleet_artifacts",
            "fleet_memory_promotion_intents",
        )
        for action in ("INSERT", "UPDATE", "DELETE")
    ),
    # SessionCatalog and AttachmentCatalog lazily create these parent rows
    # before inserting their dependent records.
    ("fleet_users", "INSERT"),
    ("fleet_workspaces", "INSERT"),
)

# Every table touched by the normal durable request paths is read as well as
# written.  A role with INSERT/UPDATE/DELETE but no SELECT can still pass a
# write-only check while claim, settlement, recovery, and artifact lookups
# fail at runtime.  Keep this list explicit so the operator receipt describes
# the complete table-read contract rather than only the session table.
_REQUIRED_SELECT_TABLES: tuple[str, ...] = (
    "fleet_users",
    "fleet_workspaces",
    "fleet_sessions",
    "fleet_runs",
    "fleet_turns",
    "fleet_sandbox_bindings",
    "fleet_attachments",
    "fleet_artifacts",
    "fleet_memory_promotion_intents",
    "fleet_warm_pool_ownership",
)


class ManagedDatabasePreflightError(RuntimeError):
    """Raised when a managed database cannot be certified safely."""


@dataclass(frozen=True, slots=True)
class ManagedDatabasePreflight:
    backend: str
    server_version_num: str
    current_user: str
    database: str
    schema: str
    schema_usage: bool
    schema_create: bool
    alembic_select: bool
    fleet_sessions_select: bool
    alembic_head: bool
    mlflow_storage_separate: bool | None
    fleet_dml_privileges: dict[str, bool] = field(default_factory=dict)
    fleet_select_privileges: dict[str, bool] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        # Keep receipts complete even when a caller constructs this value from
        # a partial observation.  Missing privileges must never look granted.
        fleet_dml = dict(self.fleet_dml_privileges)
        for table, action in _REQUIRED_DML_PRIVILEGES:
            fleet_dml.setdefault(f"{table}:{action.lower()}", False)
        fleet_select = dict(self.fleet_select_privileges)
        # Preserve the original single-table field for callers that construct
        # a receipt directly, but never let omitted observations look granted.
        fleet_select.setdefault("fleet_sessions", self.fleet_sessions_select)
        fleet_sessions_select = fleet_select["fleet_sessions"]
        for table in _REQUIRED_SELECT_TABLES:
            fleet_select.setdefault(table, False)
        return {
            "backend": self.backend,
            "server_version_num": self.server_version_num,
            "database": self.database,
            "schema": self.schema,
            "current_user": self.current_user,
            "privileges": {
                "schema_usage": self.schema_usage,
                "schema_create": self.schema_create,
                "alembic_select": self.alembic_select,
                "fleet_sessions_select": fleet_sessions_select,
                "fleet_select": fleet_select,
                "fleet_dml": fleet_dml,
            },
            "alembic_head": self.alembic_head,
            "mlflow_storage_separate": self.mlflow_storage_separate,
        }


def _storage_host(value: str | None) -> str | None:
    """Return a non-secret storage host, or ``None`` for logical backends."""
    if not value or value in {"databricks", "mlflow", "file"}:
        return None
    parsed = urlsplit(value)
    return parsed.hostname.lower() if parsed.hostname else None


def storage_is_separate(database_url: str, mlflow_tracking_uri: str | None) -> bool | None:
    """Compare storage endpoints without retaining URLs or credentials.

    Logical MLflow backends such as ``databricks`` cannot be compared by host;
    they are therefore reported as unknown rather than guessed.
    """
    mlflow_host = _storage_host(mlflow_tracking_uri)
    if mlflow_tracking_uri in {"databricks", "mlflow", "file"}:
        return None
    db_host = _storage_host(normalize_database_url(database_url))
    if mlflow_host is None or db_host is None:
        return None
    return mlflow_host != db_host


async def inspect_managed_postgres(
    database_url: str,
    *,
    repo_root: Any = None,
    mlflow_tracking_uri: str | None = None,
) -> ManagedDatabasePreflight:
    """Run the non-mutating managed PostgreSQL preflight."""
    try:
        validate_managed_postgres_url(database_url)
        await ensure_database_compatible(database_url, repo_root=repo_root)
        engine = create_async_engine_from_url(database_url)
        try:
            async with engine.connect() as connection:
                identity = (
                    await connection.execute(
                        text(
                            "SELECT current_user, current_database(), current_schema(), "
                            "current_setting('server_version_num')"
                        )
                    )
                ).one()
                privileges = (
                    await connection.execute(
                        text(
                            "SELECT "
                            "has_schema_privilege(current_user, current_schema(), 'USAGE'), "
                            "has_schema_privilege(current_user, current_schema(), 'CREATE'), "
                            "has_table_privilege(current_user, 'alembic_version', 'SELECT'), "
                            + ", ".join(
                                f"has_table_privilege(current_user, '{table}', 'SELECT')"
                                for table in _REQUIRED_SELECT_TABLES
                            )
                            + ", "
                            + ", ".join(
                                f"has_table_privilege(current_user, '{table}', '{action}')"
                                for table, action in _REQUIRED_DML_PRIVILEGES
                            )
                        )
                    )
                ).one()
        finally:
            await engine.dispose()
    except ManagedDatabasePreflightError:
        raise
    except Exception as exc:
        raise ManagedDatabasePreflightError("managed database preflight failed") from exc

    result = ManagedDatabasePreflight(
        backend="postgresql",
        server_version_num=str(identity[3]),
        current_user=str(identity[0]),
        database=str(identity[1]),
        schema=str(identity[2]),
        schema_usage=bool(privileges[0]),
        schema_create=bool(privileges[1]),
        alembic_select=bool(privileges[2]),
        fleet_sessions_select=bool(privileges[3 + _REQUIRED_SELECT_TABLES.index("fleet_sessions")]),
        fleet_dml_privileges={
            f"{table}:{action.lower()}": bool(value)
            for (table, action), value in zip(
                _REQUIRED_DML_PRIVILEGES,
                privileges[3 + len(_REQUIRED_SELECT_TABLES) :],
                strict=True,
            )
        },
        fleet_select_privileges={
            table: bool(value)
            for table, value in zip(
                _REQUIRED_SELECT_TABLES,
                privileges[3 : 3 + len(_REQUIRED_SELECT_TABLES)],
                strict=True,
            )
        },
        alembic_head=True,
        mlflow_storage_separate=storage_is_separate(database_url, mlflow_tracking_uri),
    )
    if result.current_user != "fleet_app":
        raise ManagedDatabasePreflightError("managed database is not using the fleet_app role")
    if not all((result.schema_usage, result.schema_create, result.alembic_select)):
        raise ManagedDatabasePreflightError("fleet_app lacks required schema or Fleet table privileges")
    if not all(result.fleet_select_privileges.get(table, False) for table in _REQUIRED_SELECT_TABLES):
        raise ManagedDatabasePreflightError("fleet_app lacks required Fleet SELECT privileges")
    if not all(
        result.fleet_dml_privileges.get(f"{table}:{action.lower()}", False)
        for table, action in _REQUIRED_DML_PRIVILEGES
    ):
        raise ManagedDatabasePreflightError("fleet_app lacks required Fleet write privileges")
    return result


__all__ = [
    "ManagedDatabasePreflight",
    "ManagedDatabasePreflightError",
    "inspect_managed_postgres",
    "storage_is_separate",
]
