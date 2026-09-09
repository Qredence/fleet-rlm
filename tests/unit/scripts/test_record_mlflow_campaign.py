from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.record_mlflow_campaign import (
    ADAPTER_SCHEMA,
    RUNTIME_SCHEMA,
    CampaignRecordError,
    load_receipt,
    record,
)
from scripts.benchmarks.runtime_v2 import seal


def _runtime_receipt() -> dict[str, Any]:
    return seal(
        {
            "schema": RUNTIME_SCHEMA,
            "runtime_variant": "legacy",
            "execution_mode": "scripted",
            "repetitions": 2,
            "source_revision": "0" * 40,
            "source_dirty": False,
            "dataset_digest": "a" * 64,
            "scorer_digest": "b" * 64,
            "scorer_ids": ["echo-answer/v1"],
            "semantic_scorer_ids": ["semantic-keywords/v1"],
            "identities": {"profile": "private-testing", "dspy": "3.3.1", "mlflow": "3.16.0"},
            "samples": [
                {
                    "scenario": "s1",
                    "repetition": 0,
                    "seconds": 0.5,
                    "scores": {"echo-answer/v1": True},
                    "semantic_scores": {"semantic-keywords/v1": True},
                    "event_types": ["start"],
                },
                {
                    "scenario": "s1",
                    "repetition": 1,
                    "seconds": 1.5,
                    "scores": {"echo-answer/v1": False},
                    "semantic_scores": {"semantic-keywords/v1": True},
                    "event_types": ["start"],
                },
            ],
            "event_fixtures": {"s1": ["start"]},
            "latency_seconds": {"p50": 1.0, "p95": 1.5},
            "passed": False,
            "live_semantic_gate": "not_exercised",
        }
    )


def _adapter_receipt() -> dict[str, Any]:
    return seal(
        {
            "schema": ADAPTER_SCHEMA,
            "scope": "scripted-adapter-protocol-only",
            "runtime_variant": "legacy",
            "dspy_version": "3.3.1",
            "repetitions": 2,
            "dataset_digest": "c" * 64,
            "scorer_digest": "d" * 64,
            "implementation_digest": "e" * 64,
            "source_revision": "0" * 40,
            "source_dirty": False,
            "semantic_gate": "not_exercised",
            "scorer_ids": ["adapter-outcome/v1"],
            "samples": [],
            "summary": {
                "fleet": {
                    "samples": 2,
                    "correct": 2,
                    "provider_attempts": 4,
                    "latency_seconds": {"p50": 0.1, "p95": 0.2},
                }
            },
            "gates": {"sync_async_parity": True, "fleet_contract": False},
            "passed": False,
        }
    )


def _install_fake_mlflow(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    calls = SimpleNamespace(params=[], metrics=[], tags={}, artifacts=[], terminated=[])

    class _Info:
        run_id = "campaign-run-1"

    class _Run:
        info = _Info()

    class _Client:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def create_run(self, *, experiment_id: str, tags: dict[str, str], run_name: str) -> _Run:
            calls.tags = dict(tags)
            calls.run_name = run_name  # type: ignore[attr-defined]
            calls.experiment_id = experiment_id  # type: ignore[attr-defined]
            return _Run()

        def log_param(self, run_id: str, key: str, value: str) -> None:
            calls.params.append((run_id, key, value))

        def log_metric(self, run_id: str, key: str, value: float, **_kwargs: Any) -> None:
            calls.metrics.append((run_id, key, value))

        def log_artifact(self, run_id: str, path: str, *, artifact_path: str) -> None:
            calls.artifacts.append((run_id, artifact_path, Path(path).read_text(encoding="utf-8")))

        def set_terminated(self, run_id: str, *, status: str) -> None:
            calls.terminated.append((run_id, status))

    client_mod = ModuleType("mlflow.tracking.client")
    client_mod.MlflowClient = _Client  # type: ignore[attr-defined]
    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking.client", client_mod)
    return calls


def _write(tmp_path: Path, receipt: dict[str, Any], name: str = "receipt.json") -> Path:
    source = tmp_path / name
    source.write_text(json.dumps(receipt), encoding="utf-8")
    return source


def _args(source: Path, tmp_path: Path, purpose: str = "runtime-baseline") -> SimpleNamespace:
    return SimpleNamespace(
        mlflow_url="http://example.test",
        experiment_id="exp-1",
        receipt=source,
        purpose=purpose,
        artifact_path="fleet-benchmark-campaign",
        output=tmp_path / "result.json",
    )


def test_load_receipt_rejects_tampered_contents(tmp_path) -> None:
    source = _write(tmp_path, _runtime_receipt())
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["passed"] = True
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CampaignRecordError, match="digest"):
        load_receipt(tampered)


