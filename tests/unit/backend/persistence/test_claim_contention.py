"""Claim contention and reconciliation contracts.

* ``test_contention_scenarios.py``: Execute live-lane assertions locally; this is not PostgreSQL certification.
* ``test_claim_constraint_classification.py``: Only expected unique claim constraints may enter race reconciliation.
"""

import sqlite3

import asyncpg
import pytest
from sqlalchemy.exc import IntegrityError

from fleet_rlm.persistence.repositories.turns import _expected_claim_conflict
from tests.support import claim_scenarios
from tests.support.sqlite_claims import postgres_claim_store

__all__ = ["postgres_claim_store"]

# --- from test_contention_scenarios.py --------------------------------
pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("race", ["duplicate", "conflicting_input", "active_run"])
async def test_postgres_concurrent_claims_have_one_owner(postgres_claim_store, race):
    await claim_scenarios.concurrent_claims_have_one_owner(postgres_claim_store, race)


async def test_postgres_cancel_settlement_races_commit(postgres_claim_store):
    await claim_scenarios.cancel_settlement_races_commit(postgres_claim_store)


async def test_postgres_recovery_owner_cas_fences_stale_commit(postgres_claim_store):
    await claim_scenarios.recovery_owner_cas_fences_stale_commit(postgres_claim_store)


async def test_postgres_outbox_claims_are_disjoint(postgres_claim_store):
    await claim_scenarios.outbox_claims_are_disjoint(postgres_claim_store)


# --- from test_claim_constraint_classification.py ---------------------
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
    original = asyncpg.exceptions.UniqueViolationError() if code == "23505" else Exception()
    original.sqlstate = code
    original.constraint_name = constraint
    assert _expected_claim_conflict(IntegrityError(None, None, original)) is expected
