"""Domain values and port for the durable Session Workspace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    path: str
    kind: Literal["file", "directory"]
    byte_size: int | None
    modified_at: str | None


@dataclass(frozen=True, slots=True)
class WorkspaceListResult:
    entries: tuple[WorkspaceEntry, ...]
    truncated: bool = False
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceTextPage:
    content: str
    next_cursor: str | None
    byte_size: int
    eof: bool


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilityMetadata:
    available: bool
    root: str
    instructions: str

    def to_input(self) -> dict[str, object]:
        return {
            "available": self.available,
            "root": self.root,
            "instructions": self.instructions,
        }


DAYTONA_WORKSPACE_CAPABILITY = WorkspaceCapabilityMetadata(
    available=True,
    root=".",
    instructions=(
        "REPL variables and sandbox-local files are temporary to the Run. Session Workspace tool writes are "
        "immediately durable independently of Turn success. Artifact Candidates are promoted only by a "
        "successful Turn Commit. Use Workspace tools only when durable state is relevant."
    ),
)

UNAVAILABLE_WORKSPACE_CAPABILITY = WorkspaceCapabilityMetadata(
    available=False,
    root=".",
    instructions=(
        "Session Workspace is unavailable. REPL variables and sandbox-local files are temporary to the Run; "
        "no durable Workspace or Turn Commit artifact workflow is available."
    ),
)


class SessionWorkspaceFS(Protocol):
    def list_entries(
        self,
        path: str,
        *,
        limit: int = 100,
        after: str | None = None,
    ) -> WorkspaceListResult: ...

    def stat(self, path: str) -> WorkspaceEntry | None: ...

    def read_text_page(
        self,
        path: str,
        *,
        cursor: str | None,
        max_chars: int,
        max_bytes: int,
    ) -> WorkspaceTextPage: ...

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry: ...

    def append_text(self, path: str, content: str) -> WorkspaceEntry: ...
