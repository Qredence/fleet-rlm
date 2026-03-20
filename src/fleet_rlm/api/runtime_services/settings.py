"""Runtime settings service helpers used by the runtime router."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from fleet_rlm.infrastructure.config.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    RUNTIME_SETTINGS_KEYS,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)

from ..dependencies import ServerState
from ..schemas.core import (
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
)

PlannerFactory = Callable[..., Any]
DelegateFactory = Callable[..., Any]


def runtime_setting_overrides(
    *, secret_name: str, volume_name: str | None
) -> dict[str, str]:
    return {
        "SECRET_NAME": secret_name,
        "VOLUME_NAME": volume_name or "",
    }


def resolve_active_model(value: str | None, env_key: str) -> str:
    direct = (value or "").strip()
    if direct:
        return direct
    fallback = (os.environ.get(env_key) or "").strip()
    return fallback


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


def refresh_runtime_models(
    *,
    state: ServerState,
    planner_lm_factory: PlannerFactory,
    delegate_lm_factory: DelegateFactory,
) -> None:
    config = state.config
    state.planner_lm = planner_lm_factory(
        env_file=config.env_path,
        model_name=config.agent_model,
    )
    state.delegate_lm = delegate_lm_factory(
        env_file=config.env_path,
        model_name=config.agent_delegate_model,
        default_max_tokens=config.agent_delegate_max_tokens,
    )


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


def apply_runtime_settings_patch(
    *,
    state: ServerState,
    request: RuntimeSettingsUpdateRequest,
    planner_lm_factory: PlannerFactory,
    delegate_lm_factory: DelegateFactory,
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
    apply_runtime_settings_to_config(state=state, normalized=normalized)
    refresh_runtime_models(
        state=state,
        planner_lm_factory=planner_lm_factory,
        delegate_lm_factory=delegate_lm_factory,
    )
    return RuntimeSettingsUpdateResponse(**result)
