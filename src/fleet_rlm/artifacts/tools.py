"""Host-mediated Artifact Candidate tools for :class:`dspy.RLM`."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID, uuid4

import dspy

from fleet_rlm.artifacts.errors import ArtifactValidationError
from fleet_rlm.artifacts.models import KIND_EXTENSIONS, ArtifactCandidate, ArtifactKind
from fleet_rlm.artifacts.safety import (
    encode_content,
    media_type_for,
    parse_kind,
    sanitize_title,
    validate_content_size,
)
from fleet_rlm.json_types import JsonValue
from fleet_rlm.tool_events import ToolEventView, bound_event_text
from fleet_rlm.workspace.paths import VolumePaths, as_posix, normalize_workspace_path
from fleet_rlm.workspace.storage import VolumeBlobFs

_EVENT_TEXT_MAX_CHARS = 256


def _project_fields(result: object, fields: tuple[str, ...]) -> JsonValue:
    if not isinstance(result, Mapping):
        return {}
    values = cast(Mapping[str, JsonValue], result)
    return {
        key: bound_event_text(values[key], max_chars=_EVENT_TEXT_MAX_CHARS)
        if isinstance(values[key], str)
        else values[key]
        for key in fields
        if key in values
    }


class ArtifactToolHost:
    """Bound Artifact Candidate tools for one Run.

    This host owns only candidate validation, staging, and cleanup.  Durable
    Artifact publication remains exclusively in ``RunLifecycle.finish``.
    """

    def __init__(
        self,
        *,
        volume_fs: VolumeBlobFs,
        user_id: UUID,
        workspace_id: UUID,
        session_id: UUID,
        run_id: UUID,
        max_artifact_bytes: int = 10 * 1024 * 1024,
        volume_paths: VolumePaths | None = None,
    ) -> None:
        self._volume_fs = volume_fs
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._session_id = session_id
        self._run_id = run_id
        self._max_artifact_bytes = max(1, int(max_artifact_bytes))
        self._paths = volume_paths or VolumePaths.from_mount()
        self._pending_events: list[dict[str, Any]] = []
        self._artifact_candidates: list[ArtifactCandidate] = []
        self._artifact_staging_paths: list[str] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
        """Return and clear bounded metadata-only artifact events."""
        events = list(self._pending_events)
        self._pending_events.clear()
        return events

    def drain_artifact_candidates(self) -> tuple[ArtifactCandidate, ...]:
        candidates = tuple(self._artifact_candidates)
        self._artifact_candidates.clear()
        return candidates

    async def aclose(self) -> None:
        """Remove uncommitted Artifact Candidate bytes owned by this Run."""
        staging_paths = tuple(self._artifact_staging_paths)
        self._artifact_candidates.clear()
        self._artifact_staging_paths.clear()
        for staging_path in reversed(staging_paths):
            try:
                self._volume_fs.remove(staging_path)
            except Exception:
                continue

    def create_artifact(
        self,
        kind: str,
        content: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Stage a private Artifact Candidate. Turn Commit owns publication."""
        try:
            parsed_kind = parse_kind(kind)
            safe_title = sanitize_title(title)
            data = encode_content(parsed_kind, content)
            return self._stage_artifact_candidate(parsed_kind, data, safe_title)
        except ValueError as exc:
            return {"ok": False, "error": "validation", "message": str(exc)[:200]}
        except Exception:
            return {"ok": False, "error": "validation"}

    def _stage_artifact_candidate(
        self,
        parsed_kind: ArtifactKind,
        data: bytes,
        safe_title: str | None,
    ) -> dict[str, Any]:
        validate_content_size(len(data), max_bytes=self._max_artifact_bytes)
        artifact_id = uuid4()
        extension = KIND_EXTENSIONS[parsed_kind]
        staging_path = as_posix(
            self._paths.run_artifacts_dir(self._session_id, self._run_id) / f"{artifact_id}{extension}"
        )
        durable_path = as_posix(self._paths.artifact_blob_path(artifact_id))
        self._volume_fs.write_bytes(staging_path, data)
        candidate = ArtifactCandidate(
            id=artifact_id,
            user_id=self._user_id,
            workspace_id=self._workspace_id,
            session_id=self._session_id,
            run_id=self._run_id,
            kind=parsed_kind,
            title=safe_title,
            media_type=media_type_for(parsed_kind),
            byte_size=len(data),
            checksum_sha256=hashlib.sha256(data).hexdigest(),
            staging_path=staging_path,
            durable_path=durable_path,
        )

        self._artifact_candidates.append(candidate)
        self._artifact_staging_paths.append(candidate.staging_path)
        return {
            "ok": True,
            "artifact_candidate_id": str(candidate.id),
            "kind": candidate.kind,
            "title": candidate.title,
            "byte_size": candidate.byte_size,
            "checksum_sha256": candidate.checksum_sha256,
        }

    def publish_workspace_artifact(
        self,
        path: str,
        kind: str,
        title: str | None = None,
        expected_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Stage an existing Session Workspace file as a private candidate."""
        try:
            relative = normalize_workspace_path(path)
            parsed_kind = parse_kind(kind)
            safe_title = sanitize_title(title)
            source = as_posix(self._paths.session_workspace_dir(self._session_id) / relative)
            data = self._volume_fs.read_bytes(source)
            validate_content_size(len(data), max_bytes=self._max_artifact_bytes)
            text = data.decode("utf-8")
            encode_content(parsed_kind, text)
            checksum = hashlib.sha256(data).hexdigest()
            if expected_sha256 is not None and (
                not isinstance(expected_sha256, str) or not hmac.compare_digest(checksum, expected_sha256)
            ):
                return {"ok": False, "error": "checksum_mismatch"}
            result = self._stage_artifact_candidate(parsed_kind, data, safe_title)
            if result.get("ok") is True:
                self._pending_events.append(
                    {
                        "event_kind": "artifact.workspace_publish",
                        "path": relative,
                        "kind": parsed_kind,
                        "title": safe_title,
                        "byte_size": len(data),
                    }
                )
            return result
        except (ArtifactValidationError, ValueError, UnicodeError) as exc:
            return {"ok": False, "error": "validation", "message": str(exc)[:200]}
        except Exception:
            return {"ok": False, "error": "not_found"}

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Return the two Artifact tools in their frozen registration order."""

        def create_artifact(
            kind: str,
            content: str,
            title: str | None = None,
        ) -> dict[str, Any]:
            """Stage a text/markdown/json Artifact Candidate for Turn Commit."""
            return self.create_artifact(kind, content, title=title)

        def publish_workspace_artifact(
            path: str,
            kind: str,
            title: str | None = None,
            expected_sha256: str | None = None,
        ) -> dict[str, Any]:
            """Stage an existing Workspace file as an Artifact Candidate for Turn Commit."""
            return self.publish_workspace_artifact(path, kind, title=title, expected_sha256=expected_sha256)

        return (
            dspy.Tool(
                create_artifact,
                name="create_artifact",
                desc=(
                    "Stage generated text, markdown, or JSON as a private Artifact Candidate; it is promoted "
                    "only by a successful Turn Commit."
                ),
            ),
            dspy.Tool(
                publish_workspace_artifact,
                name="publish_workspace_artifact",
                desc=(
                    "Stage an existing durable Session Workspace text file as a private Artifact Candidate; "
                    "it is promoted only by a successful Turn Commit."
                ),
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return bounded metadata projections for Artifact tools."""

        def artifact_input(arguments: Mapping[str, Any]) -> JsonValue:
            content = arguments.get("content")
            return {
                "kind": bound_event_text(arguments.get("kind")),
                "title": bound_event_text(arguments.get("title")) if arguments.get("title") is not None else None,
                "content_chars": len(str(content or "")),
            }

        def workspace_artifact_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                "path": bound_event_text(arguments.get("path")),
                "kind": bound_event_text(arguments.get("kind")),
                "title": bound_event_text(arguments.get("title")) if arguments.get("title") is not None else None,
                "expected_sha256_present": bool(arguments.get("expected_sha256")),
            }

        return MappingProxyType(
            {
                "create_artifact": ToolEventView(
                    input_projection=artifact_input,
                    output_projection=lambda result: _project_fields(
                        result,
                        ("ok", "error", "kind", "title", "byte_size"),
                    ),
                ),
                "publish_workspace_artifact": ToolEventView(
                    input_projection=workspace_artifact_input,
                    output_projection=lambda result: _project_fields(
                        result,
                        ("ok", "error", "artifact_candidate_id", "kind", "title", "byte_size"),
                    ),
                ),
            }
        )


__all__ = ["ArtifactToolHost"]
