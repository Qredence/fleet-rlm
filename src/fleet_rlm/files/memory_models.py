"""Runtime-neutral Workspace Memory values and storage port."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

WORKSPACE_MEMORY_BYTE_BUDGET = 262_144
WORKSPACE_MEMORY_MAX_RECORD_BYTES = 4_096
_CATEGORY_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]*")
_MEMORY_RECORD = re.compile(
    r"- \[(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\] "
    r"\*\*(?P<category>[A-Za-z0-9][A-Za-z0-9 _-]*)\*\*: "
    r"(?P<learning>[^\r\n]+)\n"
)


class WorkspaceMemoryRecordError(ValueError):
    """A Workspace Memory record or learning violates the canonical format."""


class WorkspaceMemoryCategoryError(WorkspaceMemoryRecordError):
    """A Workspace Memory category violates the canonical format."""


def normalize_workspace_memory_category(category: str) -> str:
    """Return one valid plain category using the current Tool contract."""
    if not isinstance(category, str) or "\x00" in category:
        raise WorkspaceMemoryCategoryError
    normalized = category.strip()
    if not normalized or len(normalized) > 64 or _CATEGORY_LABEL.fullmatch(normalized) is None:
        raise WorkspaceMemoryCategoryError
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceMemoryCategoryError from exc
    return normalized


def format_workspace_memory_record(
    key_learning: str,
    category: str,
    *,
    timestamp: datetime,
) -> tuple[str, str]:
    """Normalize and format one canonical Workspace Memory record."""
    if not isinstance(key_learning, str) or "\x00" in key_learning:
        raise WorkspaceMemoryRecordError
    learning = " ".join(key_learning.split())
    if not learning:
        raise WorkspaceMemoryRecordError
    normalized_category = normalize_workspace_memory_category(category)
    if not isinstance(timestamp, datetime):
        raise WorkspaceMemoryRecordError
    record = f"- [{timestamp.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}] **{normalized_category}**: {learning}\n"
    validate_workspace_memory_record(record)
    return record, normalized_category


def validate_workspace_memory_record(record: str) -> None:
    """Reject anything other than one complete canonical memory record."""
    if not isinstance(record, str):
        raise WorkspaceMemoryRecordError
    try:
        encoded = record.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise WorkspaceMemoryRecordError from exc
    if not encoded or len(encoded) > WORKSPACE_MEMORY_MAX_RECORD_BYTES:
        raise WorkspaceMemoryRecordError
    match = _MEMORY_RECORD.fullmatch(record)
    if match is None:
        raise WorkspaceMemoryRecordError
    try:
        datetime.strptime(match["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise WorkspaceMemoryRecordError from exc
    category = normalize_workspace_memory_category(match["category"])
    if category != match["category"]:
        raise WorkspaceMemoryRecordError
    learning = match["learning"]
    if learning != " ".join(learning.split()) or "\x00" in learning:
        raise WorkspaceMemoryRecordError


def validate_workspace_memory_content(content: str) -> None:
    """Validate every returned record without requiring omitted history."""
    if not isinstance(content, str):
        raise WorkspaceMemoryRecordError
    for record in content.splitlines(keepends=True):
        validate_workspace_memory_record(record)


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryReadResult:
    """One bounded chronological suffix of Workspace Memory."""

    content: str
    truncated: bool
    bytes_returned: int
    byte_budget: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class WorkspaceMemoryAppendResult:
    """Metadata for one durable Workspace Memory append."""

    entry_bytes: int
    total_bytes: int


class WorkspaceMemoryStoreFullError(RuntimeError):
    """The configured Workspace Memory capacity is exhausted."""


class WorkspaceMemoryStoreUnavailableError(RuntimeError):
    """Workspace Memory could not safely complete its storage operation."""


class WorkspaceMemoryStore(Protocol):
    """Runtime-neutral durable Workspace Memory boundary."""

    def read_tail(self, *, byte_budget: int) -> WorkspaceMemoryReadResult: ...

    def append_record(self, record: str) -> WorkspaceMemoryAppendResult: ...
