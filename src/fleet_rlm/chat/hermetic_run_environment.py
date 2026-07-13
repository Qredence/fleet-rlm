"""Hermetic Run environment and canonical Turn preparation adapters."""

from __future__ import annotations

from types import SimpleNamespace

from fleet_rlm.chat.turn_lifecycle import ExecuteTurn
from fleet_rlm.chat.turn_preparation import (
    PreparedTurn,
    RunEnvironment,
    RunEnvironmentProvider,
    TurnPreparationModule,
)
from fleet_rlm.files.lifecycle import AttachmentLifecycle
from fleet_rlm.files.models import PreparedAttachments
from fleet_rlm.rlm.budgets import RunBudget, RunBudgetLedger
from fleet_rlm.rlm.model_bundle import RLMModelBundle
from fleet_rlm.skills.capabilities import TurnCapabilityBlueprint


class HermeticLM:
    def __init__(self, name: str) -> None:
        self.model = name


class HermeticInterpreter:
    def execute(self, code: str) -> str:
        del code
        return ""


class HermeticRunSink:
    """In-memory bounded sink used by tests and the local development server."""

    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    async def read(self, location: str, *, max_bytes: int) -> bytes:
        value = self.values[location]
        if len(value) > max_bytes:
            raise ValueError("value exceeds read bound")
        return value

    async def write(self, location: str, data: bytes) -> None:
        self.values[location] = bytes(data)

    async def remove(self, location: str) -> None:
        self.values.pop(location, None)

    async def read_private(self, logical_path: str) -> bytes:
        return await self.read(logical_path, max_bytes=2**31 - 1)

    async def write_private(self, logical_path: str, data: bytes) -> None:
        await self.write(logical_path, data)

    async def remove_private(self, logical_path: str) -> None:
        await self.remove(logical_path)


class HermeticRunEnvironmentProvider(RunEnvironmentProvider):
    async def acquire(self, turn: ExecuteTurn) -> RunEnvironment:
        del turn
        sink = HermeticRunSink()

        async def release() -> None:
            return None

        return RunEnvironment(HermeticInterpreter(), sink, sink, release)


class HermeticPreparedCapabilities:
    blueprint = TurnCapabilityBlueprint()

    def drain_public_details(self) -> tuple[()]:
        return ()

    def drain_artifact_candidates(self) -> tuple[()]:
        return ()

    async def aclose(self) -> None:
        return None


class HermeticCapabilityPreparer:
    async def prepare(
        self,
        turn: ExecuteTurn,
        environment: RunEnvironment,
        attachments: PreparedAttachments,
        budget: RunBudgetLedger,
    ) -> HermeticPreparedCapabilities:
        del turn, environment, attachments, budget
        return HermeticPreparedCapabilities()


class _HermeticRLM:
    async def acall(self, **kwargs):
        request = str(kwargs.get("request") or "").strip()
        return SimpleNamespace(answer=request or "")


class HermeticRLMFactory:
    """Deterministic local RLM substitute; never calls an external provider."""

    def create(self, **kwargs):
        del kwargs
        return _HermeticRLM()


class HermeticTurnPreparation:
    """Build hermetic turns through the same module as Daytona turns."""

    def __init__(self, *, attachments: AttachmentLifecycle, budget: RunBudget | None = None) -> None:
        selected_budget = budget or RunBudget()
        self._module = TurnPreparationModule(
            models=RLMModelBundle(HermeticLM("offline/root"), HermeticLM("offline/sub")),
            budget=selected_budget,
            attachments=attachments,
            environments=HermeticRunEnvironmentProvider(),
            capabilities=HermeticCapabilityPreparer(),
        )

    async def prepare(self, turn: ExecuteTurn) -> PreparedTurn:
        return await self._module.prepare(turn)


__all__ = [
    "HermeticInterpreter",
    "HermeticLM",
    "HermeticRunEnvironmentProvider",
    "HermeticRunSink",
    "HermeticRLMFactory",
    "HermeticTurnPreparation",
]
