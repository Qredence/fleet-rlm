"""Daytona SDK Protocol definitions.

These Protocols define the structural seam between fleet-rlm and the Daytona SDK.
They type the *exact* surface fleet-rlm consumes so that internal code can depend on
narrow, runtime-checkable interfaces rather than concrete SDK classes or bare ``Any``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Sub-service Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxProcessService(Protocol):
    """Protocol for the ``sandbox.process`` service."""

    def exec(self, command: str, **kwargs: Any) -> Any:
        pass

    def code_run(self, code: str, **kwargs: Any) -> Any:
        pass

    def create_session(self, session_id: str) -> None:
        pass

    def execute_session_command(self, *args: Any, **kwargs: Any) -> Any:
        pass

    def delete_session(self, session_id: str) -> None:
        pass


@runtime_checkable
class SandboxCodeInterpreter(Protocol):
    """Protocol for the ``sandbox.code_interpreter`` service."""

    def run_code(self, *args: Any, **kwargs: Any) -> Any:
        pass

    def list_contexts(self) -> list[Any]:
        pass

    def create_context(self, *, cwd: str) -> Any:
        pass

    def delete_context(self, context: Any) -> None:
        pass


@runtime_checkable
class SandboxGitService(Protocol):
    """Protocol for the ``sandbox.git`` service."""

    def clone(self, **kwargs: Any) -> None:
        pass

    def status(self, path: str) -> Any:
        pass

    def pull(self, path: str) -> None:
        pass

    def branches(self, path: str) -> Any:
        pass

    def checkout_branch(self, path: str, ref: str) -> None:
        pass


@runtime_checkable
class SandboxFsService(Protocol):
    """Protocol for the ``sandbox.fs`` service."""

    def list_files(self, path: str) -> Any:
        pass

    def download_file(self, path: str) -> bytes:
        pass

    def upload_file(self, payload: bytes, path: str) -> None:
        pass


# ---------------------------------------------------------------------------
# Composite Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class DaytonaSandbox(Protocol):
    """Protocol for a Daytona sandbox instance."""

    @property
    def id(self) -> str:
        pass

    @property
    def process(self) -> SandboxProcessService:
        pass

    @property
    def code_interpreter(self) -> SandboxCodeInterpreter:
        pass

    @property
    def git(self) -> SandboxGitService:
        pass

    @property
    def fs(self) -> SandboxFsService:
        pass

    def delete(self) -> None:
        pass

    def stop(self, **kwargs: Any) -> None:
        pass


@runtime_checkable
class DaytonaVolumeService(Protocol):
    """Protocol for the ``client.volume`` service."""

    def get(self, name: str, *, create: bool = False) -> Any:
        pass

    def list(self, **kwargs: Any) -> Any:
        pass


@runtime_checkable
class DaytonaSnapshotService(Protocol):
    """Protocol for the ``client.snapshot`` service."""

    def list(self) -> Any:
        pass

    def get(self, name: str) -> Any:
        pass

    def create(self, params: Any, **kwargs: Any) -> Any:
        pass

    def delete(self, snapshot: Any) -> None:
        pass


@runtime_checkable
class DaytonaClient(Protocol):
    """Protocol for the top-level Daytona client object."""

    @property
    def volume(self) -> DaytonaVolumeService:
        pass

    @property
    def snapshot(self) -> DaytonaSnapshotService:
        pass

    def create(self, params: Any, **kwargs: Any) -> Any:
        pass

    def get(self, sandbox_id: str) -> Any:
        pass

    def close(self) -> None:
        pass


__all__ = [
    "DaytonaClient",
    "DaytonaSandbox",
    "DaytonaSnapshotService",
    "DaytonaVolumeService",
    "SandboxCodeInterpreter",
    "SandboxFsService",
    "SandboxGitService",
    "SandboxProcessService",
]
