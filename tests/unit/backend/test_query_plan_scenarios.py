"""Execute query-plan workload setup locally without claiming PostgreSQL proof."""

import pytest

from tests.live.backend.test_postgres_query_plans import test_postgres_repository_query_plan
from tests.unit.backend.test_contention_scenarios import postgres_claim_store

__all__ = ["postgres_claim_store", "test_postgres_repository_query_plan"]
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def local_query_plan_policy(monkeypatch):
    monkeypatch.setenv("FLEET_TEST_DATABASE_EXCLUSIVE", "1")
    monkeypatch.setenv("FLEET_POSTGRES_QUERY_SAMPLES", "8")
