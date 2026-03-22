"""Recursive-task and evidence-link types for Daytona runs."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .types_serialization import (
    _coerce_nonnegative_int,
    _coerce_positive_int,
    _normalize_optional_text,
)

if TYPE_CHECKING:
    from .types_result import DaytonaRunResult


@dataclass(slots=True)
class TaskSourceProvenance:
    """Normalized provenance for one recursive child task."""

    kind: str
    source_id: str | None = None
    path: str | None = None
    line: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    chunk_index: int | None = None
    header: str | None = None
    pattern: str | None = None
    preview: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> TaskSourceProvenance:
        if not isinstance(raw, dict):
            return cls(kind="manual")

        kind = _normalize_optional_text(raw.get("kind")) or "manual"
        line = _coerce_positive_int(raw.get("line"))
        start_line = _coerce_positive_int(raw.get("start_line"))
        end_line = _coerce_positive_int(raw.get("end_line"))
        chunk_index = _coerce_nonnegative_int(raw.get("chunk_index"))

        if line is not None:
            start_line = start_line or line
            end_line = end_line or line
        if start_line is not None and end_line is None:
            end_line = start_line
        if start_line is not None and end_line is not None and end_line < start_line:
            end_line = start_line

        normalized = cls(
            kind=kind,
            source_id=_normalize_optional_text(raw.get("source_id")),
            path=_normalize_optional_text(raw.get("path")),
            line=line,
            start_line=start_line,
            end_line=end_line,
            chunk_index=chunk_index,
            header=_normalize_optional_text(raw.get("header")),
            pattern=_normalize_optional_text(raw.get("pattern")),
            preview=_normalize_optional_text(raw.get("preview"), limit=240),
        )
        if normalized.source_id is None:
            normalized.source_id = normalized._derive_source_id()
        return normalized

    def _derive_source_id(self) -> str | None:
        stable_parts = [
            self.kind,
            self.path or "",
            str(self.start_line or ""),
            str(self.end_line or ""),
            str(self.chunk_index if self.chunk_index is not None else ""),
            self.header or "",
            self.pattern or "",
        ]
        if not any(stable_parts[1:]):
            return None
        digest = hashlib.sha1(
            "|".join(stable_parts).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]
        return f"src-{digest}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RecursiveTaskSpec:
    """Normalized recursive child task spec."""

    task: str
    label: str | None = None
    source: TaskSourceProvenance = field(
        default_factory=lambda: TaskSourceProvenance(kind="manual")
    )

    @classmethod
    def from_raw(cls, raw: Any) -> RecursiveTaskSpec:
        if isinstance(raw, str):
            task = _normalize_optional_text(raw)
            if task is None:
                raise ValueError("Recursive task cannot be empty.")
            return cls(task=task)

        if isinstance(raw, dict):
            task = _normalize_optional_text(raw.get("task"))
            if task is None:
                raise ValueError("Recursive task cannot be empty.")
            label = _normalize_optional_text(raw.get("label"))
            source = TaskSourceProvenance.from_raw(raw.get("source"))
            return cls(task=task, label=label, source=source)

        raise ValueError(f"Unsupported recursive task payload: {type(raw).__name__}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "label": self.label,
            "source": self.source.to_dict(),
        }


@dataclass(slots=True)
class DaytonaEvidenceRef:
    """Normalized evidence reference used for recursive child synthesis."""

    kind: str
    source_id: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    header: str | None = None
    preview: str | None = None
    chunk_index: int | None = None
    pattern: str | None = None

    @classmethod
    def from_raw(cls, raw: Any) -> DaytonaEvidenceRef:
        if not isinstance(raw, dict):
            raise ValueError("Daytona evidence payload must be a dict.")

        line = _coerce_positive_int(raw.get("line"))
        start_line = _coerce_positive_int(raw.get("start_line")) or line
        end_line = _coerce_positive_int(raw.get("end_line")) or start_line
        if start_line is not None and end_line is not None and end_line < start_line:
            end_line = start_line

        evidence = cls(
            kind=_normalize_optional_text(raw.get("kind")) or "manual",
            source_id=_normalize_optional_text(raw.get("source_id")),
            path=_normalize_optional_text(raw.get("path")),
            start_line=start_line,
            end_line=end_line,
            header=_normalize_optional_text(raw.get("header")),
            preview=_normalize_optional_text(raw.get("preview"), limit=240),
            chunk_index=_coerce_nonnegative_int(raw.get("chunk_index")),
            pattern=_normalize_optional_text(raw.get("pattern")),
        )
        if evidence.source_id is None:
            evidence.source_id = evidence._derive_source_id()
        return evidence

    def _derive_source_id(self) -> str | None:
        stable_parts = [
            self.kind,
            self.path or "",
            str(self.start_line or ""),
            str(self.end_line or ""),
            str(self.chunk_index if self.chunk_index is not None else ""),
            self.header or "",
            self.pattern or "",
        ]
        if not any(stable_parts[1:]):
            return None
        digest = hashlib.sha1(
            "|".join(stable_parts).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]
        return f"evidence-{digest}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChildLink:
    """Persisted parent -> child recursive linkage."""

    child_id: str | None
    callback_name: str
    iteration: int | None
    task: RecursiveTaskSpec
    result_preview: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "callback_name": self.callback_name,
            "iteration": self.iteration,
            "task": self.task.to_dict(),
            "result_preview": self.result_preview,
            "status": self.status,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> ChildLink:
        if not isinstance(raw, dict):
            raise ValueError("Child link payload must be a dict.")
        return cls(
            child_id=_normalize_optional_text(raw.get("child_id")),
            callback_name=_normalize_optional_text(raw.get("callback_name"))
            or "llm_query",
            iteration=_coerce_positive_int(raw.get("iteration")),
            task=RecursiveTaskSpec.from_raw(raw.get("task", {})),
            result_preview=_normalize_optional_text(
                raw.get("result_preview"), limit=280
            )
            or "",
            status=_normalize_optional_text(raw.get("status")) or "completed",
        )


@dataclass(slots=True)
class ChildTaskResult:
    """Internal child execution result returned to recursive callers."""

    child_id: str | None
    task: RecursiveTaskSpec
    text: str
    result_preview: str
    status: str
    evidence: list[DaytonaEvidenceRef] = field(default_factory=list)
    confidence: float | None = None
    follow_up_needed: bool = False
    run_result: DaytonaRunResult | None = None


__all__ = [
    "TaskSourceProvenance",
    "RecursiveTaskSpec",
    "DaytonaEvidenceRef",
    "ChildLink",
    "ChildTaskResult",
]
