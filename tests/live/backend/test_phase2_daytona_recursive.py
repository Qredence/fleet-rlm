"""One-Turn live proof for Phase 2 native DSPy recursion on Daytona."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

import dspy
import pytest
from daytona.common.errors import DaytonaNotFoundError
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from fleet_rlm.app import create_app
from fleet_rlm.config.loader import active_profile, require_live_execution
from fleet_rlm.config.settings import FleetConfigurationError, Settings
from fleet_rlm.daytona import runtime as recursive_child_runtime
from fleet_rlm.rlm.events import ToolEventView
from fleet_rlm.rlm.program import has_llm_credentials
from tests.live.backend._cleanup import _retry_cleanup, _strict_cleanup
from tests.live.backend._database import upgrade_to_head

pytestmark = [pytest.mark.live_daytona, pytest.mark.timeout(960)]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RECEIPT_SCHEMA = "fleet.phase2-daytona-recursive/v1"
_EVIDENCE_ENV = "FLEET_PHASE2_RECURSIVE_EVIDENCE_PATH"
_P27_SESSION_SNAPSHOT_ENV = "FLEET_P27_SESSION_SNAPSHOT"
_P27_CHILD_SNAPSHOT_ENV = "FLEET_P27_CHILD_SNAPSHOT"
_LIVE_ROOT_MODEL = os.environ.get("FLEET_LIVE_ROOT_MODEL", "deepseek-v4.1-flash")
_LIVE_SUB_MODEL = os.environ.get("FLEET_LIVE_SUB_MODEL", "deepseek-v4.1-flash")
_CONTRACT_ID = "fleet.phase2-daytona-recursive"


class Phase2Result(dspy.Signature):
    """Return a multi-field Root result so completion is necessarily typed."""

    request: str = dspy.InputField()
    session_context: dict = dspy.InputField()
    skill_cards: list[dict] = dspy.InputField()
    attachments: list[dict] = dspy.InputField()
    answer: str = dspy.OutputField()
    evidence: str = dspy.OutputField()


@dataclass(slots=True)
class _ProofCapabilityPreparer:
    delegate: Any
    tools: tuple[dspy.Tool, ...]
    event_views: MappingProxyType[str, ToolEventView]

    async def prepare(self, turn: Any, environment: Any, attachments: Any, *, deadline: float) -> Any:
        """
        Prepare a turn with the Phase 2 recursive execution specification.

        Parameters:
            deadline (float): Maximum time allowed for preparation.

        Returns:
            Any: The prepared turn with the Phase 2 signature, contract metadata, tools, and event views.
        """
        prepared = await self.delegate.prepare(turn, environment, attachments, deadline=deadline)
        prepared.spec = replace(
            prepared.spec,
            # Instructions are recomposed from Fleet fragments at worker start; steering rides the request text.
            signature=Phase2Result,
            output_schema_id=_CONTRACT_ID,
            output_schema_version="1",
            tools=(*prepared.spec.tools, *self.tools),
            tool_event_views={**prepared.spec.tool_event_views, **self.event_views},
        )
        return prepared


@dataclass(slots=True)
class _ProofLedger:
    calls: int = 0
    root_marker_absent_in_child: bool = False
    root_continuity: bool = False
    child_source_verified: bool = False
    unselected_material_hidden: bool = False
    source_revision_verified: bool = False
    result_file_verified: bool = False
    result_file_count: int = 0
    expected_source_bytes: int = 0
    expected_source_sha256: str = ""
    expected_source_manifest_sha256: str = ""
    expected_last_record: str = ""
    source_path: str = "selected/evidence.txt"
    private_path: str = "unselected/private.txt"

    def verify_phase2(
        self,
        child_result: str,
        root_marker: str,
        source_manifest_sha256: str,
        result_files: list[str],
    ) -> dict[str, bool]:
        """
        Verify child isolation and root-state continuity for the Phase 2 recursion proof.

        Parameters:
                child_result (str): Child-submitted summary of the selected source.
                root_marker (str): Root marker expected to be "root-only".
                source_manifest_sha256 (str): Runtime-owned revision for the staged source.
                result_files (list[str]): Persisted result references returned after child cleanup.

        Returns:
                dict[str, bool]: A result containing `{"ok": True}` when both checks pass.

        Raises:
                ValueError: If the verifier is called more than once or an isolation check fails.
        """
        self.calls += 1
        if self.calls != 1:
            raise ValueError("phase2 verifier must be called exactly once")
        try:
            summary = json.loads(child_result)
        except (TypeError, ValueError):
            summary = {}
        self.child_source_verified = (
            isinstance(summary, dict)
            and summary.get("byte_count") == self.expected_source_bytes
            and summary.get("sha256") == self.expected_source_sha256
            and summary.get("last_record") == self.expected_last_record
        )
        self.unselected_material_hidden = isinstance(summary, dict) and summary.get("private_visible") is False
        self.root_marker_absent_in_child = isinstance(summary, dict) and summary.get("root_marker_visible") is False
        self.source_revision_verified = source_manifest_sha256 == self.expected_source_manifest_sha256
        self.result_file_count = len(result_files)
        self.result_file_verified = (
            len(result_files) == 1
            and result_files[0].startswith("run/children/1/")
            and result_files[0].endswith("/results/source-summary.json")
        )
        self.root_continuity = root_marker == "root-only"
        if not all(
            (
                self.child_source_verified,
                self.unselected_material_hidden,
                self.root_marker_absent_in_child,
                self.source_revision_verified,
                self.result_file_verified,
                self.root_continuity,
            )
        ):
            raise ValueError("recursive child source isolation proof failed")
        return {
            "ok": True,
            "source_verified": self.child_source_verified,
            "unselected_hidden": self.unselected_material_hidden,
            "root_marker_absent": self.root_marker_absent_in_child,
            "revision_verified": self.source_revision_verified,
            "result_file_saved": self.result_file_verified,
        }


@dataclass(slots=True)
class _ChildEvidence:
    created: int = 0
    same_volume_sibling_scope: bool = False
    volumeless_semantic_isolation: bool = False
    provider_mounts_inspected: bool = False
    provider_no_volume_mount: bool = False
    cleanup_succeeded: bool = False
    source_file_count: int = 0
    source_bytes: int = 0
    selected_input_staged: bool = False
    unselected_source_staged: bool = False
    staging_duration_ms: float = 0.0
    child_duration_ms: int = 0
    started_at: float | None = None


def _load_live_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """
    Load and validate live settings for the Phase 2 recursive canary.

    Parameters:
        tmp_path (Path): Temporary directory for the copied policy and SQLite database.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture used to apply the temporary configuration path.

    Returns:
        Settings: Validated settings configured for the Daytona recursive canary.
    """
    if not os.environ.get(_EVIDENCE_ENV):
        pytest.skip("Run this credentialed canary with live Daytona credentials")
    load_dotenv(_REPO_ROOT / ".env", override=False)
    import fleet_rlm.config.loader as configuration

    copied_policy = tmp_path / "phase2-fleet.toml"
    # Keep this canary tied to the shipped recursive profile while using a
    # copied policy so its database and snapshot overrides stay isolated.
    copied_policy.write_text(
        (_REPO_ROOT / "config" / "fleet.toml")
        .read_text(encoding="utf-8")
        .replace('default_profile = "daytona-native"', 'default_profile = "daytona-recursive"', 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(configuration, "_CONFIG_PATH", copied_policy)
    try:
        policy = require_live_execution()
    except FleetConfigurationError:
        pytest.fail("Phase 2 recursive canary requires runtime.live_enabled=true")
    if active_profile(policy) != "daytona-recursive" or policy.run_environment != "daytona":
        pytest.fail("Phase 2 recursive canary requires the daytona-recursive profile")
    if not policy.rlm_recursion_enabled or (policy.root_model, policy.sub_model) != (
        _LIVE_ROOT_MODEL,
        _LIVE_SUB_MODEL,
    ):
        pytest.fail("Phase 2 recursive canary requires the selected configured recursive policy")
    if policy.daytona_api_key is None or not has_llm_credentials(policy):
        pytest.fail("Phase 2 recursive canary is missing configured provider credentials")
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'phase2-recursive.db').resolve()}"
    upgrade_to_head(database_url)
    overrides: dict[str, object] = {
        "database_url": database_url,
        "volume_name": f"fleet-rlm-phase2-recursive-{uuid4()}",
        "rlm_max_iters": 7,
        "rlm_max_llm_calls": 10,
        "turn_timeout_seconds": 900,
    }
    session_snapshot = os.environ.get(_P27_SESSION_SNAPSHOT_ENV)
    child_snapshot = os.environ.get(_P27_CHILD_SNAPSHOT_ENV)
    if bool(session_snapshot) != bool(child_snapshot):
        pytest.fail("P2.7 candidate snapshot overrides must include Session and SemanticChild images")
    if session_snapshot:
        overrides["daytona_snapshot"] = session_snapshot
        overrides["daytona_child_snapshot"] = child_snapshot
    return policy.model_copy(update=overrides)


def _install_child_evidence(
    monkeypatch: pytest.MonkeyPatch,
    evidence: _ChildEvidence,
    ledger: _ProofLedger,
) -> None:
    """Instrument child-runtime acquisition and cleanup to record evidence for the test."""
    original = recursive_child_runtime.DaytonaRuntime._acquire_child_runtime

    async def observed(
        owner: recursive_child_runtime.DaytonaRuntime, **kwargs: object
    ) -> recursive_child_runtime.ChildRuntimeLease:
        """
        Wrap child-runtime acquisition to record creation, recursive sibling scope, cleanup success, and duration.

        Parameters:
                **kwargs (object): Child-runtime acquisition arguments, including workspace, run, call, and
                    volume identifiers.

        Returns:
                recursive_child_runtime.ChildRuntimeLease: The acquired child-runtime lease.
        """
        evidence.started_at = time.perf_counter()
        lease = await original(owner, **kwargs)  # type: ignore[arg-type]
        evidence.created += 1
        expected_scope = f"recursive/{kwargs['workspace_id']}/{kwargs['run_id']}/{kwargs['call_index']}"
        evidence.same_volume_sibling_scope = (
            lease.volume_subpath == expected_scope and lease.volume_id == kwargs["volume_id"]
        )
        # P2.7 lean SemanticChild sandboxes are Volume-less by contract: no
        # volume mount means no sibling scope to share. Isolation by absence
        # is the expected scope for that profile, not a scope violation.
        evidence.volumeless_semantic_isolation = lease.volume_id in (None, "") and lease.volume_subpath in (
            None,
            "",
        )
        # Lease metadata describes Fleet's request. Read the acquired Sandbox
        # back from Daytona so the receipt can distinguish provider evidence.
        try:
            provider_child = await owner._platform.get(lease.sandbox_id) if owner._platform is not None else None
        except Exception:
            provider_child = None
        provider_mounts = getattr(provider_child, "volumes", None)
        evidence.provider_mounts_inspected = provider_child is not None and provider_mounts is not None
        evidence.provider_no_volume_mount = evidence.provider_mounts_inspected and len(provider_mounts) == 0
        stage_files = lease._stage_files
        if stage_files is not None:

            def observed_stage(files: Mapping[str, bytes]) -> None:
                manifest = [
                    {"path": path, "sha256": hashlib.sha256(content).hexdigest()}
                    for path, content in sorted(files.items())
                ]
                encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ledger.expected_source_manifest_sha256 = hashlib.sha256(encoded).hexdigest()
                evidence.source_file_count = len(files)
                evidence.source_bytes = sum(map(len, files.values()))
                selected = files.get(ledger.source_path)
                evidence.selected_input_staged = (
                    selected is not None and hashlib.sha256(selected).hexdigest() == ledger.expected_source_sha256
                )
                evidence.unselected_source_staged = ledger.private_path in files
                stage_started = time.perf_counter()
                stage_files(files)
                evidence.staging_duration_ms = round((time.perf_counter() - stage_started) * 1000, 3)

            lease._stage_files = observed_stage
        close = lease._close

        def observed_close() -> None:
            """Close the child runtime and record cleanup success and elapsed duration."""
            try:
                close()
            except Exception:
                raise
            else:
                evidence.cleanup_succeeded = True
            finally:
                if evidence.started_at is not None:
                    evidence.child_duration_ms = int((time.perf_counter() - evidence.started_at) * 1000)

        lease._close = observed_close
        return lease

    monkeypatch.setattr(recursive_child_runtime.DaytonaRuntime, "_acquire_child_runtime", observed)


def _sse_chunks(response: Any) -> tuple[list[dict[str, Any]], int]:
    """
    Parse Server-Sent Event data from a response.

    Parameters:
        response (Any): Response whose text contains SSE lines.

    Returns:
        tuple[list[dict[str, Any]], int]: Parsed JSON event chunks and the number of `[DONE]` markers.
    """
    chunks: list[dict[str, Any]] = []
    done = 0
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        if payload == "[DONE]":
            done += 1
        else:
            chunks.append(json.loads(payload))
    return chunks, done


def _recursive_completion(chunks: list[dict[str, Any]]) -> dict[str, object] | None:
    """Finds the completed recursive tool output in a sequence of event chunks.

    Parameters:
        chunks (list[dict[str, Any]]): Event chunks to inspect.

    Returns:
        dict[str, object] | None: The first completed recursive output, or `None` if no matching output is found.
    """
    for chunk in chunks:
        if chunk.get("type") != "tool-output-available":
            continue
        output = chunk.get("output")
        if isinstance(output, dict) and output.get("status") == "completed" and "recursive_depth" in output:
            return output
    return None


def _write_receipt(payload: dict[str, object]) -> None:
    """
    Write the evidence payload as formatted JSON to the configured output path.
    """
    output = Path(os.environ[_EVIDENCE_ENV]).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
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
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


async def _delete_sandboxes_on_canary_volume(resources: Any, volume_name: str) -> bool:
    """Delete provider-visible sandboxes mounting this canary's unique volume."""
    daytona = resources.runtime._client
    try:
        volume = await daytona.volume.get(volume_name, create=False)
    except DaytonaNotFoundError:
        return True
    except Exception:
        return False
    try:
        matching = [
            sandbox
            async for sandbox in daytona.list()
            if any(mount.volume_id == volume.id for mount in sandbox.volumes or ())
        ]
    except Exception:
        return False
    succeeded = True
    for sandbox in matching:
        if not await _retry_cleanup(lambda sandbox=sandbox: daytona.delete(sandbox, wait=True)):
            succeeded = False
    return succeeded


