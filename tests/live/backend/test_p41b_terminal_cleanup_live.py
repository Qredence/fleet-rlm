"""P41b same-SHA live terminal cleanup proof (VAL-TURN-056).

Serial, credentialed, ``FLEET_LIVE=1``-only lanes running each live Turn
ending on the real Daytona provider with protocol-stable scripted LMs
(mission guidance: deterministic fixtures for acceptance lanes). Endings:

- ``success``: one Root Turn with a Workspace marker write plus exactly one
  native depth-1 child; ``finish``/stop terminal; marker survives cleanup.
- ``provider_failure``: the scripted Root LM raises after one real cell;
  closed sanitized ``error`` + ``finish`` terminal; canary never leaks.
- ``cancellation``: host-forced broker stall + ``request_cancel``; exactly
  one ``abort`` terminal and no success chunks.
- ``timeout``: short absolute Turn deadline + host-forced stall; one
  ``finish``/error "timed out" terminal, no ``abort``.
- ``disconnect``: real loopback uvicorn + httpx SSE client closes mid-Turn;
  detached settlement lands the cancellation tombstone durably.

Every ending proves: Runtime Event terminal recorded, every Turn-owned
Root/child Sandbox absent, admission restored to baseline, lease registry
empty, and the shared Workspace Volume intact. Receipts are sanitized: the
aggregate receipt records the git SHA, terminal categories, absence/admission
counts, and pass/fail only -- never credentials, provider internals, Sandbox
identities, or private content (VAL-TURN-056 evidence rule).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.chat.run_lifecycle import TurnAccess
from fleet_rlm.config import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.http_broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.files.volume_paths import volume_paths_from_settings
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.runtime.bindings import workspace_volume_subpath
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import (
    _receipt_projection,
    _wait_for_admission_baseline,
)
from tests.live.backend.test_p39c_batch_live import _all_absent

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(2400)]

_RECEIPT_SCHEMA = "fleet.p41b-terminal-cleanup/v1"
_AGGREGATE_SCHEMA = "fleet.p41b-terminal-cleanup-proof/v1"
_CANARY = "FAKE-CANARY-provider-failure-0000"
_MARKER_REL_PATH = "p41b-terminal-marker.txt"
_MARKER_CONTENT = "P41B-VOL-MARKER"
_MARKER_SHA = hashlib.sha256(_MARKER_CONTENT.encode("utf-8")).hexdigest()
_ENDINGS = ("success", "provider_failure", "cancellation", "timeout", "disconnect")
_RECEIPT_DIR = Path(__file__).resolve().parents[3] / ".fleet-evidence" / "receipts"


def _live_enabled() -> bool:
    import os

    return os.environ.get("FLEET_LIVE", "").strip().lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------------------
# Settings + scripted LMs
# ---------------------------------------------------------------------------


def _case_settings(tmp_path: Path, *, name: str, recursion: bool, turn_timeout_seconds: int) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / f'p41b-{name}.db').resolve()}",
            "volume_name": f"fleet-rlm-p41b-{name}-{uuid4()}",
            "rlm_recursion_enabled": recursion,
            "rlm_recursion_max_calls": 2,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 2,
            "rlm_recursion_child_max_llm_calls": 2,
            "rlm_max_iters": 4,
            "rlm_max_llm_calls": 6,
            "turn_timeout_seconds": turn_timeout_seconds,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


class _SuccessRootLM(dspy.utils.DummyLM):
    """Root: delegate exactly one native depth-1 child, then SUBMIT."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "delegate exactly one native child",
                    "code": "child = rlm_query(prompt='P41B-CHILD bounded child task')",
                },
                {
                    "reasoning": "submit the bounded success answer",
                    "code": "SUBMIT(answer='P41B-SUCCESS child=' + child)",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return dspy.utils.DummyLM(
            {"P41B-CHILD": {"reasoning": "child", "code": "SUBMIT(answer='P41B-CHILD-TOKEN')"}},
            adapter=dspy.JSONAdapter(),
        )


class _ProviderFailureLM(dspy.utils.DummyLM):
    """First iteration runs one real cell; the NEXT LM call raises provider-flavor failure."""

    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "run one real cell", "code": "print('P41B-FAIL-CELL')"}],
            adapter=dspy.JSONAdapter(),
        )
        self.calls = 0

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return super().forward(prompt=prompt, messages=messages, **kwargs)
        raise RuntimeError(f"simulated provider authentication failure {_CANARY}")

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _ProviderFailureLM()


