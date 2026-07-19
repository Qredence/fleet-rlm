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
    instructions="Use workspace tools for durable files; REPL variables are not durable.",
)

DENO_WORKSPACE_CAPABILITY = WorkspaceCapabilityMetadata(
    available=False,
    root=".",
    instructions="Durable workspace files require the Daytona runtime; REPL variables are not durable.",
)


class SessionWorkspaceFS(Protocol):
    def list_entries(self, path: str, *, limit: int = 100) -> WorkspaceListResult: ...

    def stat(self, path: str) -> WorkspaceEntry | None: ...

    def read_text(self, path: str, *, max_bytes: int) -> str: ...

    def write_text(self, path: str, content: str, *, overwrite: bool) -> WorkspaceEntry: ...
