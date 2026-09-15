"""Offline contracts for the Oolong predict adapter."""

from __future__ import annotations

import contextlib
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
    invoke_live_prediction,
    kwargs_context_mode,
    load_fixture,
    resolve_datapoints,
    score_prediction,
    stage_attachment_context_on_lease,
)
from scripts.benchmarks.oolong.scoring import synth_process_response


def test_official_synth_scoring_matches_label_answer() -> None:
    datapoint = load_fixture()
    score = synth_process_response(datapoint, "Label: spam", "fleet-test")
    assert score["score"] == 1
    assert score["attempted_parse"] == "spam"


def test_production_kwargs_use_attachment_capsule(tmp_path: Path) -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(
        datapoint,
        mode="production",
        staging_root=tmp_path / "staging",
    )
    assert isinstance(kwargs.get("attachment_context"), AttachmentContextCapsule)
    assert kwargs_context_mode(kwargs) == "attachment_context_capsule"
    assert "context_window_text" not in kwargs["request"]
    assert len(str(kwargs["request"])) < len(datapoint["context_window_text"])


@pytest.mark.asyncio
async def test_invoke_live_prediction_binds_attachment_context(tmp_path: Path) -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(
        datapoint,
        mode="production",
        staging_root=tmp_path / "staging",
    )
    capsule = kwargs["attachment_context"]
    interpreter = MagicMock()
    settings = MagicMock()
    root_lm = MagicMock()
    sub_lm = MagicMock()
    rlm = MagicMock()
    rlm.acall = AsyncMock(return_value=MagicMock(answer="Label: spam"))

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: rlm,
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        answer = await invoke_live_prediction(
            settings,
            kwargs,
            interpreter=interpreter,
            root_lm=root_lm,
            sub_lm=sub_lm,
        )

    interpreter.bind_context_capsule.assert_called_once_with(capsule)
    rlm.acall.assert_awaited_once()
    assert rlm.acall.await_args.args[0] is interpreter
    assert "attachment_context" not in rlm.acall.await_args.kwargs
    assert answer == "Label: spam"


@pytest.mark.asyncio
async def test_invoke_live_prediction_uses_fleet_json_adapter(tmp_path: Path) -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(
        datapoint,
        mode="production",
        staging_root=tmp_path / "staging",
    )
    interpreter = MagicMock()
    settings = MagicMock(turn_timeout_seconds=600, rlm_wrap_up_seconds=30)
    root_lm = MagicMock()
    sub_lm = MagicMock()
    rlm = MagicMock()
    rlm.acall = AsyncMock(return_value=MagicMock(answer="Label: spam"))
    captured: dict[str, object] = {}

    @contextlib.contextmanager
    def capture_context(**context_kwargs: object):
        captured.update(context_kwargs)
        yield

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: rlm,
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        patcher.setattr("scripts.benchmarks.oolong.adapter.dspy.context", capture_context)
        await invoke_live_prediction(
            settings,
            kwargs,
            interpreter=interpreter,
            root_lm=root_lm,
            sub_lm=sub_lm,
        )

    adapter = captured.get("adapter")
    assert isinstance(adapter, FleetJSONAdapter)
    assert type(adapter) is not dspy.JSONAdapter
    assert adapter._budget.reserve_seconds == 30.0


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


def test_dry_cli_rejects_invalid_limit(tmp_path: Path) -> None:
    output = tmp_path / "oolong-invalid.json"
    assert runner.main(["--output", str(output), "--limit", "0"]) == 2
    receipt = json.loads(output.read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"


def test_real_dataset_requires_test_split() -> None:
    with pytest.raises(OolongAdapterError, match="split"):
        resolve_datapoints(
            dataset="real",
            split="validation",
            start_index=0,
            limit=1,
            fixture=runner.DEFAULT_FIXTURE,
        )
