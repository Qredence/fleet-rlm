"""Load Agent Skills-compatible directories into the runtime catalog."""

from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml

from fleet_rlm.skills.errors import SkillValidationError
from fleet_rlm.skills.models import SkillRecord, SkillResource, SkillResourceDescriptor
from fleet_rlm.skills.registry import (
    MAX_SKILL_DIRECTORY_BYTES,
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_MANIFEST_BYTES,
    MAX_SKILL_RESOURCE_COUNT,
    MAX_SKILL_RESOURCE_PATH_CHARS,
    InMemorySkillRegistry,
    validate_skill_description,
    validate_skill_name,
    validate_skill_version,
)

_BUNDLED_SKILL_NAMESPACE = UUID("6f1e0c2a-9b3d-4e5f-8a1b-2c3d4e5f6071")
_FRONTMATTER_FENCE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_TEXT_EXTENSIONS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".csv"})
_ASSET_BINARY_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"})
_SKIPPED_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", "tests", "test"})


def bundled_skills_root() -> Path:
    """Return the on-disk root of package Skills."""
    return Path(__file__).resolve().parent / "skills"


def stable_skill_id(name: str) -> UUID:
    """Return the deterministic UUID for a bundled Skill name."""
    return uuid5(_BUNDLED_SKILL_NAMESPACE, name.strip())


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    """Parse real YAML frontmatter and return it with the instruction body."""
    if not isinstance(text, str) or not text.strip():
        raise SkillValidationError("empty skill markdown")
    match = _FRONTMATTER_FENCE.match(text)
    if match is None:
        raise SkillValidationError("skill markdown requires YAML frontmatter")
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillValidationError("invalid skill YAML frontmatter") from exc
    if not isinstance(value, Mapping):
        raise SkillValidationError("skill frontmatter must be a mapping")
    metadata = dict(value)
    body = text[match.end() :].strip()
    if not body:
        raise SkillValidationError("skill instructions body is required")
    return metadata, body


def _optional_text(meta: Mapping[str, Any], key: str, *, max_chars: int) -> str | None:
    value = meta.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SkillValidationError(f"{key} must be a string")
    result = value.strip()
    if not result or len(result) > max_chars:
        raise SkillValidationError(f"invalid {key}")
    return result


