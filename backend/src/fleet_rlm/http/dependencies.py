"""Application runtime and common FastAPI dependencies."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import gettempdir
from typing import Annotated, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import dspy
from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import chatgpt_plan
from .. import db as store
from ..config import Settings
from ..interpreter import AsyncDaytona
from ..skills import SkillRegistry
from ..storage import ObjectStorage

_STREAM_HEADER = "x-vercel-ai-ui-message-stream"


@dataclass
class Runtime:
    settings: Settings
    sessionmaker: store.SessionFactory
    daytona: AsyncDaytona
    storage: ObjectStorage
    skills: SkillRegistry
    lm: dspy.BaseLM
    sub_lm: dspy.BaseLM
    decision_lm: dspy.BaseLM
    turn_semaphore: asyncio.Semaphore
    optimized_program_state: dict[str, Any] | None
    mlflow_experiment_id: str | None = None
    mlflow_optimization_experiment_id: str | None = None
    tracking_pending: bool = False
    volume_daytona: AsyncDaytona | None = None
    chatgpt: chatgpt_plan.ChatGPTPlan | None = None
    artifact_cache_dir: Path = field(
        default_factory=lambda: Path(gettempdir()) / "fleet-rlm-artifacts"
    )
    rlm_state_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    optimization_tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict)
    optimization_procs: dict[UUID, Any] = field(default_factory=dict)
    optimization_cancel_events: dict[UUID, asyncio.Event] = field(default_factory=dict)
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    memory_semaphore: asyncio.Semaphore = field(init=False)

    def __post_init__(self) -> None:
        self.memory_semaphore = asyncio.Semaphore(
            min(4, self.settings.max_concurrent_turns_per_worker)
        )


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(503, "Fleet is not ready")
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


async def get_db(runtime: RuntimeDep) -> AsyncIterator[AsyncSession]:
    async with runtime.sessionmaker() as db:
        yield db


DbDep = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def _skill_registry(runtime: Runtime) -> SkillRegistry:
    return await asyncio.to_thread(runtime.skills.with_installed, runtime.settings.skills_dir)


def _display_endpoint_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme in {"http", "https"} and not parts.hostname:
        return None
    if (
        parts.scheme in {"http", "https"}
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        authority = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parts.scheme, authority, parts.path, "", ""))
    return value


def _has_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _clean_display_name(raw: str, *, field_label: str, max_length: int = 120) -> str:
    cleaned = raw.strip()
    if not cleaned or len(cleaned) > max_length:
        raise HTTPException(422, f"{field_label} must be between 1 and {max_length} characters")
    if _has_control_chars(cleaned):
        raise HTTPException(422, f"{field_label} must not contain control characters")
    return cleaned
