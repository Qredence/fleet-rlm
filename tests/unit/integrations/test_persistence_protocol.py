from __future__ import annotations

import uuid

import pytest


def test_unsupported_local_capability_error_preserves_capability_name() -> None:
    from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

    error = UnsupportedLocalCapabilityError("store_trace_feedback")

    assert error.capability == "store_trace_feedback"
    assert "store_trace_feedback" in str(error)
    assert "local SQLite persistence mode" in str(error)


def test_local_store_satisfies_runtime_checkable_protocol() -> None:
    from fleet_rlm.integrations.local_store import LocalStore
    from fleet_rlm.integrations.persistence_protocol import PersistenceProtocol

    assert isinstance(LocalStore(), PersistenceProtocol)


@pytest.mark.asyncio
async def test_local_store_raises_explicit_errors_for_unsupported_trace_operations() -> None:
    from fleet_rlm.integrations.local_store import LocalStore
    from fleet_rlm.integrations.persistence_protocol import UnsupportedLocalCapabilityError

    store = LocalStore()
    tenant_id = uuid.uuid4()

    with pytest.raises(UnsupportedLocalCapabilityError, match="store_trace_feedback") as feedback_error:
        await store.store_trace_feedback(
            tenant_id=tenant_id,
            trace_id="trace-1",
            is_correct=True,
        )

    with pytest.raises(UnsupportedLocalCapabilityError, match="store_rlm_trace") as trace_error:
        await store.store_rlm_trace(
            tenant_id=tenant_id,
            run_id=uuid.uuid4(),
            trace_id="trace-2",
        )

    assert feedback_error.value.capability == "store_trace_feedback"
    assert trace_error.value.capability == "store_rlm_trace"
