"""P39c live certification: end-to-end Root recursion flow on the real provider.

Serial, credentialed, ``FLEET_LIVE=1``-only proof using deterministic scripted
LMs (protocol-stable per the mission AGENTS.md known pre-existing issue note).
One session runs two Turns on one SHA:

- Turn 1 (VAL-CROSS-004, VAL-REC-034, VAL-REC-038): Root delegates exactly one
  native depth-1 child; the child requests deeper recursion and is served by
  the bounded Sub-LM fallback without any grandchild Sandbox; Root stages an
  Artifact Candidate and an autonomous Memory candidate; success settles only
  after child cleanup (delete + confirmed absence + admission restore) and the
  wire order is ``data-artifact`` then exactly one ``finish``/stop; post-commit
  Memory promotion settles without another terminal.
- Turn 2: a later public Turn reads Workspace Memory and observes exactly the
  post-commit promoted candidate.

The same lane records VAL-REC-038 provider deletion evidence (delete-request
acceptance distinct from eventual confirmed absence, tied to exact Sandbox
ids) and VAL-REC-039/VAL-RLM-064 shared-Volume preservation evidence: a
Root-Workspace marker written before Turn 1 and an unaffected recursive
sibling-scope marker written after Turn 1 both survive Turn 2's Volume access
unchanged, the attempted child scope is purged, and the shared Volume stays
readable through validator-owned Sandboxes after all product compute settled.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from fleet_rlm.daytona.broker import sync_sandbox
from fleet_rlm.daytona.sandbox_lease import SandboxLeaseReceipt
from fleet_rlm.daytona.session_manager import get_active_lease_registry
from fleet_rlm.rlm.program import RLMModelBundle
from fleet_rlm.runtime.bindings import workspace_volume_subpath
from fleet_rlm.workspace.paths import volume_paths_from_settings
from tests.live.backend._database import upgrade_to_head
from tests.live.backend._p35d_evidence import candidate_identity
from tests.live.backend._p39c_evidence import record_observed_sandbox_ids, write_lane_receipt
from tests.live.backend.test_fleet_rlm_daytona_mvp import _live_settings, _sse_chunks, _strict_cleanup
from tests.live.backend.test_p39a_child_cleanup_ownership_live import (
    _receipt_projection,
    _wait_for_admission_baseline,
)

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(1800)]

_RECEIPT_SCHEMA = "fleet.p39c-root-flow/v1"
_ROOT_TOKEN = "P39C-ROOT-TOKEN"
_CHILD_TOKEN = "P39C-CHILD-OK"
_NESTED_TOKEN = "P39C-NESTED-FALLBACK"
_MEM_TOKEN = "P39C-MEM-TOKEN"
_ART_TOKEN = "P39C-ART-TOKEN"
_ROOT_MARKER_NAME = "p39c-root-marker.txt"
_SIBLING_MARKER_NAME = "p39c-sibling-marker.txt"
_SCOPE_LABELS = {"fleet.p39c": "marker"}


def _make_child_lm() -> dspy.utils.DummyLM:
    """Depth-1 child: one cell that requests deeper recursion, proves namespace
    isolation from the Root, and typed-SUBMITs the bounded answer.

    The child's own ``rlm_query`` call targets depth 2, which the policy serves
    through the bounded Sub-LM fallback (no grandchild Sandbox). The child
    interpreter is fresh: the Root's ``root_marker`` must be absent from its
    globals while the nested fallback answer is present.
    """
    code = (
        "nested = rlm_query(prompt='P39C-NESTED-TASK resolve the nested subproblem')\n"
        f"SUBMIT(answer='{_CHILD_TOKEN} isolated=' + str('root_marker' not in globals())"
        " + ' nested=' + nested)"
    )
    return dspy.utils.DummyLM(
        {"P39C-CHILD-TASK": {"reasoning": "bounded child with nested fallback", "code": code}},
        adapter=dspy.JSONAdapter(),
    )


class _RootScriptedLM(dspy.utils.DummyLM):
    """Root Turn 1: marker, delegate once, propose memory, stage artifact, SUBMIT."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "mark Root state and delegate exactly one native child",
                    "code": (
                        f"root_marker = '{_ROOT_TOKEN}-MARKER'\n"
                        "child_answer = rlm_query(prompt='P39C-CHILD-TASK bounded child with nested subproblem')"
                    ),
                },
                {
                    "reasoning": "stage the autonomous memory candidate",
                    "code": (
                        f"memory_result = propose_memory(key_learning='{_MEM_TOKEN} operator prefers certified "
                        "recursion receipts', category='operator preference')"
                    ),
                },
                {
                    "reasoning": "stage the artifact candidate",
                    "code": (
                        "artifact_result = create_artifact(kind='markdown', "
                        f"content='# {_ART_TOKEN} recursion proof\\n\\nchild=' + child_answer, "
                        "title='P39c Recursion Proof')"
                    ),
                },
                {
                    "reasoning": "submit the bounded root answer",
                    "code": (f"SUBMIT(answer='{_ROOT_TOKEN} root_marker=' + root_marker + ' child=' + child_answer)"),
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _make_child_lm()


class _ReadbackRootLM(dspy.utils.DummyLM):
    """Root Turn 2: one Workspace Memory search, then typed SUBMIT."""

    def __init__(self) -> None:
        super().__init__(
            [
                {
                    "reasoning": "read workspace memory",
                    "code": f"found = search_memories(query='{_MEM_TOKEN} certified recursion receipts', limit=8)",
                },
                {
                    "reasoning": "submit the readback answer",
                    "code": "SUBMIT(answer='P39C-MEM-READBACK')",
                },
            ],
            adapter=dspy.JSONAdapter(),
        )

    def copy(self, **kwargs: Any) -> dspy.utils.DummyLM:
        del kwargs
        return _make_child_lm()


@dataclass
class _FlowEvidence:
    child_sandbox_ids: list[str] = field(default_factory=list)
    child_subpaths: list[str] = field(default_factory=list)
    call_indexes: list[int] = field(default_factory=list)
    receipts: list[dict[str, object]] = field(default_factory=list)
    confirmations: list[dict[str, object]] = field(default_factory=list)
    delete_acceptance_seconds: dict[str, float] = field(default_factory=dict)
    platform_creates: list[dict[str, object]] = field(default_factory=list)


def _case_settings(tmp_path: Path) -> Settings:
    settings = _live_settings(tmp_path).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'p39c-root-flow.db').resolve()}",
            "volume_name": f"fleet-rlm-p39c-root-flow-{uuid4()}",
            "rlm_recursion_enabled": True,
            "rlm_recursion_max_calls": 2,
            "rlm_recursion_max_prompt_chars": 2_000,
            "rlm_recursion_child_max_iters": 2,
            "rlm_recursion_child_max_llm_calls": 3,
            "rlm_max_iters": 4,
            "rlm_max_llm_calls": 6,
            "rlm_autonomous_memory_categories": ("operator preference",),
            "turn_timeout_seconds": 840,
            "run_stale_after_seconds": 600,
            "mlflow_tracing_enabled": False,
        }
    )
    upgrade_to_head(settings.database_url or "")
    return settings


