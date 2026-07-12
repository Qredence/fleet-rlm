"""Live RLMTurnContext builder: real models + DaytonaSessionManager lease."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fleet_rlm_clean.chat.commands import ChatTurnCommand
from fleet_rlm_clean.config import Settings
from fleet_rlm_clean.daytona.bindings import BindingStore, InMemoryBindingStore
from fleet_rlm_clean.daytona.client import build_daytona_client
from fleet_rlm_clean.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient
from fleet_rlm_clean.daytona.session_manager import DaytonaSessionManager, LeaseRequest
from fleet_rlm_clean.daytona.volumes import volume_config_from_settings
from fleet_rlm_clean.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm_clean.rlm.budgets import RLMBudget
from fleet_rlm_clean.rlm.context import RLMTurnContext
from fleet_rlm_clean.rlm.lm_factory import build_model_bundle
from fleet_rlm_clean.sessions.repository import SessionRepository


def _load_dotenv_into_environ() -> None:
    """Load repo ``.env`` without overriding already-exported process vars."""
    import os

    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover
        return
    path = Path.cwd() / ".env"
    if not path.is_file():
        return
    for key, value in dotenv_values(path).items():
        if value is None or key in os.environ:
            continue
        os.environ[key] = value


def settings_with_env_fallbacks(settings: Settings | None = None) -> Settings:
    """Merge Settings with process env for Daytona/LLM keys (live tests)."""
    import os

    from pydantic import SecretStr

    _load_dotenv_into_environ()
    base = settings or Settings()
    updates: dict[str, object] = {}
    if base.daytona_api_key is None and os.environ.get("DAYTONA_API_KEY"):
        updates["daytona_api_key"] = SecretStr(os.environ["DAYTONA_API_KEY"])
    if base.llm_api_key is None:
        for name in (
            "FLEET_CLEAN_LLM_API_KEY",
            "DSPY_LLM_API_KEY",
            "DSPY_LM_API_KEY",
            "OPENAI_API_KEY",
        ):
            value = os.environ.get(name)
            if value and not value.startswith("http"):
                updates["llm_api_key"] = SecretStr(value)
                break
    if updates:
        return base.model_copy(update=updates)
    return base


class LiveKernelResources:
    """Holds live clients for one process; delete sandboxes on cleanup.

    Default: in-memory bindings (kernel smoke).
    Pass ``session_factory`` for durable bindings + optional SessionRepository
    (stateful recovery / API-restart proofs).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        bindings: Any | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        engine: AsyncEngine | None = None,
        allow_ephemeral_fallback: bool = True,
    ) -> None:
        self.settings = settings_with_env_fallbacks(settings)
        self.client = build_daytona_client(self.settings)
        self.platform = LiveDaytonaPlatform(self.client)
        self.volume_client = LiveDaytonaVolumeClient(self.client)
        self.volume_config = volume_config_from_settings(self.settings)
        self._engine = engine
        self._session_factory = session_factory
        if bindings is not None:
            self.bindings = bindings
        elif session_factory is not None:
            self.bindings = BindingStore(session_factory)
        else:
            self.bindings = InMemoryBindingStore()
        self.session_manager = DaytonaSessionManager(
            platform=self.platform,
            volume_client=self.volume_client,
            volume_config=self.volume_config,
            bindings=self.bindings,
        )
        self.models = build_model_bundle(self.settings)
        self.sessions: SessionRepository | None = (
            SessionRepository(session_factory) if session_factory is not None else None
        )
        self.allow_ephemeral_fallback = allow_ephemeral_fallback
        self._sandbox_ids: list[str] = []
        self.last_used_ephemeral = False

    @classmethod
    async def with_sqlite_file(
        cls,
        db_path: Path | str,
        settings: Settings | None = None,
        *,
        allow_ephemeral_fallback: bool = False,
    ) -> LiveKernelResources:
        """Durable sqlite-backed bindings + sessions for recovery proofs."""
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite+aiosqlite:///{path.resolve()}"
        engine = create_async_engine_from_url(url)
        await create_tables(engine)
        factory = create_session_factory(engine)
        return cls(
            settings or Settings(),
            session_factory=factory,
            engine=engine,
            allow_ephemeral_fallback=allow_ephemeral_fallback,
        )

    @classmethod
    async def reopen_sqlite_file(
        cls,
        db_path: Path | str,
        settings: Settings | None = None,
        *,
        allow_ephemeral_fallback: bool = False,
    ) -> LiveKernelResources:
        """Simulate API restart: new clients against the same sqlite file."""
        return await cls.with_sqlite_file(
            db_path,
            settings,
            allow_ephemeral_fallback=allow_ephemeral_fallback,
        )

    async def build_context(self, command: ChatTurnCommand) -> RLMTurnContext:
        self.last_used_ephemeral = False
        try:
            lease = await self.session_manager.acquire(
                LeaseRequest(
                    session_id=command.session_id,
                    user_id=command.user_id,
                    workspace_id=command.workspace_id,
                )
            )
        except Exception:
            if not self.allow_ephemeral_fallback:
                raise
            # Disk/volume limits: fall back to ephemeral sandbox without Volume mount.
            self.last_used_ephemeral = True
            lease = self._acquire_ephemeral_lease()
        self.track_sandbox(lease.sandbox_id)
        return RLMTurnContext(
            run_id=uuid4(),
            session_id=command.session_id,
            user_id=command.user_id,
            workspace_id=command.workspace_id,
            request=command.message,
            models=self.models,
            budget=RLMBudget(max_iterations=6, max_llm_calls=16, max_output_chars=3000),
            lease=lease,
        )

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    def _acquire_ephemeral_lease(self):
        from fleet_rlm_clean.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
        from fleet_rlm_clean.daytona.leases import InterpreterLease

        sandbox = self.platform.create(
            volume_id=None,
            mount_path=None,
            labels={"fleet_package": "fleet_rlm_clean", "mode": "ephemeral"},
            with_volume=False,
        )
        sid = str(getattr(sandbox, "id", sandbox))
        interpreter = DaytonaCodeInterpreter(backend=sandbox_backend(sandbox))
        return InterpreterLease(
            sandbox_id=sid,
            interpreter_id=f"ephemeral-{sid[:8]}",
            volume_id="none",
            mount_path=self.volume_config.mount_path,
            interpreter=interpreter,
        )

    def forget_sandboxes(self) -> None:
        """Drop tracked sandbox ids without deleting (API-restart simulation)."""
        self._sandbox_ids.clear()

    def cleanup(self) -> None:
        """Delete tracked sandboxes (best-effort). Does not dispose the DB engine."""
        for sid in list(self._sandbox_ids):
            try:
                self.platform.delete(sid)
            except Exception:  # noqa: BLE001 - best-effort live cleanup
                pass
        self._sandbox_ids.clear()

    async def adispose_engine(self) -> None:
        """Dispose the sqlite engine without deleting sandboxes."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def adispose(self) -> None:
        """Delete sandboxes and dispose engine (end of proof)."""
        self.cleanup()
        await self.adispose_engine()
