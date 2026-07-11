"""Live RLMTurnContext builder: real models + DaytonaSessionManager lease."""

from __future__ import annotations

from uuid import uuid4

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.chat.turn_coordinator import ephemeral_lease
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.bindings import InMemoryBindingStore
from fleet_rlm_clean.daytona.client import build_daytona_client
from fleet_rlm_clean.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient
from fleet_rlm_clean.daytona.session_manager import DaytonaSessionManager, LeaseRequest
from fleet_rlm_clean.daytona.volumes import volume_config_from_settings
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.lm_factory import build_model_bundle


def settings_with_env_fallbacks(settings: Settings | None = None) -> Settings:
    """Merge Settings with process env for Daytona/LLM keys (live tests)."""
    import os

    from pydantic import SecretStr

    base = settings or Settings()
    updates: dict[str, object] = {}
    if base.daytona_api_key is None and os.environ.get("DAYTONA_API_KEY"):
        updates["daytona_api_key"] = SecretStr(os.environ["DAYTONA_API_KEY"])
    if base.llm_api_key is None:
        for name in ("FLEET_CLEAN_LLM_API_KEY", "OPENAI_API_KEY"):
            if os.environ.get(name):
                updates["llm_api_key"] = SecretStr(os.environ[name])
                break
    if updates:
        return base.model_copy(update=updates)
    return base


class LiveKernelResources:
    """Holds live clients for one process; delete sandboxes on cleanup."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings_with_env_fallbacks(settings)
        self.client = build_daytona_client(self.settings)
        self.platform = LiveDaytonaPlatform(self.client)
        self.volume_client = LiveDaytonaVolumeClient(self.client)
        self.volume_config = volume_config_from_settings(self.settings)
        self.bindings = InMemoryBindingStore()
        self.session_manager = DaytonaSessionManager(
            platform=self.platform,
            volume_client=self.volume_client,
            volume_config=self.volume_config,
            bindings=self.bindings,
        )
        self.models = build_model_bundle(self.settings)
        self._sandbox_ids: list[str] = []

    async def build_context(self, command: ChatTurnCommand) -> RLMTurnContext:
        lease = await self.session_manager.acquire(
            LeaseRequest(
                session_id=command.session_id,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
            )
        )
        self._sandbox_ids.append(lease.sandbox_id)
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=self.models,
            budget=RLMBudget(max_iterations=8, max_llm_calls=20, max_output_chars=4000),
            lease=lease,
        )

    def cleanup(self) -> None:
        for sid in list(self._sandbox_ids):
            try:
                self.platform.delete(sid)
            except Exception:  # noqa: BLE001 - best-effort live cleanup
                pass
        self._sandbox_ids.clear()
