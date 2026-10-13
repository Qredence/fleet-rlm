"""Shared RLM unit-test capability fakes."""

from __future__ import annotations

from typing import Any

from fleet_rlm.daytona.children import ChildRuntimeLease
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, InProcessInterpreterBackend
from fleet_rlm.rlm.execution import RLMExecutionSpec
from fleet_rlm.workspace.artifacts import ArtifactCandidate
from fleet_rlm.workspace.memory import MemoryCandidate


def invocation_factory(**configuration: Any) -> DaytonaCodeInterpreter:
    configuration.pop("deadline_monotonic", None)
    return DaytonaCodeInterpreter(backend=InProcessInterpreterBackend(), **configuration)


invocation_factory.__dict__["fleet_host_tool_dispatch_available"] = True


def configured_invocation_factory(interpreter: Any):
    """Adapt explicitly supplied unit-test interpreters to the invocation factory contract."""
    if callable(interpreter):
        return interpreter

    def create(**configuration: Any):
        bindings = {
            "turn_budget": "bind_turn_budget",
            "async_bridge": "bind_async_bridge",
            "context_capsule": "bind_context_capsule",
        }
        for key, method_name in bindings.items():
            method = getattr(interpreter, method_name, None)
            if callable(method) and (key != "context_capsule" or configuration.get(key) is not None):
                method(configuration.get(key))
        observer = getattr(interpreter, "bind_observer", None)
        if callable(observer):
            observer(configuration.get("observer"), max_chars=configuration.get("observation_max_chars", 10000))
        return interpreter

    return create


class EmptyCapabilities:
    """Minimal PreparedCapabilities stand-in with empty drains."""

    def __init__(self, *, spec: Any | None = None) -> None:
        self.spec = RLMExecutionSpec() if spec is None else spec

    def loaded_skills(self):
        return ()

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

    def factory(self, call_index: int, *, profile: str = "semantic-child") -> ChildRuntimeLease:
        del profile
        self.call_indexes.append(call_index)
        backend = InProcessInterpreterBackend()
        self.backends.append(backend)

        def interpreter_factory(**configuration: Any) -> DaytonaCodeInterpreter:
            configuration.pop("deadline_monotonic", None)
            interpreter = DaytonaCodeInterpreter(backend=backend, **configuration)
            self.interpreters[call_index] = interpreter
            return interpreter

        def close() -> None:
            self.close_calls[call_index] = self.close_calls.get(call_index, 0) + 1
            self.close_order.append(call_index)
            backend.close()

        lease = ChildRuntimeLease(
            interpreter_factory,
            f"{self._sandbox_prefix}-{call_index}",
            self._volume,
            f"recursive/test-workspace/test-run/{call_index}",
            close,
        )
        self.leases.append(lease)
        return lease


__all__ = ["ChildLeaseRecorder", "EmptyCapabilities"]