def test_load_receipt_rejects_unsupported_schema(tmp_path) -> None:
    source = _write(tmp_path, {"schema": "something-else", "receipt_digest": "x"})

    with pytest.raises(CampaignRecordError, match="schema"):
        load_receipt(source)


def test_record_requires_live_opt_in(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_LIVE", raising=False)
    source = _write(tmp_path, _runtime_receipt())

    with pytest.raises(CampaignRecordError, match="FLEET_LIVE"):
        record(_args(source, tmp_path))


def test_record_creates_run_with_identity_metrics_and_evidence_tags(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)
    source = _write(tmp_path, _runtime_receipt())

    result = record(_args(source, tmp_path))

    assert result["run_id"] == "campaign-run-1"
    assert result["promotion_eligible"] is False
    assert result["metrics_unknown"] == []
    assert calls.terminated == [("campaign-run-1", "FINISHED")]
    # Configuration identity: source revision, runtime variant, dependency identities.
    tags = calls.tags
    assert tags["fleet.campaign.purpose"] == "runtime-baseline"
    assert tags["fleet.campaign.source_revision"] == "0" * 40
    assert tags["fleet.campaign.promotion_eligible"] == "false"
    assert tags["fleet.campaign.semantic_gate"] == "not_exercised"
    assert tags["fleet.campaign.metrics_unknown"] == "none"
    params = {key: value for _, key, value in calls.params}
    assert params["fleet.campaign.runtime_variant"] == "legacy"
    assert params["fleet.campaign.identity.dspy"] == "3.3.1"
    assert params["fleet.campaign.identity.mlflow"] == "3.16.0"
    # Full-run measurements: failures stay in the denominator and are not zeroed out.
    metrics = {key: value for _, key, value in calls.metrics}
    assert metrics["fleet.benchmark.samples"] == 2.0
    assert metrics["fleet.benchmark.failed_samples"] == 1.0
    assert metrics["fleet.benchmark.latency.p95_seconds"] == 1.5
    assert metrics["fleet.benchmark.score_passes.echo-answer/v1"] == 1.0
    assert len(calls.artifacts) == 1
    assert json.loads(calls.artifacts[0][2])["schema"] == RUNTIME_SCHEMA


def test_record_adapter_receipt_projects_variant_and_gate_metrics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)
    source = _write(tmp_path, _adapter_receipt())

    result = record(_args(source, tmp_path, purpose="adapter-comparison"))

    assert calls.tags["fleet.campaign.evidence_lane"] == "scripted-adapter-protocol-only"
    metrics = {key: value for _, key, value in calls.metrics}
    assert metrics["fleet.benchmark.variant.fleet.correct"] == 2.0
    assert metrics["fleet.benchmark.variant.fleet.provider_attempts"] == 4.0
    assert metrics["fleet.benchmark.gate.fleet_contract"] == 0.0
    assert metrics["fleet.benchmark.gate.sync_async_parity"] == 1.0
    assert result["metrics_unknown"] == []


def test_record_marks_missing_latency_unknown_instead_of_zero(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = _install_fake_mlflow(monkeypatch)
    receipt = _runtime_receipt()
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    body.pop("latency_seconds")
    source = _write(tmp_path, seal(body))

    result = record(_args(source, tmp_path))

    metrics = {key: value for _, key, value in calls.metrics}
    assert "fleet.benchmark.latency.p50_seconds" not in metrics
    assert "fleet.benchmark.latency.p95_seconds" not in metrics
    assert metrics["fleet.benchmark.samples"] == 2.0
    assert result["metrics_unknown"] == ["latency.p50_seconds", "latency.p95_seconds"]
    assert calls.tags["fleet.campaign.metrics_unknown"] == "latency.p50_seconds,latency.p95_seconds"


def test_record_rejects_oversized_identity_param(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    _install_fake_mlflow(monkeypatch)
    receipt = _runtime_receipt()
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    body["identities"]["dspy"] = "x" * 501
    source = _write(tmp_path, seal(body))

    with pytest.raises(CampaignRecordError, match="bounded parameter size"):
        record(_args(source, tmp_path))


def test_main_writes_failed_result_receipt_without_leaking_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # load_dotenv re-reads the operator .env, which may legitimately set
    # FLEET_LIVE; pin an empty value so the opt-in gate fails closed here.
    monkeypatch.setenv("FLEET_LIVE", "")
    source = _write(tmp_path, _runtime_receipt())
    output = tmp_path / "result.json"

    from scripts.benchmarks.record_mlflow_campaign import main

    exit_code = main(
        [
            "--mlflow-url",
            "http://example.test",
            "--experiment-id",
            "exp-1",
            "--receipt",
            str(source),
            "--purpose",
            "runtime-baseline",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 1
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["error_category"] == "CampaignRecordError"
