"""Run from backend with uv. See the Slice 2 gate in docs/plans/fleet-neon-mlflow-rollout.md."""

import argparse
import json
import os
import secrets
import subprocess
import sys
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2] / "backend"
PROJECT = "polished-cherry-13944788"
REFERENCE = "br-flat-rice-baxyhhya"


def neon(*args):
    return subprocess.check_output(
        ["neonctl", *args, "--project-id", PROJECT], text=True
    ).strip()


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def rehearsal_branch():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "branch",
        nargs="?",
        help="Existing rehearsal ID. Omit to create a 24-hour child.",
    )
    branch_id = parser.parse_args().branch
    if branch_id is None:
        expiry = datetime.now(UTC) + timedelta(days=1)
        name = f"fleet-role-rehearsal-{secrets.token_hex(4)}"
        neon(
            "branches",
            "create",
            "--parent",
            REFERENCE,
            "--name",
            name,
            "--expires-at",
            expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        branch_id = json.loads(neon("branches", "get", name, "-o", "json"))["id"]
        print(f"Created rehearsal branch {branch_id}", flush=True)
    branch = json.loads(neon("branches", "get", branch_id, "-o", "json"))
    require(
        branch["id"] == branch_id
        and branch["parent_id"] == REFERENCE
        and branch["name"].startswith("fleet-role-rehearsal-")
        and not branch["default"]
        and not branch["protected"]
        and branch.get("expires_at")
        and datetime.fromisoformat(branch["expires_at"]) > datetime.now(UTC),
        "Expected an expiring rehearsal child of the schema-only Fleet reference",
    )
    return branch_id


def endpoints(branch_id, direct_role, pooled_role):
    direct = make_url(neon("connection-string", branch_id, "--role-name", direct_role))
    pooled = make_url(
        neon("connection-string", branch_id, "--pooled", "--role-name", pooled_role)
    )
    require(
        direct.host
        and pooled.host
        and "-pooler." not in direct.host
        and pooled.host.replace("-pooler.", ".", 1) == direct.host
        and pooled.host != direct.host
        and direct.database == pooled.database == "neondb",
        "Unexpected endpoint or database",
    )
    # Optional local forwarding preserves the endpoint hostname for TLS/SNI.
    if os.getenv("REHEARSAL_DIRECT_PORT") or os.getenv("REHEARSAL_POOL_PORT"):
        direct = direct.set(port=int(os.environ["REHEARSAL_DIRECT_PORT"]))
        pooled = pooled.set(port=int(os.environ["REHEARSAL_POOL_PORT"]))
        direct = direct.update_query_dict({"hostaddr": "127.0.0.1"})
        pooled = pooled.update_query_dict({"hostaddr": "127.0.0.1"})
    direct = direct.update_query_dict({"sslmode": "verify-full"})
    pooled = pooled.update_query_dict({"sslmode": "verify-full"})

    return direct, pooled


def checked(url, branch_id):
    conn = psycopg.connect(url.render_as_string(hide_password=False), connect_timeout=8)
    try:
        identity = conn.execute(
            "SELECT current_setting('neon.branch_id'), current_user, session_user, "
            "current_database()"
        ).fetchone()
        require(
            identity == (branch_id, url.username, url.username, url.database),
            "Connection identity mismatch",
        )
    except BaseException:
        conn.close()
        raise
    return conn


def provision(owner):
    with owner.transaction():
        before = owner.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchone()[0]
        roles = owner.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('app_runtime','app_migrator')"
        ).fetchall()
        if not roles:
            require(
                before == "0003_fk_and_query_indexes", "Expected the reference revision"
            )
            owner.execute(Path(__file__).with_name("fleet_roles.sql").read_text())
            for role in ("app_migrator", "app_runtime"):
                owner.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(secrets.token_urlsafe(32))
                    )
                )
        else:
            require(len(roles) == 2, "Incomplete role pair on rehearsal branch")

    return before


