"""Opt-in query plans from real repository calls on an exclusive test database.

Fixture scale is explicit. Plans retain topology/costs only, never SQL values,
filters, URLs or provider exceptions. Synthetic plans do not certify deployment
representativeness; the operator must retain the workload basis separately.
"""

from __future__ import annotations

import pytest

from tests.live.backend._postgres import postgres_claim_store
from tests.support.query_scenarios import repository_query_plan

__all__ = ["postgres_claim_store"]


@pytest.mark.parametrize("operation", ["sessions", "history", "replay", "recovery", "outbox"])
async def test_postgres_repository_query_plan(postgres_claim_store, record_testsuite_property, operation):
    await repository_query_plan(postgres_claim_store, record_testsuite_property, operation)
