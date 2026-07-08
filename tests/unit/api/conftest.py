"""Shared non-autouse fixtures for chat API tests.

Extracted from the formerly duplicated ``stub_identity`` fixture in
``test_chat_sse.py`` and ``test_cross_flows.py`` during Phase 2A.2
test/contract cleanup.

Deliberately excludes the autouse chat-router stubs
(``stub_chat_agent_context``, ``stub_prepare_chat_runtime``): those stay
defined locally in each file that needs them (as thin wrappers around the
installers in ``tests.unit.api.fakes``) so the monkeypatching doesn't leak
to the other, unrelated test modules under ``tests/unit/api/``.
"""

from __future__ import annotations

import pytest

from fleet_rlm.api.auth.types import NormalizedIdentity


@pytest.fixture
def stub_identity() -> NormalizedIdentity:
    """Return a fixed NormalizedIdentity for test use."""
    return NormalizedIdentity(
        tenant_claim="tenant-1",
        user_claim="user-1",
        email="test@example.com",
        name="Test User",
    )
