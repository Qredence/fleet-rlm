"""Opt-in PostgreSQL claim races; only uniquely owned fixture rows are removed.

Requires FLEET_LIVE=1 and an explicitly exported FLEET_DATABASE_URL at
the current Alembic head. Does not load dotenv, migrate, or select global work.
"""

import pytest

from tests.live.backend._postgres import postgres_claim_store
from tests.support import claim_scenarios

__all__ = ["postgres_claim_store"]
pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.mark.parametrize("race", ["duplicate", "conflicting_input", "active_run"])
async def test_postgres_concurrent_claims_have_one_owner(postgres_claim_store, race):
    await claim_scenarios.concurrent_claims_have_one_owner(postgres_claim_store, race)


async def test_postgres_cancel_settlement_races_commit(postgres_claim_store):
    await claim_scenarios.cancel_settlement_races_commit(postgres_claim_store)


async def test_postgres_recovery_owner_cas_fences_stale_commit(postgres_claim_store):
    await claim_scenarios.recovery_owner_cas_fences_stale_commit(postgres_claim_store)


async def test_postgres_outbox_claims_are_disjoint(postgres_claim_store):
    await claim_scenarios.outbox_claims_are_disjoint(postgres_claim_store)