class _StallThenSubmitLM(dspy.utils.DummyLM):
    """One real cell, then a slow host-side second LM call (disconnect window)."""

    def __init__(self, *, stall_seconds: float = 25.0) -> None:
        super().__init__(
            [
                {"reasoning": "run one real cell", "code": "print('P41B-DISCONNECT-CELL')"},
                {"reasoning": "submit", "code": "SUBMIT(answer='P41B-UNREACHABLE')"},
            ],
            adapter=dspy.JSONAdapter(),
        )
        self.calls = 0
        self._stall_seconds = stall_seconds

    def forward(self, prompt: Any = None, messages: Any = None, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return super().forward(prompt=prompt, messages=messages, **kwargs)
        time.sleep(self._stall_seconds)
        return super().forward(prompt=prompt, messages=messages, **kwargs)

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _StallThenSubmitLM()


class _OneCellLM(dspy.utils.DummyLM):
    """One real cell; on exhaustion return a repair-shaped extra cell."""

    def __init__(self) -> None:
        super().__init__(
            [{"reasoning": "run one real cell", "code": "print('P41B-STALL-CELL')"}],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _OneCellLM()


# ---------------------------------------------------------------------------
# Evidence + orchestration helpers
# ---------------------------------------------------------------------------


@dataclass
class _EndingEvidence:
    child_sandbox_ids: list[str] = field(default_factory=list)
    receipts: list[dict[str, object]] = field(default_factory=list)
    block_calls: int = 0
    cancel_state: str | None = None


def _install_child_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _EndingEvidence) -> None:
    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        evidence.child_sandbox_ids.append(lease.sandbox_id)
        return lease

    class RecordingLease(original_lease):  # type: ignore[misc, valid-type]
        async def aclose(self) -> Any:
            receipt = await super().aclose()
            evidence.receipts.append(_receipt_projection(receipt))
            return receipt

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "SandboxLease", RecordingLease)


