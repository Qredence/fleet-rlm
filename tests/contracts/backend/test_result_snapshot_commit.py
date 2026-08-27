"""Integrated commit-gated result snapshot contract."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest


def _turn():
    from fleet_rlm.chat.run_lifecycle import ClaimedRun, _RunClaimToken
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    access = TurnAccess(uuid4(), uuid4())

    async def not_cancelled() -> bool:
        return False

    return ClaimedRun(
        uuid4(),
        uuid4(),
        access,
        TurnInput("summarize"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )


class _SnapshotSink:
    def __init__(self, turn) -> None:
        self.session_id = turn.session_id
        self.run_id = turn.run_id
        self.path = f"/sessions/{turn.session_id}/runs/{turn.run_id}/result.json"
        self.values: dict[str, bytes] = {}
        self.operations: list[str] = []

    def result_path(self, session_id, run_id):
        assert (session_id, run_id) == (self.session_id, self.run_id)
        return self.path

    async def write(self, location, data):
        self.operations.append("write")
        self.values[location] = data

    async def remove(self, location):
        self.operations.append("remove")
        self.values.pop(location, None)


@pytest.mark.asyncio
async def test_successful_turn_retains_one_closed_deterministic_snapshot() -> None:
    from fleet_rlm.chat.run_lifecycle import CommittedTurnReceipt, RunLifecycleService
    from fleet_rlm.result_snapshot import encode_result_snapshot
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    turn = _turn()
    snapshot = _SnapshotSink(turn)
    prediction = PredictionResult(
        "Three findings",
        {"summary": "Three findings", "findings": [{"title": "First"}]},
        "fleet.report",
        "1",
    )
    usage = {"iterations": 2, "observed_lm_usage": {}, "duration_ms": 4}

    class Store:
        async def commit(self, claimed, committed, artifacts):
            assert claimed is turn
            assert snapshot.values.keys() == {snapshot.path}
            snapshot.operations.append("commit")
            return CommittedTurnReceipt(turn.run_id, 1, committed, artifacts)

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.result import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            raise AssertionError((claimed, failure))

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=1024).finish(
        turn,
        RLMOutcome(
            "completed",
            prediction,
            usage=usage,
        ),
        result_snapshot_sink=snapshot,
    )

    assert isinstance(receipt, CommittedTurnReceipt)
    assert snapshot.operations == ["write", "commit"]
    encoded = snapshot.values[snapshot.path]
    assert encoded == encode_result_snapshot(turn.session_id, turn.run_id, prediction, usage)
    decoded = json.loads(encoded)
    assert set(decoded) == {
        "schema_version",
        "session_id",
        "run_id",
        "contract_id",
        "contract_version",
        "outputs",
        "usage",
    }
    assert decoded["outputs"] == {
        "summary": "Three findings",
        "findings": [{"title": "First"}],
    }
    assert decoded["session_id"] == str(turn.session_id)
    assert decoded["run_id"] == str(turn.run_id)
    assert decoded["schema_version"] == 1
    assert decoded["contract_id"] == "fleet.report"
    assert decoded["contract_version"] == "1"
    assert decoded["usage"] == usage
    for forbidden in ("trajectory", "prompt", "history", "locals", "credential"):
        assert forbidden not in encoded.decode("utf-8")


@pytest.mark.asyncio
async def test_commit_failure_removes_snapshot_before_failure_is_durable() -> None:
    from fleet_rlm.chat.run_lifecycle import FailedRunReceipt, RunLifecycleService
    from fleet_rlm.rlm.result import PredictionResult, RLMOutcome

    turn = _turn()
    snapshot = _SnapshotSink(turn)

    class Store:
        async def commit(self, claimed, committed, artifacts):
            del claimed, committed, artifacts
            raise RuntimeError("database unavailable")

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.result import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                "failed",
                failure.failure_code,
                failure.public_message,
                True,
            )

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=1024).finish(
        turn,
        RLMOutcome(
            "completed",
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
        ),
        result_snapshot_sink=snapshot,
    )

    assert isinstance(receipt, FailedRunReceipt)
    assert snapshot.values == {}
    assert snapshot.operations == ["write", "remove"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "timeout"])
async def test_non_successful_turn_never_requests_a_snapshot(status: str) -> None:
    from fleet_rlm.chat.run_lifecycle import FailedRunReceipt, RunLifecycleService
    from fleet_rlm.rlm.result import RLMOutcome

    turn = _turn()

    class Store:
        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.result import empty_rlm_usage

            assert isinstance(command, FailClaim)
            failure = RunFailure(
                command.failure.status,
                command.failure.code,
                command.failure.public_message,
                command.usage or empty_rlm_usage(),
            )
            return FailedRunReceipt(
                claimed.run_id,
                failure.terminal_status,
                failure.failure_code,
                failure.public_message,
                True,
            )

    class NeverSnapshot:
        def __getattr__(self, name):
            raise AssertionError(name)

    receipt = await RunLifecycleService(Store(), max_artifact_bytes=1024).finish(
        turn,
        RLMOutcome(status, public_error_message="Turn failed"),
        result_snapshot_sink=NeverSnapshot(),
    )

    assert isinstance(receipt, FailedRunReceipt)
