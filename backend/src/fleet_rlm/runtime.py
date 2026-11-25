"""Process runtime state and per-request settings shared by the API helpers."""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import dspy
from sqlalchemy.ext.asyncio import AsyncSession

from . import agent
from . import db as store
from .config import Settings
from .interpreter import AsyncDaytona
from .schemas import EDITABLE_SETTINGS_FIELDS
from .skills import SkillRegistry
from .storage import ObjectStorage


@dataclass(frozen=True)
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


async def active_session(
    db: AsyncSession, session_id: UUID, *, for_update: bool = False
) -> store.Session:
    session = await db.get(
        store.Session, session_id, with_for_update=store.SESSION_WRITE_LOCK if for_update else None
    )
    if session is None or session.deleting:
        raise store.SessionNotFoundError(session_id)
    return session


def sanitize_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in raw.items()
        if key in EDITABLE_SETTINGS_FIELDS and value is not None
    }


def merge_settings(base: Settings, overrides: dict[str, Any]) -> Settings:
    clean = sanitize_overrides(overrides)
    return base.model_copy(update=clean) if clean else base


async def effective_settings(runtime: Runtime) -> Settings:
    overrides = await store.get_workspace_settings_overrides(runtime.sessionmaker)
    return merge_settings(runtime.settings, overrides)


def request_root_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.llm_model != runtime.settings.llm_model:
        return agent._model(effective, effective.llm_model)
    return runtime.lm


def request_sub_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.sub_llm_model != runtime.settings.sub_llm_model:
        return agent._model(effective, effective.sub_llm_model)
    return runtime.sub_lm


def request_decision_lm(runtime: Runtime, effective: Settings) -> dspy.BaseLM:
    if effective.decision_model != runtime.settings.decision_model:
        return dspy.LM(effective.decision_model)
    return runtime.decision_lm
