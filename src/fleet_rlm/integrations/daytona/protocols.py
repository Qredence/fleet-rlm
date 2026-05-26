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

    def exec(self, command: str, **kwargs: Any) -> Any: ...

    def code_run(self, code: str, **kwargs: Any) -> Any: ...

    def create_session(self, session_id: str) -> None: ...

    def execute_session_command(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete_session(self, session_id: str) -> None: ...


@runtime_checkable
class SandboxCodeInterpreter(Protocol):
    """Protocol for the ``sandbox.code_interpreter`` service."""

    def run_code(self, *args: Any, **kwargs: Any) -> Any: ...

    def list_contexts(self) -> list[Any]: ...

    def create_context(self, *, cwd: str) -> Any: ...

    def delete_context(self, context: Any) -> None: ...


@runtime_checkable
class SandboxGitService(Protocol):
    """Protocol for the ``sandbox.git`` service."""

    def clone(self, **kwargs: Any) -> None: ...

    def status(self, path: str) -> Any: ...

    def pull(self, path: str) -> None: ...

    def branches(self, path: str) -> Any: ...

    def checkout_branch(self, path: str, ref: str) -> None: ...


@runtime_checkable
class SandboxFsService(Protocol):
    """Protocol for the ``sandbox.fs`` service."""

    def list_files(self, path: str) -> Any: ...

    def download_file(self, path: str) -> bytes: ...

    def upload_file(self, payload: bytes, path: str) -> None: ...


# ---------------------------------------------------------------------------
# Composite Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class DaytonaSandbox(Protocol):
    """Protocol for a Daytona sandbox instance."""

    @property
    def id(self) -> str: ...

    @property
    def process(self) -> SandboxProcessService: ...

    @property
    def code_interpreter(self) -> SandboxCodeInterpreter: ...

    @property
    def git(self) -> SandboxGitService: ...

    @property
    def fs(self) -> SandboxFsService: ...

    def delete(self) -> None: ...

    def stop(self, **kwargs: Any) -> None: ...


@runtime_checkable
class DaytonaVolumeService(Protocol):
    """Protocol for the ``client.volume`` service."""

    def get(self, name: str, *, create: bool = False) -> Any: ...

    def list(self, **kwargs: Any) -> Any: ...


@runtime_checkable
class DaytonaSnapshotService(Protocol):
    """Protocol for the ``client.snapshot`` service."""

    def list(self) -> Any: ...

    def get(self, name: str) -> Any: ...

    def create(self, params: Any, **kwargs: Any) -> Any: ...

    def delete(self, snapshot: Any) -> None: ...


@runtime_checkable
class DaytonaClient(Protocol):
    """Protocol for the top-level Daytona client object."""

    @property
    def volume(self) -> DaytonaVolumeService: ...

    @property
    def snapshot(self) -> DaytonaSnapshotService: ...

    def create(self, params: Any, **kwargs: Any) -> Any: ...

    def get(self, sandbox_id: str) -> Any: ...

    def close(self) -> None: ...


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
