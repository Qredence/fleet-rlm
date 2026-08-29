"""P39c live certification: shared Volume preservation across recursive outcomes.

Serial, credentialed, ``FLEET_LIVE=1``-only proof (VAL-REC-039, VAL-RLM-064)
using deterministic scripted LMs. Before recursive execution, checksum markers
are written in the Root Workspace scope and (after the first Run) in an
unaffected recursive sibling scope. Four child outcomes then run against the
SAME shared Volume in one lane:

1. successful child cleanup,
2. fault-injected absence-confirmation failure (child cleanup fails closed),
3. cancellation while the child executes,
4. Turn deadline while the child executes.

After every outcome both markers remain byte-identical (same SHA-256), the
attempted child scope is purged, the Volume id never changes, and after all
product Sandboxes are confirmed absent the Volume is still readable through
the validator-owned scratch Sandbox until the validator's separately owned
final cleanup deletes it. Sibling-child distinctness and interpreter namespace
isolation are certified by the batch and root-flow lanes on the same SHA.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from fastapi.testclient import TestClient

from fleet_rlm.api.local_scope import LocalScope
from fleet_rlm.app import create_app
from fleet_rlm.config.settings import Settings
from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.daytona.broker import DaytonaHttpToolBroker
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.sessions.models import TurnAccess
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import record_observed_sandbox_ids, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import _wait_for_admission_baseline

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(2400)]

_RECEIPT_SCHEMA = "fleet.p39c-volume-preservation/v1"
_ROOT_MARKER = "p39c-vol-root-marker.txt"
_SIBLING_MARKER = "p39c-vol-sibling-marker.txt"
_HOLD_SECONDS = 12.0


def _child_lm(token: str, answer: str) -> dspy.utils.DummyLM:
    return dspy.utils.DummyLM(
        {token: {"reasoning": "bounded volume child", "code": f"SUBMIT(answer='{answer}')"}},
        adapter=dspy.JSONAdapter(),
    )


def _single_child_root_lm(prompt_token: str) -> dspy.utils.DummyLM:
    class _Root(dspy.utils.DummyLM):
        def __init__(self) -> None:
            super().__init__(
                [
                    {
                        "reasoning": "delegate exactly one child",
                        "code": f"child = rlm_query(prompt='{prompt_token} bounded volume subproblem')",
                    },
                    {
                        "reasoning": "submit",
                        "code": "SUBMIT(answer='P39C-VOL-ROOT ' + child)",
                    },
                ],
                adapter=dspy.JSONAdapter(),
            )

        def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
            del kwargs
            return _child_lm(prompt_token, f"P39C-VOL-ANSWER-{prompt_token}")

    return _Root()


def _no_submit_root_lm(prompt_token: str) -> dspy.utils.DummyLM:
    class _Root(dspy.utils.DummyLM):
        def __init__(self) -> None:
            super().__init__(
                [
                    {
                        "reasoning": "delegate exactly one child and stop",
                        "code": f"child = rlm_query(prompt='{prompt_token} bounded volume subproblem')",
                    },
                ],
                adapter=dspy.JSONAdapter(),
            )

        def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
            del kwargs
            return _child_lm(prompt_token, f"P39C-VOL-ANSWER-{prompt_token}")

    return _Root()


@dataclass
class _VolumeEvidence:
    child_sandbox_ids: list[str] = field(default_factory=list)
    scenario_for_child: list[str] = field(default_factory=list)
    current_scenario: str = ""
    fault_armed: bool = False
    cleanup_errors: list[str] = field(default_factory=list)
    receipts: list[dict[str, object]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)


def _case_settings(tmp_path: Path, *, turn_timeout_seconds: int) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p39c-volume.db').resolve()}",
            "volume_name": f"fleet-rlm-p39c-volume-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 1,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 1,
            "rlm_recursion_child_max_llm_calls": 1,
            "rlm_recursion_max_parallel_children": 1,
            "rlm_max_iters": 2,
            "rlm_max_llm_calls": 4,
            "turn_timeout_seconds": turn_timeout_seconds,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


def _settings_with_timeout(settings: Settings, *, turn_timeout_seconds: int) -> Settings:
    return settings.model_copy(update={"turn_timeout_seconds": turn_timeout_seconds})


def _install_volume_evidence(monkeypatch: pytest.MonkeyPatch, evidence: _VolumeEvidence) -> None:
    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_cleanup = recursive_child_runtime.cleanup_child_runtime_async

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        with evidence._lock:
            evidence.child_sandbox_ids.append(lease.sandbox_id)
            evidence.scenario_for_child.append(evidence.current_scenario)
        return lease

    async def faultable_cleanup(**kwargs: Any) -> None:
        from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError

        if evidence.fault_armed:
            from fleet_rlm.daytona.lifecycle import AbsenceProbeError

            async def failing_confirm(**confirm_kwargs: Any) -> Any:
                return AbsenceProbeError(
                    sandbox_id=str(confirm_kwargs.get("sandbox_id")),
                    error="fault-injected absence confirmation failure",
                    observations=("fault_injected",),
                    duration_s=0.0,
                )

            kwargs["confirm"] = failing_confirm
        try:
            await original_cleanup(**kwargs)
        except ChildRuntimeCleanupError as exc:
            evidence.cleanup_errors.append(str(exc)[:240])
            raise

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "cleanup_child_runtime_async", faultable_cleanup)


def _block_child_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence: _VolumeEvidence,
    on_blocked: Any,
    hold_seconds: float,
) -> None:
    """Block every CHILD broker execution for the current scenario once."""
    original = DaytonaHttpToolBroker.execute_code
    lock = threading.Lock()
    blocked_scenarios: set[str] = set()

    def blocking(
        self: DaytonaHttpToolBroker,
        code: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout_s: float = 130.0,
        on_stdout: Any | None = None,
    ) -> Any:
        broker_sandbox = getattr(self, "_sandbox", None)
        # The DSPy worker seam wraps the async SDK sandbox in a sync view whose
        # ``_sandbox`` attribute holds the object carrying the provider ``id``.
        if broker_sandbox is not None and not hasattr(broker_sandbox, "id"):
            broker_sandbox = getattr(broker_sandbox, "_sandbox", broker_sandbox)
        sandbox_id = str(getattr(broker_sandbox, "id", "") or "")
        with lock:
            is_child = sandbox_id in evidence.child_sandbox_ids
            scenario = evidence.current_scenario
            first_block = is_child and scenario not in blocked_scenarios
            if first_block:
                blocked_scenarios.add(scenario)
        if first_block:
            on_blocked()
            time.sleep(hold_seconds)
            raise TimeoutError("p39c host-forced volume-lane child stall")
        return original(self, code, variables, timeout_s=timeout_s, on_stdout=on_stdout)

    monkeypatch.setattr(DaytonaHttpToolBroker, "execute_code", blocking)


def _write_receipt(payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt("p39c-volume-preservation.json", "-p39c-volume", payload)


async def _absent(resources: Any, sandbox_id: str) -> bool:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        target = await resources.platform.get(sandbox_id)
        if target is None:
            return True
        state = str(getattr(getattr(target, "state", None), "value", getattr(target, "state", None)) or "")
        if state.strip().lower() in {"destroyed", "deleted"}:
            return True
        await asyncio.sleep(1.0)
    return await resources.platform.get(sandbox_id) is None


async def _delete_volume_with_grace(resources: Any, volume_name: str, grace_seconds: float) -> tuple[str, ...]:
    """Retry Volume deletion while the provider detaches the last Sandboxes."""
    deadline = time.monotonic() + grace_seconds
    while True:
        try:
            volume = await resources.client.volume.get(volume_name, create=False)
            if volume is None:
                return ()
            await resources.client.volume.delete(volume)
            return ()
        except Exception:
            if time.monotonic() >= deadline:
                return ("volume",)
            await asyncio.sleep(10.0)


async def _write_marker(sandbox: Any, mount_path: str, rel_path: str, content: str) -> str:
    target = f"{mount_path.rstrip('/')}/{rel_path.lstrip('/')}"
    await sandbox.fs.upload_file(content.encode("utf-8"), target)
    readback = await sandbox.fs.download_file(target)
    return hashlib.sha256(readback).hexdigest()


async def _read_marker_sha(sandbox: Any, mount_path: str, rel_path: str) -> str | None:
    target = f"{mount_path.rstrip('/')}/{rel_path.lstrip('/')}"
    try:
        data = await sandbox.fs.download_file(target)
    except Exception:
        return None
    return hashlib.sha256(data).hexdigest()


async def _list_files(sandbox: Any, mount_path: str) -> list[str]:
    code = (
        "import json, os\n"
        f"root = {mount_path!r}\n"
        "found = []\n"
        "if os.path.isdir(root):\n"
        "    for dirpath, _dirnames, filenames in os.walk(root):\n"
        "        for name in filenames:\n"
        "            found.append(os.path.relpath(os.path.join(dirpath, name), root))\n"
        "print(json.dumps(sorted(found)))"
    )
    result = await sandbox.process.code_run(code, timeout=60)
    if getattr(result, "exit_code", 1) != 0:
        return []
    try:
        return [str(item) for item in json.loads(str(getattr(result, "result", "[]")).strip())]
    except json.JSONDecodeError:
        return []


def test_live_volume_preservation_across_all_child_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-REC-039/VAL-RLM-064: markers survive success, failure, cancel, deadline."""
    base_settings = _case_settings(tmp_path, turn_timeout_seconds=840)
    evidence = _VolumeEvidence()
    _install_volume_evidence(monkeypatch, evidence)
    mount_path = base_settings.volume_mount_path or "/home/daytona/fleet"
    workspace_subpath = f"workspaces/{LocalScope().workspace_id}"
    volume_id = ""
    scratch_sandbox_id = ""
    sandbox_ids: set[str] = set()
    root_marker_pre = ""
    sibling_marker_pre = ""
    scenario_checks: dict[str, dict[str, object]] = {}
    sibling_subpath = ""
    run_ids: dict[str, str] = {}
    child_by_scenario: dict[str, str] = {}
    scenario_session_ids: set[str] = set()

    def run_scenario(
        *,
        name: str,
        settings: Settings,
        root_lm: dspy.utils.DummyLM,
        expect_terminal: str,
        cancel: bool = False,
        fault: bool = False,
    ) -> tuple[Any, UUID, list[dict[str, Any]]]:
        """Run one Turn scenario in a fresh app on the shared Volume."""
        nonlocal volume_id, scratch_sandbox_id, root_marker_pre, sibling_marker_pre, sibling_subpath
        evidence.current_scenario = name
        evidence.fault_armed = fault
        app = create_app(settings=settings)
        session_id: UUID | None = None
        scenario_sandbox_ids: set[str] = set()
        chunks: list[dict[str, Any]] = []
        with TestClient(app) as client:
            inventory = app.state.runtime_inventory
            resources = inventory.run_environment_resources
            preparation = inventory.run_preparation
            assert resources is not None
            assert preparation is not None
            portal = client.portal
            assert portal is not None
            preparation._models = RLMModelBundle(
                root_lm,
                dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
            )
            try:
                created = client.post("/api/sessions", json={"title": f"P39c volume {name} canary"})
                assert created.status_code == 201
                session_id = UUID(created.json()["id"])

                # First scenario bootstraps the shared Volume and both markers.
                if name == "success":
                    volume = portal.call(lambda: resources.volume_client.get(settings.volume_name, create=True))
                    volume_id = str(getattr(volume, "id", "") or "")
                    assert volume_id
                    scratch = portal.call(
                        lambda: resources.platform.create(
                            volume_id=volume_id,
                            mount_path=mount_path,
                            volume_subpath=workspace_subpath,
                            ephemeral=True,
                            labels={"fleet.p39c": "volume-marker-scratch"},
                        )
                    )
                    scratch_sandbox_id = str(getattr(scratch, "id", ""))
                    sandbox_ids.add(scratch_sandbox_id)
                    root_marker_pre = portal.call(
                        lambda: _write_marker(scratch, mount_path, _ROOT_MARKER, f"{_ROOT_MARKER}:{uuid4()}")
                    )

                if cancel:
                    cancel_state: dict[str, Any] = {}

                    def request_cancel() -> None:
                        run_id = get_active_lease_registry().holder(session_id)
                        assert run_id is not None

                        async def _mark_cancelled() -> str:
                            scope = LocalScope()
                            lifecycle = inventory.run_lifecycle
                            assert lifecycle is not None
                            return await lifecycle.request_cancel(
                                TurnAccess(scope.user_id, scope.workspace_id),
                                run_id,
                            )

                        cancel_state["state"] = portal.call(_mark_cancelled)

                    _block_child_execution(
                        monkeypatch,
                        evidence=evidence,
                        on_blocked=request_cancel,
                        hold_seconds=_HOLD_SECONDS,
                    )
                elif name == "deadline":
                    _block_child_execution(
                        monkeypatch,
                        evidence=evidence,
                        on_blocked=lambda: None,
                        hold_seconds=settings.turn_timeout_seconds + 25,
                    )

                response = client.post(
                    f"/api/sessions/{session_id}/turns",
                    json={"text": f"Run the bounded P39c volume {name} proof."},
                    headers={"Idempotency-Key": f"p39c-volume-{name}-{uuid4()}"},
                )
                assert response.status_code == 200
                chunks, done = _sse_chunks(response)
                assert done == 1
                last = chunks[-1]
                if expect_terminal == "stop":
                    assert last.get("type") == "finish" and last.get("finishReason") == "stop"
                elif expect_terminal == "abort":
                    assert last.get("type") == "abort"
                else:
                    assert last.get("type") == "finish" and last.get("finishReason") == "error"
                run_id = str(next(chunk["messageId"] for chunk in chunks if chunk.get("type") == "start"))
                run_ids[name] = run_id
                if cancel:
                    assert cancel_state.get("state") in {"requested", "already_requested"}

                # The scenario acquired exactly one child Sandbox.
                scenario_children = [
                    sandbox_id
                    for sandbox_id, scenario in zip(
                        evidence.child_sandbox_ids, evidence.scenario_for_child, strict=True
                    )
                    if scenario == name
                ]
                assert len(scenario_children) == 1
                child_by_scenario[name] = scenario_children[0]

                _wait_for_admission_baseline(
                    resources, session_id, permits=settings.max_active_daytona_leases, portal=portal
                )
                assert get_active_lease_registry().holder(session_id) is None
                binding = portal.call(resources.bindings.get, session_id)
                if binding is not None and binding.sandbox_id is not None:
                    scenario_sandbox_ids.add(binding.sandbox_id)
                scenario_sandbox_ids.update(scenario_children)
                scenario_sandbox_ids.update(resources._sandbox_ids)
                # The scratch marker Sandbox is validator-owned: never hand it
                # to a scenario's strict cleanup.
                scenario_sandbox_ids.discard(scratch_sandbox_id)
                sandbox_ids.update(scenario_sandbox_ids)

                # Post-scenario verification through validator-owned Sandboxes:
                # the Root marker is unchanged and the attempted child scope is
                # purged (no regular files remain under it).
                scratch = portal.call(resources.platform.get, scratch_sandbox_id)
                assert scratch is not None
                root_marker_post = portal.call(lambda: _read_marker_sha(scratch, mount_path, _ROOT_MARKER))
                child_scope_subpath = f"recursive/{LocalScope().workspace_id}/{run_id}/1"
                child_scope_sandbox = portal.call(
                    lambda: resources.platform.create(
                        volume_id=volume_id,
                        mount_path=mount_path,
                        volume_subpath=child_scope_subpath,
                        ephemeral=True,
                        labels={"fleet.p39c": "volume-scope-check"},
                    )
                )
                child_scope_sandbox_id = str(getattr(child_scope_sandbox, "id", ""))
                sandbox_ids.add(child_scope_sandbox_id)
                scope_files = portal.call(lambda: _list_files(child_scope_sandbox, mount_path))
                portal.call(resources.platform.delete, child_scope_sandbox_id)

                # After the first Run, write the unaffected sibling-scope marker.
                if name == "success":
                    sibling_subpath = f"recursive/{LocalScope().workspace_id}/{run_id}/2"
                    sibling_scratch = portal.call(
                        lambda: resources.platform.create(
                            volume_id=volume_id,
                            mount_path=mount_path,
                            volume_subpath=sibling_subpath,
                            ephemeral=True,
                            labels={"fleet.p39c": "volume-marker-scratch"},
                        )
                    )
                    sibling_scratch_id = str(getattr(sibling_scratch, "id", ""))
                    sandbox_ids.add(sibling_scratch_id)
                    sibling_marker_pre = portal.call(
                        lambda: _write_marker(
                            sibling_scratch, mount_path, _SIBLING_MARKER, f"{_SIBLING_MARKER}:{uuid4()}"
                        )
                    )
                    portal.call(resources.platform.delete, sibling_scratch_id)

                sibling_marker_post = None
                if sibling_subpath:
                    sibling_scratch = portal.call(
                        lambda: resources.platform.create(
                            volume_id=volume_id,
                            mount_path=mount_path,
                            volume_subpath=sibling_subpath,
                            ephemeral=True,
                            labels={"fleet.p39c": "volume-marker-check"},
                        )
                    )
                    sibling_scratch_id = str(getattr(sibling_scratch, "id", ""))
                    sandbox_ids.add(sibling_scratch_id)
                    sibling_marker_post = portal.call(
                        lambda: _read_marker_sha(sibling_scratch, mount_path, _SIBLING_MARKER)
                    )
                    portal.call(resources.platform.delete, sibling_scratch_id)

                scenario_checks[name] = {
                    "root_marker_pre": root_marker_pre,
                    "root_marker_post": root_marker_post,
                    "sibling_marker_pre": sibling_marker_pre or None,
                    "sibling_marker_post": sibling_marker_post,
                    "attempted_child_scope": child_scope_subpath,
                    "attempted_child_scope_files": scope_files,
                    "child_sandbox_id": scenario_children[0],
                }
                assert root_marker_post == root_marker_pre
                if sibling_marker_pre:
                    assert sibling_marker_post == sibling_marker_pre
                assert scope_files == []

                # Scenario cleanup is intentionally NOT run here: the shared
                # Volume must survive every scenario. Product Sandboxes were
                # already deleted by the product cleanup law; the validator's
                # final cleanup owns the Volume and scratch Sandboxes.
            finally:
                # Release scenario resources without deleting the shared Volume.
                async def _release_scenario() -> None:
                    for scenario_sandbox_id in sorted(scenario_sandbox_ids):
                        try:
                            target = await resources.platform.get(scenario_sandbox_id)
                            if target is not None:
                                await resources.platform.delete(scenario_sandbox_id)
                        except Exception:
                            pass

                portal.call(_release_scenario)
                portal.call(resources.client.close)
        evidence.fault_armed = False
        scenario_session_ids.add(str(session_id))
        return resources, session_id, chunks

    run_scenario(
        name="success",
        settings=base_settings,
        root_lm=_single_child_root_lm("P39C-VOL-A"),
        expect_terminal="stop",
    )
    run_scenario(
        name="failure",
        settings=_settings_with_timeout(base_settings, turn_timeout_seconds=840),
        root_lm=_no_submit_root_lm("P39C-VOL-B"),
        expect_terminal="error",
        fault=True,
    )
    assert any("cleanup failed" in text for text in evidence.cleanup_errors)
    run_scenario(
        name="cancel",
        settings=_settings_with_timeout(base_settings, turn_timeout_seconds=840),
        root_lm=_single_child_root_lm("P39C-VOL-C"),
        expect_terminal="abort",
        cancel=True,
    )
    # The deadline scenario needs live-provider acquisition headroom: the
    # stall must start before the Turn timeout fires (180s matches the p35d
    # canary for this same race and the cancel-deadline lane).
    deadline_settings = _settings_with_timeout(base_settings, turn_timeout_seconds=180)
    run_scenario(
        name="deadline",
        settings=deadline_settings,
        root_lm=_single_child_root_lm("P39C-VOL-D"),
        expect_terminal="error",
    )

    # Final certification: every scenario child Sandbox is provider-side
    # absent while the shared Volume remains present and readable through the
    # validator-owned scratch Sandbox, until the validator's separately owned
    # final cleanup.
    with TestClient(create_app(settings=base_settings)) as client:
        inventory = client.app.state.runtime_inventory
        resources = inventory.run_environment_resources
        portal = client.portal
        assert resources is not None and portal is not None
        for name, child_id in child_by_scenario.items():
            assert portal.call(_absent, resources, child_id), f"{name} child Sandbox not absent"
        volume = portal.call(lambda: resources.volume_client.get(base_settings.volume_name, create=False))
        assert volume is not None
        assert str(getattr(volume, "id", "")) == volume_id
        scratch = portal.call(resources.platform.get, scratch_sandbox_id)
        assert scratch is not None
        final_root_marker = portal.call(lambda: _read_marker_sha(scratch, mount_path, _ROOT_MARKER))
        assert final_root_marker == root_marker_pre
        final_sibling_marker = None
        if sibling_subpath:
            sibling_scratch = portal.call(
                lambda: resources.platform.create(
                    volume_id=volume_id,
                    mount_path=mount_path,
                    volume_subpath=sibling_subpath,
                    ephemeral=True,
                    labels={"fleet.p39c": "volume-final-check"},
                )
            )
            sandbox_ids.add(str(getattr(sibling_scratch, "id", "")))
            final_sibling_marker = portal.call(lambda: _read_marker_sha(sibling_scratch, mount_path, _SIBLING_MARKER))
            portal.call(resources.platform.delete, str(getattr(sibling_scratch, "id", "")))
        assert final_sibling_marker == sibling_marker_pre
        # Validator's separately owned final cleanup: scratch Sandboxes, then
        # the shared Volume itself. The Volume deletion can race the provider
        # detaching the last deleted Sandbox, so it gets a bounded grace retry.
        cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, base_settings.volume_name)
        if cleanup_failures == ("volume",):
            cleanup_failures = portal.call(_delete_volume_with_grace, resources, base_settings.volume_name, 90.0)
        portal.call(resources.client.close)
    assert cleanup_failures == ()
    record_observed_sandbox_ids("volume-preservation", sandbox_ids, scenario_session_ids)
    _write_receipt(
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "volume-preservation",
            "volume": {"id": volume_id, "name": base_settings.volume_name},
            "logical_scopes": {
                "root_workspace": workspace_subpath,
                "recursive_sibling": sibling_subpath,
                "attempted_child_scopes": {
                    name: check["attempted_child_scope"] for name, check in scenario_checks.items()
                },
            },
            "markers": {
                "root_workspace": {
                    "path": _ROOT_MARKER,
                    "pre_sha256": root_marker_pre,
                    "per_scenario_post_sha256": {
                        name: check["root_marker_post"] for name, check in scenario_checks.items()
                    },
                    "final_sha256": final_root_marker,
                },
                "recursive_sibling": {
                    "path": _SIBLING_MARKER,
                    "pre_sha256": sibling_marker_pre,
                    "per_scenario_post_sha256": {
                        name: check["sibling_marker_post"] for name, check in scenario_checks.items()
                    },
                    "final_sha256": final_sibling_marker,
                },
            },
            "child_purge_listings": {
                name: check["attempted_child_scope_files"] for name, check in scenario_checks.items()
            },
            "child_sandboxes": child_by_scenario,
            "run_ids": run_ids,
            "assertions": {
                "markers_unchanged_all_outcomes": True,
                "volume_id_unchanged": True,
                "attempted_child_scopes_purged": True,
                "volume_readable_after_all_sandboxes_absent": True,
                "no_cross_sandbox_workspace_memory_coordination_claim": True,
            },
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
