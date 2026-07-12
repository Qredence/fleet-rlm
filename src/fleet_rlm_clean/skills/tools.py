"""Host-mediated progressive skill tools for dspy.RLM."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fleet_rlm_clean.skills.authorize import SkillAuthorizer
from fleet_rlm_clean.skills.errors import SkillNotFoundError, SkillPathError
from fleet_rlm_clean.skills.models import SkillRecord
from fleet_rlm_clean.skills.paths import normalize_skill_resource_path

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
        max_skill_loads: int = 8,
        allowed_trust: frozenset[str] | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._user_id = user_id
        self._workspace_id = workspace_id
        self._max_skill_loads = max(0, int(max_skill_loads))
        self._allowed_trust = allowed_trust if allowed_trust is not None else _DEFAULT_TRUST
        self._load_count = 0
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
        if self._load_count >= self._max_skill_loads:
            return {"ok": False, "error": "budget_exceeded"}
        record, err = self._resolve_record(skill_id, expected_version=expected_version)
        if err or record is None:
            return {"ok": False, "error": err or "skill_not_found"}
        self._load_count += 1
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

    def as_tool_callables(self) -> tuple[Callable[..., Any], ...]:
        """Named callables suitable for dspy.RLM tools=."""

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

        return (load_skill, read_skill_resource)
