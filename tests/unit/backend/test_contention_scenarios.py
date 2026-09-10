"""Execute live-lane assertions locally; this is not PostgreSQL certification."""

import pytest

from tests.support import claim_scenarios
from tests.support.sqlite_claims import postgres_claim_store

__all__ = ["postgres_claim_store"]
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
