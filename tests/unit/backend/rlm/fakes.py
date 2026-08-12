"""Shared RLM unit-test capability fakes."""

from __future__ import annotations

from typing import Any

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.rlm.context import RLMExecutionSpec


class EmptyCapabilities:
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


__all__ = ["EmptyCapabilities"]