def _install_flow_evidence(
    monkeypatch: pytest.MonkeyPatch,
    evidence: _FlowEvidence,
    *,
    resources: Any,
) -> None:
    """Observe child acquisition/close receipts, deletion acceptance vs absence, and creates."""
    original_acquire = recursive_child_runtime._acquire_child_runtime
    original_lease = recursive_child_runtime.SandboxLease
    original_confirm = recursive_child_runtime.confirm_absence
    original_platform_delete = resources.platform.delete
    original_platform_create = resources.platform.create

    async def observed_acquire(**kwargs: Any) -> Any:
        lease = await original_acquire(**kwargs)
        evidence.child_sandbox_ids.append(lease.sandbox_id)
        evidence.child_subpaths.append(lease.volume_subpath)
        evidence.call_indexes.append(int(kwargs["call_index"]))
        return lease

    class RecordingLease(original_lease):  # type: ignore[misc, valid-type]
        async def aclose(self) -> SandboxLeaseReceipt:
            receipt = await super().aclose()
            evidence.receipts.append(_receipt_projection(receipt))
            return receipt

    async def recording_confirm(**kwargs: Any) -> Any:
        probe = kwargs["probe"]
        sandbox_id = str(kwargs["sandbox_id"])

        def _raw_state(target: Any) -> str:
            raw = getattr(target, "state", None)
            if raw is None:
                raw = getattr(target, "status", None)
            return str(getattr(raw, "value", raw) or "").strip().lower()

        try:
            first_target = await probe(sandbox_id)
        except Exception as exc:
            first_state = f"probe_error:{type(exc).__name__}"
        else:
            first_state = "not_found" if first_target is None else (_raw_state(first_target) or "unknown")
        outcome = await original_confirm(**kwargs)
        evidence.confirmations.append(
            {
                "sandbox_id": sandbox_id,
                "immediate_post_delete_state": first_state,
                "absent": bool(getattr(outcome, "absent", False)),
                "observations": list(getattr(outcome, "observations", ())),
                "confirmation_duration_s": round(float(getattr(outcome, "duration_s", 0.0)), 3),
            }
        )
        return outcome

    async def recording_delete(sandbox_id: Any) -> None:
        key = sandbox_id if isinstance(sandbox_id, str) else str(getattr(sandbox_id, "id", sandbox_id))
        requested_at = time.monotonic()
        await original_platform_delete(sandbox_id)
        evidence.delete_acceptance_seconds[key] = round(time.monotonic() - requested_at, 3)

    async def recording_create(**kwargs: Any) -> Any:
        created = await original_platform_create(**kwargs)
        evidence.platform_creates.append(
            {
                "sandbox_id": str(getattr(created, "id", "")),
                "labels": {str(key): str(value) for key, value in (kwargs.get("labels") or {}).items()},
            }
        )
        return created

    monkeypatch.setattr(recursive_child_runtime, "_acquire_child_runtime", observed_acquire)
    monkeypatch.setattr(recursive_child_runtime, "SandboxLease", RecordingLease)
    monkeypatch.setattr(recursive_child_runtime, "confirm_absence", recording_confirm)
    monkeypatch.setattr(resources.platform, "delete", recording_delete)
    monkeypatch.setattr(resources.platform, "create", recording_create)


