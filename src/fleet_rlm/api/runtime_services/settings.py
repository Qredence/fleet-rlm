"""Runtime settings helpers for the runtime router."""

from __future__ import annotations

import asyncio

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
    ensure_runtime_models,
    schedule_optional_runtime_startup,
)
from ..dependencies import ServerState
from ..schemas.core import (
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
)


def runtime_setting_overrides(
    *, secret_name: str, volume_name: str | None
) -> dict[str, str]:
    return {
        "SECRET_NAME": secret_name,
        "VOLUME_NAME": volume_name or "",
    }


def apply_runtime_settings_to_config(
    *, state: ServerState, normalized: dict[str, str]
) -> None:
    config = state.config

    if "SECRET_NAME" in normalized:
        resolved_secret_name = normalized["SECRET_NAME"].strip()
        config.secret_name = resolved_secret_name or "LITELLM"

    if "VOLUME_NAME" in normalized:
        resolved_volume_name = normalized["VOLUME_NAME"].strip()
        config.volume_name = resolved_volume_name or None

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

    if "SANDBOX_PROVIDER" in normalized:
        resolved_sandbox_provider = normalized["SANDBOX_PROVIDER"].strip().lower()
        if resolved_sandbox_provider in {"modal", "daytona"}:
            config.sandbox_provider = resolved_sandbox_provider


def build_runtime_settings_snapshot(*, state: ServerState) -> RuntimeSettingsSnapshot:
    snapshot = get_settings_snapshot(
        keys=list(RUNTIME_SETTINGS_KEYS),
        extra_values=runtime_setting_overrides(
            secret_name=state.config.secret_name,
            volume_name=state.config.volume_name,
        ),
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

    result = apply_env_updates(updates=normalized, env_path=config.env_path)
    await cancel_optional_runtime_startup(state)
    apply_runtime_settings_to_config(state=state, normalized=normalized)
    saved_delegate = state.delegate_lm
    state.planner_lm = None
    state.delegate_lm = None
    if planner_loader is None and delegate_loader is None:
        await ensure_runtime_models(state, config)
    else:
        planner_model_name = config.agent_model
        delegate_model_name = config.agent_delegate_model
        if planner_loader is None:
            await ensure_runtime_models(state, config)
        else:
            state.planner_lm = await asyncio.to_thread(
                planner_loader,
                env_file=config.env_path,
                model_name=planner_model_name,
            )
        if delegate_loader is None:
            state.delegate_lm = saved_delegate
        else:
            state.delegate_lm = await asyncio.to_thread(
                delegate_loader,
                env_file=config.env_path,
                model_name=delegate_model_name,
                default_max_tokens=config.agent_delegate_max_tokens,
            )
    schedule_optional_runtime_startup(state, config)
    return RuntimeSettingsUpdateResponse(**result)
