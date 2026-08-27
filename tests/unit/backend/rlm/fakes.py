"""Shared RLM unit-test capability fakes."""

from __future__ import annotations

from typing import Any

from fleet_rlm.artifacts.models import ArtifactCandidate
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.daytona.recursive_child_runtime import ChildRuntimeLease
from fleet_rlm.files.memory_candidates import MemoryCandidate
from fleet_rlm.rlm.runtime import RLMExecutionSpec


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


class ChildLeaseRecorder:
    """Shared child-runtime factory recorder for recursion tests.

    Records every child acquisition: call indexes, leases, interpreters,
    interpreter backends, close counts, and close order. ``interpreters`` is
    keyed by call index, matching the production factory contract; ``backends``
    and ``close_order`` keep append order.
    """

    def __init__(
        self,
        *,
        sandbox_prefix: str = "child",
        volume: str = "test-volume",
    ) -> None:
        self.call_indexes: list[int] = []
        self.leases: list[ChildRuntimeLease] = []
        self.interpreters: dict[int, DaytonaCodeInterpreter] = {}
        self.backends: list[InProcessInterpreterBackend] = []
        self.close_calls: dict[int, int] = {}
        self.close_order: list[int] = []
        self._sandbox_prefix = sandbox_prefix
        self._volume = volume

    def factory(self, call_index: int) -> ChildRuntimeLease:
        self.call_indexes.append(call_index)
        backend = InProcessInterpreterBackend()
        interpreter = DaytonaCodeInterpreter(backend=backend)
        self.backends.append(backend)
        self.interpreters[call_index] = interpreter

        def close() -> None:
            self.close_calls[call_index] = self.close_calls.get(call_index, 0) + 1
            self.close_order.append(call_index)
            interpreter.shutdown()

        lease = ChildRuntimeLease(
            interpreter,
            f"{self._sandbox_prefix}-{call_index}",
            self._volume,
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )
        self.leases.append(lease)
        return lease


__all__ = ["ChildLeaseRecorder", "EmptyCapabilities"]
