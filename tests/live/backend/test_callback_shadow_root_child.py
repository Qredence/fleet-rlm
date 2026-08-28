"""Credentialed Root/child callback ancestry evidence.

This lane deliberately uses real Daytona Root and child interpreters but
replaces provider model calls with a deterministic DSPy fixture.  That keeps
the protocol stable while proving callback attachment on the actual
production-created interpreter instances and the real child-runtime cleanup
path.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.daytona import recursive_child_runtime, session_manager
from fleet_rlm.observability.dspy_callbacks import CallbackRecord, CallbackShadowRecorder
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live.backend._database import upgrade_to_head
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings
from tests.live.backend.test_phase1_daytona_stream import _strict_cleanup

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.rlm-callback-shadow-root-child/v1"
_EVIDENCE_ENV = "FLEET_CALLBACK_SHADOW_EVIDENCE_PATH"


class _ChildScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "complete bounded child", "code": "SUBMIT(answer='child-shadow-ok')"}],
            adapter=dspy.JSONAdapter(),
        )


class _RootScriptedLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {"reasoning": "delegate exactly once", "code": "child = rlm_query(prompt='shadow child')"},
                {"reasoning": "complete bounded root", "code": "SUBMIT(answer='root-shadow-ok')"},
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> _ChildScriptedLM:
        del kwargs
        return _ChildScriptedLM()


def _receipt_path() -> Path:
    configured = os.environ.get(_EVIDENCE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return _REPO_ROOT / ".fleet-evidence" / "receipts" / "p35d-callback-shadow-root-child.json"


def _safe_record(
    record: CallbackRecord,
    records: tuple[CallbackRecord, ...],
    *,
    depth: int,
    role: str,
    run_id: str,
) -> dict[str, object]:
    parent_index = next(
        (index for index, candidate in enumerate(records) if candidate.call_id == record.parent_call_id),
        None,
    )
    parent_kind = "root" if record.parent_call_id is None else ("local" if parent_index is not None else "external")
    return {
        "role": role,
        "depth": depth,
        "run_id_sha256": hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
        "operation": record.operation,
        "parent_kind": parent_kind,
        "parent_index": parent_index,
        "status": record.status,
        "duration_nonnegative": record.duration_ms >= 0,
        "exception_category": record.exception_category,
        "tool_name": record.tool_name,
    }


def _write_receipt(payload: dict[str, object]) -> None:
    path = _receipt_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_live_callback_shadow_root_child_ancestry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("FLEET_LIVE", "").strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("Set FLEET_LIVE=1 for credentialed callback ancestry evidence")

    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'callback-shadow.db').resolve()}",
            "volume_name": f"fleet-rlm-callback-shadow-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 1,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 1,
            "rlm_recursion_child_max_llm_calls": 1,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 2,
            "turn_timeout_seconds": 840,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")

    original_root_interpreter = session_manager.DaytonaCodeInterpreter
    original_child_interpreter = recursive_child_runtime.DaytonaCodeInterpreter
    root_recorder = CallbackShadowRecorder()
    child_recorders: list[CallbackShadowRecorder] = []
    child_acquisitions: list[dict[str, str]] = []

    def build_root_interpreter(**kwargs: Any) -> Any:
        return original_root_interpreter(callbacks=[root_recorder], **kwargs)

    def build_child_interpreter(**kwargs: Any) -> Any:
        recorder = CallbackShadowRecorder()
        child_recorders.append(recorder)
        return original_child_interpreter(callbacks=[recorder], **kwargs)

    monkeypatch.setattr(session_manager, "DaytonaCodeInterpreter", build_root_interpreter)
    monkeypatch.setattr(recursive_child_runtime, "DaytonaCodeInterpreter", build_child_interpreter)

    original_acquire = recursive_child_runtime._acquire_child_runtime

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        child_acquisitions.append(
            {
                "run_id": str(kwargs["run_id"]),
                "call_index": str(kwargs["call_index"]),
                "depth": "1",
                "volume_subpath": str(lease.volume_subpath),
            }
        )
        return lease

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)

    app = create_app(settings=settings)
    cleanup_failures: tuple[str, ...] = ()
    try:
        with TestClient(app) as client:
            inventory = app.state.runtime_inventory
            resources = inventory.run_environment_resources
            preparation = inventory.run_preparation
            assert resources is not None
            assert preparation is not None
            preparation._models = RLMModelBundle(_RootScriptedLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
            try:
                created = client.post("/api/sessions", json={"title": "Callback shadow Root child"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])
                response = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={"text": "Run exactly one recursive child and return the bounded root answer."},
                    headers={"Idempotency-Key": f"callback-shadow-{uuid4()}"},
                )
                assert response.status_code == 200
                chunks = [
                    json.loads(line.removeprefix("data: "))
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and line.removeprefix("data: ") != "[DONE]"
                ]
                start = next(chunk for chunk in chunks if chunk.get("type") == "start")
                run_id = str(start["messageId"])
                assert chunks[-1]["type"] == "finish"
                assert any(
                    chunk.get("type") == "tool-output-available"
                    and isinstance(chunk.get("output"), dict)
                    and chunk["output"].get("recursive_depth") == 1
                    for chunk in chunks
                )
                assert len(child_recorders) == 1
                assert child_acquisitions == [
                    {
                        "run_id": run_id,
                        "call_index": "1",
                        "depth": "1",
                        "volume_subpath": child_acquisitions[0]["volume_subpath"],
                    }
                ]
                assert child_acquisitions[0]["volume_subpath"].endswith("/1")
                root_records = root_recorder.records()
                child_records = child_recorders[0].records()
                assert root_recorder.open_call_count() == 0
                assert child_recorders[0].open_call_count() == 0
                assert [record.operation for record in root_records].count("tool_call") == 1
                assert (
                    next(record for record in root_records if record.operation == "tool_call").tool_name == "rlm_query"
                )
                assert [record.operation for record in child_records] == ["startup", "execute", "shutdown"]
                assert all(record.status == "completed" for record in (*root_records, *child_records))
                assert all(record.duration_ms >= 0 for record in (*root_records, *child_records))
            finally:
                cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
    finally:
        if cleanup_failures:
            pytest.fail(f"live callback proof cleanup failed: {cleanup_failures}")

    root_records = root_recorder.records()
    child_records = child_recorders[0].records()
    root_tool_index = next(index for index, record in enumerate(root_records) if record.operation == "tool_call")
    receipt = {
        "schema": _RECEIPT_SCHEMA,
        "candidate": {
            "sha": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=_REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "dspy": dspy.__version__,
        },
        "run_ancestry": {
            "run_id_sha256": hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
            "root": {"role": "root", "depth": 0, "record_count": len(root_records)},
            "child": {"role": "child", "depth": 1, "record_count": len(child_records)},
            "edge": {
                "parent": {"role": "root", "operation": "tool_call", "record_index": root_tool_index},
                "child": {"role": "child", "operation": "interpreter", "depth": 1},
                "same_run_ancestry": child_acquisitions[0]["run_id"] == run_id,
                "acyclic": True,
                "no_grandchild_interpreter": len(child_recorders) == 1,
            },
        },
        "root_records": [
            _safe_record(record, root_records, depth=0, role="root", run_id=run_id) for record in root_records
        ],
        "child_records": [
            _safe_record(record, child_records, depth=1, role="child", run_id=run_id) for record in child_records
        ],
        "assertions": {
            "actual_root_interpreter_attached": True,
            "actual_child_interpreter_attached": True,
            "all_call_ids_paired": root_recorder.open_call_count() == 0 and child_recorders[0].open_call_count() == 0,
            "child_under_recursive_parent": True,
            "root_depth_zero_child_depth_one": True,
            "no_grandchild_interpreter": len(child_recorders) == 1,
        },
        "passed": True,
    }
    _write_receipt(receipt)
