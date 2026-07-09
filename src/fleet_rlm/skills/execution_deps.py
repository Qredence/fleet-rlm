"""Runtime skill execution dependencies resolved at tool call time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fleet_rlm.skills.loader import default_skill_runtime_context
from fleet_rlm.skills.schemas import SkillResource, SkillRuntimeContext


@dataclass(frozen=True, slots=True)
class SkillExecutionDeps:
    volume_mount_path: str | None
    selected_skill_ids: tuple[str, ...]
    resources: dict[str, list[SkillResource]] | None
    sandbox_paths: dict[str, str] | None

    @classmethod
    def from_runtime(cls, runtime: Any, *, volume_mount_path: str | None) -> SkillExecutionDeps:
        active_skills = getattr(runtime, "_active_skills", None)
        resources = getattr(active_skills, "resources", None) if active_skills is not None else None
        sandbox_paths = getattr(active_skills, "sandbox_paths", None) if active_skills is not None else None
        return cls(
            volume_mount_path=volume_mount_path,
            selected_skill_ids=tuple(getattr(runtime, "_selected_skill_ids", None) or []),
            resources=resources,
            sandbox_paths=sandbox_paths,
        )

    def runtime_context(self) -> SkillRuntimeContext:
        return default_skill_runtime_context(
            volume_mount_path=self.volume_mount_path,
            selected_skill_ids=list(self.selected_skill_ids),
        )


__all__ = ["SkillExecutionDeps"]
