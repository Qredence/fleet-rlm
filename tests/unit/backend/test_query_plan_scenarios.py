"""Execute query-plan workload setup locally without claiming PostgreSQL proof."""

import pytest

from tests.support.query_scenarios import repository_query_plan
from tests.support.sqlite_claims import postgres_claim_store

__all__ = ["postgres_claim_store"]
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def local_query_plan_policy(monkeypatch):
    monkeypatch.setenv("FLEET_TEST_DATABASE_EXCLUSIVE", "1")
    monkeypatch.setenv("FLEET_POSTGRES_QUERY_SAMPLES", "8")


@pytest.mark.parametrize("operation", ["sessions", "history", "replay", "recovery", "outbox"])
async def test_postgres_repository_query_plan(postgres_claim_store, record_testsuite_property, operation):
    await repository_query_plan(postgres_claim_store, record_testsuite_property, operation)
