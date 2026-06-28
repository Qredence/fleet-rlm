"""LRU eviction tests for the bounded evaluation store (VAL-SEC-012, VAL-SEC-013).

These tests verify:
- VAL-SEC-012: ``_EVALUATION_STORE`` is bounded to 100 entries; the 101st
  insert evicts the oldest (least recently used) entry, keeping the size at
  exactly 100.
- VAL-SEC-013: accessing an entry moves it to the most-recently-used
  position, so a recently-accessed entry survives a subsequent eviction
  while the (now) least-recently-used entry is evicted instead.
"""

from __future__ import annotations

from collections import OrderedDict

import pytest

from fleet_rlm.api.runtime_services import evaluations as evaluation_service
from fleet_rlm.api.runtime_services.evaluations import (
    _EVALUATION_STORE,
    _EVALUATION_STORE_MAX_ENTRIES,
    _RunState,
    _store_insert,
    _store_touch,
)


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    """Ensure each test starts with an empty store."""
    _EVALUATION_STORE.clear()
    yield
    _EVALUATION_STORE.clear()


def _make_state(run_id: str) -> _RunState:
    return _RunState(run_id=run_id, status="pending")


def test_store_is_ordered_dict() -> None:
    """VAL-SEC-012: the store is an OrderedDict (LRU structure)."""
    assert isinstance(_EVALUATION_STORE, OrderedDict)


def test_store_max_entries_is_100() -> None:
    """VAL-SEC-012: the configured bound is 100."""
    assert _EVALUATION_STORE_MAX_ENTRIES == 100


def test_101st_insert_evicts_oldest() -> None:
    """VAL-SEC-012: inserting the 101st entry evicts the oldest (first inserted)."""
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(100)]
    for rid in ids:
        _store_insert(rid, _make_state(rid))

    assert len(_EVALUATION_STORE) == 100
    assert ids[0] in _EVALUATION_STORE

    # Insert the 101st entry.
    new_id = "11111111-0000-0000-0000-000000000001"
    _store_insert(new_id, _make_state(new_id))

    # Store size stays at exactly 100.
    assert len(_EVALUATION_STORE) == 100, len(_EVALUATION_STORE)
    # The oldest (first-inserted) entry is evicted.
    assert ids[0] not in _EVALUATION_STORE, f"{ids[0]} should have been evicted"
    # The newest entry is present.
    assert new_id in _EVALUATION_STORE


def test_store_never_exceeds_100_entries() -> None:
    """VAL-SEC-012: inserting many entries keeps the store at <= 100."""
    for i in range(250):
        rid = f"00000000-0000-0000-0000-{i:012d}"
        _store_insert(rid, _make_state(rid))

    assert len(_EVALUATION_STORE) == 100
    # The most recently inserted is present.
    last_rid = f"00000000-0000-0000-0000-{249:012d}"
    assert last_rid in _EVALUATION_STORE


def test_access_moves_entry_to_most_recently_used() -> None:
    """VAL-SEC-013: accessing an entry moves it to the MRU position.

    After filling the store to 100, accessing entry[0] (the oldest), then
    inserting a 101st entry, the evicted entry should be entry[1] (now LRU),
    not entry[0] (which was just touched).
    """
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(100)]
    for rid in ids:
        _store_insert(rid, _make_state(rid))

    # Access entry[0] (the oldest) -> moves it to MRU.
    state = _store_touch(ids[0])
    assert state is not None
    assert state.run_id == ids[0]

    # The order should now have ids[0] last.
    keys = list(_EVALUATION_STORE.keys())
    assert keys[-1] == ids[0]

    # Insert the 101st entry -> evicts the LRU, which is now ids[1].
    new_id = "11111111-0000-0000-0000-000000000002"
    _store_insert(new_id, _make_state(new_id))

    assert len(_EVALUATION_STORE) == 100
    # ids[0] (recently accessed) survives.
    assert ids[0] in _EVALUATION_STORE, "recently accessed entry should survive eviction"
    # ids[1] (now LRU) is evicted.
    assert ids[1] not in _EVALUATION_STORE, "LRU entry should have been evicted"
    # new entry is present.
    assert new_id in _EVALUATION_STORE


def test_get_evaluation_report_touches_entry_lru_order(monkeypatch) -> None:
    """VAL-SEC-013: get_evaluation_report moves the entry to MRU on access.

    This exercises the full service path: a completed run in the store is
    fetched via get_evaluation_report, then a 101st insert evicts a different
    (LRU) entry, not the just-fetched one.
    """
    import asyncio

    from fleet_rlm.quality.eval.report import EvaluationReport

    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(100)]
    for rid in ids:
        report = EvaluationReport(
            run_id=rid,
            created_at="2026-01-01T00:00:00+00:00",
            filters={},
            per_trace=[],
            aggregates={"mean": {}, "median": {}},
        )
        _store_insert(rid, _RunState(run_id=rid, status="completed", report=report))

    # Fetch entry[0] via the service. This should move it to MRU.
    asyncio.run(evaluation_service.get_evaluation_report(ids[0]))

    keys = list(_EVALUATION_STORE.keys())
    assert keys[-1] == ids[0], "get_evaluation_report did not move entry to MRU"

    # Insert a 101st entry -> evicts the LRU (ids[1]), not ids[0].
    new_id = "11111111-0000-0000-0000-000000000003"
    _store_insert(new_id, _make_state(new_id))

    assert ids[0] in _EVALUATION_STORE
    assert ids[1] not in _EVALUATION_STORE
    assert new_id in _EVALUATION_STORE


def test_touch_unknown_run_returns_none() -> None:
    """Edge case: touching an unknown run_id returns None without raising."""
    _store_insert("00000000-0000-0000-0000-000000000000", _make_state("00000000-0000-0000-0000-000000000000"))
    result = _store_touch("11111111-2222-3333-4444-555555555555")
    assert result is None


def test_reinsert_updates_existing_entry() -> None:
    """Edge case: re-inserting an existing run_id updates it and moves to MRU."""
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(100)]
    for rid in ids:
        _store_insert(rid, _make_state(rid))

    # Re-insert ids[0] with a new status.
    updated = _RunState(run_id=ids[0], status="completed")
    _store_insert(ids[0], updated)

    assert len(_EVALUATION_STORE) == 100
    keys = list(_EVALUATION_STORE.keys())
    assert keys[-1] == ids[0], "re-insert should move entry to MRU"
    assert _EVALUATION_STORE[ids[0]].status == "completed"
