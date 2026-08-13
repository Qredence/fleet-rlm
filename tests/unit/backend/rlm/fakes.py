"""Shared RLM unit-test capability fakes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.rlm.child_runtime import ChildRuntimeLease
from fleet_rlm.rlm.context import RLMExecutionSpec
from fleet_rlm.rlm.inputs import AttachmentContextCapsule


class HostCapabilityDefaults:
    """Named PreparedCapabilities fields for test doubles that are not EmptyCapabilities."""

    workspace_memory_digest = ""
    preparation_notices: tuple[Any, ...] = ()

    def record_attachment_accesses(self, attachment_ids: tuple[str, ...]) -> None:
        del attachment_ids


class EmptyCapabilities(HostCapabilityDefaults):
    """Minimal PreparedCapabilities stand-in with empty drains."""

    def __init__(self, *, spec: Any | None = None) -> None:
        self.spec = RLMExecutionSpec() if spec is None else spec

    def drain_public_details(self) -> tuple[Any, ...]:
        return ()

    def drain_artifact_candidates(self) -> tuple[ArtifactCandidate, ...]:
        return ()

    def drain_memory_candidates(self) -> tuple[MemoryCandidate, ...]:
        return ()

    async def aclose(self) -> None:
        return None


class FakeChildRuntimeFactory:
    """Test adapter: wrap a create callable with no-op late-acquisition ownership."""

    def __init__(self, create: Callable[[int], Any]) -> None:
        self._create = create

    def __call__(self, call_index: int) -> ChildRuntimeLease:
        return self._create(call_index)

    def wait_owned(self) -> None:
        return None

    def raise_if_cleanup_failed(self) -> None:
        return None


class FakeRLMInterpreter:
    """No-op Fleet pre-acall hooks for runner tests that are not Daytona interpreters."""

    def bind_context_capsule(self, capsule: AttachmentContextCapsule) -> None:
        del capsule

    def drain_context_accesses(self) -> tuple[str, ...]:
        return ()


__all__ = [
    "EmptyCapabilities",
    "FakeChildRuntimeFactory",
    "FakeRLMInterpreter",
    "HostCapabilityDefaults",
]
