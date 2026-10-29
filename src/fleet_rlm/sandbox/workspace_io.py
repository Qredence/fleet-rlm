"""Workspace file I/O, learned notes, Turn inputs, artifact promotion, and verification for DaytonaSandboxManager."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mlflow
from daytona import FileDownloadRequest, FileUpload

from fleet_rlm.observability import activity_text, phase_span
from fleet_rlm.sandbox.repl import ExecutionOutcomeUnknownError, with_retries
from fleet_rlm.sandbox.runtime.fs_ops import (
    list_workspace_files_code,
    turn_inputs_code,
    valid_notes,
    verify_python_artifact_code,
    workspace_files_metadata_code,
)
from fleet_rlm.workspace import _workspace_relative_path

if TYPE_CHECKING:
    from fleet_rlm.sandbox.manager import DaytonaSandboxManager

_LOGGER = logging.getLogger(__name__)


class WorkspaceIOMixin:
    """Mixin providing workspace file CRUD, Turn input gathering, learned notes, and artifact promotion."""

    __slots__ = ()

    def write_learned_note(
        self: DaytonaSandboxManager, turn_id: str, note: str, trace_id: str | None = None
    ) -> str | None:
        """Write one Turn's note as an unreviewed file on the shared mount; None when skipped."""
        if not self.shared_notes_subpath or not note.strip():
            return None
        if self.exec_remote_python("import os\nprint(os.path.ismount('/learned'))").strip() != "True":
            return None  # Sandbox predates the mount; the note still reaches the Session memory summary.
        turn = re.sub(r"[^A-Za-z0-9]", "", turn_id)[:8] or "turn"
        path = f"/learned/{self.session_id}-{turn}.md"
        body = activity_text(note, limit=10_000)
        summary = next((line.strip() for line in body.splitlines() if line.strip()), "")[:120]
        header = [
            "---",
            f"session: {self.session_id}",
            f"turn: {turn}",
            f"trace_id: {trace_id or ''}",
            "status: unreviewed",
            f"summary: {summary}",
            "---",
        ]
        sandbox = self.ensure_sandbox()
        with_retries(lambda: sandbox.fs.upload_file("\n".join([*header, body, ""]).encode("utf-8"), path, timeout=60))
        return path

    def turn_inputs(self: DaytonaSandboxManager, notes_limit: int = 20) -> dict[str, Any]:
        """Gather a Turn's remote inputs in one exec: file digests, shared notes, and the environment.

        Digests follow `list_workspace_files` (symbolic links skipped, never traversed) and are required;
        notes are optional hints, so an unreadable mount yields none instead of failing the Turn.
        `notes_limit` enables shared notes only from 1 through 50; other values omit notes.
        Missing optional modules may emit one advisory event per Session.

        Raises:
            RuntimeError: The workspace digest listing is malformed.

        Remote execution and JSON decoding errors propagate.
        """
        with_notes = bool(self.shared_notes_subpath) and 1 <= notes_limit <= 50
        code = turn_inputs_code(with_notes=with_notes, notes_limit=notes_limit)
        result = json.loads(self.exec_remote_python(code))
        digests = result.get("digests") if isinstance(result, dict) else None
        if not isinstance(digests, dict) or not all(
            isinstance(path, str) and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
            for path, digest in digests.items()
        ):
            raise RuntimeError("Invalid workspace digest listing")
        for path in digests:
            _workspace_relative_path(path)
        if result.get("notes") is None:
            _LOGGER.warning("Shared learned notes were unavailable for Session %s", self.session_id)
        self._report_incomplete_environment(result.get("missing"))
        environment = result.get("environment")
        return {
            "digests": digests,
            "notes": valid_notes(result.get("notes")),
            "environment": environment if isinstance(environment, str) else "",
        }

    def list_workspace_files(self: DaytonaSandboxManager) -> list[str]:
        """List validated regular files beneath the Session's managed workspace roots.

        Unmanaged files are excluded without modification. Symbolic links are never traversed.
        """
        trace = phase_span("sandbox.workspace_inventory") if mlflow.get_current_active_span() else nullcontext(None)
        with trace as phase:
            paths = json.loads(self.exec_remote_python(list_workspace_files_code()))
            if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
                raise RuntimeError("Invalid workspace file listing")
            result = sorted({_workspace_relative_path(path) for path in paths})
            if phase is not None:
                phase.set_outputs({"files": len(result)})
            return result

    def write_workspace_file(self: DaytonaSandboxManager, rel_path: str, content: bytes) -> str:
        """Write one validated file beneath this Session's mounted Volume."""
        clean_path = _workspace_relative_path(rel_path)
        if not isinstance(content, bytes):
            raise TypeError("workspace content must be bytes")
        if len(content) > 25_000_000:
            raise ValueError("Workspace file exceeds the 25 MB limit")
        sandbox = self.ensure_sandbox()
        self._workspace_file_metadata(sandbox, clean_path, create_parents=True)
        with_retries(lambda: sandbox.fs.upload_file(content, f"/workspace/{clean_path}", timeout=1800))
        return f"/workspace/{clean_path}"

    def write_workspace_file_from_path(self: DaytonaSandboxManager, rel_path: str, local_path: str | Path) -> str:
        """Upload a local file through Daytona without loading it into memory."""
        clean_path = _workspace_relative_path(rel_path)
        source = Path(local_path)
        size = source.stat().st_size
        if size > 25_000_000:
            raise ValueError("Workspace file exceeds the 25 MB limit")
        sandbox = self.ensure_sandbox()
        self._workspace_file_metadata(sandbox, clean_path, create_parents=True)
        with_retries(lambda: sandbox.fs.upload_file(str(source), f"/workspace/{clean_path}", timeout=1800))
        return f"/workspace/{clean_path}"

    def read_workspace_file(self: DaytonaSandboxManager, rel_path: str, *, max_bytes: int = 25_000_000) -> bytes:
        """Read one workspace file, refusing anything over max_bytes.

        Metadata is checked remotely first, and a size change during download raises.
        """
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        # The SDK's download_file is positional-only at runtime (an *args overload); keep the timeout positional.
        content = with_retries(lambda: sandbox.fs.download_file(f"/workspace/{clean_path}", 1800))
        if not isinstance(content, bytes) or len(content) != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return content

    def stat_workspace_file(self: DaytonaSandboxManager, rel_path: str) -> dict[str, Any]:
        """Return size and SHA-256 without downloading content, with the same 25 MB and path checks."""
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_files_metadata(
            sandbox, [clean_path], create_parents=False, max_bytes=25_000_000, include_digest=True
        )[0]
        return {"path": clean_path, "size_bytes": metadata["size"], "sha256": metadata["sha256"]}

    def read_workspace_file_to_path(
        self: DaytonaSandboxManager, rel_path: str, local_path: str | Path, *, max_bytes: int = 25_000_000
    ) -> int:
        """Download a workspace file to disk through Daytona's path overload."""
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 <= max_bytes <= 25_000_000:
            raise ValueError("max_bytes must be an integer between 0 and 25000000")
        clean_path = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_file_metadata(sandbox, clean_path, create_parents=False, max_bytes=max_bytes)
        destination = Path(local_path)
        with_retries(lambda: sandbox.fs.download_file(f"/workspace/{clean_path}", str(destination), 1800))
        if not destination.is_file() or destination.stat().st_size != metadata["size"]:
            raise RuntimeError("Workspace file changed or could not be downloaded")
        return metadata["size"]

    def _workspace_file_metadata(
        self: DaytonaSandboxManager,
        sandbox: Any,
        rel_path: str,
        *,
        create_parents: bool,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Check a Volume path remotely before using Daytona's file-transfer API."""
        return self._workspace_files_metadata(sandbox, [rel_path], create_parents=create_parents, max_bytes=max_bytes)[
            0
        ]

    def _workspace_files_metadata(
        self: DaytonaSandboxManager,
        sandbox: Any,
        rel_paths: list[str],
        *,
        create_parents: bool,
        max_bytes: int | None = None,
        include_digest: bool = False,
    ) -> list[dict[str, Any]]:
        """Check Volume paths remotely in one call, in order, before using Daytona's file-transfer API."""
        code = workspace_files_metadata_code(
            rel_paths,
            create_parents=create_parents,
            max_bytes=max_bytes,
            include_digest=include_digest,
        )
        name = "sandbox.workspace_prepare" if create_parents else "sandbox.workspace_metadata"
        trace = self._lifecycle_span(name) if mlflow.get_current_active_span() else nullcontext(None)
        with trace as phase:
            if phase is not None:
                phase.set_attribute("files_requested", len(rel_paths))
            metadata = json.loads(self._remote_exec(sandbox, code, retry_span=phase))
            if (
                not isinstance(metadata, list)
                or len(metadata) != len(rel_paths)
                or not all(
                    isinstance(item, dict)
                    and (item.get("size") is None or type(item["size"]) is int)
                    and (
                        not include_digest
                        or (
                            type(item.get("size")) is int
                            and item["size"] >= 0
                            and isinstance(item.get("sha256"), str)
                            and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is not None
                        )
                    )
                    for item in metadata
                )
            ):
                raise RuntimeError("Invalid workspace file metadata")
            return metadata

    def read_workspace_files(
        self: DaytonaSandboxManager, rel_paths: list[str], *, max_total_bytes: int = 100_000_000
    ) -> dict[str, bytes]:
        """Read several workspace files with one remote check and one batched download.

        Every path gets the same symlink, containment, and size checks as `read_workspace_file`.
        """
        clean_paths = [_workspace_relative_path(path) for path in rel_paths]
        if not clean_paths:
            return {}
        sandbox = self.ensure_sandbox()
        metadata = self._workspace_files_metadata(sandbox, clean_paths, create_parents=False, max_bytes=25_000_000)
        if sum(item["size"] for item in metadata) > max_total_bytes:
            raise ValueError("Workspace batch exceeds the transfer quota")
        trace = (
            self._lifecycle_span("sandbox.workspace_download")
            if mlflow.get_current_active_span()
            else nullcontext(None)
        )
        with trace as phase:
            if phase is not None:
                phase.set_attributes(
                    {"files_requested": len(clean_paths), "bytes_requested": sum(item["size"] for item in metadata)}
                )
            responses = with_retries(
                lambda: sandbox.fs.download_files(
                    [FileDownloadRequest(source=f"/workspace/{path}") for path in clean_paths], 1800
                ),
                span=phase,
            )
            by_source = {response.source: response for response in responses}
            contents: dict[str, bytes] = {}
            for path, item in zip(clean_paths, metadata, strict=True):
                response = by_source.get(f"/workspace/{path}")
                content = getattr(response, "result", None)
                if response is None or response.error or not isinstance(content, bytes) or len(content) != item["size"]:
                    raise RuntimeError(f"Workspace file changed or could not be downloaded: {path}")
                contents[path] = content
            return contents

    def write_workspace_files(self: DaytonaSandboxManager, files: dict[str, bytes]) -> None:
        """Write several validated files beneath this Session's Volume with one check and one upload."""
        clean = {_workspace_relative_path(path): content for path, content in files.items()}
        if not clean:
            return
        if any(not isinstance(content, bytes) or len(content) > 25_000_000 for content in clean.values()):
            raise ValueError("Workspace files must be bytes of at most 25 MB")
        sandbox = self.ensure_sandbox()
        self._workspace_files_metadata(sandbox, list(clean), create_parents=True)
        uploads = [FileUpload(source=content, destination=f"/workspace/{path}") for path, content in clean.items()]
        trace = (
            self._lifecycle_span("sandbox.workspace_upload") if mlflow.get_current_active_span() else nullcontext(None)
        )
        with trace as phase:
            if phase is not None:
                phase.set_attributes(
                    {"files_requested": len(clean), "bytes_requested": sum(len(data) for data in clean.values())}
                )
            with_retries(lambda: sandbox.fs.upload_files(uploads, timeout=1800), span=phase)

    def delete_workspace_file(self: DaytonaSandboxManager, rel_path: str) -> None:
        """Delete one validated workspace file, refusing symbolic links."""
        clean = _workspace_relative_path(rel_path)
        sandbox = self.ensure_sandbox()
        # The remote check rejects symbolic links; Daytona's file API does not report them.
        self._workspace_file_metadata(sandbox, clean, create_parents=False)
        with_retries(lambda: sandbox.fs.delete_file(f"/workspace/{clean}"))

    def collect_child_artifacts(self: DaytonaSandboxManager) -> list[dict[str, Any]]:
        """Promote this child's new artifacts into the parent's artifacts/<agent-id>/ tree.

        Copied-in artifacts are skipped, and promoted paths are recorded relative to the root.
        """
        if self.parent is None:
            return []
        self.check_turn()
        results = []
        total = 0
        with self.root._budget_lock:
            inputs = dict(self.root._child_receipts.get(self.agent_id, {}).get("inputs", {}))
        for path in self.list_workspace_files():
            if not path.startswith("artifacts/"):
                continue
            raw = self.read_workspace_file(path)
            if inputs.get(path) == hashlib.sha256(raw).hexdigest():
                continue  # an unchanged copied-in artifact is the parent's, not this child's output
            total += len(raw)
            if total > 25_000_000 or len(results) >= 100:
                raise ValueError("Child output promotion quota exceeded")
            target = f"artifacts/{self.agent_id}/{path.removeprefix('artifacts/')}"
            self.parent.write_workspace_file(target, raw)
            results.append({"path": target, "sha256": hashlib.sha256(raw).hexdigest(), "agent_id": self.agent_id})
        self.emit("agent_result", artifacts=results, status="succeeded")
        root_receipts = []
        for row in results:
            root_path = row["path"]
            ancestor = self.parent
            while ancestor is not None and ancestor.parent is not None:
                root_path = f"artifacts/{ancestor.agent_id}/{root_path.removeprefix('artifacts/')}"
                ancestor = ancestor.parent
            root_receipts.append({**row, "root_path": root_path})
        with self.root._budget_lock:
            self.root._child_receipts[self.agent_id]["artifacts"] = root_receipts
        self.record_child_outcome(True)
        return results

    def execution_evidence(self: DaytonaSandboxManager) -> dict[str, Any]:
        """Observe actual workspace bytes before teardown, never model-provided receipts."""
        from fleet_rlm.execution_evidence import collect_artifacts

        trace = phase_span("sandbox.artifact_collection") if mlflow.get_current_active_span() else nullcontext(None)
        with trace as phase:
            artifacts = collect_artifacts(self) if self._sandbox is not None else {"complete": True, "files": {}}
            if phase is not None:
                phase.set_outputs({"files": len(artifacts["files"]), "complete": artifacts["complete"]})
        with self.root._budget_lock:
            children = json.loads(json.dumps(list(self.root._child_receipts.values())))
        return {
            "schema_version": 1,
            "artifacts": artifacts,
            "children": children,
            "children_complete": all(child["status"] != "running" for child in children),
        }

    def verify_python_artifact(self: DaytonaSandboxManager, path: str, timeout_seconds: int) -> dict[str, Any]:
        """Execute one declared entry point in Daytona, bounded by the remaining Turn deadline.

        `timeout_seconds` must be an integer from 1 through 60. Return the file's SHA-256,
        process exit code, up to 65,536 stdout bytes decoded as UTF-8, and `complete`.
        A process timeout or oversized stdout sets `complete` to False; a nonzero exit
        code alone does not.

        Raises:
            ValueError: The artifact path, size, or verification timeout is invalid.
            TimeoutError: The Turn deadline has expired or too little time remains.
            RuntimeError: The Turn was cancelled.
            ExecutionOutcomeUnknownError: Execution or result decoding failed; cancellation
                is requested and the verification is not replayed.

        Errors while reading the file before verification also propagate.
        """
        from fleet_rlm.execution_evidence import MAX_ARTIFACT_BYTES, artifact_path

        artifact_path(path)
        if not path.endswith(".py") or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError("Invalid Python verification entry")
        self.check_turn()
        remaining = self.remaining_turn_seconds()
        if remaining is None:
            timeout = timeout_seconds
        else:
            if remaining <= 1:
                raise TimeoutError("No Turn time remains for artifact verification")
            timeout = min(timeout_seconds, max(1, int(remaining) - 1))
        raw = self.read_workspace_file(path, max_bytes=MAX_ARTIFACT_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        code = verify_python_artifact_code(path, digest=digest, timeout=timeout)
        try:
            result = json.loads(self._remote_exec(self.ensure_sandbox(), code, timeout=timeout + 1, retry=False))
        except Exception as exc:
            # The process transport cannot establish whether execution finished. Never replay it.
            self.request_cancellation()
            raise ExecutionOutcomeUnknownError("Python artifact verification outcome unknown") from exc
        self.check_turn()
        return {**result, "sha256": digest}
