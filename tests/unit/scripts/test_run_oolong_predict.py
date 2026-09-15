"""Offline contracts for the Oolong predict adapter."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.provisioning import EphemeralInterpreterLease
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, VolumePaths
from fleet_rlm.rlm.program import AttachmentContextCapsule, FleetJSONAdapter
from scripts.benchmarks import run_oolong_predict as runner
from scripts.benchmarks.oolong.adapter import (
    OolongAdapterError,
    build_predict_kwargs,
    build_receipt,
    invoke_live_prediction,
    kwargs_context_mode,
    load_fixture,
    receipt_safe_score,
    release_ephemeral_lease,
    resolve_datapoints,
    score_prediction,
    stage_attachment_context_on_lease,
    stage_context_capsule,
)
from scripts.benchmarks.oolong.scoring import synth_process_response


def _production_kwargs(datapoint: dict[str, object], capsule: AttachmentContextCapsule) -> dict[str, object]:
    return build_predict_kwargs(
        datapoint,
        mode="production",
        attachment_context=capsule,
    )


def test_official_synth_scoring_matches_label_answer() -> None:
    datapoint = load_fixture()
    score = synth_process_response(datapoint, "Label: spam", "fleet-test")
    assert score["score"] == 1
    assert score["attempted_parse"] == "spam"


def test_production_kwargs_require_attachment_context() -> None:
    datapoint = load_fixture()
    with pytest.raises(OolongAdapterError, match="lease-staged attachment_context"):
        build_predict_kwargs(datapoint, mode="production")


def test_production_kwargs_use_attachment_capsule(tmp_path: Path) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule("hello", staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    assert isinstance(kwargs.get("attachment_context"), AttachmentContextCapsule)
    assert kwargs_context_mode(kwargs) == "attachment_context_capsule"
    assert "context_window_text" not in kwargs["request"]
    assert len(str(kwargs["request"])) < len(datapoint["context_window_text"])


@pytest.mark.asyncio
async def test_invoke_live_prediction_binds_attachment_context(tmp_path: Path) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule(datapoint["context_window_text"], staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    interpreter = MagicMock()
    settings = MagicMock()
    root_lm = MagicMock()
    sub_lm = MagicMock()
    worker_result = MagicMock(answer="Label: spam")
    deadline = 1_000_000.0

    async def fake_settle() -> MagicMock:
        settled = MagicMock(caller_cancelled=False)
        settled.result.return_value = worker_result
        return settled

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        patcher.setattr(
            "fleet_rlm.runtime.owned_effect.OwnedEffect.start",
            lambda _awaitable: MagicMock(settle=fake_settle),
        )
        answer = await invoke_live_prediction(
            settings,
            kwargs,
            interpreter=interpreter,
            deadline=deadline,
            wrap_up_seconds=30.0,
            root_lm=root_lm,
            sub_lm=sub_lm,
        )

    interpreter.bind_context_capsule.assert_called_once_with(capsule)
    assert answer == "Label: spam"


@pytest.mark.asyncio
async def test_invoke_live_prediction_uses_fleet_json_adapter(tmp_path: Path) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule("hello", staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    interpreter = MagicMock()
    settings = MagicMock()
    root_lm = MagicMock()
    sub_lm = MagicMock()
    created: list[FleetJSONAdapter] = []
    original_adapter = FleetJSONAdapter
    deadline = 1_000_000.0

    def capture_adapter(*args: object, **kwargs: object) -> FleetJSONAdapter:
        adapter = original_adapter(*args, **kwargs)
        created.append(adapter)
        return adapter

    async def fake_settle() -> MagicMock:
        settled = MagicMock(caller_cancelled=False)
        settled.result.return_value = MagicMock(answer="Label: spam")
        return settled

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        patcher.setattr("fleet_rlm.rlm.program.FleetJSONAdapter", capture_adapter)
        patcher.setattr(
            "fleet_rlm.runtime.owned_effect.OwnedEffect.start",
            lambda _awaitable: MagicMock(settle=fake_settle),
        )
        await invoke_live_prediction(
            settings,
            kwargs,
            interpreter=interpreter,
            deadline=deadline,
            wrap_up_seconds=30.0,
            root_lm=root_lm,
            sub_lm=sub_lm,
        )

    adapter = created[0]
    assert isinstance(adapter, FleetJSONAdapter)
    assert type(adapter) is not dspy.JSONAdapter
    assert adapter._budget.deadline == deadline
    assert adapter._budget.reserve_seconds == 30.0


@pytest.mark.asyncio
async def test_invoke_live_prediction_skips_bundle_when_models_injected(tmp_path: Path) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule("hello", staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    calls: list[object] = []

    async def fake_settle() -> MagicMock:
        settled = MagicMock(caller_cancelled=False)
        settled.result.return_value = MagicMock(answer="Label: spam")
        return settled

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: calls.append("bundle") or MagicMock(),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        patcher.setattr(
            "fleet_rlm.runtime.owned_effect.OwnedEffect.start",
            lambda _awaitable: MagicMock(settle=fake_settle),
        )
        await invoke_live_prediction(
            MagicMock(),
            kwargs,
            interpreter=MagicMock(),
            deadline=1_000_000.0,
            wrap_up_seconds=30.0,
            root_lm=MagicMock(),
            sub_lm=MagicMock(),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_stage_attachment_context_on_lease_uses_volume_mount_paths() -> None:
    session_id = uuid4()
    run_id = uuid4()
    volume_paths = VolumePaths.from_mount(DEFAULT_VOLUME_MOUNT_PATH)
    lease = EphemeralInterpreterLease(
        interpreter=MagicMock(),
        sandbox=MagicMock(),
        platform=MagicMock(),
        session_id=session_id,
        run_id=run_id,
        workspace_id=uuid4(),
        context_mount_path=str(volume_paths.mount_path),
        volume_paths=volume_paths,
    )
    storage = MagicMock()
    storage.write_bytes = AsyncMock()

    with patch(
        "fleet_rlm.workspace.storage.AgentAsyncVolumeStorage",
        return_value=storage,
    ) as storage_ctor:
        capsule = await stage_attachment_context_on_lease(lease, "hello context")

    storage_ctor.assert_called_once_with(lease.sandbox, mount_path=str(volume_paths.mount_path))
    storage.write_bytes.assert_awaited_once()
    written_path = storage.write_bytes.await_args.args[0]
    assert written_path.startswith(str(volume_paths.mount_path))
    assert f"/sessions/{session_id}/runs/{run_id}/attachments/" in written_path
    assert capsule.mount_root == str(volume_paths.mount_path)
    assert capsule.entries[0].sandbox_path == written_path


@pytest.mark.asyncio
async def test_release_ephemeral_lease_propagates_cleanup_failure() -> None:
    lease = EphemeralInterpreterLease(
        interpreter=MagicMock(),
        sandbox=MagicMock(),
        platform=MagicMock(),
        session_id=uuid4(),
        run_id=uuid4(),
        workspace_id=uuid4(),
        context_mount_path=str(DEFAULT_VOLUME_MOUNT_PATH),
        volume_paths=VolumePaths.from_mount(DEFAULT_VOLUME_MOUNT_PATH),
    )
    lease.interpreter.shutdown = MagicMock()
    lease.platform.delete = AsyncMock(side_effect=RuntimeError("delete failed"))

    with pytest.raises(OolongAdapterError, match="cleanup failed"):
        await release_ephemeral_lease(lease, staged_paths=())


def test_fixture_rejects_real_dataset() -> None:
    with pytest.raises(OolongAdapterError, match="dataset=real"):
        resolve_datapoints(
            dataset="real",
            split="test",
            start_index=0,
            limit=1,
            fixture=runner.DEFAULT_FIXTURE,
        )


def test_fixture_rejects_multi_row_ranges() -> None:
    with pytest.raises(OolongAdapterError, match="index 0 --limit 1"):
        resolve_datapoints(
            dataset="synth",
            split="validation",
            start_index=1,
            limit=1,
            fixture=runner.DEFAULT_FIXTURE,
        )


def test_receipt_safe_score_drops_full_answer() -> None:
    raw = score_prediction(load_fixture(), "Label: spam", dataset="synth", model_name="fleet-test")
    assert "full_answer" in raw
    safe = receipt_safe_score(raw)
    assert "full_answer" not in safe
    assert safe["score"] == 1


def test_build_receipt_projects_scores() -> None:
    raw = score_prediction(load_fixture(), "Label: spam", dataset="synth", model_name="fleet-test")
    receipt = build_receipt(
        mode="dry",
        dataset="synth",
        split="validation",
        limit=1,
        rows=(),
        scores=[raw],
        context_mode="dry_request_concat",
        model_name="fleet-test",
        dataset_revision="main",
        source="fixture",
    )
    assert "full_answer" not in receipt["scores"][0]


def test_dry_shortcut_concatenates_context_into_request() -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(datapoint, mode="dry_shortcut")
    assert kwargs_context_mode(kwargs) == "dry_request_concat"
    assert datapoint["context_window_text"] in str(kwargs["request"])
    assert datapoint["question"] in str(kwargs["request"])


def test_score_prediction_delegates_to_official_helper() -> None:
    datapoint = load_fixture()
    payload = score_prediction(datapoint, "Label: spam", dataset="synth", model_name="fleet-test")
    assert payload["score"] == 1


def test_dry_cli_writes_fixture_receipt(tmp_path: Path) -> None:
    output = tmp_path / "oolong-dry.json"
    assert runner.main(["--output", str(output), "--limit", "1"]) == 0
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["schema"] == runner.RECEIPT_SCHEMA
    assert receipt["mode"] == "dry"
    assert receipt["context_mode"] == "dry_request_concat"
    assert receipt["summary"]["count"] == 1
    assert receipt["summary"]["mean"] == 1.0
    assert receipt["rows"][0]["mocked"]["daytona_interpreter"] is True
    assert receipt["rows"][0]["mocked"]["provider_llm"] is True
    assert "full_answer" not in receipt["scores"][0]


def test_dry_cli_rejects_invalid_limit(tmp_path: Path) -> None:
    output = tmp_path / "oolong-invalid.json"
    assert runner.main(["--output", str(output), "--limit", "0"]) == 2
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"


def test_dry_default_answer_is_per_row() -> None:
    first = {"answer": "['spam']", "answer_type": "ANSWER_TYPE.LABEL"}
    second = {"answer": "['ham']", "answer_type": "ANSWER_TYPE.LABEL"}
    assert runner._default_dry_answer(first) == "Label: spam"
    assert runner._default_dry_answer(second) == "Label: ham"


def test_mlflow_logging_is_fail_soft(capsys: pytest.CaptureFixture[str]) -> None:
    args = runner.build_parser().parse_args(
        ["--output", "out.json", "--mlflow-url", "http://example", "--mlflow-experiment", "x"]
    )
    receipt = {
        "dataset": "synth",
        "mode": "dry",
        "context_mode": "dry_request_concat",
        "summary": {"mean": 1.0},
    }
    with patch("mlflow.set_tracking_uri", side_effect=RuntimeError("boom")):
        runner._maybe_log_mlflow(args, receipt)
    captured = capsys.readouterr()
    assert "mlflow logging skipped" in captured.err


def test_real_dataset_requires_test_split() -> None:
    with pytest.raises(OolongAdapterError, match="split"):
        resolve_datapoints(
            dataset="real",
            split="validation",
            start_index=0,
            limit=1,
            fixture=None,
        )
