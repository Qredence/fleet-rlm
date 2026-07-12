"""In-memory skill registry (offline foundation; DB port later)."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from fleet_rlm_clean.skills.errors import SkillNotFoundError, SkillValidationError
from fleet_rlm_clean.skills.models import (
    SkillRecord,
    SkillScope,
    SkillTrust,
    SkillVisibility,
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def _validate_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw or not _SAFE_NAME.match(raw):
        raise SkillValidationError("invalid skill name")
    if "/" in raw or "\\" in raw or ".." in raw:
        raise SkillValidationError("invalid skill name")
    return raw


def _validate_version(version: str) -> str:
    raw = (version or "").strip()
    if not raw or not _SAFE_VERSION.match(raw):
        raise SkillValidationError("invalid skill version")
    return raw


class InMemorySkillRegistry:
    """Process-local skill catalog."""

    def __init__(self) -> None:
        self._items: dict[UUID, SkillRecord] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        scope: SkillScope = "system",
        version: str = "1.0.0",
        trust: SkillTrust = "system",
        visibility: SkillVisibility = "visible",
        workspace_id: UUID | None = None,
        affordances: tuple[str, ...] = ("load", "read_resource"),
        resources: tuple[str, ...] = (),
        resource_bodies: dict[str, str] | None = None,
        skill_id: UUID | None = None,
    ) -> SkillRecord:
        safe_name = _validate_name(name)
        safe_version = _validate_version(version)
        if scope == "workspace" and workspace_id is None:
            raise SkillValidationError("workspace-scoped skills require workspace_id")
        if scope == "system" and workspace_id is not None:
            raise SkillValidationError("system skills must not set workspace_id")
        if not isinstance(instructions, str) or not instructions.strip():
            raise SkillValidationError("instructions are required on host records")

        bodies = dict(resource_bodies or {})
        # Resource inventory is union of explicit names and body keys
        resource_names = tuple(dict.fromkeys([*resources, *bodies.keys()]))

        sid = skill_id or uuid4()
        if sid in self._items:
            raise SkillValidationError("skill id already registered")

        record = SkillRecord(
            id=sid,
            name=safe_name,
            description=(description or "").strip(),
            scope=scope,
            version=safe_version,
            trust=trust,
            visibility=visibility,
            workspace_id=workspace_id,
            affordances=tuple(affordances),
            resources_available=bool(resource_names),
            instructions=instructions,
            resources=resource_names,
            resource_bodies=tuple(sorted(bodies.items())),
        )
        self._items[sid] = record
        return record

    def get(self, skill_id: UUID) -> SkillRecord | None:
        return self._items.get(skill_id)

    def require(self, skill_id: UUID) -> SkillRecord:
        record = self.get(skill_id)
        if record is None:
            raise SkillNotFoundError("skill not found")
        return record

    def list_ids(self) -> tuple[UUID, ...]:
        return tuple(self._items.keys())

    def list_records(self) -> tuple[SkillRecord, ...]:
        return tuple(self._items.values())