def test_phase2_daytona_recursive_through_fastapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Run the live Daytona recursive canary through the FastAPI application.

    Parameters:
        tmp_path (Path): Temporary directory used to create the live test database.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for patching runtime behavior and environment settings.
    """
    settings = _load_live_settings(tmp_path, monkeypatch)
    ledger = _ProofLedger()
    child_evidence = _ChildEvidence()
    _install_child_evidence(monkeypatch, child_evidence, ledger)
    proof_tool = dspy.Tool(
        ledger.verify_phase2,
        name="verify_phase2",
        desc="Verify child isolation and Root continuity exactly once.",
    )
    proof_views = MappingProxyType(
        {
            "verify_phase2": ToolEventView(
                input_projection=lambda values: {
                    "result_file_count": len(values.get("result_files", ()))
                    if isinstance(values.get("result_files"), (tuple, list))
                    else 0,
                },
                output_projection=lambda result: {
                    "ok": bool(result.get("ok")),
                    "source_verified": bool(result.get("source_verified")),
                    "unselected_hidden": bool(result.get("unselected_hidden")),
                    "root_marker_absent": bool(result.get("root_marker_absent")),
                    "revision_verified": bool(result.get("revision_verified")),
                    "result_file_saved": bool(result.get("result_file_saved")),
                },
            )
        }
    )
    started = time.perf_counter()
    pending_receipt: dict[str, object] | None = None
    cleanup_failures: tuple[str, ...] = ()
    app = create_app(settings=settings)
    with TestClient(app) as client:
        inventory = app.state.runtime_inventory
        resources = inventory.run_environment_resources
        preparation = inventory.run_preparation
        assert resources is not None
        assert preparation is not None
        object.__setattr__(
            preparation,
            "capabilities",
            _ProofCapabilityPreparer(preparation.capabilities, (proof_tool,), proof_views),
        )
        try:
            assert client.portal is not None
            source_path = "selected/evidence.txt"
            private_path = "unselected/private.txt"
            records = [f"record-{index:05d};value-{index % 17:02d}" for index in range(4_000)]
            last_record = f"P6-END-{uuid4()}"
            source_text = "\n".join((*records, last_record))
            source_bytes = source_text.encode("utf-8")
            assert len(source_bytes) > 50_000
            private_marker = f"PARENT-ONLY-{uuid4()}"
            ledger.expected_source_bytes = len(source_bytes)
            ledger.expected_source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            ledger.expected_last_record = last_record
            source_write = client.put(
                "/api/files/content",
                json={"path": source_path, "content": source_text, "overwrite": False},
            )
            assert source_write.status_code == 200
            private_write = client.put(
                "/api/files/content",
                json={"path": private_path, "content": private_marker, "overwrite": False},
            )
            assert private_write.status_code == 200
            created = client.post("/api/sessions", json={"title": "Phase 2 Daytona recursive canary"})
            assert created.status_code == 201
            session_id = UUID(created.json()["id"])
            response = client.post(
                f"/api/sessions/{session_id}/turns",
                json={
                    "text": (
                        "Execute the native DSPy recursive child source-isolation proof. Run exactly one child."
                        ' First set root_marker = "root-only". Then call rlm_query exactly once with'
                        " task asking the child to read only selected/evidence.txt under"
                        " os.path.join(FLEET_RUN_SCRATCH, path), compute its byte count, SHA-256, and last"
                        " line, check that unselected/private.txt does not exist under that same scratch, and"
                        " check that the Python name root_marker is absent from the child's globals."
                        " The selected input is a large exhaustive record list; inspect the whole file, not a"
                        " preview. Have the child write the computed JSON to"
                        " results/source-summary.json under FLEET_RUN_SCRATCH and typed-submit that JSON as"
                        " answer, cite the selected file hash, use no gaps, and declare that result file."
                        " Pass only inputs=['selected/evidence.txt']. Do not inspect or copy the unselected"
                        " file, do not call llm_query or rlm_query inside the child, and do not use extraction"
                        " fallback. After return, assert the child status is completed and call verify_phase2"
                        " exactly once with the child answer, the unchanged root_marker, the runtime-provided"
                        " source_manifest_sha256, and result_files. Require its ok result, then issue exactly"
                        ' one typed SUBMIT(answer="phase 6 child source isolation complete", evidence='
                        '"selected staged source and persisted child result").'
                    ),
                },
                headers={"Idempotency-Key": f"phase2-daytona-recursive-{uuid4()}"},
            )
            assert response.status_code == 200
            chunks, done = _sse_chunks(response)
            assert done == 1
            assert chunks[-1].get("type") == "finish"
            assert chunks[-1].get("finishReason") == "stop"
            completion = _recursive_completion(chunks)
            assert completion is not None
            assert completion["status"] == "completed"
            assert completion["call_index"] == 1
            assert completion["recursive_depth"] == 1
            assert completion["termination_mode"] == "typed_submit"
            assert isinstance(completion["child_iterations"], int) and completion["child_iterations"] >= 1
            result_files = completion.get("result_files")
            assert isinstance(result_files, list) and len(result_files) == 1
            assert result_files[0].startswith("run/children/1/")
            assert result_files[0].endswith("/results/source-summary.json")
            assert completion.get("source_manifest_sha256") == ledger.expected_source_manifest_sha256
            ledger.result_file_count = len(result_files)
            ledger.result_file_verified = True
            structured = [chunk for chunk in chunks if chunk.get("type") == "data-structured-result"]
            assert len(structured) == 1
            assert structured[0].get("data", {}).get("schema_id") == _CONTRACT_ID
            assert ledger.calls == 1
            assert ledger.child_source_verified
            assert ledger.unselected_material_hidden
            assert ledger.source_revision_verified
            assert ledger.result_file_verified
            assert child_evidence.selected_input_staged
            assert not child_evidence.unselected_source_staged
            assert child_evidence.source_file_count >= 1
            assert child_evidence.source_bytes >= len(source_bytes)
            assert child_evidence.created == 1
            assert child_evidence.same_volume_sibling_scope or child_evidence.volumeless_semantic_isolation
            assert child_evidence.provider_mounts_inspected and child_evidence.provider_no_volume_mount
            assert child_evidence.cleanup_succeeded
            parent_pages: list[str] = []
            cursor: str | None = None
            for _ in range(16):
                params: dict[str, str | int] = {"path": source_path, "max_chars": 25_000}
                if cursor is not None:
                    params["cursor"] = cursor
                source_read = client.get("/api/files/content", params=params)
                assert source_read.status_code == 200
                source_page = source_read.json()
                parent_pages.append(str(source_page["content"]))
                if source_page["eof"] is True:
                    break
                cursor = source_page["next_cursor"]
                assert isinstance(cursor, str) and cursor
            else:
                raise AssertionError("parent source verification exceeded its page bound")
            parent_source_after = "".join(parent_pages)
            parent_file_unchanged = (
                parent_source_after == source_text
                and hashlib.sha256(parent_source_after.encode("utf-8")).hexdigest() == ledger.expected_source_sha256
            )
            assert parent_file_unchanged
            pending_receipt = {
                "schema": _RECEIPT_SCHEMA,
                "timing": {
                    "turn_duration_ms": int((time.perf_counter() - started) * 1000),
                    "child_duration_ms": child_evidence.child_duration_ms,
                },
                "source_isolation": {
                    "selected_input_bytes": ledger.expected_source_bytes,
                    "selected_input_sha256": ledger.expected_source_sha256,
                    "source_manifest_sha256": ledger.expected_source_manifest_sha256,
                    "staged_source_file_count": child_evidence.source_file_count,
                    "staged_source_bytes": child_evidence.source_bytes,
                    "staging_duration_ms": child_evidence.staging_duration_ms,
                    "selected_input_staged": child_evidence.selected_input_staged,
                    "unselected_source_staged": child_evidence.unselected_source_staged,
                    "child_source_verified": ledger.child_source_verified,
                    "unselected_material_hidden": ledger.unselected_material_hidden,
                    "root_marker_absent_in_child": ledger.root_marker_absent_in_child,
                    "parent_file_unchanged": parent_file_unchanged,
                    "result_file_count": ledger.result_file_count,
                    "result_file_verified": ledger.result_file_verified,
                },
                "assertions": {
                    "dedicated_child_sandbox": True,
                    "child_isolation_scope": True,
                    "provider_mounts_inspected": child_evidence.provider_mounts_inspected,
                    "provider_no_volume_mount": child_evidence.provider_no_volume_mount,
                    "root_marker_absent_in_child": ledger.root_marker_absent_in_child,
                    "root_continuity": ledger.root_continuity,
                    "child_typed_submit": completion["termination_mode"] == "typed_submit",
                    "root_typed_submit": True,
                    "strict_child_cleanup": child_evidence.cleanup_succeeded,
                    "terminal_ordering": True,
                    "no_grandchild_sandbox": child_evidence.created == 1,
                    "large_selected_source_staged": ledger.child_source_verified,
                    "unselected_parent_source_unavailable": ledger.unselected_material_hidden,
                    "source_revision_fixed": ledger.source_revision_verified,
                    "source_materialized_before_provider_admission": child_evidence.selected_input_staged,
                    "parent_source_unchanged": parent_file_unchanged,
                    "result_harvested_before_cleanup": ledger.result_file_verified and child_evidence.cleanup_succeeded,
                },
                "failure": None,
                "passed": True,
            }
        finally:
            assert client.portal is not None
            sandbox_cleanup_succeeded = client.portal.call(
                _delete_sandboxes_on_canary_volume, resources, settings.volume_name
            )
            cleanup_failures = client.portal.call(_strict_cleanup, resources, settings.volume_name)
            assert sandbox_cleanup_succeeded and cleanup_failures == (), "Phase 2 canary cleanup did not settle"
    assert pending_receipt is not None
    _write_receipt(pending_receipt)
