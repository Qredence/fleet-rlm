"""Host-mediated progressive Skill loading and resource reads."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import dspy

from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text
from fleet_rlm.skills.authorize import SkillAuthorizer
from fleet_rlm.skills.errors import SkillNotFoundError, SkillPathError
from fleet_rlm.skills.models import SkillRecord
from fleet_rlm.skills.paths import normalize_skill_resource_path

_DEFAULT_TRUST = frozenset({"system", "workspace"})


def skill_activated_public_payload(record: SkillRecord) -> dict[str, Any]:
    """Return bounded activation event metadata, never Skill bodies."""
    return {
        "kind": "skill.activated",
        "skill_id": str(record.id),
        "name": record.name,
        "version": record.version,
        "trust": record.trust,
        "affordances": list(record.affordances),
    }


def skill_loaded_public_payload(record: SkillRecord) -> dict[str, Any]:
    """Return bounded loaded event metadata, never Skill bodies."""
    return {
        "kind": "skill.loaded",
        "skill_id": str(record.id),
        "name": record.name,
        "version": record.version,
    }


class SkillToolHost:
    """Turn-bound progressive Skill tools with per-call authorization."""

    def __init__(
        self,
        authorizer: SkillAuthorizer,
        *,
        user_id: UUID,
        workspace_id: UUID,
        allowed_trust: frozenset[str] | None = None,
        allowed_skill_ids: frozenset[UUID] | None = None,
        max_loaded_skills: int = 4,
    ) -> None:
        self._authorizer = authorizer
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._allowed_trust = allowed_trust if allowed_trust is not None else _DEFAULT_TRUST
        self._allowed_skill_ids = allowed_skill_ids
        self._max_loaded_skills = min(4, max(0, int(max_loaded_skills)))
        self._loaded_ids: set[UUID] = set()
        self._pending_events: list[dict[str, Any]] = []
        self._lock = RLock()

    @property
    def loaded_skill_ids(self) -> frozenset[UUID]:
        with self._lock:
            return frozenset(self._loaded_ids)

    def drain_public_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._pending_events)
            self._pending_events.clear()
            return events

    def _resolve_record(
        self,
        skill_id: str,
        *,
        expected_version: str | None,
    ) -> tuple[SkillRecord | None, str | None]:
        try:
            sid = UUID(str(skill_id).strip())
        except (ValueError, AttributeError, TypeError):
            return None, "skill_not_found"
        if self._allowed_skill_ids is not None and sid not in self._allowed_skill_ids:
            return None, "skill_not_found"
        try:
            record = self._authorizer.get_record_if_authorized(
                sid,
                user_id=self._user_id,
                workspace_id=self._workspace_id,
                include_hidden=self._allowed_skill_ids is not None,
            )
        except SkillNotFoundError:
            return None, "skill_not_found"
        if expected_version is not None and str(expected_version).strip() != record.version:
            return None, "version_mismatch"
        if record.trust not in self._allowed_trust:
            return None, "untrusted"
        return record, None

    def mark_preloaded(self, record: SkillRecord) -> None:
        """Record an already-authorized explicit preload and its lifecycle events."""
        with self._lock:
            if self._allowed_skill_ids is not None and record.id not in self._allowed_skill_ids:
                raise ValueError("preloaded Skill is outside the explicit restriction")
            if record.id in self._loaded_ids:
                return
            if len(self._loaded_ids) >= self._max_loaded_skills:
                raise ValueError("too many loaded Skills")
            self._loaded_ids.add(record.id)
            self._pending_events.extend((skill_activated_public_payload(record), skill_loaded_public_payload(record)))

    def load_skill(self, skill_id: str, expected_version: str | None = None) -> dict[str, Any]:
        """Return full SKILL.md and its resource manifest after authorization."""
        record, error = self._resolve_record(skill_id, expected_version=expected_version)
        if error or record is None:
            return {"ok": False, "error": error or "skill_not_found"}
        with self._lock:
            if record.id not in self._loaded_ids:
                if len(self._loaded_ids) >= self._max_loaded_skills:
                    return {"ok": False, "error": "skill_limit_exceeded"}
                self.mark_preloaded(record)
        return {
            "ok": True,
            "skill_id": str(record.id),
            "name": record.name,
            "description": record.description,
            "version": record.version,
            "skill_markdown": record.skill_markdown,
            "metadata": {
                "license": record.license,
                "compatibility": record.compatibility,
                "allowed_tools": list(record.allowed_tools),
                "custom": dict(record.metadata),
            },
            "resources": [asdict(descriptor) for descriptor in record.resource_manifest()],
        }

    def read_skill_resource(
        self,
        skill_id: str,
        resource_path: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        """Read an allowlisted resource only after its Skill has been loaded."""
        record, error = self._resolve_record(skill_id, expected_version=expected_version)
        if error or record is None:
            return {"ok": False, "error": error or "skill_not_found"}
        with self._lock:
            if record.id not in self._loaded_ids:
                return {"ok": False, "error": "skill_not_loaded"}
        try:
            path = normalize_skill_resource_path(resource_path)
        except SkillPathError:
            return {"ok": False, "error": "invalid_path"}
        resource = record.resource_map().get(path)
        if resource is None:
            return {"ok": False, "error": "resource_not_found"}
        descriptor = resource.descriptor
        content = (
            resource.body.decode("utf-8")
            if descriptor.encoding == "utf-8"
            else base64.b64encode(resource.body).decode("ascii")
        )
        return {
            "ok": True,
            "skill_id": str(record.id),
            "path": path,
            "content": content,
            "encoding": descriptor.encoding,
            "media_type": descriptor.media_type,
            "byte_size": descriptor.byte_size,
        }

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Return canonical typed Tools owned by this host."""

        def load_skill(skill_id: str, expected_version: str | None = None) -> dict[str, Any]:
            """Load one authorized Skill body and resource manifest."""
            return self.load_skill(skill_id, expected_version=expected_version)

        def read_skill_resource(
            skill_id: str,
            resource_path: str,
            expected_version: str | None = None,
        ) -> dict[str, Any]:
            """Read one resource from a previously loaded Skill."""
            return self.read_skill_resource(
                skill_id,
                resource_path,
                expected_version=expected_version,
            )

        return (
            dspy.Tool(load_skill, name="load_skill", desc="Load an authorized Skill progressively."),
            dspy.Tool(
                read_skill_resource,
                name="read_skill_resource",
                desc="Read one resource from a previously loaded Skill.",
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return metadata-only public projections for Skill Tools."""

        def load_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                key: bound_event_text(arguments[key])
                for key in ("skill_id", "expected_version")
                if arguments.get(key) is not None
            }

        def resource_input(arguments: Mapping[str, Any]) -> JsonValue:
            return {
                key: bound_event_text(arguments[key])
                for key in ("skill_id", "resource_path", "expected_version")
                if arguments.get(key) is not None
            }

        def load_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            return {
                key: bound_event_text(values[key]) if isinstance(values[key], str) else values[key]
                for key in ("ok", "error", "skill_id", "name", "version")
                if key in values
            }

        def resource_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            return {
                key: bound_event_text(values[key]) if isinstance(values[key], str) else values[key]
                for key in ("ok", "error", "skill_id", "path", "encoding", "media_type", "byte_size")
                if key in values
            }

        return MappingProxyType(
            {
                "load_skill": ToolEventView(input_projection=load_input, output_projection=load_output),
                "read_skill_resource": ToolEventView(
                    input_projection=resource_input,
                    output_projection=resource_output,
                ),
            }
        )
