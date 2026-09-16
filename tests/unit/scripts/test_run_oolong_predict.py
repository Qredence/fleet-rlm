"""Offline contracts for the Oolong predict adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import dspy
import pytest

from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.errors import DaytonaAdapterError
from fleet_rlm.daytona.provisioning import EphemeralInterpreterLease
from fleet_rlm.paths import DEFAULT_VOLUME_MOUNT_PATH, VolumePaths
from fleet_rlm.rlm.program import AttachmentContextCapsule, FleetJSONAdapter
from fleet_rlm.sessions.history_transport import CommittedSessionHistory
from scripts.benchmarks import run_oolong_predict as runner
from scripts.benchmarks.oolong.adapter import (
    DEFAULT_HF_DATASET_REVISIONS,
    LoadedDatapoint,
    OolongAdapterError,
    build_predict_kwargs,
    build_receipt,
    invoke_live_prediction,
    kwargs_context_mode,
    load_fixture,
    load_hf_row,
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


def test_production_kwargs_use_committed_session_history(tmp_path: Path) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule("hello", staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    history = kwargs["history"]
    assert type(history) is CommittedSessionHistory
    assert isinstance(history, dspy.SandboxSerializable)
    assert history.to_sandbox() == b"[]"
    with pytest.raises(DaytonaAdapterError, match="unsupported"):
        DaytonaHttpToolBroker._encode_value(dspy.History(messages=[]))


def test_dry_shortcut_uses_dspy_history() -> None:
    datapoint = load_fixture()
    kwargs = build_predict_kwargs(datapoint, mode="dry_shortcut")
    assert type(kwargs["history"]).__name__ == "History"


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

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr("scripts.benchmarks.oolong.adapter._run_prediction_on_worker", lambda *_args: worker_result)
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
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

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: MagicMock(root_lm=root_lm, sub_lm=sub_lm),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter._run_prediction_on_worker",
            lambda *_args: MagicMock(answer="Label: spam"),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        patcher.setattr("fleet_rlm.rlm.program.FleetJSONAdapter", capture_adapter)
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

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: calls.append("bundle") or MagicMock(),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda *_args, **_kwargs: MagicMock(),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter._run_prediction_on_worker",
            lambda *_args: MagicMock(answer="Label: spam"),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
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
@pytest.mark.parametrize("provided_role", ("root", "sub"))
async def test_invoke_live_prediction_preserves_partially_injected_model(
    tmp_path: Path,
    provided_role: str,
) -> None:
    datapoint = load_fixture()
    capsule = stage_context_capsule("hello", staging_root=tmp_path / "staging")
    kwargs = _production_kwargs(datapoint, capsule)
    provided = MagicMock(name=f"provided_{provided_role}")
    fallback_root = MagicMock(name="fallback_root")
    fallback_sub = MagicMock(name="fallback_sub")
    bundles: list[tuple[object, object]] = []

    class CapturingBundle:
        def __init__(self, *, root_lm: object, sub_lm: object) -> None:
            self.root_lm = root_lm
            self.sub_lm = sub_lm
            bundles.append((root_lm, sub_lm))

        def bind_turn_deadline(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(root_lm=self.root_lm, sub_lm=self.sub_lm)

    root_lm = provided if provided_role == "root" else None
    sub_lm = provided if provided_role == "sub" else None
    expected_root = provided if provided_role == "root" else fallback_root
    expected_sub = provided if provided_role == "sub" else fallback_sub

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(
            "fleet_rlm.rlm.program.build_model_bundle",
            lambda _settings: SimpleNamespace(root_lm=fallback_root, sub_lm=fallback_sub),
        )
        patcher.setattr("fleet_rlm.rlm.program.RLMModelBundle", CapturingBundle)
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter.build_native_program",
            lambda _settings, **_kwargs: MagicMock(),
        )
        patcher.setattr(
            "scripts.benchmarks.oolong.adapter._run_prediction_on_worker",
            lambda *_args: MagicMock(answer="Label: spam"),
        )
        patcher.setattr("fleet_rlm.rlm.compat_3_3_1.assert_dspy_version", lambda: None)
        await invoke_live_prediction(
            MagicMock(),
            kwargs,
            interpreter=MagicMock(),
            deadline=1_000_000.0,
            wrap_up_seconds=30.0,
            root_lm=root_lm,
            sub_lm=sub_lm,
        )

    assert bundles == [(expected_root, expected_sub)]


@pytest.mark.parametrize(("dataset", "split"), (("synth", "validation"), ("real", "test")))
def test_load_hf_row_uses_immutable_dataset_default(
    monkeypatch: pytest.MonkeyPatch,
    dataset: str,
    split: str,
) -> None:
    calls: list[dict[str, str]] = []
    datasets = ModuleType("datasets")

    def fake_load_dataset(dataset_id: str, *, split: str, revision: str) -> list[dict[str, object]]:
        calls.append({"dataset_id": dataset_id, "split": split, "revision": revision})
        return [{"id": "row"}]

    datasets.load_dataset = fake_load_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", datasets)

    datapoint = load_hf_row(dataset=dataset, split=split, index=2)

    assert calls[0]["revision"] == DEFAULT_HF_DATASET_REVISIONS[dataset]
    assert datapoint.dataset_revision == DEFAULT_HF_DATASET_REVISIONS[dataset]


def test_load_hf_row_preserves_explicit_revision_override(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    datasets = ModuleType("datasets")

    def fake_load_dataset(_dataset_id: str, *, split: str, revision: str) -> list[dict[str, object]]:
        assert split == "validation[0:1]"
        calls.append(revision)
        return [{"id": "row"}]

    datasets.load_dataset = fake_load_dataset  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "datasets", datasets)

    datapoint = load_hf_row(dataset="synth", split="validation", index=0, revision="test-revision")

    assert calls == ["test-revision"]
    assert datapoint.dataset_revision == "test-revision"


@pytest.mark.asyncio
async def test_live_rows_receive_independent_deadlines_and_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = (
        LoadedDatapoint(
            row={"id": "first", "context_window_id": "first", "context_window_text": "first"},
            dataset="synth",
            split="validation",
            index=0,
            source="fixture",
        ),
        LoadedDatapoint(
            row={"id": "second", "context_window_id": "second", "context_window_text": "second"},
            dataset="synth",
            split="validation",
            index=1,
            source="fixture",
        ),
    )
    leases = [
        SimpleNamespace(interpreter=MagicMock(), session_id=uuid4()),
        SimpleNamespace(interpreter=MagicMock(), session_id=uuid4()),
    ]
    observed: list[tuple[float, object]] = []

    async def fake_acquire(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return leases.pop(0)

    async def fake_stage(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(entries=())

    async def fake_invoke(*_args: object, deadline: float, turn_budget: object, **_kwargs: object) -> str:
        observed.append((deadline, turn_budget))
        return "Label: spam"

    async def fake_release(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner, "resolve_datapoints", lambda **_kwargs: rows)
    monkeypatch.setattr("fleet_rlm.daytona.provisioning.acquire_ephemeral_interpreter", fake_acquire)
    monkeypatch.setattr(runner, "stage_attachment_context_on_lease", fake_stage)
    monkeypatch.setattr(runner, "build_predict_kwargs", lambda *_args, **_kwargs: {"request": "request"})
    monkeypatch.setattr(runner, "kwargs_context_mode", lambda _kwargs: "attachment_context_capsule")
    monkeypatch.setattr(runner, "invoke_live_prediction", fake_invoke)
    monkeypatch.setattr(runner, "release_ephemeral_lease", fake_release)
    monkeypatch.setattr(runner, "score_prediction", lambda *_args, **_kwargs: {"score": 1})
    monkeypatch.setattr(runner, "build_receipt", lambda **_kwargs: {"schema": runner.RECEIPT_SCHEMA})
    fake_loop = SimpleNamespace(time=MagicMock(side_effect=(100.0, 200.0)))
    monkeypatch.setattr(runner.asyncio, "get_running_loop", lambda: fake_loop)

    await runner._run_live_async(
        SimpleNamespace(
            dataset="synth",
            split="validation",
            index=0,
            limit=2,
            hf=True,
            model_name="fleet-test",
        ),
        SimpleNamespace(turn_timeout_seconds=30.0, rlm_wrap_up_seconds=5.0),
    )

    assert [deadline for deadline, _budget in observed] == [130.0, 230.0]
    assert observed[0][1] is not observed[1][1]
    assert [budget.deadline for _deadline, budget in observed] == [130.0, 230.0]


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
