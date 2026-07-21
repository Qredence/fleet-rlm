"""Host-mediated progressive Skill loading and resource reads."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import dspy

from fleet_rlm.rlm.events import JsonValue
from fleet_rlm.rlm.tool_observer import ToolEventView, bound_event_text
from fleet_rlm.skills.catalog import SkillCatalog
from fleet_rlm.skills.models import SkillDefinition


def skill_activated_public_payload(skill: SkillDefinition) -> dict[str, Any]:
    card = skill.card
    return {
        "kind": "skill.activated",
        "skill_id": str(card.id),
        "name": card.name,
        "version": card.version,
        "trust": "system",
        "affordances": [],
    }


def skill_loaded_public_payload(skill: SkillDefinition) -> dict[str, Any]:
    card = skill.card
    return {"kind": "skill.loaded", "skill_id": str(card.id), "name": card.name, "version": card.version}


class SkillToolHost:
    """Turn-bound progressive tools over one immutable catalog."""

    def __init__(
        self,
        catalog: SkillCatalog,
        *,
        allowed_skill_ids: frozenset[UUID] | None = None,
        max_loaded_skills: int = 4,
    ) -> None:
        self._catalog = catalog
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
            values = list(self._pending_events)
            self._pending_events.clear()
            return values

    def _resolve(self, skill_id: str, *, expected_version: str | None) -> tuple[SkillDefinition | None, str | None]:
        try:
            sid = UUID(str(skill_id).strip())
        except (ValueError, AttributeError, TypeError):
            return None, "skill_not_found"
        if self._allowed_skill_ids is not None and sid not in self._allowed_skill_ids:
            return None, "skill_not_found"
        skill = self._catalog.get(sid)
        if skill is None:
            return None, "skill_not_found"
        if expected_version is not None and str(expected_version).strip() != skill.card.version:
            return None, "version_mismatch"
        return skill, None

    def mark_preloaded(self, skill: SkillDefinition) -> None:
        with self._lock:
            if self._allowed_skill_ids is not None and skill.card.id not in self._allowed_skill_ids:
                raise ValueError("preloaded Skill is outside the explicit restriction")
            if skill.card.id in self._loaded_ids:
                return
            if len(self._loaded_ids) >= self._max_loaded_skills:
                raise ValueError("too many loaded Skills")
            self._loaded_ids.add(skill.card.id)
            self._pending_events.extend((skill_activated_public_payload(skill), skill_loaded_public_payload(skill)))

    def load_skill(self, skill_id: str, expected_version: str | None = None) -> dict[str, Any]:
        skill, error = self._resolve(skill_id, expected_version=expected_version)
        if error or skill is None:
            return {"ok": False, "error": error or "skill_not_found"}
        with self._lock:
            if skill.card.id not in self._loaded_ids:
                if len(self._loaded_ids) >= self._max_loaded_skills:
                    return {"ok": False, "error": "skill_limit_exceeded"}
                self.mark_preloaded(skill)
        card = skill.card
        return {
            "ok": True,
            "skill_id": str(card.id),
            "name": card.name,
            "description": card.description,
            "version": card.version,
            "skill_markdown": skill.instructions,
            "resources": [
                {
                    "path": resource.path,
                    "media_type": resource.media_type,
                    "byte_size": len(resource.content.encode("utf-8")),
                    "encoding": "utf-8",
                }
                for resource in skill.resources.values()
            ],
        }

    def read_skill_resource(
        self, skill_id: str, resource_path: str, expected_version: str | None = None
    ) -> dict[str, Any]:
        skill, error = self._resolve(skill_id, expected_version=expected_version)
        if error or skill is None:
            return {"ok": False, "error": error or "skill_not_found"}
        with self._lock:
            if skill.card.id not in self._loaded_ids:
                return {"ok": False, "error": "skill_not_loaded"}
        resource = skill.resources.get(resource_path)
        if resource is None:
            return {"ok": False, "error": "resource_not_found"}
        return {
            "ok": True,
            "skill_id": str(skill.card.id),
            "path": resource.path,
            "content": resource.content,
            "encoding": "utf-8",
            "media_type": resource.media_type,
            "byte_size": len(resource.content.encode("utf-8")),
        }

    def as_tools(self) -> tuple[dspy.Tool, dspy.Tool]:
        return (
            dspy.Tool(self.load_skill, name="load_skill", desc="Load an authorized Skill progressively."),
            dspy.Tool(
                self.read_skill_resource,
                name="read_skill_resource",
                desc="Read one resource from a previously loaded Skill.",
            ),
        )

    def event_views(self) -> Mapping[str, ToolEventView]:
        def project_input(arguments: Mapping[str, Any]) -> JsonValue:
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
                "load_skill": ToolEventView(input_projection=project_input, output_projection=load_output),
                "read_skill_resource": ToolEventView(input_projection=project_input, output_projection=resource_output),
            }
        )
