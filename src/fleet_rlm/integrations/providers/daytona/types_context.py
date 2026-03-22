"""Prompt and staged-context types for Daytona workspaces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .types_serialization import (
    _PROMPT_PREVIEW_LIMIT,
    _coerce_nonnegative_int,
    _coerce_positive_int,
    _normalize_optional_text,
)


@dataclass(slots=True)
class PromptHandle:
    """Metadata for one externalized prompt object stored in the sandbox."""

    handle_id: str
    kind: str
    label: str | None = None
    path: str = ""
    char_count: int = 0
    line_count: int = 0
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> PromptHandle:
        if not isinstance(raw, dict):
            raise ValueError("Prompt handle payload must be a dict.")
        handle_id = _normalize_optional_text(raw.get("handle_id"))
        if handle_id is None:
            raise ValueError("Prompt handle is missing handle_id.")
        kind = _normalize_optional_text(raw.get("kind")) or "manual"
        return cls(
            handle_id=handle_id,
            kind=kind,
            label=_normalize_optional_text(raw.get("label")),
            path=str(raw.get("path", "") or ""),
            char_count=_coerce_nonnegative_int(raw.get("char_count")) or 0,
            line_count=_coerce_nonnegative_int(raw.get("line_count")) or 0,
            preview=_normalize_optional_text(
                raw.get("preview"), limit=_PROMPT_PREVIEW_LIMIT
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromptSliceRef:
    """Metadata describing one bounded prompt slice read from the sandbox."""

    handle_id: str
    start_line: int | None = None
    end_line: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> PromptSliceRef:
        if not isinstance(raw, dict):
            raise ValueError("Prompt slice payload must be a dict.")
        handle_id = _normalize_optional_text(raw.get("handle_id"))
        if handle_id is None:
            raise ValueError("Prompt slice is missing handle_id.")
        return cls(
            handle_id=handle_id,
            start_line=_coerce_positive_int(raw.get("start_line")),
            end_line=_coerce_positive_int(raw.get("end_line")),
            start_char=_coerce_nonnegative_int(raw.get("start_char")),
            end_char=_coerce_nonnegative_int(raw.get("end_char")),
            preview=_normalize_optional_text(
                raw.get("preview"), limit=_PROMPT_PREVIEW_LIMIT
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PromptManifest:
    """Collection of prompt handles currently available in one sandbox."""

    handles: list[PromptHandle] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> PromptManifest:
        if not isinstance(raw, dict):
            return cls()
        handles_raw = raw.get("handles", [])
        if not isinstance(handles_raw, list):
            return cls()
        handles: list[PromptHandle] = []
        for item in handles_raw:
            try:
                handles.append(PromptHandle.from_raw(item))
            except ValueError:
                continue
        return cls(handles=handles)

    def to_dict(self) -> dict[str, Any]:
        return {"handles": [handle.to_dict() for handle in self.handles]}


@dataclass(slots=True)
class ContextSource:
    """Host-sourced local context staged into the Daytona workspace."""

    source_id: str
    kind: str
    host_path: str
    staged_path: str
    source_type: str | None = None
    extraction_method: str | None = None
    file_count: int = 1
    skipped_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> ContextSource:
        if not isinstance(raw, dict):
            raise ValueError("Context source payload must be a dict.")
        source_id = _normalize_optional_text(raw.get("source_id"))
        kind = _normalize_optional_text(raw.get("kind"))
        host_path = _normalize_optional_text(raw.get("host_path"))
        staged_path = _normalize_optional_text(raw.get("staged_path"))
        if (
            source_id is None
            or kind is None
            or host_path is None
            or staged_path is None
        ):
            raise ValueError(
                "Context source requires source_id, kind, host_path, and staged_path."
            )
        return cls(
            source_id=source_id,
            kind=kind,
            host_path=host_path,
            staged_path=staged_path,
            source_type=_normalize_optional_text(raw.get("source_type")),
            extraction_method=_normalize_optional_text(raw.get("extraction_method")),
            file_count=_coerce_positive_int(raw.get("file_count")) or 1,
            skipped_count=_coerce_nonnegative_int(raw.get("skipped_count")) or 0,
            warnings=[
                str(item)
                for item in raw.get("warnings", []) or []
                if item is not None and str(item).strip()
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "PromptHandle",
    "PromptSliceRef",
    "PromptManifest",
    "ContextSource",
]
