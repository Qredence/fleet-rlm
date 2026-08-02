"""Unit contracts for host-owned strict curated evaluation input."""

from __future__ import annotations

import json

import pytest

from fleet_rlm.optimization.curated_input import CuratedEvaluationStore, CuratedInputError
from fleet_rlm.optimization.types import OptimizationRecord


def _record() -> OptimizationRecord:
    return OptimizationRecord(
        record_id="record-1",
        query="summarize the synthetic report",
        output_contract={"answer": "string"},
        expectations={"must_include": ["A"]},
        execution_requirements={"no_network": True},
        provenance={"redaction_version": "v1"},
        content_sha256="a" * 64,
    )


def test_store_uses_stable_canonical_digest_and_handle_metadata() -> None:
    first = CuratedEvaluationStore(candidate="candidate", record=_record())
    second = CuratedEvaluationStore(candidate="candidate", record=_record())

    assert first.receipt.sha256 == second.receipt.sha256
    assert first.handle.sha256 == first.receipt.sha256
    assert first.handle.schema == "fleet.curated-evaluation-input/v1"
    assert first.handle.byte_size > 0
    assert first.handle.transaction_id != second.handle.transaction_id


def test_read_returns_detached_bounded_projection() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_record())
    handle = store.handle

    response = store.read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/record/expectations",
    )
    value = json.loads(response["json"])
    value["must_include"].append("forged")

    reread = store.read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/record/expectations",
    )
    assert json.loads(reread["json"]) == {"must_include": ["A"]}
    assert response["complete"] is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"transaction_id": "wrong", "sha256": "a" * 64}, "unknown"),
        ({"transaction_id": "", "sha256": "wrong"}, "unknown"),
        ({"json_pointer": "/record/provenance"}, "not permitted"),
        ({"start": -1}, "negative"),
        ({"limit": 0}, "maximum"),
        ({"limit": 8_001}, "maximum"),
    ],
)
def test_read_rejects_invalid_capability_or_bounds(kwargs: dict[str, object], message: str) -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_record())
    handle = store.handle
    values: dict[str, object] = {
        "transaction_id": handle.transaction_id,
        "sha256": handle.sha256,
    }
    values.update(kwargs)

    with pytest.raises(CuratedInputError, match=message):
        store.read(**values)  # type: ignore[arg-type]


def test_broker_tool_still_reads_host_canonical_input_after_handle_rebinding() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_record())
    handle = store.handle
    read = store.broker_tool(handle=handle)

    fake_repl_handle = {
        "transaction_id": "attacker-controlled",
        "sha256": "0" * 64,
    }
    del fake_repl_handle

    response = read(
        transaction_id=handle.transaction_id,
        sha256=handle.sha256,
        json_pointer="/candidate",
    )
    assert json.loads(response["json"]) == "candidate"


def test_store_is_single_use_for_host_lifecycle_accounting() -> None:
    store = CuratedEvaluationStore(candidate="candidate", record=_record())

    receipt = store.consume()
    assert receipt.sha256 == store.handle.sha256
    with pytest.raises(CuratedInputError, match="already consumed"):
        store.consume()
