"""In-memory host Skill registry."""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from fleet_rlm.skills.errors import SkillNotFoundError, SkillValidationError
from fleet_rlm.skills.models import (
    SkillRecord,
    SkillResource,
    SkillResourceDescriptor,
    SkillScope,
    SkillTrust,
    SkillVisibility,
)
from fleet_rlm.skills.paths import normalize_skill_resource_path

MAX_SKILL_FILE_BYTES = 64 * 1024
MAX_SKILL_DIRECTORY_BYTES = 256 * 1024
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_RESOURCE_COUNT = 128
MAX_SKILL_RESOURCE_PATH_CHARS = 256
MAX_SKILL_MANIFEST_BYTES = 32 * 1024

_SAFE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_TEXT_RESOURCE_EXTENSIONS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv"})
_BINARY_ASSET_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"})
_EXCLUDED_RESOURCE_PARTS = frozenset({"__pycache__", ".pytest_cache", "tests", "test"})


def validate_skill_name(name: str) -> str:
    raw = (name or "").strip()
    if len(raw) > 64 or not _SAFE_NAME.fullmatch(raw):
        raise SkillValidationError("invalid skill name")
    return raw


def validate_skill_description(description: str) -> str:
    raw = (description or "").strip()
    if not raw or len(raw) > MAX_SKILL_DESCRIPTION_CHARS:
        raise SkillValidationError("invalid skill description")
    return raw


def validate_skill_version(version: str) -> str:
    raw = (version or "").strip()
    if not raw or not _SAFE_VERSION.fullmatch(raw):
        raise SkillValidationError("invalid skill version")
    return raw


def resource_encoding_for_path(path: str) -> Literal["utf-8", "base64"]:
    """Return the only allowed encoding for one declared Skill resource path."""
    normalized = normalize_skill_resource_path(path)
    pure_path = PurePosixPath(normalized)
    if (
        len(normalized) > MAX_SKILL_RESOURCE_PATH_CHARS
        or len(pure_path.parts) < 2
        or any(part.startswith(".") or part in _EXCLUDED_RESOURCE_PARTS for part in pure_path.parts)
    ):
        raise SkillValidationError("unsupported skill resource path")
    section, extension = pure_path.parts[0], pure_path.suffix.lower()
    if section == "scripts" and extension == ".py":
        return "utf-8"
    if section == "references" and extension in _TEXT_RESOURCE_EXTENSIONS:
        return "utf-8"
    if section == "assets" and (extension in _TEXT_RESOURCE_EXTENSIONS or extension == ".svg"):
        return "utf-8"
    if section == "assets" and extension in _BINARY_ASSET_EXTENSIONS:
        return "base64"
    raise SkillValidationError("unsupported skill resource path")


def _validate_manifest(resources: list[SkillResource]) -> None:
    if len(resources) > MAX_SKILL_RESOURCE_COUNT:
        raise SkillValidationError("skill resource manifest exceeds entry bound")
    manifest_bytes = sum(
        len(resource.descriptor.path.encode("utf-8")) + len(resource.descriptor.media_type.encode("utf-8")) + 32
        for resource in resources
    )
    if manifest_bytes > MAX_SKILL_MANIFEST_BYTES:
        raise SkillValidationError("skill resource manifest exceeds byte bound")


def _programmatic_resource(path: str, body: str | bytes) -> SkillResource:
    normalized = normalize_skill_resource_path(path)
    encoding = resource_encoding_for_path(normalized)
    if encoding == "base64" and isinstance(body, str):
        raise SkillValidationError("binary skill resources require bytes")
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if len(raw) > MAX_SKILL_FILE_BYTES:
        raise SkillValidationError("skill resource exceeds byte bound")
    if encoding == "utf-8":
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillValidationError("text skill resource must be UTF-8") from exc
    media_type = mimetypes.guess_type(normalized)[0] or (
        "text/plain" if encoding == "utf-8" else "application/octet-stream"
    )
    return SkillResource(
        SkillResourceDescriptor(
            path=normalized,
            media_type=media_type,
            byte_size=len(raw),
            encoding=encoding,
        ),
        raw,
    )


