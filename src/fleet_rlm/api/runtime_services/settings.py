"""Runtime settings helpers for the runtime router."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Mapping, TypedDict

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
    get_delegate_small_lm_from_env,
    get_planner_lm_from_env,
    schedule_optional_runtime_startup,
)
from ..config import AppConfig
from ..dependencies import (
    AuthDeps,
    ConfigDeps,
    DiagnosticsDeps,
    LmDeps,
    PersistenceDeps,
    SessionCacheDeps,
    compose_server_state,
)
from ..schemas.runtime import (
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
)

logger = logging.getLogger(__name__)

RUNTIME_MODEL_RELOAD_KEYS = frozenset(
    {
        "DSPY_LM_MODEL",
        "DSPY_DELEGATE_LM_MODEL",
        "DSPY_DELEGATE_LM_SMALL_MODEL",
        "DSPY_DELEGATE_LM_MAX_TOKENS",
        "DSPY_LM_API_BASE",
        "DSPY_DELEGATE_LM_API_BASE",
        "DSPY_LM_MAX_TOKENS",
        "DSPY_ADAPTER",
        "DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING",
        "DSPY_LLM_API_KEY",
        "DSPY_LM_API_KEY",
        "DSPY_DELEGATE_LM_API_KEY",
        "FLEET_RLM_ACTION_MAX_TOKENS",
    }
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _settings_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _settings_positive_int(value: str, *, default: int) -> int:
    return max(int(value.strip() or str(default)), 1)


def _settings_positive_float(value: str, *, default: float) -> float:
    parsed = value.strip() or str(default)
    try:
        result = float(parsed)
    except ValueError as exc:
        raise ValueError(f"expected a positive float, got {value!r}") from exc
    if result <= 0:
        raise ValueError(f"expected a positive float, got {result}")
    return result


def _settings_optional_float(value: str) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError as exc:
        raise ValueError(f"expected a float, got {value!r}") from exc


class RuntimeConfigSnapshot(TypedDict):
    agent_model: str | None
    agent_delegate_model: str | None
    agent_delegate_small_model: str | None
    agent_delegate_max_tokens: int
    rlm_action_max_tokens: int
    planner_lm: object | None
    delegate_lm: object | None
    delegate_small_lm: object | None
    planner_max_tokens: int
    planner_lm_timeout_s: float
    delegate_lm_timeout_s: float
    planner_temperature: float | None


def apply_runtime_settings_to_config(*, config: AppConfig, normalized: dict[str, str]) -> None:
    if "DSPY_LM_MODEL" in normalized:
        resolved_planner_model = normalized["DSPY_LM_MODEL"].strip()
        config.agent_model = resolved_planner_model or None

    if "DSPY_DELEGATE_LM_MODEL" in normalized:
        resolved_delegate_model = normalized["DSPY_DELEGATE_LM_MODEL"].strip()
        config.agent_delegate_model = resolved_delegate_model or None

    if "DSPY_DELEGATE_LM_SMALL_MODEL" in normalized:
        resolved_delegate_small_model = normalized["DSPY_DELEGATE_LM_SMALL_MODEL"].strip()
        config.agent_delegate_small_model = resolved_delegate_small_model or None

    if "DSPY_DELEGATE_LM_MAX_TOKENS" in normalized:
        config.agent_delegate_max_tokens = _settings_positive_int(
            normalized["DSPY_DELEGATE_LM_MAX_TOKENS"],
            default=64000,
        )

    if "DSPY_LM_MAX_TOKENS" in normalized:
        config.planner_max_tokens = _settings_positive_int(
            normalized["DSPY_LM_MAX_TOKENS"],
            default=64000,
        )

    if "DSPY_PLANNER_LM_TIMEOUT_S" in normalized:
        config.planner_lm_timeout_s = _settings_positive_float(
            normalized["DSPY_PLANNER_LM_TIMEOUT_S"],
            default=60.0,
        )

    if "DSPY_DELEGATE_LM_TIMEOUT_S" in normalized:
        config.delegate_lm_timeout_s = _settings_positive_float(
            normalized["DSPY_DELEGATE_LM_TIMEOUT_S"],
            default=60.0,
        )

    if "DSPY_PLANNER_LM_TEMPERATURE" in normalized:
        config.planner_temperature = _settings_optional_float(
            normalized["DSPY_PLANNER_LM_TEMPERATURE"],
        )

    if "FLEET_RLM_ACTION_MAX_TOKENS" in normalized:
        config.rlm_action_max_tokens = _settings_positive_int(
            normalized["FLEET_RLM_ACTION_MAX_TOKENS"],
            default=4096,
        )

    if "VOLUME_NAME" in normalized:
        resolved_volume_name = normalized["VOLUME_NAME"].strip()
        config.volume_name = resolved_volume_name or None

    if "TIMEOUT" in normalized:
        config.timeout = _settings_positive_int(normalized["TIMEOUT"], default=900)

    if "INTERPRETER_ASYNC_EXECUTE" in normalized:
        config.interpreter_async_execute = _settings_bool(normalized["INTERPRETER_ASYNC_EXECUTE"])

    if "DATABASE_URL" in normalized:
        resolved_database_url = normalized["DATABASE_URL"].strip()
        config.database_url = resolved_database_url or None

    if "DATABASE_ADMIN_URL" in normalized:
        resolved_database_admin_url = normalized["DATABASE_ADMIN_URL"].strip()
        config.database_admin_url = resolved_database_admin_url or None

    if "DATABASE_REQUIRED" in normalized:
        config.database_required = _settings_bool(normalized["DATABASE_REQUIRED"])

    if "DB_ECHO" in normalized:
        config.db_echo = _settings_bool(normalized["DB_ECHO"])

    if "DB_VALIDATE_ON_STARTUP" in normalized:
        config.db_validate_on_startup = _settings_bool(normalized["DB_VALIDATE_ON_STARTUP"])


def _capture_runtime_config_snapshot(*, config: AppConfig, lm_deps: LmDeps) -> RuntimeConfigSnapshot:
    return {
        "agent_model": config.agent_model,
        "agent_delegate_model": config.agent_delegate_model,
        "agent_delegate_small_model": config.agent_delegate_small_model,
        "agent_delegate_max_tokens": config.agent_delegate_max_tokens,
        "rlm_action_max_tokens": config.rlm_action_max_tokens,
        "planner_lm": lm_deps.planner_lm,
        "delegate_lm": lm_deps.delegate_lm,
        "delegate_small_lm": lm_deps.delegate_small_lm,
        "planner_max_tokens": config.planner_max_tokens,
        "planner_lm_timeout_s": config.planner_lm_timeout_s,
        "delegate_lm_timeout_s": config.delegate_lm_timeout_s,
        "planner_temperature": config.planner_temperature,
    }


def _restore_runtime_config_snapshot(
    *,
    config: AppConfig,
    lm_deps: LmDeps,
    snapshot: RuntimeConfigSnapshot,
) -> None:
    config.agent_model = snapshot["agent_model"]
    config.agent_delegate_model = snapshot["agent_delegate_model"]
    config.agent_delegate_small_model = snapshot["agent_delegate_small_model"]
    config.agent_delegate_max_tokens = snapshot["agent_delegate_max_tokens"]
    config.rlm_action_max_tokens = snapshot["rlm_action_max_tokens"]
    lm_deps.planner_lm = snapshot["planner_lm"]
    lm_deps.delegate_lm = snapshot["delegate_lm"]
    lm_deps.delegate_small_lm = snapshot["delegate_small_lm"]
    config.planner_max_tokens = snapshot["planner_max_tokens"]
    config.planner_lm_timeout_s = snapshot["planner_lm_timeout_s"]
    config.delegate_lm_timeout_s = snapshot["delegate_lm_timeout_s"]
    config.planner_temperature = snapshot["planner_temperature"]


def _restore_runtime_settings_env(
    *,
    env_path: Path,
    env_text: str | None,
    env_snapshot: dict[str, str | None],
) -> None:
    try:
        if env_text is None:
            if env_path.exists():
                env_path.unlink()
        else:
            env_path.write_text(env_text, encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not restore env file %s: %s", env_path, exc)

    for key, value in env_snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def build_runtime_settings_snapshot(
    *,
    config_deps: ConfigDeps,
    extra_values: Mapping[str, str | None] | None = None,
) -> RuntimeSettingsSnapshot:
    snapshot = get_settings_snapshot(
        keys=list(RUNTIME_SETTINGS_KEYS),
        extra_values=extra_values,
        env_path=config_deps.config.env_path,
    )
    return RuntimeSettingsSnapshot(**snapshot)


async def apply_runtime_settings_patch(
    *,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    diagnostics_deps: DiagnosticsDeps,
    request: RuntimeSettingsUpdateRequest,
    planner_loader=None,
    delegate_loader=None,
) -> RuntimeSettingsUpdateResponse:
    config = config_deps.config
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

    validation_config = config.model_copy(deep=True)
    try:
        apply_runtime_settings_to_config(config=validation_config, normalized=normalized)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid runtime setting value: {exc}") from exc

    runtime_snapshot = _capture_runtime_config_snapshot(config=config, lm_deps=lm_deps)
    env_text = config.env_path.read_text(encoding="utf-8") if config.env_path.exists() else None
    env_snapshot = {key: os.environ.get(key) for key in RUNTIME_SETTINGS_KEYS}
    result = apply_env_updates(updates=normalized, env_path=config.env_path)
    applied_updates = {key: normalized[key] for key in result["updated"] if key in normalized}
    if not applied_updates:
        return RuntimeSettingsUpdateResponse(**result)

    if not any(key in RUNTIME_MODEL_RELOAD_KEYS for key in applied_updates):
        apply_runtime_settings_to_config(config=config, normalized=applied_updates)
        return RuntimeSettingsUpdateResponse(**result)

    trial_config = config.model_copy(deep=True)
    apply_runtime_settings_to_config(config=trial_config, normalized=applied_updates)

    state = compose_server_state(
        config_deps=config_deps,
        lm_deps=lm_deps,
        auth_deps=AuthDeps(),
        session_cache_deps=SessionCacheDeps(),
        persistence_deps=PersistenceDeps(),
        diagnostics_deps=diagnostics_deps,
    )
    await cancel_optional_runtime_startup(state)
    try:
        planner_model_name = trial_config.agent_model
        delegate_model_name = trial_config.agent_delegate_model
        delegate_small_model_name = trial_config.agent_delegate_small_model
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
        next_delegate_small_lm = await asyncio.to_thread(
            get_delegate_small_lm_from_env,
            env_file=config.env_path,
            model_name=delegate_small_model_name,
            default_max_tokens=trial_config.agent_delegate_max_tokens,
        )
    except Exception:
        _restore_runtime_settings_env(
            env_path=config.env_path,
            env_text=env_text,
            env_snapshot=env_snapshot,
        )
        _restore_runtime_config_snapshot(config=config, lm_deps=lm_deps, snapshot=runtime_snapshot)
        schedule_optional_runtime_startup(state)
        raise

    apply_runtime_settings_to_config(config=config, normalized=applied_updates)
    lm_deps.planner_lm = next_planner_lm
    lm_deps.delegate_lm = next_delegate_lm
    lm_deps.delegate_small_lm = next_delegate_small_lm
    schedule_optional_runtime_startup(state)
    return RuntimeSettingsUpdateResponse(**result)
