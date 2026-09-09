from __future__ import annotations

import pytest

from fleet_rlm.persistence.database import ManagedDatabasePolicyError, validate_managed_postgres_url


@pytest.mark.parametrize(
    "url",
    (
        "sqlite+aiosqlite:///fleet.sqlite3",
        "postgresql://other:password@lakebase.example/fleet?sslmode=require",
        "postgresql://fleet_app:password@lakebase.example/fleet",
    ),
)
def test_managed_database_policy_rejects_non_durable_or_non_tls_urls(url: str) -> None:
    with pytest.raises(ManagedDatabasePolicyError):
        validate_managed_postgres_url(url)


def test_managed_database_policy_accepts_tls_durable_role() -> None:
    validate_managed_postgres_url("postgresql://fleet_app:password@lakebase.example/fleet?sslmode=require")
