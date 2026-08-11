"""Private Daytona result snapshot encoding and path contract."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest


def test_result_snapshot_is_deterministic_strict_utf8_and_closed() -> None:
    from fleet_rlm.result_snapshot import encode_result_snapshot
    from fleet_rlm.rlm.dspy_contract import PredictionResult

    session_id, run_id = uuid4(), uuid4()
    prediction = PredictionResult(
        "display must not be duplicated",
        {"zeta": ["café", 2], "answer": "done"},
        "fleet.report",
        "7",
    )
    usage = {
        "iterations": 3,
        "observed_lm_usage": {"provider": {"completion_tokens": 5}},
        "duration_ms": 11,
    }

    first = encode_result_snapshot(session_id, run_id, prediction, usage)
    second = encode_result_snapshot(session_id, run_id, prediction, usage)

    assert first == second
    assert first.decode("utf-8") == (
        '{"contract_id":"fleet.report","contract_version":"7",'
        '"outputs":{"answer":"done","zeta":["café",2]},'
        f'"run_id":"{run_id}","schema_version":1,"session_id":"{session_id}",'
        '"usage":{"duration_ms":11,"iterations":3,'
        '"observed_lm_usage":{"provider":{"completion_tokens":5}}}}'
    )
    decoded = json.loads(first)
    assert set(decoded) == {
        "schema_version",
        "session_id",
        "run_id",
        "contract_id",
        "contract_version",
        "outputs",
        "usage",
    }
    encoded = first.decode("utf-8")
    for excluded in (
        "display_text",
        "display must not be duplicated",
        "trajectory",
        "final_reasoning",
        "prompt",
        "history",
        "locals",
        "credential",
        "provider_internal",
    ):
        assert excluded not in encoded


def test_result_snapshot_rejects_non_strict_usage() -> None:
    from fleet_rlm.result_snapshot import encode_result_snapshot
    from fleet_rlm.rlm.dspy_contract import PredictionResult

    with pytest.raises(ValueError, match="usage must contain exactly"):
        encode_result_snapshot(
            uuid4(),
            uuid4(),
            PredictionResult("done", {"answer": "done"}, "fleet.default", "1"),
            {
                "iterations": 1,
                "observed_lm_usage": {},
                "duration_ms": 2,
                "provider_internal": 9,
            },
        )


def test_volume_result_path_is_unique_and_path_safe() -> None:
    from fleet_rlm.files.volume_paths import UnsafePathError, VolumePaths

    paths = VolumePaths.from_mount()
    session_id, first_run, second_run = uuid4(), uuid4(), uuid4()

    assert paths.run_result_path(session_id, first_run) == (paths.run_dir(session_id, first_run) / "result.json")
    assert paths.run_result_path(session_id, first_run) != paths.run_result_path(session_id, second_run)
    with pytest.raises(UnsafePathError):
        paths.run_result_path(str(session_id), "../escape")


def test_daytona_volume_adapter_removes_exact_file_path() -> None:
    from fleet_rlm.daytona.workspace_fs import DaytonaSandboxVolumeFs

    calls: list[tuple[str, str]] = []

    class Fs:
        def delete_file(self, path: str) -> None:
            calls.append(("delete_file", path))

    adapter = DaytonaSandboxVolumeFs(type("Sandbox", (), {"fs": Fs()})())
    adapter.remove("/home/daytona/fleet/sessions/s/runs/r/result.json")

    assert calls == [("delete_file", "/home/daytona/fleet/sessions/s/runs/r/result.json")]


@pytest.mark.asyncio
async def test_live_daytona_sink_commit_failure_deletes_snapshot_through_adapter() -> None:
    from fleet_rlm.chat.run_lifecycle import (
        ClaimedRun,
        FailedRunReceipt,
        RunLifecycleService,
        _RunClaimToken,
    )
    from fleet_rlm.daytona.run_environment import _DaytonaRunSink
    from fleet_rlm.files.volume_paths import VolumePaths
    from fleet_rlm.rlm.dspy_contract import PredictionResult
    from fleet_rlm.rlm.outcome import RLMOutcome
    from fleet_rlm.sessions.models import SessionHistory, TurnAccess, TurnInput

    values: dict[str, bytes] = {}
    deleted: list[str] = []

    class Fs:
        async def create_folder(self, path: str, mode: str | None = None) -> None:
            del path, mode
            return None

        async def upload_file(self, data: bytes, path: str) -> None:
            values[path] = data

        async def download_file(self, path: str) -> bytes:
            return values[path]

        async def delete_file(self, path: str) -> None:
            deleted.append(path)
            values.pop(path, None)

    paths = VolumePaths.from_mount()
    sink = _DaytonaRunSink(
        type("Sandbox", (), {"fs": Fs()})(),
        paths=paths,
    )
    access, session_id, run_id = TurnAccess(uuid4(), uuid4()), uuid4(), uuid4()

    async def not_cancelled() -> bool:
        return False

    turn = ClaimedRun(
        run_id,
        session_id,
        access,
        TurnInput("hello"),
        SessionHistory(),
        not_cancelled,
        _RunClaimToken(uuid4()),
    )

    class Store:
        async def commit(self, claimed, committed, artifacts):
            del claimed, committed, artifacts
            raise RuntimeError("commit failed")

        async def transition_claim(self, claimed, command):
            from fleet_rlm.chat.run_claim import FailClaim
            from fleet_rlm.chat.run_lifecycle import RunFailure
            from fleet_rlm.rlm.dspy_contract import empty_rlm_usage

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
        result_snapshot_sink=sink,
    )

    result_path = str(paths.run_result_path(session_id, run_id))
    assert isinstance(receipt, FailedRunReceipt)
    assert deleted == [result_path]
    assert values == {}