class InMemorySkillRegistry:
    """Process-local Skill catalog containing host-only records."""

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
        capability_refs: tuple[str, ...] = (),
        task_contract_ref: str | None = None,
        resources: tuple[SkillResource, ...] | tuple[str, ...] = (),
        resource_bodies: Mapping[str, str | bytes] | None = None,
        skill_markdown: str | None = None,
        license: str | None = None,
        compatibility: str | None = None,
        metadata: Mapping[str, str] | None = None,
        allowed_tools: tuple[str, ...] = (),
        skill_id: UUID | None = None,
    ) -> SkillRecord:
        safe_name = validate_skill_name(name)
        safe_description = validate_skill_description(description)
        safe_version = validate_skill_version(version)
        if scope not in {"system", "workspace"}:
            raise SkillValidationError("invalid skill scope")
        if trust not in {"system", "workspace", "untrusted"}:
            raise SkillValidationError("invalid skill trust")
        if visibility not in {"visible", "hidden"}:
            raise SkillValidationError("invalid skill visibility")
        if scope == "workspace" and workspace_id is None:
            raise SkillValidationError("workspace-scoped skills require workspace_id")
        if scope == "system" and workspace_id is not None:
            raise SkillValidationError("system skills must not set workspace_id")
        if not isinstance(instructions, str) or not instructions.strip():
            raise SkillValidationError("instructions are required on host records")

        values: list[SkillResource] = []
        for resource in resources:
            if isinstance(resource, SkillResource):
                descriptor = resource.descriptor
                expected_encoding = resource_encoding_for_path(descriptor.path)
                if (
                    normalize_skill_resource_path(descriptor.path) != descriptor.path
                    or descriptor.byte_size != len(resource.body)
                    or descriptor.byte_size > MAX_SKILL_FILE_BYTES
                    or not descriptor.media_type
                    or len(descriptor.media_type) > 128
                    or descriptor.encoding not in {"utf-8", "base64"}
                    or descriptor.encoding != expected_encoding
                ):
                    raise SkillValidationError("invalid skill resource descriptor")
                if descriptor.encoding == "utf-8":
                    try:
                        resource.body.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise SkillValidationError("text skill resource must be UTF-8") from exc
                values.append(resource)
            elif isinstance(resource, str):
                body = (resource_bodies or {}).get(resource)
                if body is not None:
                    values.append(_programmatic_resource(resource, body))
            else:
                raise SkillValidationError("invalid skill resource")
        existing_paths = {resource.path for resource in values}
        for path, body in (resource_bodies or {}).items():
            normalized = normalize_skill_resource_path(path)
            if normalized not in existing_paths:
                values.append(_programmatic_resource(normalized, body))
                existing_paths.add(normalized)
        values.sort(key=lambda resource: resource.path)
        if len(existing_paths) != len(values):
            raise SkillValidationError("duplicate skill resource path")
        _validate_manifest(values)

        markdown = skill_markdown if skill_markdown is not None else instructions
        if not isinstance(markdown, str) or not markdown.strip():
            raise SkillValidationError("SKILL.md is required")
        markdown_bytes = markdown.encode("utf-8")
        if len(markdown_bytes) > MAX_SKILL_FILE_BYTES:
            raise SkillValidationError("SKILL.md exceeds byte bound")
        total_bytes = len(markdown_bytes) + sum(resource.descriptor.byte_size for resource in values)
        if total_bytes > MAX_SKILL_DIRECTORY_BYTES:
            raise SkillValidationError("skill directory exceeds byte bound")

        if license is not None and (not isinstance(license, str) or not license.strip() or len(license) > 512):
            raise SkillValidationError("invalid skill license")
        if compatibility is not None and (
            not isinstance(compatibility, str) or not compatibility.strip() or len(compatibility) > 500
        ):
            raise SkillValidationError("invalid skill compatibility")
        metadata_values = dict(metadata or {})
        if len(metadata_values) > 32 or any(
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 64
            or not isinstance(value, str)
            or len(value) > 256
            for key, value in metadata_values.items()
        ):
            raise SkillValidationError("invalid skill metadata")
        if len(allowed_tools) > 64 or any(
            not isinstance(tool, str) or not tool.strip() or len(tool) > 128 for tool in allowed_tools
        ):
            raise SkillValidationError("invalid allowed tools")

        sid = skill_id or uuid4()
        if sid in self._items:
            raise SkillValidationError("skill id already registered")
        record = SkillRecord(
            id=sid,
            name=safe_name,
            description=safe_description,
            scope=scope,
            version=safe_version,
            trust=trust,
            visibility=visibility,
            workspace_id=workspace_id,
            affordances=tuple(dict.fromkeys(affordances)),
            resources_available=bool(values),
            capability_refs=tuple(dict.fromkeys(capability_refs)),
            task_contract_ref=task_contract_ref,
            instructions=instructions,
            skill_markdown=markdown,
            license=license,
            compatibility=compatibility,
            metadata=tuple(sorted(metadata_values.items())),
            allowed_tools=tuple(dict.fromkeys(allowed_tools)),
            resources=tuple(values),
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


class UnavailableSkillRegistry(InMemorySkillRegistry):
    """Catalog sentinel that lets a Turn degrade without exposing the cause."""

    unavailable = True

    def list_ids(self) -> tuple[UUID, ...]:
        return ()
