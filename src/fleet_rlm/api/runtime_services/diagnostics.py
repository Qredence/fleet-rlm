"""Runtime diagnostics and status service helpers."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Literal

from fleet_rlm.infrastructure.providers.daytona import DaytonaConfigError

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
from .settings import resolve_active_model

LoadModalConfig = Callable[[], Any]
PlannerFactory = Callable[..., Any]
DelegateFactory = Callable[..., Any]
ResolveDaytonaConfig = Callable[[], Any]
BuildDaytonaClient = Callable[[], Any]


def cache_runtime_test(
    *, state: ServerState, result: RuntimeConnectivityTestResponse
) -> None:
    state.runtime_test_results[result.kind] = result.model_dump(mode="json")


def connectivity_result_from_cache(
    *, state: ServerState, kind: str
) -> RuntimeConnectivityTestResponse | None:
    cached = state.runtime_test_results.get(kind)
    if not isinstance(cached, dict):
        return None
    return RuntimeConnectivityTestResponse(**cached)


def modal_preflight(
    *,
    secret_name: str,
    load_modal_config: LoadModalConfig,
) -> tuple[dict[str, bool], list[str]]:
    modal_cfg = load_modal_config()
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
    resolve_daytona_config: ResolveDaytonaConfig,
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


def preflight_failure_result(
    *,
    kind: Literal["modal", "lm", "daytona"],
    checked_at: str,
    checks: dict[str, Any],
    guidance: list[str],
    error: str,
) -> RuntimeConnectivityTestResponse:
    return RuntimeConnectivityTestResponse(
        kind=kind,
        ok=False,
        preflight_ok=False,
        checked_at=checked_at,
        checks=checks,
        guidance=guidance,
        error=error,
    )


async def run_modal_connection_test(
    *,
    state: ServerState,
    load_modal_config: LoadModalConfig,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = modal_preflight(
        secret_name=state.config.secret_name,
        load_modal_config=load_modal_config,
    )
    checked_at = utc_now_iso()
    if not (checks["credentials_available"] and checks["secret_name_set"]):
        result = preflight_failure_result(
            kind="modal",
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error="Modal preflight checks failed.",
        )
        cache_runtime_test(state=state, result=result)
        return result

    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None
    ok = False

    sandbox = None
    started = time.perf_counter()
    try:
        import modal

        app = await modal.App.lookup.aio(
            "fleet-rlm-runtime-smoke", create_if_missing=True
        )
        sandbox = await modal.Sandbox.create.aio(app=app, timeout=30)
        proc = await sandbox.exec.aio("python", "-c", "print('ok')", timeout=15)
        await proc.wait.aio()
        output_preview = coerce_output_text(await proc.stdout.read.aio())
        ok = output_preview == "ok"
        if not ok:
            error = "Modal sandbox returned unexpected output."
    except Exception as exc:  # pragma: no cover - network/provider path
        error = sanitize_error(exc)
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if sandbox is not None:
            with suppress(Exception):
                await sandbox.terminate.aio()

    if not ok and not error:
        error = "Modal connectivity test failed."

    result = RuntimeConnectivityTestResponse(
        kind="modal",
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


async def run_lm_connection_test(
    *,
    state: ServerState,
    planner_lm_factory: PlannerFactory,
    delegate_lm_factory: DelegateFactory,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = lm_preflight()
    checked_at = utc_now_iso()
    if not (checks["model_set"] and checks["api_key_set"]):
        result = preflight_failure_result(
            kind="lm",
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error="LM preflight checks failed.",
        )
        cache_runtime_test(state=state, result=result)
        return result

    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None
    ok = False

    started = time.perf_counter()
    try:
        planner_lm = planner_lm_factory(
            env_file=state.config.env_path,
            model_name=state.config.agent_model,
        )
        if planner_lm is None:
            raise RuntimeError(
                "Failed to construct planner LM from environment settings."
            )

        def _invoke() -> str:
            response = planner_lm("Reply with exactly OK")
            return extract_lm_text(response)

        output_preview = await run_blocking(
            _invoke,
            timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
        )

        ok = bool(output_preview)
        state.planner_lm = planner_lm
        state.delegate_lm = delegate_lm_factory(
            env_file=state.config.env_path,
            model_name=state.config.agent_delegate_model,
            default_max_tokens=state.config.agent_delegate_max_tokens,
        )
    except asyncio.TimeoutError:
        error = (
            f"LM test timed out after {RUNTIME_TEST_TIMEOUT_SECONDS}s. "
            "Check API connectivity and credentials."
        )
    except Exception as exc:  # pragma: no cover - provider/network path
        error = sanitize_error(exc)
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)

    if not ok and not error:
        error = "LM connectivity test failed."

    result = RuntimeConnectivityTestResponse(
        kind="lm",
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


async def run_daytona_connection_test(
    *,
    state: ServerState,
    resolve_daytona_config: ResolveDaytonaConfig,
    build_daytona_client_factory: BuildDaytonaClient,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = daytona_preflight(
        sandbox_provider=state.config.sandbox_provider,
        resolve_daytona_config=resolve_daytona_config,
    )
    checked_at = utc_now_iso()
    if not checks["configured"]:
        result = preflight_failure_result(
            kind="daytona",
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error="Daytona preflight checks failed.",
        )
        cache_runtime_test(state=state, result=result)
        return result

    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None
    ok = False

    started = time.perf_counter()
    try:
        resolve_daytona_config()
        client = build_daytona_client_factory()

        def _fetch() -> Any:
            return client.list(limit=1)

        response = await run_blocking(
            _fetch,
            timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
        )
        items = getattr(response, "items", [])
        output_preview = (
            f"Daytona connectivity verified. Found {len(items)} sandboxes (limited)."
        )
        ok = True
    except ImportError:
        error = "Daytona SDK is not installed."
    except Exception as exc:  # pragma: no cover - provider/network path
        error = sanitize_error(exc)
    finally:
        latency_ms = int((time.perf_counter() - started) * 1000)

    if not ok and not error:
        error = "Daytona connectivity test failed."

    result = RuntimeConnectivityTestResponse(
        kind="daytona",
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


def build_runtime_status_response(
    *,
    state: ServerState,
    load_modal_config: LoadModalConfig,
    resolve_daytona_config: ResolveDaytonaConfig,
) -> RuntimeStatusResponse:
    llm_checks, llm_guidance = lm_preflight()
    modal_checks, modal_guidance = modal_preflight(
        secret_name=state.config.secret_name,
        load_modal_config=load_modal_config,
    )
    daytona_checks, daytona_guidance = daytona_preflight(
        sandbox_provider=state.config.sandbox_provider,
        resolve_daytona_config=resolve_daytona_config,
    )

    modal_test = connectivity_result_from_cache(state=state, kind="modal")
    lm_test = connectivity_result_from_cache(state=state, kind="lm")
    daytona_test = connectivity_result_from_cache(state=state, kind="daytona")

    ready = bool(modal_test and modal_test.ok and lm_test and lm_test.ok)

    guidance: list[str] = []
    guidance.extend(llm_guidance)
    guidance.extend(modal_guidance)
    if modal_test is None or lm_test is None:
        guidance.append(
            "Run Runtime connection tests to validate live provider connectivity."
        )

    return RuntimeStatusResponse(
        app_env=state.config.app_env,
        write_enabled=state.config.app_env == "local",
        ready=ready,
        sandbox_provider=state.config.sandbox_provider,
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
