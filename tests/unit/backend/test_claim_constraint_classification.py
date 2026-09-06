"""Only expected unique claim constraints may enter race reconciliation."""

import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from fleet_rlm.persistence.repositories.turns import _expected_claim_conflict


@pytest.mark.parametrize(
    "message,code,expected",
    [
        ("UNIQUE constraint failed: fleet_runs.session_id", "SQLITE_CONSTRAINT_UNIQUE", True),
        (
            "UNIQUE constraint failed: fleet_runs.session_id, fleet_runs.idempotency_key",
            "SQLITE_CONSTRAINT_UNIQUE",
            True,
        ),
        ("UNIQUE constraint failed: fleet_runs.id", "SQLITE_CONSTRAINT_PRIMARYKEY", False),
        ("FOREIGN KEY constraint failed", "SQLITE_CONSTRAINT_FOREIGNKEY", False),
        ("CHECK constraint failed: ck_fleet_runs_status", "SQLITE_CONSTRAINT_CHECK", False),
    ],
)
def test_sqlite_constraint_allowlist(message, code, expected):
    original = sqlite3.IntegrityError(message)
    original.sqlite_errorname = code
    assert _expected_claim_conflict(IntegrityError(None, None, original)) is expected


@pytest.mark.parametrize(
    "constraint,code,expected",
    [
        ("uq_fleet_runs_one_running", "23505", True),
        ("uq_fleet_runs_live_idempotency", "23505", True),
        ("uq_fleet_runs_id_session", "23505", False),
        ("fk_fleet_turns_run_session", "23503", False),
        ("uq_fleet_runs_one_running", "23503", False),
    ],
)
def test_postgres_constraint_allowlist(constraint, code, expected):
    class DriverError(Exception):
        pass

    original = DriverError()
    original.sqlstate = code
    original.diag = SimpleNamespace(constraint_name=constraint)
    assert _expected_claim_conflict(IntegrityError(None, None, original)) is expected