def _block_first_root_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: _EndingEvidence,
    on_first: Any,
    hold_seconds: float,
) -> None:
    """Block the first (Root-only here) broker execution, fire ``on_first``, stall, then raise."""
    original = DaytonaHttpToolBroker.execute_code
    lock = threading.Lock()

    def blocking(
        self: DaytonaHttpToolBroker,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Any | None = None,
    ) -> Any:
        with lock:
            first = evidence.block_calls == 0
            evidence.block_calls += 1
        if first:
            on_first()
            time.sleep(hold_seconds)
            raise TimeoutError("p41b host-forced root stall")
        return original(self, code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", blocking)


async def _volume_exists(resources: Any, volume_name: str) -> bool:
    try:
        return (await resources.client.volume.get(volume_name, create=False)) is not None
    except Exception:
        return False


async def _scratch_marker(resources: Any, settings: Settings) -> tuple[Any, str, str]:
    """Validator-owned ephemeral Sandbox writing the marker on the shared Volume.

    Returns ``(sandbox, volume_id, mount_path)``; the caller deletes the
    Sandbox after read-back.
    """
    paths = volume_paths_from_settings(settings)
    volume = await resources.client.volume.get(settings.volume_name, create=False)
    subpath = workspace_volume_subpath(LocalScope().workspace_id)
    sandbox = await resources.platform.create(
        volume_id=volume.id,
        mount_path=paths.mount_path,
        volume_subpath=subpath,
        ephemeral=True,
        labels={"fleet.p41b": "marker"},
    )
    target = f"{paths.mount_path.rstrip('/')}/{_MARKER_REL_PATH.lstrip('/')}"
    await sandbox.fs.upload_file(_MARKER_CONTENT.encode("utf-8"), target)
    readback = await sandbox.fs.download_file(target)
    return sandbox, str(volume.id), paths.mount_path, hashlib.sha256(readback).hexdigest()


async def _read_marker_sha(sandbox: Any, mount_path: str) -> str | None:
    target = f"{mount_path.rstrip('/')}/{_MARKER_REL_PATH.lstrip('/')}"
    try:
        data = await sandbox.fs.download_file(target)
    except Exception:
        return None
    return hashlib.sha256(data).hexdigest()


def _write_receipt(ending: str, payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt(f"p41b-terminal-{ending}.json", f"-p41b-terminal-{ending}", payload)


def _chained_sandbox_ids(
    *,
    portal: Any,
    resources: Any,
    session_id: UUID | None,
    evidence: _EndingEvidence,
) -> set[str]:
    ids: set[str] = set(evidence.child_sandbox_ids)
    if session_id is not None:
        binding = portal.call(resources.bindings.get, session_id)
        if binding is not None and binding.sandbox_id is not None:
            ids.add(str(binding.sandbox_id))
    ids.update(str(item) for item in resources._sandbox_ids)
    return ids


# ---------------------------------------------------------------------------
# Ending 1: success (Root + one native child + Workspace marker)
# ---------------------------------------------------------------------------


def test_p41b_success_terminal_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _case_settings(tmp_path, name="success", recursion=True, turn_timeout_seconds=600)
    evidence = _EndingEvidence()
    _install_child_evidence(monkeypatch, evidence)
    app = create_app(settings=settings)
    session_id: UUID | None = None
    cleanup_failures: tuple[str, ...] = ()
    marker_sha: str | None = None
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        portal = client.portal
        marker_sandbox: Any = None
        marker_mount_path = volume_paths_from_settings(settings).mount_path
        preparation._models = RLMModelBundle(
            _SuccessRootLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P41b success canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            # Validator-owned marker on the shared Volume, written before the Turn.
            marker_sandbox, _volume_id, marker_mount_path, present_sha = portal.call(
                _scratch_marker, resources, settings
            )
            assert present_sha == _MARKER_SHA

            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P41b success proof."},
                headers={"Idempotency-Key": f"p41b-success-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            finish = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish) == 1
            assert finish[0].get("finishReason") == "stop"

            assert evidence.child_sandbox_ids, "success ending must exercise one native child"

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            absence = portal.call(_all_absent, resources, sorted(sandbox_ids))
            assert absence and all(absence.values()), f"turn-owned sandboxes must be absent: {absence}"

            assert evidence.receipts, "the child lease must record a cleanup receipt"
            for receipt in evidence.receipts:
                assert receipt["provider_confirmed_absent"] is True
                assert receipt["admission_released"] is True
                assert receipt["clean"] is True

            # The shared Volume stays intact with the marker bytes after cleanup.
            marker_sha = portal.call(_read_marker_sha, marker_sandbox, marker_mount_path)
            assert marker_sha == _MARKER_SHA, f"volume marker drifted: {marker_sha}"
            assert portal.call(_volume_exists, resources, settings.volume_name) is True
        finally:
            if marker_sandbox is not None:
                with contextlib.suppress(Exception):
                    portal.call(resources.platform.delete, marker_sandbox)
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            if marker_sandbox is not None:
                sandbox_ids.add(str(marker_sandbox.id))
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        "success",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "ending": "success",
            "terminal": {"category": "completed", "wire_terminal": "finish:stop"},
            "cleanup": {
                "turn_owned_absent": True,
                "admission_restored": True,
                "admission_permits": settings.max_active_daytona_leases,
                "lease_holder_released": True,
                "child_count": len(evidence.child_sandbox_ids),
                "volume_intact": True,
                "volume_marker_sha256": marker_sha,
            },
            "passed": True,
        },
    )


# ---------------------------------------------------------------------------
# Ending 2: provider failure (sanitized runtime_failure terminal)
# ---------------------------------------------------------------------------


def test_p41b_provider_failure_terminal_cleanup(tmp_path: Path) -> None:
    settings = _case_settings(tmp_path, name="failure", recursion=False, turn_timeout_seconds=600)
    app = create_app(settings=settings)
    session_id: UUID | None = None
    cleanup_failures: tuple[str, ...] = ()
    evidence = _EndingEvidence()
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        portal = client.portal
        preparation._models = RLMModelBundle(
            _ProviderFailureLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P41b failure canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P41b provider-failure proof."},
                headers={"Idempotency-Key": f"p41b-failure-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            finish = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish) == 1
            assert finish[0].get("finishReason") == "error"
            errors = [chunk for chunk in chunks if chunk.get("type") == "error"]
            assert len(errors) == 1
            assert errors[0].get("errorText") in {"Turn failed", "Turn could not be prepared"}
            assert _CANARY not in response.text
            assert "Traceback" not in response.text

            # The failed Turn ran one real cell: a Root Sandbox was owned and is gone.
            binding = portal.call(resources.bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None

            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            assert _CANARY not in page.text

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            absence = portal.call(_all_absent, resources, sorted(sandbox_ids))
            assert absence and all(absence.values()), f"turn-owned sandboxes must be absent: {absence}"
            assert portal.call(_volume_exists, resources, settings.volume_name) is True
        finally:
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        "provider_failure",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "ending": "provider_failure",
            "terminal": {"category": "runtime_failure", "wire_terminal": "finish:error", "error_text": "Turn failed"},
            "sanitization": {"canary_absent_from_stream": True, "canary_absent_from_durable": True},
            "cleanup": {
                "turn_owned_absent": True,
                "admission_restored": True,
                "admission_permits": settings.max_active_daytona_leases,
                "lease_holder_released": True,
                "child_count": 0,
                "volume_intact": True,
            },
            "passed": True,
        },
    )


# ---------------------------------------------------------------------------
# Endings 3+4: cancellation and timeout (host-forced broker stall)
# ---------------------------------------------------------------------------


def _stall_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    ending: str,
    turn_timeout_seconds: int,
    hold_seconds: float,
    cancel: bool,
) -> None:
    settings = _case_settings(tmp_path, name=name, recursion=False, turn_timeout_seconds=turn_timeout_seconds)
    evidence = _EndingEvidence()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    cleanup_failures: tuple[str, ...] = ()
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        portal = client.portal
        preparation._models = RLMModelBundle(
            _OneCellLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": f"P41b {name} canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            def on_first() -> None:
                if not cancel:
                    return
                assert session_id is not None

                async def _mark_cancelled() -> str:
                    scope = LocalScope()
                    run_id = get_active_lease_registry().holder(session_id)
                    assert run_id is not None, "cancel proof requires an active Run lease"
                    lifecycle = inventory.run_lifecycle
                    return await lifecycle.request_cancel(
                        TurnAccess(scope.user_id, scope.workspace_id),
                        run_id,
                    )

                evidence.cancel_state = portal.call(_mark_cancelled)

            _block_first_root_execution(monkeypatch, evidence=evidence, on_first=on_first, hold_seconds=hold_seconds)
            started_at = time.perf_counter()
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": f"Run the bounded P41b {name} proof."},
                headers={"Idempotency-Key": f"p41b-{name}-{uuid4()}"},
            )
            elapsed = time.perf_counter() - started_at
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert evidence.block_calls >= 1, "the stall must land before the terminal"

            if cancel:
                assert evidence.cancel_state in {"requested", "already_requested"}
                abort = [chunk for chunk in chunks if chunk.get("type") == "abort"]
                assert len(abort) == 1
                assert chunks[-1] is abort[0]
                assert not [chunk for chunk in chunks if chunk.get("type") == "finish"]
            else:
                assert turn_timeout_seconds <= elapsed < turn_timeout_seconds + 240, f"elapsed={elapsed:.1f}"
                finish = [chunk for chunk in chunks if chunk.get("type") == "finish"]
                assert len(finish) == 1
                assert finish[0].get("finishReason") == "error"
                assert chunks[-1] is finish[0]
                errors = [chunk for chunk in chunks if chunk.get("type") == "error"]
                assert len(errors) == 1 and "timed out" in str(errors[0].get("errorText"))
                assert not [chunk for chunk in chunks if chunk.get("type") == "abort"]

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases)
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            assert sandbox_ids, "stalled ending must prove a turn-owned Root Sandbox existed"
            absence = portal.call(_all_absent, resources, sorted(sandbox_ids))
            assert all(absence.values()), f"turn-owned sandboxes must be absent: {absence}"
            assert portal.call(_volume_exists, resources, settings.volume_name) is True
        finally:
            sandbox_ids = _chained_sandbox_ids(
                portal=portal, resources=resources, session_id=session_id, evidence=evidence
            )
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        ending,
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "ending": ending,
            "terminal": {
                "category": ending,
                "wire_terminal": "abort" if cancel else "finish:error",
            },
            "cleanup": {
                "turn_owned_absent": True,
                "admission_restored": True,
                "admission_permits": settings.max_active_daytona_leases,
                "lease_holder_released": True,
                "child_count": 0,
                "volume_intact": True,
            },
            "passed": True,
        },
    )


