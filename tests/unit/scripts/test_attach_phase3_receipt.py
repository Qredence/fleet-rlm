from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from scripts.benchmarks.attach_phase3_receipt import (
    NATIVE_RECEIPT_SCHEMA,
    NATIVE_RECEIPT_SCHEMA_V2,
    Phase3AttachmentError,
    attach,
    load_receipt,
)


def _payload() -> dict[str, Any]:
    return {
        "schema": NATIVE_RECEIPT_SCHEMA,
        "versions": {"python": "3.13.13", "dspy": "3.3.1", "daytona": "0.210.0"},
        "transport": {
            "native_context": True,
            "composition_bridge": True,
            "loopback_host_assumption": False,
            "host_callback": "daytona_preview_http_poll",
        },
        "timings_ms": {"native_first_action_ms": 42, "cleanup_ms": 9.5},
        "assertions": {
            "native_nested_root_child_resume": True,
            "native_typed_submit": True,
            "native_preview_transport": True,
            "native_context_is_explicit": True,
            "broker_typed_submit": True,
            "native_broker_output_contract_parity": True,
            "native_host_callback_authority_loss_blocks_publication": True,
            "detached_process_probe_recorded": True,
            "uncertain_native_process_is_quarantined": True,
            "different_sessions_have_distinct_sandboxes": True,
            "different_sessions_execute_concurrently": True,
            "all_disposable_sandboxes_absent": True,
        },
        "containment": {
            "detached_process_contained": False,
            "quarantined_when_uncertain": True,
            "all_disposable_sandboxes_absent": True,
        },
        "go_no_go": {
            "native_production": False,
            "retained_broker_compatibility": True,
            "reason": "detached_process_containment_uncertified",
        },
        "passed": True,
    }


def _payload_v2() -> dict[str, Any]:
    payload = _payload()
    payload["schema"] = NATIVE_RECEIPT_SCHEMA_V2
    payload["containment"] = {
        "strategy": "sandbox_delete",
        "detached_process_probe": "blocked_after_fence",
        "context_deleted": True,
        "sandbox_fenced": True,
        "sandbox_absent_confirmed": True,
        "replacement_generation": True,
        "volume_scope_preserved": True,
        "quarantined_when_uncertain": True,
        "all_disposable_sandboxes_absent": True,
    }
    payload["continuity"] = {
        "same_process_fresh_context": True,
        "process_restart": True,
        "sandbox_stop_start": True,
        "full_replacement": True,
        "durable_file_readable": True,
        "python_only_state_absent": True,
        "checksum_preserved": True,
    }
    payload["go_no_go"] = {
        "native_production": False,
        "retained_broker_compatibility": True,
        "reason": "whole_sandbox_fencing_certified",
    }
    return payload


def test_load_receipt_projects_only_bounded_capability_fields(tmp_path) -> None:
    source = tmp_path / "native.json"
    payload = _payload()
    payload["secret_prompt"] = "do not export"
    source.write_text(json.dumps(payload), encoding="utf-8")

    result = load_receipt(source)

    assert "secret_prompt" not in result
    assert result["timings_ms"] == {"cleanup_ms": 9.5, "native_first_action_ms": 42.0}


def test_load_receipt_rejects_native_go_without_containment(tmp_path) -> None:
    source = tmp_path / "native.json"
    payload = _payload()
    payload["go_no_go"]["native_production"] = True
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Phase3AttachmentError, match="detached-process containment"):
        load_receipt(source)


def test_load_receipt_accepts_complete_v2_fencing_and_continuity(tmp_path) -> None:
    source = tmp_path / "native-v2.json"
    source.write_text(json.dumps(_payload_v2()), encoding="utf-8")

    result = load_receipt(source)

    assert result["schema"] == NATIVE_RECEIPT_SCHEMA_V2
    assert result["containment"]["strategy"] == "sandbox_delete"
    assert all(result["continuity"].values())


def test_load_receipt_rejects_incomplete_v2_continuity(tmp_path) -> None:
    source = tmp_path / "native-v2.json"
    payload = _payload_v2()
    payload["continuity"]["process_restart"] = False
    source.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Phase3AttachmentError, match="complete fencing and continuity"):
        load_receipt(source)


def test_attach_logs_artifact_tags_and_timing_metrics(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("FLEET_LIVE", "1")
    calls = SimpleNamespace(tags=[], metrics=[], artifacts=[])

    class _Client:
        def get_run(self, run_id: str) -> Any:
            return SimpleNamespace(info=SimpleNamespace(run_id=run_id, status="FINISHED"))

        def set_tag(self, run_id: str, key: str, value: str) -> None:
            calls.tags.append((run_id, key, value))

        def log_metric(self, run_id: str, key: str, value: float, **kwargs: Any) -> None:
            calls.metrics.append((run_id, key, value, kwargs))

        def log_artifact(self, run_id: str, path: str, *, artifact_path: str) -> None:
            calls.artifacts.append((run_id, path, artifact_path, Path(path).read_text(encoding="utf-8")))

    client_mod = ModuleType("mlflow.tracking.client")
    client_mod.MlflowClient = _Client  # type: ignore[attr-defined]
    tracking_mod = ModuleType("mlflow.tracking")
    tracking_mod.client = client_mod  # type: ignore[attr-defined]
    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking_mod)
    monkeypatch.setitem(sys.modules, "mlflow.tracking.client", client_mod)

    source = tmp_path / "native.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    args = SimpleNamespace(
        mlflow_url="http://example.test",
        run_id="campaign-1",
        receipt=source,
        artifact_path="phase3/daytona-native",
    )

    result = attach(args)

    assert result["run_id"] == "campaign-1"
    assert result["artifact"] == "phase3/daytona-native/daytona-native-feasibility.json"
    assert result["tags_written"] == 7
    assert result["metrics_written"] == 2
    assert len(calls.artifacts) == 1
    assert json.loads(calls.artifacts[0][3])["schema"] == NATIVE_RECEIPT_SCHEMA
    assert all("secret" not in str(call).lower() for call in calls.tags + calls.metrics)
