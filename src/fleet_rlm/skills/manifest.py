"""Validated bundled Skill manifest parsing (expand phase).

The manifest is the future Skill-owned source for discovery and resource
metadata. Runtime catalog construction remains separately owned until the
contracted migration ticket replaces the host declarations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import cast

import yaml

from fleet_rlm.skills.models import _SAFE_AFFORDANCE, _SAFE_NAME, _SAFE_VERSION, SkillResource

_ROOT_FIELDS = frozenset({"name", "description", "compatibility", "metadata", "allowed-tools", "resources"})
_METADATA_FIELDS = frozenset({"version", "affordances"})
_RESOURCE_FIELDS = frozenset({"path", "media_type"})


@dataclass(frozen=True, slots=True)
class SkillManifestResource:
    """One explicitly declared UTF-8 resource in a Skill manifest."""

    path: str
    media_type: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        # Reuse the proven path/body contract rather than defining a second
        # resource vocabulary for the manifest layer.
        SkillResource(self.path, self.media_type, self.content)


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """Validated Skill-owned discovery, workflow, and resource metadata."""

    name: str
    description: str
    version: str
    compatibility: str
    affordances: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    resources: tuple[SkillManifestResource, ...]
    instructions: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or len(self.name) > 64 or _SAFE_NAME.fullmatch(self.name) is None:
            raise ValueError("invalid Skill manifest name")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or len(self.description) > 512
            or not self.description.isprintable()
        ):
            raise ValueError("Skill manifest description is invalid")
        if (
            not isinstance(self.version, str)
            or self.version != self.version.strip()
            or not self.version.isprintable()
            or _SAFE_VERSION.fullmatch(self.version) is None
        ):
            raise ValueError("invalid Skill manifest version")
        if (
            not isinstance(self.compatibility, str)
            or not self.compatibility.strip()
            or len(self.compatibility) > 256
            or not self.compatibility.isprintable()
        ):
            raise ValueError("Skill manifest compatibility is invalid")
        if (
            len(self.affordances) > 8
            or len(set(self.affordances)) != len(self.affordances)
            or any(
                not isinstance(value, str)
                or not 1 <= len(value) <= 32
                or not value.isprintable()
                or _SAFE_AFFORDANCE.fullmatch(value) is None
                for value in self.affordances
            )
        ):
            raise ValueError("invalid Skill manifest affordances")
        if len(self.allowed_tools) > 32 or any(
            not isinstance(value, str)
            or not 1 <= len(value) <= 64
            or not value.isprintable()
            or _SAFE_AFFORDANCE.fullmatch(value) is None
            for value in self.allowed_tools
        ):
            raise ValueError("invalid Skill manifest allowed-tool guidance")
        resource_paths = tuple(resource.path for resource in self.resources)
        if len(set(resource_paths)) != len(resource_paths):
            raise ValueError("duplicate Skill manifest resource")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("Skill manifest instructions are required")


def _frontmatter(document: str, *, source: str) -> str:
    if not document.startswith("---\n"):
        raise ValueError(f"{source} must start with YAML frontmatter")
    boundary = document.find("\n---\n", 4)
    if boundary < 0:
        raise ValueError(f"{source} must close YAML frontmatter")
    return document[4:boundary]


def _required_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Skill manifest {field} must be a mapping")
    return cast(dict[str, object], value)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Skill manifest {field} must be a non-empty string")
    return value


def _string_list(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(value.split())
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"Skill manifest {field} must be a string or list of strings")
    values = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"Skill manifest {field} must contain only non-empty strings")
    return cast(tuple[str, ...], values)


def parse_skill_manifest(document: str, *, source: str = "SKILL.md") -> SkillManifest:
    """Parse one validated manifest without reading sibling resources.

    Resource declarations are shape-validated here; callers that can access the
    bundle directory should use ``parse_bundled_skill_manifest`` so declared
    and undeclared resource bodies are verified too.
    """
    frontmatter = _frontmatter(document, source=source)
    try:
        loaded = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise ValueError(f"Skill manifest frontmatter is malformed: {source}") from exc
    root = _required_mapping(loaded, "frontmatter")
    unknown = set(root) - _ROOT_FIELDS
    if unknown:
        raise ValueError(f"unknown Skill manifest fields: {sorted(unknown)}")
    missing = {"name", "description", "compatibility", "metadata", "allowed-tools"} - set(root)
    if missing:
        raise ValueError(f"missing Skill manifest fields: {sorted(missing)}")
    metadata = _required_mapping(root["metadata"], "metadata")
    if set(metadata) != _METADATA_FIELDS:
        raise ValueError("Skill manifest metadata must contain only version")

    resources_value = root.get("resources", [])
    if not isinstance(resources_value, Sequence) or isinstance(resources_value, (str, bytes, bytearray)):
        raise ValueError("Skill manifest resources must be a list")
    resources: list[SkillManifestResource] = []
    for index, raw_resource in enumerate(resources_value):
        resource = _required_mapping(raw_resource, f"resources[{index}]")
        if set(resource) != _RESOURCE_FIELDS:
            raise ValueError(f"Skill manifest resources[{index}] must contain only path and media_type")
        path = _required_string(resource["path"], f"resources[{index}].path")
        media_type = _required_string(resource["media_type"], f"resources[{index}].media_type")
        # A bodyless resource declaration is a valid shape in frontmatter but
        # not a complete manifest; bundle parsing supplies the UTF-8 body.
        resources.append(SkillManifestResource(path, media_type, ""))
    return SkillManifest(
        name=_required_string(root["name"], "name"),
        description=_required_string(root["description"], "description"),
        version=_required_string(metadata["version"], "metadata.version"),
        compatibility=_required_string(root["compatibility"], "compatibility"),
        affordances=_string_list(metadata["affordances"], "metadata.affordances"),
        allowed_tools=_string_list(root["allowed-tools"], "allowed-tools"),
        resources=tuple(resources),
        instructions=document.split("\n---\n", 1)[1].strip() + "\n",
    )


def parse_bundled_skill_manifest(directory: Path) -> SkillManifest:
    """Read one bundled Skill directory and validate declared resource bodies."""
    skill_path = directory / "SKILL.md"
    if not skill_path.is_file():
        raise ValueError("Skill manifest SKILL.md is required")
    manifest = parse_skill_manifest(skill_path.read_text(encoding="utf-8"), source=str(skill_path))
    body_by_path: dict[str, str] = {}
    for declaration in manifest.resources:
        path = PurePosixPath(declaration.path)
        disk_path = directory.joinpath(*path.parts)
        bundle_root = directory.resolve()
        if disk_path.is_symlink() or not disk_path.resolve().is_relative_to(bundle_root):
            raise ValueError(f"Skill manifest declares an unsafe resource path: {declaration.path}")
        if not disk_path.is_file():
            raise ValueError(f"Skill manifest declares missing resource body: {declaration.path}")
        body = disk_path.read_text(encoding="utf-8")
        body_by_path[declaration.path] = body
    disk_resource_paths = {
        str(path.relative_to(directory).as_posix())
        for path in directory.rglob("*")
        if path.is_file() and path.name != "SKILL.md"
    }
    undeclared = disk_resource_paths - set(body_by_path)
    if undeclared:
        raise ValueError(f"Skill bundle contains undeclared resource bodies: {sorted(undeclared)}")
    return SkillManifest(
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        compatibility=manifest.compatibility,
        affordances=manifest.affordances,
        allowed_tools=manifest.allowed_tools,
        resources=tuple(
            SkillManifestResource(resource.path, resource.media_type, body_by_path[resource.path])
            for resource in manifest.resources
        ),
        instructions=manifest.instructions,
    )