def _metadata(meta: Mapping[str, Any]) -> dict[str, str]:
    value = meta.get("metadata", {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SkillValidationError("metadata must be a mapping")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not raw_key.strip() or len(raw_key) > 64:
            raise SkillValidationError("invalid metadata key")
        if not isinstance(raw_value, (str, int, float, bool)):
            raise SkillValidationError("metadata values must be scalar")
        text = str(raw_value).strip()
        if len(text) > 256:
            raise SkillValidationError("metadata value exceeds bound")
        result[raw_key.strip()] = text
    if len(result) > 32:
        raise SkillValidationError("too many metadata fields")
    return result


def _allowed_tools(meta: Mapping[str, Any]) -> tuple[str, ...]:
    value = meta.get("allowed-tools")
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.split()
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        values = value
    else:
        raise SkillValidationError("allowed-tools must be a string or string list")
    tools = tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if len(tools) > 64 or any(len(tool) > 128 for tool in tools):
        raise SkillValidationError("allowed-tools exceeds bound")
    return tools


def _is_executable(path: Path) -> bool:
    try:
        return bool(path.stat(follow_symlinks=False).st_mode & 0o111)
    except OSError as exc:
        raise SkillValidationError("unable to inspect skill resource") from exc


def _media_type(path: Path, *, text: bool) -> str:
    overrides = {
        ".md": "text/markdown",
        ".py": "text/x-python",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".toml": "application/toml",
        ".svg": "image/svg+xml",
    }
    return (
        overrides.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or ("text/plain" if text else "application/octet-stream")
    )


def _resource_policy(relative: Path) -> tuple[bool, bool]:
    """Return (supported, text_encoded) for an allowed relative resource."""
    if len(relative.parts) < 2:
        return False, False
    section = relative.parts[0]
    extension = relative.suffix.lower()
    if section == "scripts":
        return extension == ".py", True
    if section == "references":
        return extension in _TEXT_EXTENSIONS, True
    if section == "assets":
        if extension in _TEXT_EXTENSIONS or extension == ".svg":
            return True, True
        return extension in _ASSET_BINARY_EXTENSIONS, False
    return False, False


def _collect_resources(skill_dir: Path) -> tuple[SkillResource, ...]:
    resources: list[SkillResource] = []
    for section in ("scripts", "references", "assets"):
        root = skill_dir / section
        if not root.exists() or not root.is_dir():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            symlinked_directories = [name for name in directories if (current_path / name).is_symlink()]
            if symlinked_directories:
                raise SkillValidationError("skill resources must not contain symlinks")
            directories[:] = [
                name
                for name in sorted(directories)
                if not name.startswith(".") and name not in _SKIPPED_DIRECTORY_NAMES
            ]
            for filename in sorted(files):
                path = current_path / filename
                relative = path.relative_to(skill_dir)
                if path.is_symlink():
                    raise SkillValidationError("skill resources must not contain symlinks")
                if filename.startswith(".") or any(part.startswith(".") for part in relative.parts):
                    continue
                if not path.is_file():
                    continue
                supported, text_encoded = _resource_policy(relative)
                if not supported:
                    continue
                if _is_executable(path):
                    continue
                try:
                    body = path.read_bytes()
                except OSError as exc:
                    raise SkillValidationError("unable to read skill resource") from exc
                if len(body) > MAX_SKILL_FILE_BYTES:
                    raise SkillValidationError("skill resource exceeds byte bound")
                if text_encoded:
                    try:
                        body.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise SkillValidationError("text skill resource must be UTF-8") from exc
                descriptor = SkillResourceDescriptor(
                    path=relative.as_posix(),
                    media_type=_media_type(path, text=text_encoded),
                    byte_size=len(body),
                    encoding="utf-8" if text_encoded else "base64",
                )
                resources.append(SkillResource(descriptor, body))
    resources.sort(key=lambda resource: resource.path)
    if len(resources) > MAX_SKILL_RESOURCE_COUNT:
        raise SkillValidationError("skill resource manifest exceeds entry bound")
    if any(len(resource.path) > MAX_SKILL_RESOURCE_PATH_CHARS for resource in resources):
        raise SkillValidationError("skill resource path exceeds bound")
    manifest_bytes = sum(
        len(resource.path.encode("utf-8")) + len(resource.descriptor.media_type.encode("utf-8")) + 32
        for resource in resources
    )
    if manifest_bytes > MAX_SKILL_MANIFEST_BYTES:
        raise SkillValidationError("skill resource manifest exceeds byte bound")
    return tuple(resources)


def _directory_byte_size(skill_dir: Path) -> int:
    """Bound the complete directory, including files omitted from the manifest."""
    total = 0
    for current, directories, files in os.walk(skill_dir, followlinks=False):
        current_path = Path(current)
        if any((current_path / name).is_symlink() for name in directories):
            raise SkillValidationError("skill directories must not contain symlinks")
        for filename in files:
            path = current_path / filename
            if path.is_symlink():
                raise SkillValidationError("skill directories must not contain symlinks")
            if path.is_file():
                try:
                    total += path.stat(follow_symlinks=False).st_size
                except OSError as exc:
                    raise SkillValidationError("unable to inspect skill directory") from exc
            if total > MAX_SKILL_DIRECTORY_BYTES:
                raise SkillValidationError("skill directory exceeds byte bound")
    return total


def load_skill_directory(skill_dir: Path) -> dict[str, Any]:
    """Parse one bounded Agent Skill directory into registry arguments."""
    if skill_dir.is_symlink() or not skill_dir.is_dir():
        raise SkillValidationError("skill directory must be a regular directory")
    _directory_byte_size(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if skill_md.is_symlink() or not skill_md.is_file():
        raise SkillValidationError(f"missing regular SKILL.md in {skill_dir}")
    try:
        raw = skill_md.read_bytes()
    except OSError as exc:
        raise SkillValidationError("unable to read SKILL.md") from exc
    if len(raw) > MAX_SKILL_FILE_BYTES:
        raise SkillValidationError("SKILL.md exceeds byte bound")
    try:
        markdown = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillValidationError("SKILL.md must be UTF-8") from exc

    meta, instructions = parse_skill_markdown(markdown)
    raw_name = meta.get("name")
    raw_description = meta.get("description")
    if not isinstance(raw_name, str):
        raise SkillValidationError("skill name is required")
    if not isinstance(raw_description, str):
        raise SkillValidationError("skill description is required")
    name = validate_skill_name(raw_name)
    description = validate_skill_description(raw_description)
    if name != skill_dir.name:
        raise SkillValidationError("skill name must match its directory")

    custom_metadata = _metadata(meta)
    version = validate_skill_version(custom_metadata.get("version", "1.0.0"))
    disable = meta.get("disable-model-invocation", False)
    if not isinstance(disable, bool):
        raise SkillValidationError("disable-model-invocation must be a boolean")
    resources = _collect_resources(skill_dir)
    capability_refs_value = meta.get("capability-refs", [])
    if not isinstance(capability_refs_value, list) or not all(
        isinstance(item, str) and item.strip() and len(item.strip()) <= 128 for item in capability_refs_value
    ):
        raise SkillValidationError("capability-refs must be a bounded string list")
    capability_refs = tuple(dict.fromkeys(item.strip() for item in capability_refs_value))
    task_contract = meta.get("task-contract-ref")
    if task_contract is not None and (
        not isinstance(task_contract, str) or not task_contract.strip() or len(task_contract.strip()) > 128
    ):
        raise SkillValidationError("task-contract-ref must be a bounded string")
    return {
        "name": name,
        "description": description,
        "instructions": instructions,
        "skill_markdown": markdown,
        "version": version,
        "scope": "system",
        "trust": "system",
        "visibility": "hidden" if disable else "visible",
        "affordances": ("load", "read_resource"),
        "resources": resources,
        "skill_id": stable_skill_id(name),
        "license": _optional_text(meta, "license", max_chars=512),
        "compatibility": _optional_text(meta, "compatibility", max_chars=500),
        "metadata": custom_metadata,
        "allowed_tools": _allowed_tools(meta),
        "capability_refs": capability_refs,
        "task_contract_ref": task_contract.strip() if isinstance(task_contract, str) else None,
    }


def iter_skill_directories(root: Path | None = None) -> tuple[Path, ...]:
    base = root if root is not None else bundled_skills_root()
    if not base.is_dir() or base.is_symlink():
        return ()
    return tuple(
        child
        for child in sorted(base.iterdir())
        if child.is_dir()
        and not child.is_symlink()
        and not child.name.startswith(".")
        and child.name != "__pycache__"
        and (child / "SKILL.md").is_file()
        and not (child / "SKILL.md").is_symlink()
    )


def seed_bundled_skills(
    registry: InMemorySkillRegistry,
    *,
    root: Path | None = None,
    skip_existing_names: bool = True,
) -> tuple[SkillRecord, ...]:
    """Register all bundled Skills and return records created in this call."""
    created: list[SkillRecord] = []
    existing_names = {record.name for record in registry.list_records()} if skip_existing_names else set()
    for skill_dir in iter_skill_directories(root):
        kwargs = load_skill_directory(skill_dir)
        if kwargs["name"] in existing_names or registry.get(kwargs["skill_id"]) is not None:
            continue
        record = registry.register(**kwargs)
        created.append(record)
        existing_names.add(record.name)
    return tuple(created)
