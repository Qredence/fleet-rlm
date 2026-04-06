"""Runtime settings helpers for the runtime router."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TypedDict

from fastapi import HTTPException

from fleet_rlm.integrations.config.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    RUNTIME_SETTINGS_KEYS,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)

from ..bootstrap import (
    cancel_optional_runtime_startup,
    get_delegate_lm_from_env,
    get_planner_lm_from_env,
    schedule_optional_runtime_startup,
)
from ..config import ServerRuntimeConfig
from ..dependencies import ServerState
from ..schemas.core import (
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
)

RUNTIME_MODEL_RELOAD_KEYS = frozenset(
    {
        "DSPY_LM_MODEL",
        "DSPY_DELEGATE_LM_MODEL",
        "DSPY_DELEGATE_LM_SMALL_MODEL",
        "DSPY_DELEGATE_LM_MAX_TOKENS",
        "DSPY_LM_API_BASE",
        "DSPY_LM_MAX_TOKENS",
        "DSPY_LLM_API_KEY",
    }
)


class RuntimeConfigSnapshot(TypedDict):
    agent_model: str | None
    agent_delegate_model: str | None
    agent_delegate_small_model: str | None
    agent_delegate_max_tokens: int
    planner_lm: object | None
    delegate_lm: object | None


def apply_runtime_settings_to_config(
    *, config: ServerRuntimeConfig, normalized: dict[str, str]
) -> None:
    if "DSPY_LM_MODEL" in normalized:
        resolved_planner_model = normalized["DSPY_LM_MODEL"].strip()
        config.agent_model = resolved_planner_model or None

    if "DSPY_DELEGATE_LM_MODEL" in normalized:
        resolved_delegate_model = normalized["DSPY_DELEGATE_LM_MODEL"].strip()
        config.agent_delegate_model = resolved_delegate_model or None

    if "DSPY_DELEGATE_LM_SMALL_MODEL" in normalized:
        resolved_delegate_small_model = normalized[
            "DSPY_DELEGATE_LM_SMALL_MODEL"
        ].strip()
        config.agent_delegate_small_model = resolved_delegate_small_model or None

    if "DSPY_DELEGATE_LM_MAX_TOKENS" in normalized:
        config.agent_delegate_max_tokens = max(
            int(normalized["DSPY_DELEGATE_LM_MAX_TOKENS"].strip() or "64000"),
            1,
        )


def _capture_runtime_config_snapshot(*, state: ServerState) -> RuntimeConfigSnapshot:
    config = state.config
    return {
        "agent_model": config.agent_model,
        "agent_delegate_model": config.agent_delegate_model,
        "agent_delegate_small_model": config.agent_delegate_small_model,
        "agent_delegate_max_tokens": config.agent_delegate_max_tokens,
        "planner_lm": state.planner_lm,
        "delegate_lm": state.delegate_lm,
    }


def _restore_runtime_config_snapshot(
    *,
    state: ServerState,
    snapshot: RuntimeConfigSnapshot,
) -> None:
    config = state.config
    config.agent_model = snapshot["agent_model"]
    config.agent_delegate_model = snapshot["agent_delegate_model"]
    config.agent_delegate_small_model = snapshot["agent_delegate_small_model"]
    config.agent_delegate_max_tokens = snapshot["agent_delegate_max_tokens"]
    state.planner_lm = snapshot["planner_lm"]
    state.delegate_lm = snapshot["delegate_lm"]


def _restore_runtime_settings_env(
    *,
    env_path: Path,
    env_text: str | None,
    env_snapshot: dict[str, str | None],
) -> None:
    if env_text is None:
        if env_path.exists():
            env_path.unlink()
    else:
        env_path.write_text(env_text, encoding="utf-8")

    for key, value in env_snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def build_runtime_settings_snapshot(*, state: ServerState) -> RuntimeSettingsSnapshot:
    snapshot = get_settings_snapshot(
        keys=list(RUNTIME_SETTINGS_KEYS),
        env_path=state.config.env_path,
    )
    return RuntimeSettingsSnapshot(**snapshot)


async def apply_runtime_settings_patch(
    *,
    state: ServerState,
    request: RuntimeSettingsUpdateRequest,
    planner_loader=None,
    delegate_loader=None,
) -> RuntimeSettingsUpdateResponse:
    config = state.config
    if config.app_env != "local":
        raise HTTPException(
            status_code=403,
            detail="Runtime settings updates are allowed only when APP_ENV=local.",
        )

    try:
        normalized = normalize_updates(
            request.updates,
            allowlist=RUNTIME_SETTINGS_ALLOWLIST,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    runtime_snapshot = _capture_runtime_config_snapshot(state=state)
    env_text = (
        config.env_path.read_text(encoding="utf-8")
        if config.env_path.exists()
        else None
    )
    env_snapshot = {key: os.environ.get(key) for key in RUNTIME_SETTINGS_KEYS}
    result = apply_env_updates(updates=normalized, env_path=config.env_path)
    applied_updates = {
        key: normalized[key] for key in result["updated"] if key in normalized
    }
    if not applied_updates:
        return RuntimeSettingsUpdateResponse(**result)

    if not any(key in RUNTIME_MODEL_RELOAD_KEYS for key in applied_updates):
        apply_runtime_settings_to_config(config=config, normalized=applied_updates)
        return RuntimeSettingsUpdateResponse(**result)

    trial_config = config.model_copy(deep=True)
    apply_runtime_settings_to_config(config=trial_config, normalized=applied_updates)

    await cancel_optional_runtime_startup(state)
    try:
        planner_model_name = trial_config.agent_model
        delegate_model_name = trial_config.agent_delegate_model
        resolved_planner_loader = planner_loader or get_planner_lm_from_env
        resolved_delegate_loader = delegate_loader or get_delegate_lm_from_env
        next_planner_lm = await asyncio.to_thread(
            resolved_planner_loader,
            env_file=config.env_path,
            model_name=planner_model_name,
        )
        next_delegate_lm = await asyncio.to_thread(
            resolved_delegate_loader,
            env_file=config.env_path,
            model_name=delegate_model_name,
            default_max_tokens=trial_config.agent_delegate_max_tokens,
        )
    except Exception:
        _restore_runtime_settings_env(
            env_path=config.env_path,
            env_text=env_text,
            env_snapshot=env_snapshot,
        )
        _restore_runtime_config_snapshot(state=state, snapshot=runtime_snapshot)
        schedule_optional_runtime_startup(state)
        raise

    apply_runtime_settings_to_config(config=config, normalized=applied_updates)
    state.planner_lm = next_planner_lm
    state.delegate_lm = next_delegate_lm
    schedule_optional_runtime_startup(state)
    return RuntimeSettingsUpdateResponse(**result)
