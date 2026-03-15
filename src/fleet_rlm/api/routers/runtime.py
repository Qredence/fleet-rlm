"""Runtime settings and connectivity diagnostics endpoints."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from fleet_rlm.core.config import get_delegate_lm_from_env, get_planner_lm_from_env
from fleet_rlm.infrastructure.config.runtime_settings import (
    RUNTIME_SETTINGS_ALLOWLIST,
    RUNTIME_SETTINGS_KEYS,
    apply_env_updates,
    get_settings_snapshot,
    normalize_updates,
)
from fleet_rlm.infrastructure.providers.daytona import (
    DaytonaConfigError,
    resolve_daytona_config,
)
from fleet_rlm.utils.modal import load_modal_config

from ..dependencies import ServerStateDep, require_http_identity
from ..schemas.core import (
    RuntimeActiveModels,
    RuntimeConnectivityTestResponse,
    RuntimeSettingsSnapshot,
    RuntimeSettingsUpdateRequest,
    RuntimeSettingsUpdateResponse,
    RuntimeStatusResponse,
    RuntimeTestCache,
    VolumeFileContentResponse,
    VolumeTreeResponse,
)

router = APIRouter(
    prefix="/runtime",
    tags=["runtime"],
    dependencies=[Depends(require_http_identity)],
)

_RUNTIME_TEST_TIMEOUT_SECONDS = 20


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _json_model_response(payload: Any) -> JSONResponse:
    """Serialize runtime API payloads eagerly before returning to FastAPI.

    In the live server, several runtime endpoints were reaching access-log
    `200 OK` but hanging before clients received response headers. Eagerly
    materializing the JSON body here keeps the runtime/settings UI responsive
    while preserving the existing response models and OpenAPI contract.
    """
    if hasattr(payload, "model_dump"):
        content = payload.model_dump(mode="json")
    else:
        content = payload
    return JSONResponse(content=content)


def _runtime_setting_overrides(
    *, secret_name: str, volume_name: str | None
) -> dict[str, str]:
    return {
        "SECRET_NAME": secret_name,
        "VOLUME_NAME": volume_name or "",
    }


def _resolve_active_model(value: str | None, env_key: str) -> str:
    direct = (value or "").strip()
    if direct:
        return direct
    fallback = (os.environ.get(env_key) or "").strip()
    return fallback


def _sanitize_error(exc: Exception) -> str:
    message = str(exc)
    sensitive_values = [
        os.environ.get("DSPY_LLM_API_KEY"),
        os.environ.get("DSPY_LM_API_KEY"),
        os.environ.get("MODAL_TOKEN_SECRET"),
        os.environ.get("MODAL_TOKEN_ID"),
    ]

    for value in sensitive_values:
        if value and len(value) >= 4:
            message = message.replace(value, "***")

    return message


def _extract_lm_text(response: Any) -> str:
    if isinstance(response, list) and response:
        first = response[0]
        if isinstance(first, dict) and "text" in first:
            return str(first["text"]).strip()
        return str(first).strip()
    return str(response).strip()


def _coerce_output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _cache_runtime_test(*, state, result: RuntimeConnectivityTestResponse) -> None:
    state.runtime_test_results[result.kind] = result.model_dump(mode="json")


def _modal_preflight(*, secret_name: str) -> tuple[dict[str, bool], list[str]]:
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


def _lm_preflight() -> tuple[dict[str, bool], list[str]]:
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


def _daytona_preflight() -> tuple[dict[str, bool], list[str]]:
    api_key = (os.environ.get("DAYTONA_API_KEY") or "").strip()
    api_url = (os.environ.get("DAYTONA_API_URL") or "").strip()
    target = (os.environ.get("DAYTONA_TARGET") or "").strip()
    legacy_api_base = (os.environ.get("DAYTONA_API_BASE_URL") or "").strip()

    checks = {
        "api_key_set": bool(api_key),
        "api_url_set": bool(api_url),
        "target_set": bool(target),
        "legacy_api_base_url_set": bool(legacy_api_base),
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


@router.get("/settings", response_model=RuntimeSettingsSnapshot)
async def get_runtime_settings(state: ServerStateDep) -> JSONResponse:
    snapshot = get_settings_snapshot(
        keys=list(RUNTIME_SETTINGS_KEYS),
        extra_values=_runtime_setting_overrides(
            secret_name=state.config.secret_name,
            volume_name=state.config.volume_name,
        ),
        env_path=state.config.env_path,
    )
    return _json_model_response(RuntimeSettingsSnapshot(**snapshot))


@router.patch("/settings", response_model=RuntimeSettingsUpdateResponse)
async def patch_runtime_settings(
    state: ServerStateDep,
    request: RuntimeSettingsUpdateRequest,
) -> JSONResponse:
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

    # Rebuild planner LM in-process to apply updated env values immediately.
    state.planner_lm = get_planner_lm_from_env(
        env_file=config.env_path,
        model_name=config.agent_model,
    )
    state.delegate_lm = get_delegate_lm_from_env(
        env_file=config.env_path,
        model_name=config.agent_delegate_model,
        default_max_tokens=config.agent_delegate_max_tokens,
    )

    return _json_model_response(RuntimeSettingsUpdateResponse(**result))


@router.post("/tests/modal", response_model=RuntimeConnectivityTestResponse)
async def test_modal_connection(
    state: ServerStateDep,
) -> JSONResponse:
    checks, guidance = _modal_preflight(secret_name=state.config.secret_name)
    preflight_ok = checks["credentials_available"] and checks["secret_name_set"]

    checked_at = _utc_now_iso()
    if not preflight_ok:
        result = RuntimeConnectivityTestResponse(
            kind="modal",
            ok=False,
            preflight_ok=False,
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error="Modal preflight checks failed.",
        )
        _cache_runtime_test(state=state, result=result)
        return _json_model_response(result)

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
        output_preview = _coerce_output_text(await proc.stdout.read.aio())
        ok = output_preview == "ok"
        if not ok:
            error = "Modal sandbox returned unexpected output."
    except Exception as exc:  # pragma: no cover - network/provider path
        error = _sanitize_error(exc)
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
    _cache_runtime_test(state=state, result=result)
    return _json_model_response(result)


@router.post("/tests/lm", response_model=RuntimeConnectivityTestResponse)
async def test_lm_connection(state: ServerStateDep) -> JSONResponse:
    checks, guidance = _lm_preflight()
    preflight_ok = checks["model_set"] and checks["api_key_set"]

    checked_at = _utc_now_iso()
    if not preflight_ok:
        result = RuntimeConnectivityTestResponse(
            kind="lm",
            ok=False,
            preflight_ok=False,
            checked_at=checked_at,
            checks=checks,
            guidance=guidance,
            error="LM preflight checks failed.",
        )
        _cache_runtime_test(state=state, result=result)
        return _json_model_response(result)

    latency_ms: int | None = None
    output_preview: str | None = None
    error: str | None = None
    ok = False

    started = time.perf_counter()
    try:
        planner_lm = get_planner_lm_from_env(
            env_file=state.config.env_path,
            model_name=state.config.agent_model,
        )
        if planner_lm is None:
            raise RuntimeError(
                "Failed to construct planner LM from environment settings."
            )

        def _invoke() -> str:
            response = planner_lm("Reply with exactly OK")
            return _extract_lm_text(response)

        output_preview = await asyncio.wait_for(
            asyncio.to_thread(_invoke), timeout=_RUNTIME_TEST_TIMEOUT_SECONDS
        )

        ok = bool(output_preview)
        state.planner_lm = planner_lm
        state.delegate_lm = get_delegate_lm_from_env(
            env_file=state.config.env_path,
            model_name=state.config.agent_delegate_model,
            default_max_tokens=state.config.agent_delegate_max_tokens,
        )
    except asyncio.TimeoutError:
        error = (
            f"LM test timed out after {_RUNTIME_TEST_TIMEOUT_SECONDS}s. "
            "Check API connectivity and credentials."
        )
    except Exception as exc:  # pragma: no cover - provider/network path
        error = _sanitize_error(exc)
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
    _cache_runtime_test(state=state, result=result)
    return _json_model_response(result)


@router.get("/status", response_model=RuntimeStatusResponse)
async def get_runtime_status(state: ServerStateDep) -> JSONResponse:
    llm_checks, llm_guidance = _lm_preflight()
    modal_checks, modal_guidance = _modal_preflight(
        secret_name=state.config.secret_name
    )
    daytona_checks, daytona_guidance = _daytona_preflight()

    cached_modal = state.runtime_test_results.get("modal")
    cached_lm = state.runtime_test_results.get("lm")

    modal_test = (
        RuntimeConnectivityTestResponse(**cached_modal)
        if isinstance(cached_modal, dict)
        else None
    )
    lm_test = (
        RuntimeConnectivityTestResponse(**cached_lm)
        if isinstance(cached_lm, dict)
        else None
    )

    ready = bool(modal_test and modal_test.ok and lm_test and lm_test.ok)

    guidance: list[str] = []
    guidance.extend(llm_guidance)
    guidance.extend(modal_guidance)
    if modal_test is None or lm_test is None:
        guidance.append(
            "Run Runtime connection tests to validate live provider connectivity."
        )

    return _json_model_response(
        RuntimeStatusResponse(
            app_env=state.config.app_env,
            write_enabled=state.config.app_env == "local",
            ready=ready,
            active_models=RuntimeActiveModels(
                planner=_resolve_active_model(
                    state.config.agent_model, "DSPY_LM_MODEL"
                ),
                delegate=_resolve_active_model(
                    state.config.agent_delegate_model, "DSPY_DELEGATE_LM_MODEL"
                ),
                delegate_small=_resolve_active_model(
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
            tests=RuntimeTestCache(modal=modal_test, lm=lm_test),
            guidance=guidance,
        )
    )


@router.get("/volume/tree", response_model=VolumeTreeResponse)
async def get_volume_tree(
    state: ServerStateDep,
    root_path: Annotated[str, Query()] = "/",
    max_depth: Annotated[int, Query(ge=1, le=10)] = 3,
) -> JSONResponse:
    """List the file tree of the configured Modal Volume."""
    volume_name = state.config.volume_name
    if not volume_name:
        raise HTTPException(
            status_code=422,
            detail="No Modal Volume configured (interpreter.volume_name is unset).",
        )

    import asyncio

    from fleet_rlm.core.tools.volume_ops import list_volume_tree

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(list_volume_tree, volume_name, root_path, max_depth),
            timeout=30,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Volume listing timed out.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Volume listing failed: {exc}")

    return _json_model_response(VolumeTreeResponse(**result))


@router.get("/volume/file", response_model=VolumeFileContentResponse)
async def get_volume_file_content(
    state: ServerStateDep,
    path: Annotated[str, Query(min_length=1)],
    max_bytes: Annotated[int, Query(ge=1, le=1_000_000)] = 200_000,
) -> JSONResponse:
    """Read a volume file as UTF-8 text for frontend preview."""
    volume_name = state.config.volume_name
    if not volume_name:
        raise HTTPException(
            status_code=422,
            detail="No Modal Volume configured (interpreter.volume_name is unset).",
        )

    normalized_path = path if path.startswith("/") else f"/{path}"
    if ".." in PurePosixPath(normalized_path).parts:
        raise HTTPException(status_code=400, detail="Invalid file path.")

    import asyncio

    from fleet_rlm.core.tools.volume_ops import read_volume_file_text

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                read_volume_file_text,
                volume_name,
                normalized_path,
                max_bytes,
            ),
            timeout=30,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Volume file read timed out.")
    except Exception as exc:
        message = str(exc).lower()
        if "no such file" in message or "not found" in message:
            raise HTTPException(status_code=404, detail="File not found.")
        if "directory" in message:
            raise HTTPException(status_code=400, detail="Path must point to a file.")
        raise HTTPException(status_code=502, detail=f"Volume file read failed: {exc}")

    return _json_model_response(VolumeFileContentResponse(**result))