def test_p41b_cancellation_terminal_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stall_scenario(
        tmp_path,
        monkeypatch,
        name="cancel",
        ending="cancellation",
        turn_timeout_seconds=600,
        hold_seconds=15.0,
        cancel=True,
    )


def test_p41b_timeout_terminal_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    turn_timeout_seconds = 150
    _stall_scenario(
        tmp_path,
        monkeypatch,
        name="timeout",
        ending="timeout",
        turn_timeout_seconds=turn_timeout_seconds,
        hold_seconds=turn_timeout_seconds + 60,
        cancel=False,
    )


# ---------------------------------------------------------------------------
# Ending 5: disconnect (real loopback server, httpx SSE client closes early)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p41b_disconnect_terminal_cleanup(tmp_path: Path) -> None:
    settings = _case_settings(tmp_path, name="disconnect", recursion=False, turn_timeout_seconds=600)
    app = create_app(settings=settings)
    port = 8020
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    session_id: UUID | None = None
    cleanup_failures: tuple[str, ...] | None = None
    resources: Any = None
    try:
        while not server.started:
            if serve_task.done():
                pytest.fail("uvicorn failed to start the loopback validator")
            await asyncio.sleep(0.05)
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        preparation._models = RLMModelBundle(
            _StallThenSubmitLM(),
            dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
        )
        base = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(30.0, read=None)) as client:
                created = await client.post("/api/sessions", json={"title": "P41b disconnect canary"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                seen: list[str] = []
                async with client.stream(
                    "POST",
                    f"/api/sessions/{session_id}/turns",
                    json={"text": "Run the bounded P41b disconnect proof."},
                    headers={"Idempotency-Key": f"p41b-disconnect-{uuid4()}"},
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            seen.append(line)
                        if len(seen) >= 8:
                            break
                    # Leaving the context closes the stream mid-Turn: client disconnect.

            assert seen, "the client must observe streamed chunks before disconnecting"

            # Detached settlement: cancellation tombstone lands durably while the
            # turn-owned Sandbox is deleted and admission returns to baseline.
            binding = await resources.bindings.get(session_id)
            assert binding is not None and binding.sandbox_id is not None
            owned = {str(binding.sandbox_id)}

            permits = settings.max_active_daytona_leases
            deadline = time.perf_counter() + 300
            while time.perf_counter() < deadline:
                holder = get_active_lease_registry().holder(session_id)
                if holder is None and resources.daytona_admission._semaphore._value == permits:
                    absence = await _all_absent(resources, sorted(owned))
                    if all(absence.values()):
                        break
                await asyncio.sleep(1.0)
            else:
                pytest.fail("disconnected Turn did not settle within the bounded window")

            tombstone_seen = False
            async with httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(30.0, read=30.0)) as client:
                for _ in range(120):
                    page = await client.get(f"/api/sessions/{session_id}/turns")
                    assert page.status_code == 200
                    items = page.json()["items"]
                    assistant = [item for item in items if item["role"] == "assistant"]
                    if assistant:
                        parts = assistant[-1]["parts"]
                        status_parts = [part for part in parts if part["type"] == "status"]
                        if any(
                            part.get("phase") == "cancelled" and part.get("status") == "cancelled"
                            for part in status_parts
                        ):
                            tombstone_seen = True
                            break
                    await asyncio.sleep(1.0)
            assert tombstone_seen, "the disconnected Turn must land the cancellation tombstone durably"
            assert get_active_lease_registry().holder(session_id) is None
            assert resources.daytona_admission._semaphore._value == permits
            assert all((await _all_absent(resources, sorted(owned))).values())
            assert await _volume_exists(resources, settings.volume_name) is True

            owned |= {str(item) for item in getattr(resources, "_sandbox_ids", set())}
            cleanup_failures = await _strict_cleanup(resources, owned, settings.volume_name)
        finally:
            server.should_exit = True
            await asyncio.wait_for(serve_task, timeout=30)
    finally:
        if cleanup_failures is None and resources is not None:
            candidate_ids = {str(item) for item in getattr(resources, "_sandbox_ids", set())}
            cleanup_failures = await _strict_cleanup(resources, candidate_ids, settings.volume_name)
    assert cleanup_failures == ()
    _write_receipt(
        "disconnect",
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "ending": "disconnect",
            "terminal": {"category": "cancellation", "wire_terminal": "client_disconnect", "durable": "cancelled"},
            "cleanup": {
                "turn_owned_absent": True,
                "admission_restored": True,
                "admission_permits": settings.max_active_daytona_leases,
                "lease_holder_released": True,
                "child_count": 0,
                "volume_intact": True,
            },
            "passed": True,
        },
    )


# ---------------------------------------------------------------------------
# Aggregate same-SHA proof
# ---------------------------------------------------------------------------


def test_p41b_terminal_cleanup_aggregate() -> None:
    if not _live_enabled():
        pytest.skip("FLEET_LIVE=1 required")
    head = candidate_identity().get("sha")
    endings: dict[str, Any] = {}
    for ending in _ENDINGS:
        path = _RECEIPT_DIR / f"p41b-terminal-{ending}.json"
        assert path.is_file(), f"missing {ending} receipt: run the p41b live lane serially"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == _RECEIPT_SCHEMA
        assert payload["candidate"].get("sha") == head, (
            f"{ending} receipt SHA {payload['candidate'].get('sha')} != HEAD {head}; "
            "archive stale p41b receipts (move, never delete) and re-run at HEAD"
        )
        assert payload["passed"] is True
        endings[ending] = {
            "terminal": payload["terminal"],
            "cleanup": payload["cleanup"],
        }
        cleanup = payload["cleanup"]
        assert cleanup["turn_owned_absent"] is True
        assert cleanup["admission_restored"] is True
        assert cleanup["lease_holder_released"] is True
        assert cleanup["volume_intact"] is True

    write_lane_receipt(
        "p41b-terminal-cleanup-proof.json",
        "-p41b-terminal-cleanup-proof",
        {
            "schema": _AGGREGATE_SCHEMA,
            "candidate": candidate_identity(),
            "endings": endings,
            "all_same_sha": True,
            "passed": True,
        },
    )