def _write_receipt(payload: dict[str, object]) -> None:
    """Write the canonical receipt; FLEET_LIVE_EVIDENCE_PATH adds a copy."""
    write_lane_receipt("p39c-root-flow.json", "-p39c-root-flow", payload)


async def _scratch_marker(
    resources: Any,
    *,
    volume_id: str,
    mount_path: str,
    subpath: str,
    rel_path: str,
    content: str,
    marker_label: str,
) -> tuple[str, str]:
    """Create one validator-owned scratch Sandbox on the shared Volume, write a
    marker file, and return ``(sandbox_id, sha256)``."""
    sandbox = await resources.platform.create(
        volume_id=volume_id,
        mount_path=mount_path,
        volume_subpath=subpath,
        ephemeral=True,
        labels={**_SCOPE_LABELS, "marker": marker_label},
    )
    target = f"{mount_path.rstrip('/')}/{rel_path.lstrip('/')}"
    await sandbox.fs.upload_file(content.encode("utf-8"), target)
    readback = await sandbox.fs.download_file(target)
    return str(sandbox.id), hashlib.sha256(readback).hexdigest()


async def _read_marker_sha(resources: Any, sandbox_id: str, mount_path: str, rel_path: str) -> str | None:
    sandbox = await resources.platform.get(sandbox_id)
    if sandbox is None:
        return None
    target = f"{mount_path.rstrip('/')}/{rel_path.lstrip('/')}"
    try:
        data = await sandbox.fs.download_file(target)
    except Exception:
        return None
    return hashlib.sha256(data).hexdigest()


async def _list_scope_files(resources: Any, sandbox_id: str, mount_path: str) -> list[str]:
    sandbox = await resources.platform.get(sandbox_id)
    if sandbox is None:
        return []
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


def _status_messages(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        chunk["data"]
        for chunk in chunks
        if chunk.get("type") == "data-status"
        and isinstance(chunk.get("data"), dict)
        and chunk["data"].get("phase") == "recursive"
    ]


def _recursive_completion(chunks: list[dict[str, Any]], *, depth: int) -> dict[str, Any] | None:
    for chunk in chunks:
        if chunk.get("type") != "tool-output-available":
            continue
        output = chunk.get("output")
        if isinstance(output, dict) and output.get("status") == "completed" and output.get("recursive_depth") == depth:
            return output
    return None


def _chunk_position(chunks: list[dict[str, Any]], predicate: Any) -> int:
    for index, chunk in enumerate(chunks):
        if predicate(chunk):
            return index
    return -1


