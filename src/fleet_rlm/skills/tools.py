"""Host-mediated progressive Skill loading and resource reads."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

import dspy

from fleet_rlm.files.workspace_models import SessionWorkspaceFS
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
        "affordances": list(card.affordances),
    }


def skill_loaded_public_payload(skill: SkillDefinition) -> dict[str, Any]:
    card = skill.card
    return {"kind": "skill.loaded", "skill_id": str(card.id), "name": card.name, "version": card.version}


class SkillToolHost:
    """Turn-bound progressive tools over one immutable catalog.

    Lock discipline (RC-7): ``self._lock`` guards book-keeping only
    (``_loaded_ids``/``_installed_ids``/``_known_installed_paths``/
    ``_pending_events``). It is never held
    across brokered sandbox calls — a host lock held over a posted
    ``write_text`` deadlocks against the service loop's own synchronous
    ``drain_public_events`` on the same lock.
    """

    def __init__(
        self,
        catalog: SkillCatalog,
        *,
        allowed_skill_ids: frozenset[UUID] | None = None,
        max_loaded_skills: int = 4,
        workspace: SessionWorkspaceFS | None = None,
    ) -> None:
        self._catalog = catalog
        self._allowed_skill_ids = allowed_skill_ids
        self._max_loaded_skills = min(4, max(0, int(max_loaded_skills)))
        self._workspace = workspace
        self._loaded_ids: set[UUID] = set()
        self._installed_ids: set[UUID] = set()
        self._known_installed_paths: dict[UUID, set[str]] = {}
        self._pending_events: list[dict[str, Any]] = []
        self._lock = RLock()

    @staticmethod
    def _installed_paths(skill: SkillDefinition) -> tuple[str, ...]:
        return tuple(f"skills/{skill.card.name}/{resource.path}" for resource in skill.resources.values())

    def _install_resources(self, skill: SkillDefinition) -> tuple[str, ...]:
        """Best-effort projection of declared resources into Session Workspace.

        Resources stay read-only UTF-8 text owned by the bundled catalog. A
        partial Workspace failure does not unload the Skill, and the exact
        paths returned here are the writes known to have succeeded across this
        Turn's current or earlier install attempts. ``read_skill_resource``
        remains the canonical fallback for
        every declared resource, including ones not visible in Workspace.

        Lock discipline (RC-7): the brokered sandbox ``write_text`` calls run
        LOCK-FREE — the lock only guards the ``_installed_ids`` bookkeeping.
        An ID is marked installed only after every declared write succeeds,
        so later retries can recover a partial installation.
        """
        workspace = self._workspace
        if workspace is None:
            return ()
        declared_paths = self._installed_paths(skill)
        with self._lock:
            known = set(self._known_installed_paths.get(skill.card.id, ()))
            if skill.card.id in self._installed_ids:
                return declared_paths
        for path, resource in zip(declared_paths, skill.resources.values(), strict=True):
            if path in known:
                continue
            try:
                workspace.write_text(path, resource.content, overwrite=True)
            except Exception:
                break
            with self._lock:
                known.add(path)
                self._known_installed_paths.setdefault(skill.card.id, set()).add(path)
        with self._lock:
            if skill.resources and len(known) == len(skill.resources):
                self._installed_ids.add(skill.card.id)
            return tuple(path for path in declared_paths if path in known)

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
            # Registration itself is the install guard (RC-7): the brokered
            # sandbox writes below run lock-free so the service loop can never
            # block on this lock while a Fulfill thread waits on the post.
            self._loaded_ids.add(skill.card.id)
        self._install_resources(skill)
        with self._lock:
            self._pending_events.extend((skill_activated_public_payload(skill), skill_loaded_public_payload(skill)))

    def load_skill(self, skill_id: str, expected_version: str | None = None) -> dict[str, Any]:
        skill, error = self._resolve(skill_id, expected_version=expected_version)
        if error or skill is None:
            return {"ok": False, "error": error or "skill_not_found"}
        with self._lock:
            already_registered = skill.card.id in self._loaded_ids
            if not already_registered:
                if len(self._loaded_ids) >= self._max_loaded_skills:
                    return {"ok": False, "error": "skill_limit_exceeded"}
                self._loaded_ids.add(skill.card.id)
        installed_paths = self._install_resources(skill)
        if not already_registered:
            with self._lock:
                self._pending_events.extend((skill_activated_public_payload(skill), skill_loaded_public_payload(skill)))
        card = skill.card
        result: dict[str, Any] = {
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
        if self._workspace is not None:
            result["installed_paths"] = list(installed_paths)
        result["resource_install"] = {
            "declared": len(skill.resources),
            "installed": len(installed_paths),
            "complete": len(installed_paths) >= len(skill.resources),
        }
        return result

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
            dspy.Tool(
                self.load_skill,
                name="load_skill",
                desc=(
                    "Load an authorized Skill only when its advertised Skill Card is relevant to the current "
                    "request. Returns a dictionary with ok, skill_markdown, and resources on success, or error "
                    "on failure; do not load Skills speculatively."
                ),
            ),
            dspy.Tool(
                self.read_skill_resource,
                name="read_skill_resource",
                desc=(
                    "Read one relevant resource from a previously loaded Skill. Returns a dictionary with ok, "
                    "content, and resource metadata on success, or error on failure."
                ),
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
