"""Model resolution, credential snapshotting, and judge LM helpers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import dspy
from dotenv import dotenv_values
from pydantic import SecretStr

from fleet_rlm.models import ResolvedModel, Role

if TYPE_CHECKING:
    from fleet_rlm.config.settings import FleetSettings


def resolve_models(settings: FleetSettings) -> dict[str, ResolvedModel]:
    """Resolve role model routes, inherited options, and explicit credentials."""

    def credential(reference: str, default: SecretStr) -> SecretStr:
        if not reference:
            return default
        if reference in settings._resolved_credentials:
            return settings._resolved_credentials[reference]
        value = os.environ.get(reference)
        if value is None and settings._credential_env_file:
            value = dotenv_values(settings._credential_env_file).get(reference)
        if not value:
            raise ValueError(f"Missing credential environment variable: {reference}")
        settings._resolved_credentials[reference] = SecretStr(value)
        return settings._resolved_credentials[reference]

    default_key = credential(settings.fleet_llm_api_key_env, settings.fleet_llm_api_key)
    resolved: dict[str, ResolvedModel] = {
        "shared": ResolvedModel(
            model=settings.fleet_llm_model,
            api_base=settings.fleet_llm_api_base,
            api_key=default_key,
            adapter=settings.fleet_llm_adapter,
            model_type=settings.fleet_llm_model_type,
            response_format=settings.fleet_llm_response_format,
        )
    }
    for role in ("root", "sub", "reflection", "judge"):
        selected = settings.fleet_correctness_model if role == "judge" else getattr(settings, f"fleet_{role}_model")
        independent = bool(selected)
        base = getattr(settings, f"fleet_{role}_api_base")
        reference = getattr(settings, f"fleet_{role}_api_key_env")
        adapter = getattr(settings, f"fleet_{role}_adapter")
        model_type = getattr(settings, f"fleet_{role}_model_type")
        options: dict[str, Any] = {}
        for name in ("max_tokens", "temperature", "num_retries", "cache"):
            field = f"fleet_{role}_{name}"
            value = getattr(settings, field)
            shared = getattr(settings, f"fleet_llm_{name}")
            if settings.configuration_source(field) == "default" and shared is not None:
                value = shared
            options[name] = value
        resolved[role] = ResolvedModel(
            model=selected
            or (
                "openai/system.ai.deepseek-v4-1-flash-1017"
                if role in {"root", "sub"} and settings.configuration_source("fleet_llm_model") == "default"
                else settings.fleet_llm_model
            ),
            api_base=base if base is not None else ("" if independent else settings.fleet_llm_api_base),
            api_key=credential(reference or "", SecretStr("") if independent else default_key),
            adapter=adapter or settings.fleet_llm_adapter,
            model_type=model_type or settings.fleet_llm_model_type,
            response_format=getattr(settings, f"fleet_{role}_response_format") or settings.fleet_llm_response_format,
            **options,
        )
    return resolved


def judge_environment(settings: FleetSettings) -> dict[str, str]:
    """Environment that points MLflow's `openai:/` scorers at the judge role's own endpoint and key."""
    resolved = settings.resolved_model("judge")
    key = resolved.api_key.get_secret_value()
    if not settings.fleet_judge_model.startswith("openai:/"):
        return {}
    environment = {}
    if resolved.api_base:
        environment["OPENAI_API_BASE"] = resolved.api_base
    if key:
        environment["OPENAI_API_KEY"] = key
    return environment


def build_jev_lm(settings: FleetSettings) -> dspy.LM | None:
    """The TypeSafe System One LM, or None when no credential is configured."""
    if not settings.typesafe_api_key:
        return None
    model = settings.typesafe_model.removeprefix("typesafe:")
    return dspy.LM(f"typesafe:{model}", api_key=settings.typesafe_api_key, cache=False, engine="lm15")


def build_judge_lm(settings: FleetSettings) -> dspy.LM:
    """Build the independently configured DSPy correctness judge."""
    from fleet_rlm.models import build_lm

    return build_lm(settings, settings.fleet_correctness_model, "judge")


def list_role_models(settings: FleetSettings, scope: str = "", role: Role | Literal["shared"] = "root") -> list[str]:
    """Browse the selected role's explicit compatible endpoint."""
    from fleet_rlm.models import list_models

    return list_models(settings, scope=scope, role=role)
