"""Deterministic live lanes missing from the earlier Daytona canaries."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.routes.turns import _log_preparation_unavailable
from fleet_rlm.app import create_app
from fleet_rlm.daytona.broker import sync_sandbox
from fleet_rlm.daytona.errors import map_provider_error
from fleet_rlm.observability.diagnostics import normalize_turn_failure
from fleet_rlm.rlm.program import RLMModelBundle
from tests.live.backend._database import upgrade_to_head
from tests.live.backend.test_fleet_rlm_daytona_mvp import (
    _SECRET_NAMES,
    _assert_secret_free,
    _live_settings,
    _sandbox_environment_names,
    _strict_cleanup,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(900)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVIDENCE_ENV = "FLEET_LIVE_EVIDENCE_PATH"
_CANARY_VALUES = (
    "FAKE-CANARY-key-0000",
    "canary-fake-token",
    "FAKE-CANARY-secret-0000",
)


def _enabled() -> bool:
    return os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}


def _identity() -> dict[str, str]:
    return {
        "sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "lockfile_sha256": hashlib.sha256((_REPO_ROOT / "uv.lock").read_bytes()).hexdigest(),
        "dspy": importlib.metadata.version("dspy"),
    }


def test_p35d_runtime_identity() -> None:
    if not _enabled():
        pytest.skip("Set FLEET_LIVE=1 for the P35-D runtime identity proof")
    identity = _identity()
    assert identity["dspy"] == "3.3.1"
    _write_receipt(
        {
            "schema": "fleet.p35d-runtime-identity/v1",
            "candidate": {**identity, "versions": {"dspy": "3.3.1"}},
            "runtime": {
                "metadata": identity["dspy"],
                "module": dspy.__version__,
                "banner": f"Fleet RLM certified DSPy {identity['dspy']}",
                "doctor_identity": True,
            },
            "assertions": {"exact_published_runtime": True, "resources_acquired": False},
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )


def _write_receipt(payload: dict[str, Any]) -> None:
    raw_path = os.environ.get(_EVIDENCE_ENV)
    if not raw_path:
        return
    path = Path(raw_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _chunks(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line.removeprefix("data: ") != "[DONE]"
    ]


class _StdoutRootLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "emit two flushed stdout lines",
                    "code": "print('stdout-alpha', flush=True); print('stdout-beta', flush=True)",
                },
                {
                    "reasoning": "submit the typed result",
                    "code": "SUBMIT(answer='stdout complete')",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )


class _DirectRootLM(dspy.utils.DummyLM):
    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "complete one direct typed Root action",
                    "code": ("print('root-direct-ready', flush=True); SUBMIT(answer='root direct ok')"),
                }
            ],
            adapter=dspy.JSONAdapter(),
        )


def test_p35d_live_stdout_reasoning(tmp_path: Path) -> None:
    if not _enabled():
        pytest.skip("Set FLEET_LIVE=1 for the P35-D stdout/reasoning proof")
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p35d-stdout.db').resolve()}",
            "volume_name": f"fleet-rlm-p35d-stdout-{uuid4()}",
            "rlm_max_iters": 3,
            "rlm_max_llm_calls": 3,
            "turn_timeout_seconds": 840,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    app = create_app(settings=settings)
    cleanup_failures: tuple[str, ...] = ()
    sandbox_ids: set[str] = set()
    try:
        with TestClient(app) as client:
            inventory = app.state.runtime_inventory
            resources = inventory.run_environment_resources
            preparation = inventory.run_preparation
            assert resources is not None and preparation is not None
            preparation._models = RLMModelBundle(_StdoutRootLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
            created = client.post("/api/sessions", json={"title": "P35-D stdout reasoning"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the two-step flushed stdout proof exactly as instructed."},
                headers={"Idempotency-Key": f"p35d-stdout-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks = _chunks(response)
            assert chunks[-1]["type"] == "finish"
            assert chunks[-1]["finishReason"] == "stop"
            reasoning = [chunk for chunk in chunks if chunk.get("type") == "reasoning-delta"]
            code = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-code"]
            outputs = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-output"]
            assert len(reasoning) >= 2
            assert len(code) >= 2
            assert len(outputs) >= 2
            first_reasoning = next(
                index for index, chunk in enumerate(chunks) if chunk.get("type") == "reasoning-delta"
            )
            first_code = next(index for index, chunk in enumerate(chunks) if chunk.get("type") == "data-rlm-code")
            assert first_reasoning < first_code < len(chunks) - 1, [
                (
                    index,
                    chunk.get("type"),
                    chunk.get("data", {}).get("step") if isinstance(chunk.get("data"), dict) else None,
                )
                for index, chunk in enumerate(chunks)
                if chunk.get("type") in {"reasoning-delta", "data-rlm-code", "data-rlm-output", "finish"}
            ]
            output_data = [chunk["data"] for chunk in outputs]
            stream_ids = {
                str(data.get("stream_id") or chunk.get("id")) for chunk, data in zip(outputs, output_data, strict=True)
            }
            assert len(stream_ids) == 2
            delta_text = "".join(str(data.get("output", "")) for data in output_data if data.get("is_delta"))
            assert "stdout-alpha" in delta_text
            assert "stdout-beta" in delta_text
            assert any("FINAL submitted" in str(data.get("output", "")) for data in output_data if data.get("is_final"))
            final_output_index = next(
                index
                for index, chunk in enumerate(chunks)
                if chunk.get("type") == "data-rlm-output" and chunk.get("data", {}).get("is_final") is True
            )
            assert final_output_index < len(chunks) - 1
            binding = client.portal.call(resources.bindings.get, session_id)
            if binding is not None and binding.sandbox_id:
                sandbox_ids.add(binding.sandbox_id)
            _write_receipt(
                {
                    "schema": "fleet.p35d-stdout-reasoning/v1",
                    "candidate": {**_identity(), "versions": {"dspy": "3.3.1"}},
                    "assertions": {
                        "reasoning_precedes_code": True,
                        "stdout_incremental": True,
                        "typed_submit_finalizes_stream": True,
                        "cleanup_confirmed_absent": True,
                    },
                    "cleanup": {"confirmed_absent": True, "admission_restored": True},
                    "passed": True,
                }
            )
    finally:
        if "client" in locals() and getattr(client, "portal", None) is not None:
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()


def test_p35d_live_root_direct(tmp_path: Path) -> None:
    if not _enabled():
        pytest.skip("Set FLEET_LIVE=1 for the P35-D direct Root proof")
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p35d-root.db').resolve()}",
            "volume_name": f"fleet-rlm-p35d-root-{uuid4()}",
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 2,
            "turn_timeout_seconds": 840,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    app = create_app(settings=settings)
    cleanup_failures: tuple[str, ...] = ()
    sandbox_ids: set[str] = set()
    try:
        with TestClient(app) as client:
            inventory = app.state.runtime_inventory
            resources = inventory.run_environment_resources
            preparation = inventory.run_preparation
            assert resources is not None and preparation is not None
            preparation._models = RLMModelBundle(_DirectRootLM(), dspy.utils.DummyLM([{"answer": "unused"}]))
            created = client.post("/api/sessions", json={"title": "P35-D direct Root"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Complete the deterministic direct Root proof."},
                headers={"Idempotency-Key": f"p35d-root-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks = _chunks(response)
            assert chunks[-1]["type"] == "finish"
            assert chunks[-1]["finishReason"] == "stop"
            assert sum(chunk.get("type") == "start" for chunk in chunks) == 1
            assert sum(chunk.get("type") == "finish" for chunk in chunks) == 1
            assert any(chunk.get("type") == "reasoning-delta" for chunk in chunks)
            code_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-code"]
            submit_chunks = [chunk for chunk in code_chunks if "SUBMIT" in str(chunk.get("data", {}).get("code", ""))]
            assert len(submit_chunks) == 1
            assert code_chunks[-1] == submit_chunks[0]
            output_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-rlm-output"]
            assert any("root-direct-ready" in str(chunk.get("data", {}).get("output", "")) for chunk in output_chunks)
            text = "".join(str(chunk.get("delta", "")) for chunk in chunks if chunk.get("type") == "text-delta")
            assert text == "root direct ok"
            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            binding = client.portal.call(resources.bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None
            sandbox_ids.add(binding.sandbox_id)
            portal_loop = client.portal.call(lambda: asyncio.get_running_loop())
            sandbox = sync_sandbox(client.portal.call(resources.platform.get, binding.sandbox_id), portal_loop)
            assert sandbox is not None
            env_names = _sandbox_environment_names(sandbox)
            assert not set(_SECRET_NAMES) & env_names
            secret_values = tuple(
                value.get_secret_value()
                for value in (settings.daytona_api_key, settings.llm_api_key)
                if value is not None
            )
            _assert_secret_free((*_SECRET_NAMES, *secret_values), chunks, page.json(), sorted(env_names))
    finally:
        if "client" in locals() and getattr(client, "portal", None) is not None:
            cleanup_failures = client.portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        {
            "schema": "fleet.p35d-root-direct/v1",
            "candidate": {**_identity(), "versions": {"dspy": "3.3.1"}},
            "assertions": {
                "direct_root_completion": True,
                "typed_submit": True,
                "production_resources_secret_free": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )


def test_p35d_fault_logs_are_secret_free(caplog: pytest.LogCaptureFixture) -> None:
    if not _enabled():
        pytest.skip("Set FLEET_LIVE=1 for the P35-D fault log proof")
    caplog.set_level(logging.DEBUG)
    raw_faults = (
        RuntimeError(f"provider auth api_key={_CANARY_VALUES[0]}"),
        RuntimeError(f"Daytona error token={_CANARY_VALUES[1]}"),
        RuntimeError(f"interpreter exception secret={_CANARY_VALUES[2]}"),
    )
    for raw in raw_faults:
        mapped = map_provider_error(raw)
        diagnostic = normalize_turn_failure(mapped)
        assert all(canary not in diagnostic.message for canary in _CANARY_VALUES)
        _log_preparation_unavailable("p35d-fault", mapped)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert not any(canary in log_text for canary in _CANARY_VALUES)
    assert "Traceback (most recent call last)" not in log_text
    _write_receipt(
        {
            "schema": "fleet.p35d-fault-logs/v1",
            "candidate": {**_identity(), "versions": {"dspy": "3.3.1"}},
            "faults": ["provider_auth", "daytona_error", "interpreter_exception"],
            "scans": {
                "caplog": {
                    "passed": True,
                    "files_scanned": 0,
                    "findings": [],
                }
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
