"""Live RLMTurnContext builder: real models + DaytonaSessionManager lease."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from fleet_rlm.chat.commands import ChatTurnCommand
from fleet_rlm.config import Settings
from fleet_rlm.daytona.bindings import BindingStore, InMemoryBindingStore
from fleet_rlm.daytona.client import build_daytona_client
from fleet_rlm.daytona.platform import LiveDaytonaPlatform, LiveDaytonaVolumeClient
from fleet_rlm.daytona.session_manager import DaytonaSessionManager, LeaseRequest
from fleet_rlm.daytona.volumes import volume_config_from_settings
from fleet_rlm.persistence.database import (
    create_async_engine_from_url,
    create_session_factory,
    create_tables,
)
from fleet_rlm.persistence.repositories import SqlAlchemySessionRepository
from fleet_rlm.rlm.budgets import RLMBudget
from fleet_rlm.rlm.context import RLMTurnContext
from fleet_rlm.rlm.lm_factory import build_model_bundle


def resolve_settings(settings: Settings | None = None) -> Settings:
    """Return explicit settings or resolve the canonical ``FLEET_*`` surface."""
    return settings or Settings()


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
        self.settings = resolve_settings(settings)
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
        self.sessions: SqlAlchemySessionRepository | None = (
            SqlAlchemySessionRepository(session_factory) if session_factory is not None else None
        )
        self.allow_ephemeral_fallback = allow_ephemeral_fallback
        self._sandbox_ids: list[str] = []
        self.last_used_ephemeral = False
        # Optional capability hosts (wired by live proofs / app composition)
        self.skill_registry: Any | None = None
        self.capability_registry: Any | None = None
        self.attachment_store: Any | None = None
        self.artifact_store: Any | None = None

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

    async def build_context(
        self,
        command: ChatTurnCommand,
        *,
        run_id: UUID | None = None,
    ) -> RLMTurnContext:
        """Acquire lease and build turn context (platform I/O uses to_thread)."""
        import asyncio

        self.last_used_ephemeral = False
        turn_run_id = run_id or uuid4()
        try:
            lease = await self.session_manager.acquire(
                LeaseRequest(
                    session_id=command.session_id,
                    user_id=command.user_id,
                    workspace_id=command.workspace_id,
                    run_id=turn_run_id,
                )
            )
        except Exception:
            if not self.allow_ephemeral_fallback:
                raise
            # Disk/volume limits: fall back to ephemeral sandbox without Volume mount.
            self.last_used_ephemeral = True
            lease = await asyncio.to_thread(self._acquire_ephemeral_lease)
        try:
            self.track_sandbox(lease.sandbox_id)
            context = RLMTurnContext(
                run_id=turn_run_id,
                session_id=command.session_id,
                user_id=command.user_id,
                workspace_id=command.workspace_id,
                request=command.message,
                models=self.models,
                budget=RLMBudget(max_iterations=6, max_llm_calls=16, max_output_chars=3000),
                lease=lease,
            )
            if self.skill_registry is not None or (
                self.attachment_store is not None and self.artifact_store is not None
            ):
                from fleet_rlm.chat.capabilities import assemble_turn_capabilities
                from fleet_rlm.daytona.paths import volume_paths_from_settings
                from fleet_rlm.daytona.volume_fs import DaytonaSandboxVolumeFs

                sandbox = await asyncio.to_thread(self.platform.get, lease.sandbox_id)
                if sandbox is None:
                    msg = "acquired Sandbox is unavailable"
                    raise RuntimeError(msg)
                volume_fs = DaytonaSandboxVolumeFs(sandbox)
                return await assemble_turn_capabilities(
                    context,
                    command,
                    skill_registry=self.skill_registry,
                    attachment_store=self.attachment_store,
                    volume_fs=volume_fs,
                    volume_paths=volume_paths_from_settings(self.settings),
                    max_artifact_bytes=self.settings.max_artifact_bytes,
                    capability_registry=self.capability_registry,
                )
            return context
        except Exception:
            await self.session_manager.release(lease)
            raise

    def track_sandbox(self, sandbox_id: str | None) -> None:
        if sandbox_id and sandbox_id not in self._sandbox_ids:
            self._sandbox_ids.append(sandbox_id)

    def _acquire_ephemeral_lease(self):
        from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
        from fleet_rlm.daytona.leases import InterpreterLease

        sandbox = self.platform.create(
            volume_id=None,
            mount_path=None,
            labels={"fleet_package": "fleet_rlm", "mode": "ephemeral"},
            with_volume=False,
            ephemeral=True,
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