def test_live_root_flow_settles_child_ownership_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VAL-CROSS-004/VAL-REC-034/VAL-REC-038/VAL-REC-039/VAL-RLM-064."""
    settings = _case_settings(tmp_path)
    evidence = _FlowEvidence()
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    session_id: UUID | None = None
    sandbox_ids: set[str] = set()
    root_sandbox_id = ""
    run1_id = ""
    run2_id = ""
    artifact_id = ""
    artifact_checksum = ""
    memory_id = ""
    volume_id = ""
    mount_path = settings.volume_mount_path or "/home/daytona/fleet"
    workspace_subpath = workspace_volume_subpath(LocalScope().workspace_id)
    root_marker_sha_pre = ""
    root_marker_sha_post = ""
    sibling_marker_sha_pre = ""
    sibling_marker_sha_post = ""
    attempted_child_scope_files: list[str] = []
    scratch_root_marker_sandbox = ""
    scratch_sibling_sandbox = ""
    scratch_child_scope_sandbox = ""
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        portal = client.portal
        assert portal is not None
        _install_flow_evidence(monkeypatch, evidence, resources=resources)
        preparation._models = RLMModelBundle(
            _RootScriptedLM(),
            dspy.utils.DummyLM([{"answer": _NESTED_TOKEN}], adapter=dspy.JSONAdapter()),
        )
        try:
            created = client.post("/api/sessions", json={"title": "P39c root flow certification"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])

            # Shared Volume markers BEFORE recursive execution: one Root
            # Workspace scope marker written through one validator-owned
            # scratch Sandbox mounted at the workspace scope.
            volume = portal.call(lambda: resources.volume_client.get(settings.volume_name, create=True))
            volume_id = str(getattr(volume, "id", "") or "")
            assert volume_id
            scratch_root_marker_sandbox, root_marker_sha_pre = portal.call(
                lambda: _scratch_marker(
                    resources,
                    volume_id=volume_id,
                    mount_path=mount_path,
                    subpath=workspace_subpath,
                    rel_path=_ROOT_MARKER_NAME,
                    content=f"{_ROOT_MARKER_NAME}:{uuid4()}",
                    marker_label="root-workspace",
                )
            )
            sandbox_ids.add(scratch_root_marker_sandbox)

            # ---- Turn 1: Root delegation -> child -> depth-2 fallback -> artifact + memory ----
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Run the bounded P39c recursion proof."},
                headers={"Idempotency-Key": f"p39c-root-flow-1-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            run1_id = str(next(chunk["messageId"] for chunk in chunks if chunk.get("type") == "start"))

            # Wire ordering: child ownership (depth-1 child_completed with
            # cleanup_status=completed) settles BEFORE Root stages the
            # Artifact Candidate, and data-artifact precedes the one terminal.
            pos_child_completed = _chunk_position(
                chunks,
                lambda chunk: (
                    chunk.get("type") == "data-status"
                    and chunk.get("data", {}).get("phase") == "recursive"
                    and chunk["data"].get("status") == "child_completed"
                    and "recursive_depth=1" in str(chunk["data"].get("message"))
                    and "cleanup_status=completed" in str(chunk["data"].get("message"))
                ),
            )
            pos_create_artifact = _chunk_position(
                chunks,
                lambda chunk: (
                    chunk.get("type") == "tool-input-available" and chunk.get("toolName") == "create_artifact"
                ),
            )
            pos_artifact = _chunk_position(chunks, lambda chunk: chunk.get("type") == "data-artifact")
            pos_finish = _chunk_position(chunks, lambda chunk: chunk.get("type") == "finish")
            if pos_child_completed == -1:
                import sys as _sys
                _sys.stderr.write('DEBUG-CHUNKS ' + json.dumps([c for c in chunks if c.get('type') != 'text-delta'], default=str)[:9000] + '\n')
            assert pos_child_completed != -1
            assert pos_create_artifact != -1
            assert pos_artifact != -1
            assert pos_finish != -1
            assert pos_child_completed < pos_create_artifact < pos_artifact < pos_finish

            # Child ownership settled before successful publication: exactly
            # one native child acquired, one clean close receipt with delete +
            # confirmed absence + admission restored after confirmed cleanup.
            assert len(evidence.child_sandbox_ids) == 1
            child_id = evidence.child_sandbox_ids[0]
            assert evidence.call_indexes == [1]
            assert len(evidence.receipts) == 1
            receipt = evidence.receipts[0]
            assert receipt["sandbox_id"] == child_id
            assert receipt["provider_action"] == "delete"
            assert receipt["provider_requested"] is True
            assert receipt["provider_confirmed_absent"] is True
            assert receipt["admission_released"] is True
            assert receipt["admission_released_after"] == "confirmed_cleanup"
            assert receipt["clean"] is True
            assert receipt["first_error"] is None

            # Exactly one terminal finish/stop; post-commit memory promotion
            # produced no second terminal or error frame.
            finish_chunks = [chunk for chunk in chunks if chunk.get("type") == "finish"]
            assert len(finish_chunks) == 1
            assert finish_chunks[0].get("finishReason") == "stop"
            assert chunks[-1] is finish_chunks[0]
            assert not [chunk for chunk in chunks if chunk.get("type") in {"abort", "error"}]

            artifact_chunks = [chunk for chunk in chunks if chunk.get("type") == "data-artifact"]
            assert len(artifact_chunks) == 1
            artifact_id = str(artifact_chunks[0]["data"].get("artifact_id", ""))
            artifact_checksum = str(artifact_chunks[0]["data"].get("checksum_sha256", ""))
            assert artifact_id and len(artifact_checksum) == 64

            # Recursive evidence: one native depth-1 child (typed submit) and
            # one bounded depth-2 Sub-LM fallback with no Sandbox.
            completion_depth1 = _recursive_completion(chunks, depth=1)
            assert completion_depth1 is not None
            assert completion_depth1["call_index"] == 1
            assert completion_depth1["termination_mode"] == "typed_submit"
            completion_depth2 = _recursive_completion(chunks, depth=2)
            assert completion_depth2 is not None
            assert completion_depth2["call_index"] == 2
            assert completion_depth2["termination_mode"] == "depth_fallback"
            statuses = _status_messages(chunks)
            started = [item.get("message") for item in statuses if item.get("status") == "child_started"]
            completed = [item for item in statuses if item.get("status") == "child_completed"]
            assert started == [
                "call_index=1 recursive_depth=1",
                "call_index=2 recursive_depth=2",
            ]
            assert len(completed) == 2
            depth2_completed = next(item for item in completed if "recursive_depth=2" in str(item.get("message")))
            assert "cleanup_status=not_required" in str(depth2_completed.get("message"))

            # VAL-REC-038: delete-request acceptance (timed, request-level) is
            # recorded separately from the eventual absence confirmation with
            # its full observation plateau, tied to the exact child Sandbox id.
            child_confirmations = [item for item in evidence.confirmations if item["sandbox_id"] == child_id]
            assert len(child_confirmations) == 1
            confirmation = child_confirmations[0]
            assert confirmation["absent"] is True
            assert confirmation["observations"]
            assert child_id in evidence.delete_acceptance_seconds
            # No grandchild: the only recursive child create carried the
            # recursive-child runtime label and there was exactly one.
            child_creates = [
                item
                for item in evidence.platform_creates
                if item.get("labels", {}).get("fleet.runtime") == "recursive-child"
            ]
            assert len(child_creates) == 1
            assert child_creates[0]["sandbox_id"] == child_id

            # Memory candidate was proposed; promotion settled post-commit.
            propose_outputs = [
                chunk["output"]
                for chunk in chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and "candidate_id" in chunk["output"]
            ]
            assert len(propose_outputs) == 1
            assert propose_outputs[0]["ok"] is True

            # Root Sandbox still owns the session after the child is gone:
            # child cleanup preceded parent settlement.
            binding = portal.call(resources.bindings.get, session_id)
            assert binding is not None and binding.sandbox_id is not None
            root_sandbox_id = str(binding.sandbox_id)
            sandbox_ids.add(root_sandbox_id)

            async def _child_absent() -> bool:
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    target = await resources.platform.get(child_id)
                    if target is None:
                        return True
                    state = str(getattr(getattr(target, "state", None), "value", getattr(target, "state", None)) or "")
                    if state.strip().lower() in {"destroyed", "deleted"}:
                        return True
                    await asyncio.sleep(1.0)
                return await resources.platform.get(child_id) is None

            assert portal.call(_child_absent)
            assert portal.call(resources.platform.get, root_sandbox_id) is not None

            # Post-commit promotion readback through the durable Memory store:
            # exactly the promoted candidate, never a second promotion.
            portal_loop = portal.call(lambda: asyncio.get_running_loop())
            root_sandbox = sync_sandbox(portal.call(resources.platform.get, root_sandbox_id), portal_loop)
            paths = volume_paths_from_settings(settings)
            from fleet_rlm.workspace.memory import WorkspaceMemory
            from fleet_rlm.workspace.storage import AgentStorageSession, WorkspaceMemoryStorage

            memory_store = WorkspaceMemory.from_storage(
                WorkspaceMemoryStorage(
                    AgentStorageSession(
                        root_sandbox,
                        volume_root=str(paths.root),
                        root=str(paths.root),
                        max_file_bytes=settings.max_upload_bytes,
                        allow_volume_root=True,
                    )
                ),
                max_file_bytes=settings.max_upload_bytes,
            )
            entries = memory_store.list_entries(limit=16).entries
            promoted = [
                entry for entry in entries if entry.source == "agent_candidate" and _MEM_TOKEN in entry.learning
            ]
            assert len(promoted) == 1
            memory_id = promoted[0].memory_id

            # ---- Unaffected recursive sibling-scope marker: written after
            # Turn 1 to call index 2's scope (never attempted: depth-2
            # fallback consumed it without a Sandbox). ----
            sibling_subpath = f"recursive/{LocalScope().workspace_id}/{run1_id}/2"
            scratch_sibling_sandbox, sibling_marker_sha_pre = portal.call(
                lambda: _scratch_marker(
                    resources,
                    volume_id=volume_id,
                    mount_path=mount_path,
                    subpath=sibling_subpath,
                    rel_path=_SIBLING_MARKER_NAME,
                    content=f"{_SIBLING_MARKER_NAME}:{uuid4()}",
                    marker_label="recursive-sibling",
                )
            )
            sandbox_ids.add(scratch_sibling_sandbox)

            # ---- Turn 2: later public Turn reads Workspace Memory ----
            preparation._models = RLMModelBundle(
                _ReadbackRootLM(),
                dspy.utils.DummyLM([{"answer": "unused"}], adapter=dspy.JSONAdapter()),
            )
            second = client.post(
                f"/api/sessions/{session_id}/turns",
                json={"text": "Verify the certified memory candidate."},
                headers={"Idempotency-Key": f"p39c-root-flow-2-{uuid4()}"},
            )
            assert second.status_code == 200
            second_chunks, second_done = _sse_chunks(second)
            assert second_done == 1
            run2_id = str(next(chunk["messageId"] for chunk in second_chunks if chunk.get("type") == "start"))
            assert second_chunks[-1].get("type") == "finish"
            assert second_chunks[-1].get("finishReason") == "stop"
            search_outputs = [
                chunk["output"]
                for chunk in second_chunks
                if chunk.get("type") == "tool-output-available"
                and isinstance(chunk.get("output"), dict)
                and "top_memory_ids" in chunk["output"]
            ]
            assert len(search_outputs) == 1
            assert memory_id in tuple(search_outputs[0]["top_memory_ids"])
            # Turn 2 delegated no recursive children at all.
            assert _status_messages(second_chunks) == []

            # Committed replay: Turn 1 contains the Artifact and exactly one
            # terminal success; downloaded bytes match the Artifact digest.
            page = client.get(f"/api/sessions/{session_id}/turns")
            assert page.status_code == 200
            items = page.json()["items"]
            assistant_items = [item for item in items if item["role"] == "assistant"]
            assert len(assistant_items) == 2
            turn1_parts = assistant_items[0]["parts"]
            artifact_parts = [part for part in turn1_parts if part["type"] == "data-artifact"]
            assert len(artifact_parts) == 1
            assert artifact_parts[0]["data"]["artifactId"] == artifact_id
            assert artifact_parts[0]["data"]["checksumSha256"] == artifact_checksum
            assert not [part for part in turn1_parts if part["type"] == "data-structured-result"]
            text_parts = [part for part in turn1_parts if part["type"] == "text"]
            assert text_parts and _ROOT_TOKEN in str(text_parts[0].get("text", ""))
            download = client.get(f"/api/artifacts/{artifact_id}/content")
            assert download.status_code == 200
            downloaded_bytes = download.content
            assert hashlib.sha256(downloaded_bytes).hexdigest() == artifact_checksum
            assert download.headers.get("ETag") == f'"{artifact_checksum}"'
            assert _ART_TOKEN.encode() in downloaded_bytes
            metadata = client.get(f"/api/artifacts/{artifact_id}")
            assert metadata.status_code == 200
            assert metadata.json()["checksum_sha256"] == artifact_checksum

            # VAL-REC-039/VAL-RLM-064: after all product compute settled, the
            # shared Volume remains readable through validator-owned Sandboxes
            # and both markers are byte-identical; the attempted child scope
            # (Turn 1 call index 1) was purged.
            root_marker_sha_post = portal.call(
                lambda: _read_marker_sha(resources, scratch_root_marker_sandbox, mount_path, _ROOT_MARKER_NAME)
            )
            sibling_marker_sha_post = portal.call(
                lambda: _read_marker_sha(resources, scratch_sibling_sandbox, mount_path, _SIBLING_MARKER_NAME)
            )
            assert root_marker_sha_post == root_marker_sha_pre
            assert sibling_marker_sha_post == sibling_marker_sha_pre
            attempted_child_subpath = f"recursive/{LocalScope().workspace_id}/{run1_id}/1"
            scratch_child_scope_sandbox = portal.call(
                lambda: resources.platform.create(
                    volume_id=volume_id,
                    mount_path=mount_path,
                    volume_subpath=attempted_child_subpath,
                    ephemeral=True,
                    labels={**_SCOPE_LABELS, "marker": "attempted-child-scope"},
                )
            )
            scratch_child_scope_id = str(getattr(scratch_child_scope_sandbox, "id", ""))
            sandbox_ids.add(scratch_child_scope_id)
            attempted_child_scope_files = portal.call(
                lambda: _list_scope_files(resources, scratch_child_scope_id, mount_path)
            )
            assert attempted_child_scope_files == []

            _wait_for_admission_baseline(resources, session_id, permits=settings.max_active_daytona_leases, portal=portal)
            assert get_active_lease_registry().holder(session_id) is None
            sandbox_ids.update(resources._sandbox_ids)
        finally:
            cleanup_failures = portal.call(_strict_cleanup, resources, sandbox_ids, settings.volume_name)
    assert cleanup_failures == ()

    record_observed_sandbox_ids("root-flow", sandbox_ids, {str(session_id)})
    _write_receipt(
        {
            "schema": _RECEIPT_SCHEMA,
            "candidate": candidate_identity(),
            "scenario": "root-flow",
            "resources": {
                "session_id": str(session_id),
                "run_ids": [run1_id, run2_id],
                "root_sandbox_id": root_sandbox_id,
                "child_sandbox_ids": evidence.child_sandbox_ids,
                "child_subpaths": evidence.child_subpaths,
                "volume_id": volume_id,
                "volume_name": settings.volume_name,
                "scratch_sandbox_ids": sorted(sandbox_ids - {root_sandbox_id} - set(evidence.child_sandbox_ids)),
            },
            "deletion_evidence": {
                "delete_request_acceptance_seconds": evidence.delete_acceptance_seconds,
                "confirmations": evidence.confirmations,
                "cleanup_receipts": evidence.receipts,
                "platform_creates": evidence.platform_creates,
            },
            "volume_markers": {
                "root_workspace": {
                    "subpath": workspace_subpath,
                    "pre_sha256": root_marker_sha_pre,
                    "post_sha256": root_marker_sha_post,
                    "unchanged": root_marker_sha_pre == root_marker_sha_post,
                },
                "recursive_sibling": {
                    "subpath": f"recursive/{LocalScope().workspace_id}/{run1_id}/2",
                    "pre_sha256": sibling_marker_sha_pre,
                    "post_sha256": sibling_marker_sha_post,
                    "unchanged": sibling_marker_sha_pre == sibling_marker_sha_post,
                },
                "attempted_child_scope_files": attempted_child_scope_files,
            },
            "assertions": {
                "one_native_child": True,
                "no_grandchild_sandbox": True,
                "depth2_sub_lm_fallback": True,
                "child_ownership_settles_before_publication": True,
                "wire_order_artifact_then_one_terminal": True,
                "post_commit_promotion_no_extra_terminal": True,
                "replay_contains_artifact_one_terminal": True,
                "artifact_download_digest_match": True,
                "memory_readback_promoted_candidate": True,
                "deletion_acceptance_distinct_from_absence": True,
                "volume_markers_preserved": True,
                "attempted_child_scope_purged": True,
                "sandbox_absent": True,
                "admission_restored": True,
            },
            "memory": {"memory_id": memory_id, "artifact_id": artifact_id},
            "cleanup": {"confirmed_absent": True, "admission_restored": True},
            "passed": True,
        }
    )
