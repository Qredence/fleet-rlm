"""Editable, non-secret Fleet TOML policy service.

This module owns the committed policy document only.  It never reads values from
``.env`` or process environment variables, so callers cannot use it to recover
credentials or runtime overrides.

The editable-field inventory derives from the authoritative
:class:`fleet_rlm.config.settings.FleetFieldPolicy` declarations on ``Settings`` fields
plus their ``*_env`` reference specs; nothing here mirrors field names by hand.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit import TOMLDocument

from fleet_rlm.config.loader import (
    _MISSING,
    _flatten_policy,
    _lookup_toml,
    _policy_document_from_mapping,
)
from fleet_rlm.config.settings import (
    EditorKind,
    FleetConfigurationError,
    Settings,
    config_field_specs,
)
from fleet_rlm.workspace.memory import normalize_memory_candidate_categories
from fleet_rlm.workspace.models import WorkspaceMemoryCategoryError

__all__ = [
    "ConfigPolicyService",
    "PolicyAccessError",
    "PolicyConflictError",
    "PolicyMutation",
]


class PolicyConflictError(ValueError):
    """Raised when a caller attempts to overwrite a newer policy revision."""


class PolicyAccessError(ValueError):
    """Raised when a policy document is unsafe to edit."""


@dataclass(frozen=True, slots=True)
class PolicyField:
    path: str
    group: str
    label: str
    editor: EditorKind
    choices: tuple[str, ...] = ()
    settings_field: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyMutation:
    """One validated non-secret policy change in an atomic settings transaction."""

    path: str
    value: Any


# Derived from the authoritative Settings policy declarations (config/settings.py);
# no Settings field names or TOML paths are authored here by hand.
_FIELDS: tuple[PolicyField, ...] = tuple(
    PolicyField(
        path=spec.toml_path,
        group=spec.group,
        label=spec.label,
        editor=spec.editor,
        choices=spec.choices,
        settings_field=spec.settings_field,
    )
    for spec in config_field_specs()
    if spec.group is not None
)
_FIELD_BY_PATH = {field.path: field for field in _FIELDS}


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    revision: str
    fields: tuple[dict[str, Any], ...]


class ConfigPolicyService:
    """Read and safely edit the fixed Fleet TOML policy path."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._lock = threading.Lock()

    @classmethod
    def for_runtime(cls, *, path: Path | None = None) -> ConfigPolicyService:
        """Construct the service for the canonical policy path."""
        from fleet_rlm.config.loader import _CONFIG_PATH

        return cls(path or _CONFIG_PATH)

    def read(self) -> PolicySnapshot:
        with self._lock:
            document, raw = self._read_document()
            return self._snapshot(document, raw)

    def update(self, *, path: str, value: Any, revision: str) -> PolicySnapshot:
        return self.apply(updates=(PolicyMutation(path=path, value=value),), revision=revision)

    def apply(
        self,
        *,
        updates: Sequence[PolicyMutation] = (),
        revision: str,
    ) -> PolicySnapshot:
        """Validate and atomically persist one batch of non-secret policy changes."""
        if not updates:
            raise FleetConfigurationError("settings update is empty")
        targets = [update.path for update in updates]
        if len(set(targets)) != len(targets):
            raise FleetConfigurationError("settings update contains duplicate fields")
        with self._lock:
            document, raw = self._read_document()
            if revision != self._revision(raw):
                raise PolicyConflictError("settings changed; reload before saving")
            for update in updates:
                field = _FIELD_BY_PATH.get(update.path)
                if field is None:
                    raise FleetConfigurationError("unsupported settings field")
                parent, key = self._parent_table(document, update.path)
                parent[key] = self._normalize_value(field, update.value)
            rendered = tomlkit.dumps(document)
            self._validate(rendered)
            self._atomic_write(rendered)
            updated, updated_raw = self._read_document()
            return self._snapshot(updated, updated_raw)

    def _read_document(self) -> tuple[TOMLDocument, str]:
        if self._path.is_symlink() or not self._path.is_file():
            raise PolicyAccessError("Fleet configuration file is unavailable")
        raw = self._path.read_text(encoding="utf-8")
        try:
            return tomlkit.parse(raw), raw
        except Exception as exc:  # tomlkit does not expose a stable parse error base class.
            raise FleetConfigurationError("invalid Fleet configuration TOML") from exc

    def _snapshot(self, document: TOMLDocument, raw: str) -> PolicySnapshot:
        self._validate(raw)
        values: list[dict[str, Any]] = []
        for field in _FIELDS:
            value = self._lookup(document, field.path)
            if value is _MISSING:
                continue
            values.append(
                {
                    "path": field.path,
                    "group": field.group,
                    "label": field.label,
                    "value": value,
                    "editor": field.editor,
                    "choices": list(field.choices),
                    "environment_overridden": False,
                }
            )
        return PolicySnapshot(self._revision(raw), tuple(values))

    @staticmethod
    def _parent_table(table: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
        parts = path.split(".")
        current = table
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                current[part] = tomlkit.table()
                child = current[part]
            current = child
        return current, parts[-1]

    @staticmethod
    def _lookup(table: dict[str, Any], path: str) -> Any:
        current = _lookup_toml(table, path)
        if current is _MISSING:
            return current
        return current.unwrap() if hasattr(current, "unwrap") else current

    @staticmethod
    def _revision(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_value(field: PolicyField, value: Any) -> Any:
        if field.editor == "text":
            if not isinstance(value, str):
                raise FleetConfigurationError("settings value must be text")
            return value
        if field.editor == "boolean":
            if not isinstance(value, bool):
                raise FleetConfigurationError("settings value must be boolean")
            return value
        if field.editor == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FleetConfigurationError("settings value must be numeric")
            return value
        if field.editor == "single_choice":
            if not isinstance(value, str) or value not in field.choices:
                raise FleetConfigurationError("settings value is not an allowed choice")
            return value
        if field.editor == "multi_choice":
            if not isinstance(value, list) or any(item not in field.choices for item in value):
                raise FleetConfigurationError("settings value contains an invalid choice")
            return value
        if field.editor == "string_list":
            if isinstance(value, str):
                items = [item.strip() for item in value.split(",") if item.strip()]
            elif isinstance(value, list):
                if any(not isinstance(item, str) for item in value):
                    raise FleetConfigurationError("settings value must be a list of text categories")
                items = value
            else:
                raise FleetConfigurationError("settings value must be a category list")
            try:
                return list(normalize_memory_candidate_categories(items))
            except WorkspaceMemoryCategoryError as exc:
                raise FleetConfigurationError("settings value contains an invalid Workspace Memory category") from exc
        raise AssertionError(f"unsupported editor {field.editor}")

    def _validate(self, raw: str) -> None:
        """Validate the single Fleet policy TOML."""
        try:
            root = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise FleetConfigurationError("invalid Fleet configuration TOML") from exc
        document = _policy_document_from_mapping(root)
        flattened = _flatten_policy(document.policy)
        try:
            Settings.model_validate(dict(flattened.settings))
        except ValueError as exc:
            raise FleetConfigurationError("invalid Fleet configuration policy") from exc

    def _atomic_write(self, content: str) -> None:
        parent = self._path.parent
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, self._path.stat().st_mode)
            os.replace(temporary, self._path)
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
