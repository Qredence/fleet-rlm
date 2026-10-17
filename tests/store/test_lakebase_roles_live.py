"""Opt-in privilege contract test for Fleet's fresh Lakebase database.

Run only against the explicitly selected database with
``FLEET_RUN_LAKEBASE_ROLE_TESTS=1`` and the two ``FLEET_ROLE_TEST_*_URL``
variables set. Ordinary CI skips this test and never connects to Lakebase.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import InsufficientPrivilege

_ENABLED = os.environ.get("FLEET_RUN_LAKEBASE_ROLE_TESTS") == "1"


@pytest.mark.skipif(not _ENABLED, reason="explicit Lakebase role-test opt-in required")
def test_fresh_lakebase_role_split_and_default_privileges() -> None:
    runtime_url = os.environ.get("FLEET_ROLE_TEST_RUNTIME_URL")
    migration_url = os.environ.get("FLEET_ROLE_TEST_MIGRATION_URL")
    if not runtime_url or not migration_url:
        pytest.skip("set both FLEET_ROLE_TEST_*_URL variables for live verification")

    runtime_info = conninfo_to_dict(runtime_url)
    migration_info = conninfo_to_dict(migration_url)
    if runtime_info.get("dbname") != migration_info.get("dbname"):
        pytest.fail("runtime and migration DSNs must target the same database")
    if runtime_info.get("host") != migration_info.get("host"):
        pytest.fail("runtime and migration DSNs must target the same endpoint")
    if runtime_info.get("dbname") != "fleet_rlm_lite":
        pytest.fail("refusing live role test outside the fresh fleet_rlm_lite database")

    schema = "fleet_rlm_lite"
    table_name = f"_fleet_role_check_{uuid.uuid4().hex}"
    qualified_table = sql.Identifier(schema, table_name)

    with psycopg.connect(migration_url, autocommit=True) as migration:
        migration_role = migration.execute("SELECT current_user").fetchone()[0]
        if migration_role != "fleet_migrator":
            pytest.fail("migration DSN must authenticate as fleet_migrator")
        migration.execute(
            sql.SQL("CREATE TABLE {} (id integer PRIMARY KEY, value text NOT NULL)").format(qualified_table)
        )
        try:
            with psycopg.connect(runtime_url, autocommit=True) as runtime:
                runtime_role = runtime.execute("SELECT current_user").fetchone()[0]
                if runtime_role != "fleet_app":
                    pytest.fail("runtime DSN must authenticate as fleet_app")

                usage, create = runtime.execute(
                    "SELECT has_schema_privilege(current_user, %s, 'USAGE'), "
                    "has_schema_privilege(current_user, %s, 'CREATE')",
                    (schema, schema),
                ).fetchone()
                assert usage is True
                assert create is False

                has_dml = runtime.execute(
                    "SELECT bool_and(has_table_privilege(current_user, %s, privilege)) "
                    "FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE']) AS privilege",
                    (f"{schema}.{table_name}",),
                ).fetchone()[0]
                assert has_dml is True

                with pytest.raises(InsufficientPrivilege):
                    runtime.execute(
                        sql.SQL("CREATE TABLE {} (id integer)").format(sql.Identifier(schema, f"{table_name}_denied"))
                    )

                runtime.execute(sql.SQL("INSERT INTO {} (id, value) VALUES (1, 'ok')").format(qualified_table))
                runtime.execute(sql.SQL("UPDATE {} SET value = 'updated' WHERE id = 1").format(qualified_table))
                assert runtime.execute(sql.SQL("SELECT value FROM {}").format(qualified_table)).fetchone() == (
                    "updated",
                )
                runtime.execute(sql.SQL("DELETE FROM {} WHERE id = 1").format(qualified_table))
        finally:
            migration.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(qualified_table))
