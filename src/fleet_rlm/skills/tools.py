"""Host-mediated progressive skill tools for dspy.RLM."""

from __future__ import annotations

from collections.abc import Mapping
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


def skill_loaded_public_payload(record: SkillRecord) -> dict[str, Any]:
    """Safe public event payload — never includes instructions or resource bodies."""
    return {
        "skill_id": str(record.id),
        "name": record.name,
        "version": record.version,
        "trust": record.trust,
    }


class SkillToolHost:
    """Bound tools for one turn: reauthorize every call; track safe public events."""

    def __init__(
        self,
        authorizer: SkillAuthorizer,
        *,
        user_id: UUID,
        workspace_id: UUID,
        allowed_trust: frozenset[str] | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._allowed_trust = allowed_trust if allowed_trust is not None else _DEFAULT_TRUST
        self._pending_events: list[dict[str, Any]] = []

    def drain_public_events(self) -> list[dict[str, Any]]:
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
        try:
            record = self._authorizer.get_record_if_authorized(
                sid,
                user_id=self._user_id,
                workspace_id=self._workspace_id,
            )
        except SkillNotFoundError:
            return None, "skill_not_found"
        if expected_version is not None and str(expected_version).strip() != record.version:
            return None, "version_mismatch"
        if record.trust not in self._allowed_trust:
            return None, "untrusted"
        return record, None

    def load_skill(
        self,
        skill_id: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        """Return instructions only after host reauth. Emits skill.loaded ledger entry."""
        record, err = self._resolve_record(skill_id, expected_version=expected_version)
        if err or record is None:
            return {"ok": False, "error": err or "skill_not_found"}
        self._pending_events.append(skill_loaded_public_payload(record))
        return {
            "ok": True,
            "skill_id": str(record.id),
            "name": record.name,
            "version": record.version,
            "instructions": record.instructions,
            "resources": list(record.resources),
        }

    def read_skill_resource(
        self,
        skill_id: str,
        resource_path: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        """Read one skill-relative resource after reauth + path normalize."""
        record, err = self._resolve_record(skill_id, expected_version=expected_version)
        if err or record is None:
            return {"ok": False, "error": err or "skill_not_found"}
        try:
            path = normalize_skill_resource_path(resource_path)
        except SkillPathError:
            return {"ok": False, "error": "invalid_path"}
        if path not in record.resources:
            return {"ok": False, "error": "resource_not_found"}
        body = record.resource_body_map().get(path)
        if body is None:
            return {"ok": False, "error": "resource_not_found"}
        return {
            "ok": True,
            "skill_id": str(record.id),
            "path": path,
            "content": body,
        }

    def as_tools(self) -> tuple[dspy.Tool, ...]:
        """Return the canonical typed Tools owned by this host."""

        def load_skill(
            skill_id: str,
            expected_version: str | None = None,
        ) -> dict[str, Any]:
            """Load authorized skill instructions (host rechecks every call)."""
            return self.load_skill(skill_id, expected_version=expected_version)

        def read_skill_resource(
            skill_id: str,
            resource_path: str,
            expected_version: str | None = None,
        ) -> dict[str, Any]:
            """Read one skill-relative resource after host reauthorization."""
            return self.read_skill_resource(
                skill_id,
                resource_path,
                expected_version=expected_version,
            )

        return (
            dspy.Tool(
                load_skill,
                name="load_skill",
                desc="Load one authorized Skill instruction body after host reauthorization.",
            ),
            dspy.Tool(
                read_skill_resource,
                name="read_skill_resource",
                desc="Read one authorized Skill resource after host reauthorization.",
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        """Return bounded public metadata projections for Skill Tools."""

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
                for key in ("ok", "error", "skill_id", "name", "version", "trust")
                if key in values
            }

        def resource_output(result: object) -> JsonValue:
            if not isinstance(result, Mapping):
                return {}
            values = cast(Mapping[str, JsonValue], result)
            content = result.get("content")
            projected: dict[str, JsonValue] = {
                key: bound_event_text(values[key]) if isinstance(values[key], str) else values[key]
                for key in ("ok", "error", "skill_id", "path")
                if key in values
            }
            if isinstance(content, str):
                projected["content_chars"] = len(content)
                projected["byte_size"] = len(content.encode("utf-8"))
            return projected

        return MappingProxyType(
            {
                "load_skill": ToolEventView(input_projection=load_input, output_projection=load_output),
                "read_skill_resource": ToolEventView(
                    input_projection=resource_input,
                    output_projection=resource_output,
                ),
            }
        )
