"""Runtime diagnostics, connectivity tests, and status assembly."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from typing import Any, Literal

from pydantic import ValidationError

from fleet_rlm.integrations.observability.config import MlflowConfig
from fleet_rlm.integrations.providers.daytona import DaytonaConfigError

from ..bootstrap_observability import resolve_mlflow_auto_start_enabled
from ..dependencies import ServerState
from ..schemas.core import (
    RuntimeActiveModels,
    RuntimeConnectivityTestResponse,
    RuntimeStatusResponse,
    RuntimeTestCache,
)
from .common import (
    RUNTIME_TEST_TIMEOUT_SECONDS,
    coerce_output_text,
    extract_lm_text,
    run_blocking,
    sanitize_error,
    utc_now_iso,
)

LoadModalConfig = Callable[[], Any]


def resolve_active_model(value: str | None, env_key: str) -> str:
    direct = (value or "").strip()
    if direct:
        return direct
    fallback = (os.environ.get(env_key) or "").strip()
    return fallback


def cache_runtime_test(
    *, state: ServerState, result: RuntimeConnectivityTestResponse
) -> None:
    state.runtime_test_results[result.kind] = result.model_dump(mode="json")


def connectivity_result_from_cache(
    *, state: ServerState, kind: str
) -> RuntimeConnectivityTestResponse | None:
    cached = state.runtime_test_results.get(kind)
    if isinstance(cached, RuntimeConnectivityTestResponse):
        return cached
    if not isinstance(cached, dict):
        return None
    try:
        return RuntimeConnectivityTestResponse(**cached)
    except ValidationError:
        return None


def modal_preflight(
    *,
    secret_name: str,
    load_modal_config: LoadModalConfig,
) -> tuple[dict[str, bool], list[str]]:
    modal_cfg_candidate = load_modal_config()
    modal_cfg = modal_cfg_candidate if isinstance(modal_cfg_candidate, dict) else {}
    credentials_from_env = bool(
        os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")
    )
    credentials_from_profile = bool(
        modal_cfg.get("token_id") and modal_cfg.get("token_secret")
    )
    credentials_available = credentials_from_env or credentials_from_profile
    secret_name_set = bool(secret_name.strip())

    checks = {
        "credentials_from_env": credentials_from_env,
        "credentials_from_profile": credentials_from_profile,
        "credentials_available": credentials_available,
        "secret_name_set": secret_name_set,
    }

    guidance: list[str] = []
    if not credentials_available:
        guidance.append(
            "Modal credentials missing. Configure MODAL_TOKEN_ID/MODAL_TOKEN_SECRET or run `modal setup`."
        )
    if not secret_name_set:
        guidance.append(
            "Runtime secret name is empty. Set SECRET_NAME in Runtime settings."
        )

    return checks, guidance


def lm_preflight() -> tuple[dict[str, bool], list[str]]:
    has_model = bool((os.environ.get("DSPY_LM_MODEL") or "").strip())
    has_api_key = bool(
        (
            os.environ.get("DSPY_LLM_API_KEY")
            or os.environ.get("DSPY_LM_API_KEY")
            or ""
        ).strip()
    )
    checks = {
        "model_set": has_model,
        "api_key_set": has_api_key,
    }

    guidance: list[str] = []
    if not has_model:
        guidance.append("DSPY_LM_MODEL is not set.")
    if not has_api_key:
        guidance.append("DSPY_LLM_API_KEY (or DSPY_LM_API_KEY) is not set.")

    return checks, guidance


def daytona_preflight(
    *,
    sandbox_provider: str | None = None,
) -> tuple[dict[str, bool], list[str]]:
    api_key = (os.environ.get("DAYTONA_API_KEY") or "").strip()
    api_url = (os.environ.get("DAYTONA_API_URL") or "").strip()
    target = (os.environ.get("DAYTONA_TARGET") or "").strip()
    legacy_api_base = (os.environ.get("DAYTONA_API_BASE_URL") or "").strip()

    checks = {
        "api_key_set": bool(api_key),
        "api_url_set": bool(api_url),
        "target_set": bool(target),
        "sandbox_provider_set": bool(
            (os.environ.get("SANDBOX_PROVIDER") or sandbox_provider or "").strip()
        ),
        "legacy_api_base_url_ok": not bool(legacy_api_base),
        "configured": False,
    }

    guidance: list[str] = []
    try:
        from fleet_rlm.integrations.providers.daytona import resolve_daytona_config

        resolve_daytona_config()
        checks["configured"] = True
    except DaytonaConfigError as exc:
        guidance.append(str(exc))

    if legacy_api_base:
        guidance.append(
            "DAYTONA_API_BASE_URL is not supported here. Use DAYTONA_API_URL instead."
        )

    deduped_guidance: list[str] = []
    for item in guidance:
        if item not in deduped_guidance:
            deduped_guidance.append(item)

    return checks, deduped_guidance


def build_runtime_test_result(
    *,
    kind: Literal["modal", "lm", "daytona"],
    ok: bool,
    preflight_ok: bool,
    checked_at: str,
    checks: dict[str, Any],
    guidance: list[str],
    latency_ms: int | None = None,
    output_preview: str | None = None,
    error: str | None = None,
) -> RuntimeConnectivityTestResponse:
    return RuntimeConnectivityTestResponse(
        kind=kind,
        ok=ok,
        preflight_ok=preflight_ok,
        checked_at=checked_at,
        checks=checks,
        guidance=guidance,
        latency_ms=latency_ms,
        output_preview=output_preview,
        error=error,
    )


def preflight_failure_result(
    *,
    kind: Literal["modal", "lm", "daytona"],
    checked_at: str,
    checks: dict[str, Any],
    guidance: list[str],
    error: str,
) -> RuntimeConnectivityTestResponse:
    return build_runtime_test_result(
        kind=kind,
        ok=False,
        preflight_ok=False,
        checked_at=checked_at,
        checks=checks,
        guidance=guidance,
        error=error,
    )


async def _ensure_runtime_models(state: ServerState) -> tuple[Any | None, Any | None]:
    from ..bootstrap import ensure_runtime_models

    return await ensure_runtime_models(state)


async def run_connectivity_test(
    *,
    state: ServerState,
    kind: Literal["modal", "lm", "daytona"],
    preflight_ok: bool,
    checks: dict[str, Any],
    guidance: list[str],
    preflight_error: str,
    default_error: str,
    timeout_error: str | None,
    run_smoke: Callable[[], Awaitable[tuple[bool, str | None, str | None]]],
) -> RuntimeConnectivityTestResponse:
    checked_at = utc_now_iso()
    if not preflight_ok:
        result = preflight_failure_result(
            kind=kind,
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error=preflight_error,
        )
        cache_runtime_test(state=state, result=result)
        return result

    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None
    ok = False

    started = time.perf_counter()
    try:
        ok, output_preview, error = await run_smoke()
    except asyncio.TimeoutError:
        error = timeout_error or f"{kind.capitalize()} connectivity test timed out."
    except Exception as exc:  # pragma: no cover - provider/network path
        error = sanitize_error(exc)
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)

    if not ok and not error:
        error = default_error

    result = build_runtime_test_result(
        kind=kind,
        ok=ok,
        preflight_ok=True,
        checked_at=checked_at,
        checks=checks,
        guidance=guidance,
        latency_ms=latency_ms,
        output_preview=output_preview,
        error=error,
    )
    cache_runtime_test(state=state, result=result)
    return result


async def run_modal_connection_test(
    *,
    state: ServerState,
    load_modal_config: LoadModalConfig,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = modal_preflight(
        secret_name=state.config.secret_name,
        load_modal_config=load_modal_config,
    )
    sandbox = None

    async def _run_smoke() -> tuple[bool, str | None, str | None]:
        nonlocal sandbox
        import modal

        app = await modal.App.lookup.aio(
            "fleet-rlm-runtime-smoke", create_if_missing=True
        )
        sandbox = await modal.Sandbox.create.aio(app=app, timeout=30)
        proc = await sandbox.exec.aio("python", "-c", "print('ok')", timeout=15)
        await proc.wait.aio()
        output_preview = coerce_output_text(await proc.stdout.read.aio())
        if output_preview != "ok":
            return False, output_preview, "Modal sandbox returned unexpected output."
        return True, output_preview, None

    try:
        return await run_connectivity_test(
            state=state,
            kind="modal",
            preflight_ok=checks["credentials_available"] and checks["secret_name_set"],
            checks=checks,
            guidance=guidance,
            preflight_error="Modal preflight checks failed.",
            default_error="Modal connectivity test failed.",
            timeout_error=(
                f"Modal test timed out after {RUNTIME_TEST_TIMEOUT_SECONDS}s. "
                "Check connectivity and credentials."
            ),
            run_smoke=_run_smoke,
        )
    finally:
        if sandbox is not None:
            with suppress(Exception):
                await sandbox.terminate.aio()


async def run_lm_connection_test(
    *,
    state: ServerState,
    planner_loader=None,
    delegate_loader=None,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = lm_preflight()

    async def _run_smoke() -> tuple[bool, str | None, str | None]:
        if planner_loader is None and delegate_loader is None:
            planner_lm, delegate_lm = await _ensure_runtime_models(state)
            if planner_lm is None:
                raise RuntimeError(
                    "Failed to construct planner LM from environment settings."
                )
        else:
            if planner_loader is None:
                raise ValueError(
                    "delegate_loader was provided without planner_loader; "
                    "both must be supplied when overriding LM loaders."
                )
            planner_lm = await run_blocking(
                partial(
                    planner_loader,
                    env_file=state.config.env_path,
                    model_name=state.config.agent_model,
                ),
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )
            if planner_lm is None:
                raise RuntimeError(
                    "Failed to construct planner LM from environment settings."
                )
            delegate_lm = None
            if delegate_loader is not None:
                delegate_lm = await run_blocking(
                    partial(
                        delegate_loader,
                        env_file=state.config.env_path,
                        model_name=state.config.agent_delegate_model,
                        default_max_tokens=state.config.agent_delegate_max_tokens,
                    ),
                    timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
                )

        def _invoke() -> str:
            response = planner_lm("Reply with exactly OK")
            return extract_lm_text(response)

        output_preview = await run_blocking(
            _invoke,
            timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
        )

        state.planner_lm = planner_lm
        state.delegate_lm = delegate_lm
        return bool(output_preview), output_preview, None

    return await run_connectivity_test(
        state=state,
        kind="lm",
        preflight_ok=checks["model_set"] and checks["api_key_set"],
        checks=checks,
        guidance=guidance,
        preflight_error="LM preflight checks failed.",
        default_error="LM connectivity test failed.",
        timeout_error=(
            f"LM test timed out after {RUNTIME_TEST_TIMEOUT_SECONDS}s. "
            "Check API connectivity and credentials."
        ),
        run_smoke=_run_smoke,
    )


async def run_daytona_connection_test(
    *,
    state: ServerState,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = daytona_preflight(
        sandbox_provider=state.config.sandbox_provider,
    )

    async def _run_smoke() -> tuple[bool, str | None, str | None]:
        try:
            from daytona import Daytona, DaytonaConfig

            from fleet_rlm.integrations.providers.daytona import (
                resolve_daytona_config,
            )

            config = resolve_daytona_config()
            client = Daytona(
                DaytonaConfig(
                    api_key=config.api_key,
                    api_url=config.api_url.rstrip("/"),
                    target=config.target,
                )
            )

            def _fetch() -> Any:
                return client.list(limit=1)

            response = await run_blocking(
                _fetch,
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )
            items = getattr(response, "items", [])
            output_preview = f"Daytona connectivity verified. Found {len(items)} sandboxes (limited)."
            return True, output_preview, None
        except ImportError:
            return False, None, "Daytona SDK is not installed."

    return await run_connectivity_test(
        state=state,
        kind="daytona",
        preflight_ok=checks["configured"],
        checks=checks,
        guidance=guidance,
        preflight_error="Daytona preflight checks failed.",
        default_error="Daytona connectivity test failed.",
        timeout_error=(
            f"Daytona test timed out after {RUNTIME_TEST_TIMEOUT_SECONDS}s. "
            "Check connectivity and credentials."
        ),
        run_smoke=_run_smoke,
    )


def build_runtime_status_response(
    *,
    state: ServerState,
    load_modal_config: LoadModalConfig,
) -> RuntimeStatusResponse:
    mlflow_cfg = MlflowConfig.from_env()
    llm_checks, llm_guidance = lm_preflight()
    modal_checks, modal_guidance = modal_preflight(
        secret_name=state.config.secret_name,
        load_modal_config=load_modal_config,
    )
    daytona_checks, daytona_guidance = daytona_preflight(
        sandbox_provider=state.config.sandbox_provider,
    )

    modal_test = connectivity_result_from_cache(state=state, kind="modal")
    lm_test = connectivity_result_from_cache(state=state, kind="lm")
    daytona_test = connectivity_result_from_cache(state=state, kind="daytona")

    ready = state.is_ready and bool(
        modal_test is not None and modal_test.ok and lm_test is not None and lm_test.ok
    )
    mlflow_startup_status = state.optional_service_status.get("mlflow", "pending")
    mlflow_startup_error = state.optional_service_errors.get("mlflow")

    guidance: list[str] = []
    guidance.extend(llm_guidance)
    guidance.extend(modal_guidance)
    guidance.extend(daytona_guidance)
    if modal_test is None or lm_test is None:
        guidance.append(
            "Run Runtime connection tests to validate live provider connectivity."
        )
    if mlflow_cfg.enabled and mlflow_startup_status == "degraded":
        guidance.append(
            "MLflow startup is degraded. Verify MLFLOW_TRACKING_URI reachability/auth, "
            "set MLFLOW_AUTO_START=false to keep MLflow manual in local dev, or set "
            "MLFLOW_ENABLED=false for this environment."
        )

    mlflow_auto_start_enabled = resolve_mlflow_auto_start_enabled(
        app_env=state.config.app_env,
        mlflow_enabled=mlflow_cfg.enabled,
        tracking_uri=mlflow_cfg.tracking_uri,
    )

    return RuntimeStatusResponse(
        app_env=state.config.app_env,
        write_enabled=state.config.app_env == "local",
        ready=ready,
        sandbox_provider=(
            "daytona" if state.config.sandbox_provider == "daytona" else "modal"
        ),
        active_models=RuntimeActiveModels(
            planner=resolve_active_model(state.config.agent_model, "DSPY_LM_MODEL"),
            delegate=resolve_active_model(
                state.config.agent_delegate_model,
                "DSPY_DELEGATE_LM_MODEL",
            ),
            delegate_small=resolve_active_model(
                state.config.agent_delegate_small_model,
                "DSPY_DELEGATE_LM_SMALL_MODEL",
            ),
        ),
        llm={
            **llm_checks,
            "planner_configured": state.planner_lm is not None,
            "startup_status": state.optional_service_status.get(
                "planner_lm", "pending"
            ),
            "startup_error": state.optional_service_errors.get("planner_lm"),
        },
        mlflow={
            "enabled": mlflow_cfg.enabled,
            "auto_start_enabled": mlflow_auto_start_enabled,
            "startup_status": mlflow_startup_status,
            "startup_error": mlflow_startup_error,
        },
        modal={
            **modal_checks,
            "secret_name": state.config.secret_name,
            "configured_volume": state.config.volume_name or "",
        },
        daytona={
            **daytona_checks,
            "guidance": daytona_guidance,
        },
        tests=RuntimeTestCache(modal=modal_test, lm=lm_test, daytona=daytona_test),
        guidance=guidance,
    )
