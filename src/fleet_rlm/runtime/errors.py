"""Provider-neutral error identities shared by runtime adapters and domains."""

from __future__ import annotations


class WorkspaceConflictError(FileExistsError):
    """A workspace mutation conflict with a stable machine-readable detail."""

    def __init__(self, path: str, *, detail: str = "") -> None:
        super().__init__(path)
        self.detail = detail


class FilesystemToolError(RuntimeError):
    """Closed, sanitized error used by mounted filesystem tools."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


__all__ = ["FilesystemToolError", "WorkspaceConflictError"]