def verify_runtime(conn, existing_id):
    require(
        conn.execute(
            "DELETE FROM public.sessions WHERE id=%s RETURNING title", (existing_id,)
        ).fetchone()
        == ("pre-migration row",),
        "Pre-migration session missing",
    )
    for table, column in (("sessions", "title"), ("role_rehearsal_probe", "label")):
        relation, field = sql.Identifier("public", table), sql.Identifier(column)
        key = conn.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ('slice2') RETURNING id").format(
                relation, field
            )
        ).fetchone()[0]
        conn.execute(
            sql.SQL("UPDATE {} SET {} = 'verified' WHERE id = %s").format(
                relation, field
            ),
            (key,),
        )
        require(
            conn.execute(
                sql.SQL("SELECT {} FROM {} WHERE id = %s").format(field, relation),
                (key,),
            ).fetchone()
            == ("verified",),
            "DML failed",
        )
        require(
            conn.execute(
                sql.SQL("DELETE FROM {} WHERE id = %s").format(relation), (key,)
            ).rowcount
            == 1,
            "Delete failed",
        )
    require(
        conn.execute("SELECT nextval('public.role_rehearsal_probe_id_seq')").fetchone()[
            0
        ]
        > 1,
        "Sequence grant failed",
    )
    for statement in (
        "CREATE TABLE public.forbidden (id int)",
        "ALTER TABLE public.sessions ADD COLUMN forbidden int",
        "DROP TABLE public.role_rehearsal_probe",
        "CREATE TEMP TABLE forbidden (id int)",
        "CREATE SCHEMA forbidden",
        "UPDATE public.alembic_version SET version_num = version_num",
    ):
        try:
            with conn.transaction():
                conn.execute(statement)
                raise RuntimeError(f"Runtime unexpectedly allowed {statement}")
        except psycopg.errors.InsufficientPrivilege as exc:
            print(f"{exc.sqlstate} {statement}")


def main(resources):
    branch_id = rehearsal_branch()
    direct, pooled = endpoints(branch_id, "neondb_owner", "neondb_owner")
    with checked(pooled, branch_id):
        pass  # Verify both routes before provisioning.
    owner = resources.enter_context(checked(direct, branch_id))
    require(
        owner.execute("SELECT pg_try_advisory_lock(72188452)").fetchone() == (True,),
        "Another role rehearsal holds the database lock",
    )
    owner.commit()
    before = provision(owner)
    migrator, runtime = endpoints(branch_id, "app_migrator", "app_runtime")
    with checked(migrator, branch_id) as conn:
        require(
            conn.execute(
                "SELECT pg_has_role(current_user, 'neon_superuser','MEMBER')"
            ).fetchone()
            == (False,),
            "Elevated migrator",
        )
    with checked(runtime, branch_id) as conn:
        require(
            conn.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
                "OR pg_has_role(current_user,'neon_superuser','MEMBER') "
                "OR pg_has_role(current_user,'app_migrator','MEMBER') "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            == (False,),
            "Elevated runtime",
        )

        existing_id = conn.execute(
            "INSERT INTO public.sessions(title) VALUES ('pre-migration row') RETURNING id"
        ).fetchone()[0]

    os.chdir(BACKEND)
    env = {
        **os.environ,
        "FLEET_DAYTONA_API_KEY": "unused",
        "FLEET_LLM_MODEL": "openai/offline",
        "FLEET_SUB_LLM_MODEL": "openai/offline",
        "FLEET_LLM_API_KEY": "unused",
        "FLEET_DATABASE_URL": runtime.render_as_string(hide_password=False),
        "FLEET_DATABASE_DIRECT_URL": migrator.render_as_string(hide_password=False),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True
    )
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with checked(migrator, branch_id) as conn:
        require(
            conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
            == (head,),
            "Revision mismatch",
        )
        conn.execute(
            "CREATE TABLE public.role_rehearsal_probe (id bigint GENERATED ALWAYS AS IDENTITY "
            "PRIMARY KEY, label text)"
        )
    print(f"{branch_id}: direct app_migrator migrated {before} -> {head}", flush=True)
    try:
        with checked(runtime, branch_id) as conn:
            verify_runtime(conn, existing_id)
        lock_test = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_phase1_locking.py::test_root_lock_blocks_only_competing_roots",
                "-r",
                "s",
            ],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            text=True,
        )
        print(lock_test.stdout)
        lock_test.check_returncode()
        require("1 passed" in lock_test.stdout, "Root contention test did not run")
    finally:
        with checked(migrator, branch_id) as conn:
            conn.execute("DROP TABLE public.role_rehearsal_probe")


if __name__ == "__main__":
    with ExitStack() as resources:
        main(resources)
