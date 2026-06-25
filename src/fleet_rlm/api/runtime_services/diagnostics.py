"""Runtime diagnostics, connectivity tests, and status assembly."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

from pydantic import ValidationError

from fleet_rlm.integrations.daytona import DaytonaConfigError
from fleet_rlm.integrations.daytona.concurrency import (
    SandboxUsageStats,
    get_current_sandbox_usage,
    reconcile_sandbox_slots,
)
from fleet_rlm.integrations.llm_profiles.model_catalog import (
    MODELS_ENDPOINT_PROVIDER_TYPES,
    validate_profile_via_models_catalog,
)
from fleet_rlm.integrations.llm_profiles.types import (
    LlmProviderProfileRecord,
    LlmRoleName,
    ResolvedRoleLmConfig,
)
from fleet_rlm.integrations.observability.config import MlflowConfig

from ..bootstrap_observability import resolve_mlflow_auto_start_enabled
from ..dependencies import (
    AuthDeps,
    ConfigDeps,
    DiagnosticsDeps,
    LmDeps,
    PersistenceDeps,
    ServerState,
    SessionCacheDeps,
)
from ..schemas.runtime import (
    RuntimeActiveModels,
    RuntimeConnectivityTestResponse,
    RuntimeMlflowStatus,
    RuntimeStatusResponse,
    RuntimeTestCache,
)
from .common import (
    LM_SMOKE_TEST_TIMEOUT_SECONDS,
    RUNTIME_TEST_TIMEOUT_SECONDS,
    extract_lm_text,
    redact_secret,
    run_blocking,
    sanitize_error,
    utc_now_iso,
)

logger = logging.getLogger(__name__)


def resolve_active_model(value: str | None, env_key: str) -> str:
    direct = (value or "").strip()
    if direct:
        return direct
    fallback = (os.environ.get(env_key) or "").strip()
    return fallback


def cache_runtime_test(*, diagnostics: DiagnosticsDeps, result: RuntimeConnectivityTestResponse) -> None:
    diagnostics.runtime_test_results[result.kind] = result.model_dump(mode="json")


def connectivity_result_from_cache(
    *, diagnostics: DiagnosticsDeps, kind: str
) -> RuntimeConnectivityTestResponse | None:
    cached = diagnostics.runtime_test_results.get(kind)
    if isinstance(cached, RuntimeConnectivityTestResponse):
        return cached
    if not isinstance(cached, dict):
        return None
    try:
        return RuntimeConnectivityTestResponse(**cached)
    except ValidationError:
        return None


def _status_sandbox_usage() -> SandboxUsageStats:
    """Return sandbox slot diagnostics, reconciling obviously stale saturation.

    Normal slot release remains lifecycle-driven. This diagnostic recovery path
    only runs when the local semaphore reports at least one active slot; in
    that case a provider count is cheap and prevents the Settings/Workbench UI
    from showing stale local occupancy after Daytona sandboxes have already
    disappeared.
    """
    usage = get_current_sandbox_usage()
    if usage.active_count <= 0:
        return usage

    runtime = None
    try:
        from fleet_rlm.integrations.daytona.runtime import DaytonaSandboxRuntime

        runtime = DaytonaSandboxRuntime()
        provider_active = runtime._count_provider_fleet_sandboxes_sync()
    except Exception:
        logger.debug("Skipping sandbox slot reconciliation during runtime status", exc_info=True)
        return usage
    finally:
        if runtime is not None:
            with suppress(Exception):
                runtime.close()

    if provider_active < usage.active_count:
        return reconcile_sandbox_slots(provider_active_count=provider_active)
    return usage


def lm_preflight() -> tuple[dict[str, bool], list[str]]:
    has_model = bool((os.environ.get("DSPY_LM_MODEL") or "").strip())
    has_api_key = bool((os.environ.get("DSPY_LLM_API_KEY") or os.environ.get("DSPY_LM_API_KEY") or "").strip())
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

    checks = {
        "api_key_set": bool(api_key),
        "api_url_set": bool(api_url),
        "target_set": bool(target),
        "configured": False,
    }

    guidance: list[str] = []
    try:
        from fleet_rlm.integrations.daytona import resolve_daytona_config

        resolve_daytona_config()
        checks["configured"] = True
    except DaytonaConfigError as exc:
        guidance.append(str(exc))

    deduped_guidance: list[str] = []
    for item in guidance:
        if item not in deduped_guidance:
            deduped_guidance.append(item)

    return checks, deduped_guidance


def build_runtime_test_result(
    *,
    kind: Literal["lm", "daytona"],
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
    kind: Literal["lm", "daytona"],
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


async def _ensure_runtime_models(
    lm_deps: LmDeps, config_deps: ConfigDeps, diagnostics_deps: DiagnosticsDeps
) -> tuple[Any | None, Any | None]:
    from ..bootstrap import ensure_runtime_models

    return await ensure_runtime_models(lm_deps, config_deps, diagnostics_deps)


async def run_connectivity_test(
    *,
    diagnostics: DiagnosticsDeps,
    kind: Literal["lm", "daytona"],
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
        cache_runtime_test(diagnostics=diagnostics, result=result)
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
    cache_runtime_test(diagnostics=diagnostics, result=result)
    return result


async def _resolve_byok_planner(
    config_deps: ConfigDeps,
    persistence_deps: PersistenceDeps | None,
    persisted_identity: IdentityUpsertResult | None,
) -> tuple[LlmProviderProfileRecord | None, ResolvedRoleLmConfig | None, str | None]:
    """Resolve the caller's BYOK planner profile + config (the one chat uses).

    Returns ``(profile, config, error)``:
    - ``(None, None, None)`` — BYOK does not apply (non-neon mode, no persistence,
      no identity); the caller falls back to env-based loading.
    - ``(None, None, message)`` — neon mode is engaged but no planner BYOK profile
      is bound (or resolution failed); the caller surfaces the error instead of
      testing an irrelevant server-env LM.
    - ``(profile, config, None)`` — resolved; ``profile`` is the
      ``LlmProviderProfileRecord`` (for /models validation), ``config`` is the
      ``ResolvedRoleLmConfig`` (for the chat-completion path).
    """
    cfg = config_deps.config
    if (
        cfg.auth_mode != "neon"
        or persistence_deps is None
        or persistence_deps.db_manager is None
        or persistence_deps.repository is None
        or persisted_identity is None
        or getattr(persisted_identity, "user_id", None) is None
    ):
        return None, None, None

    from fleet_rlm.integrations.llm_profiles.resolver import resolve_role_config
    from fleet_rlm.integrations.llm_profiles.store import resolve_profile_store

    try:
        store = resolve_profile_store(persistence_deps.db_manager, identity=persisted_identity)
        bundle = await store.load_bundle()
        profiles = {p.id: p for p in bundle.profiles}
        planner_binding = next(
            (b for b in bundle.role_bindings if b.role == "planner" and b.profile_id),
            None,
        )
        if planner_binding is None or planner_binding.profile_id not in profiles:
            return (
                None,
                None,
                (
                    "No planner BYOK profile is configured for this user. "
                    "Bind a provider profile to the planner role in Settings."
                ),
            )
        profile = profiles[planner_binding.profile_id]
        config = resolve_role_config(role="planner", binding=planner_binding, profile=profile)
        if config is None or not config.litellm_model or not config.api_key:
            return None, None, "Planner BYOK profile is missing credentials."
        return profile, config, None
    except Exception as exc:
        return None, None, sanitize_error(exc)


async def run_lm_connection_test(
    *,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    diagnostics_deps: DiagnosticsDeps,
    planner_loader=None,
    delegate_loader=None,
    persistence_deps: PersistenceDeps | None = None,
    persisted_identity: IdentityUpsertResult | None = None,
) -> RuntimeConnectivityTestResponse:
    byok_profile, byok_config, byok_error = await _resolve_byok_planner(
        config_deps, persistence_deps, persisted_identity
    )
    byok_engaged = byok_profile is not None or byok_error is not None

    if byok_engaged:
        # In BYOK mode the caller's profile (not server env vars) is the source of
        # truth; satisfy preflight and let the smoke test surface the BYOK profile
        # (via /models) or a clear "not configured" error instead of a hang on the
        # server-env LM.
        has_byok = byok_profile is not None
        checks: dict[str, bool] = {
            "api_key_set": bool(getattr(byok_config, "api_key", None)),
            "api_base_set": bool(getattr(byok_profile, "api_base", None)),
        }
        guidance: list[str] = [] if has_byok else [byok_error or "No planner BYOK profile is configured for this user."]
        preflight_ok = True
    else:
        checks, guidance = lm_preflight()
        preflight_ok = checks["model_set"] and checks["api_key_set"]

    async def _run_smoke() -> tuple[bool, str | None, str | None]:
        # BYOK /models-compatible profiles validate via GET /models (no chat completion,
        # no cold start, no token spend) — same probe as the per-profile Test.
        if byok_profile is not None and byok_profile.provider_type in MODELS_ENDPOINT_PROVIDER_TYPES:
            checks["models_found"] = False
            ok, preview, err = await validate_profile_via_models_catalog(byok_profile)
            checks["models_found"] = ok
            return ok, preview, redact_secret(err, getattr(byok_config, "api_key", None))

        if byok_profile is not None:
            # BYOK non-/models provider (e.g. real Anthropic): chat-completion smoke test
            # against the profile's resolved config.
            if byok_config is None:
                raise RuntimeError("BYOK planner profile resolved without a config.")
            import dspy

            from fleet_rlm.integrations.llm_profiles.resolver import build_lm_kwargs_from_resolved

            planner_lm = await run_blocking(
                lambda: dspy.LM(
                    **build_lm_kwargs_from_resolved(byok_config, timeout=LM_SMOKE_TEST_TIMEOUT_SECONDS - 2)
                ),
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )
        elif byok_error:
            raise RuntimeError(byok_error)
        elif planner_loader is None and delegate_loader is None:
            planner_lm, _delegate_lm = await _ensure_runtime_models(lm_deps, config_deps, diagnostics_deps)
            if planner_lm is None:
                raise RuntimeError("Failed to construct planner LM from environment settings.")
        else:
            if planner_loader is None:
                raise ValueError(
                    "delegate_loader was provided without planner_loader; "
                    "both must be supplied when overriding LM loaders."
                )
            planner_lm = await run_blocking(
                partial(
                    planner_loader,
                    env_file=config_deps.config.env_path,
                    model_name=config_deps.config.agent_model,
                ),
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )
            if planner_lm is None:
                raise RuntimeError("Failed to construct planner LM from environment settings.")
            # `delegate_loader` is intentionally not invoked here: this smoke
            # test only validates the planner LM. Constructing a delegate LM just
            # to swap it into the `lm_deps` singleton was the old (removed)
            # behavior — see the singleton-mutation note below.

        def _invoke() -> str:
            response = planner_lm("Reply with exactly OK")
            return extract_lm_text(response)

        output_preview = await run_blocking(
            _invoke,
            timeout=LM_SMOKE_TEST_TIMEOUT_SECONDS,
        )

        # NOTE: do NOT write `planner_lm` back to the process-wide `lm_deps`
        # singleton. In BYOK mode it is a per-user LM carrying the caller's API
        # key; mutating the singleton would leak User A's BYOK credentials into
        # any concurrent chat reading `lm_deps.planner_lm`. The smoke test above
        # uses the local `planner_lm` directly.
        return bool(output_preview), output_preview, None

    return await run_connectivity_test(
        diagnostics=diagnostics_deps,
        kind="lm",
        preflight_ok=preflight_ok,
        checks=checks,
        guidance=guidance,
        preflight_error="LM preflight checks failed.",
        default_error="LM connectivity test failed.",
        timeout_error=(
            f"LM test timed out after {LM_SMOKE_TEST_TIMEOUT_SECONDS}s. Check API connectivity and credentials."
        ),
        run_smoke=_run_smoke,
    )


async def run_daytona_connection_test(
    *,
    config_deps: ConfigDeps,
    diagnostics_deps: DiagnosticsDeps,
) -> RuntimeConnectivityTestResponse:
    checks, guidance = daytona_preflight(
        sandbox_provider=config_deps.config.sandbox_provider,
    )

    async def _run_smoke() -> tuple[bool, str | None, str | None]:
        client = None
        try:
            from fleet_rlm.integrations.daytona import resolve_daytona_config
            from fleet_rlm.integrations.daytona.config import build_daytona_client

            config = resolve_daytona_config()
            client = build_daytona_client(config)

            response = await asyncio.wait_for(
                asyncio.to_thread(lambda: client.list(limit=1)),
                timeout=RUNTIME_TEST_TIMEOUT_SECONDS,
            )
            items = getattr(response, "items", [])
            checks["cleanup_confirmed"] = True
            checks["probe_created_sandbox"] = False
            output_preview = f"Daytona connectivity verified. Found {len(items)} sandboxes (limited)."
            return True, output_preview, None
        except ImportError:
            return False, None, "Daytona SDK is not installed."
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    with suppress(Exception):
                        await asyncio.to_thread(close)

    return await run_connectivity_test(
        diagnostics=diagnostics_deps,
        kind="daytona",
        preflight_ok=checks["configured"],
        checks=checks,
        guidance=guidance,
        preflight_error="Daytona preflight checks failed.",
        default_error="Daytona connectivity test failed.",
        timeout_error=(
            f"Daytona test timed out after {RUNTIME_TEST_TIMEOUT_SECONDS}s. Check connectivity and credentials."
        ),
        run_smoke=_run_smoke,
    )


def build_runtime_status_response(
    *,
    config_deps: ConfigDeps,
    lm_deps: LmDeps,
    diagnostics_deps: DiagnosticsDeps,
    persistence_deps: PersistenceDeps | None = None,
    profile_labels: dict[LlmRoleName, tuple[str | None, str | None]] | None = None,
) -> RuntimeStatusResponse:
    mlflow_cfg = MlflowConfig.from_env()
    llm_checks, llm_guidance = lm_preflight()
    daytona_checks, daytona_guidance = daytona_preflight(
        sandbox_provider=config_deps.config.sandbox_provider,
    )
    sandbox_usage = _status_sandbox_usage()

    lm_test = connectivity_result_from_cache(diagnostics=diagnostics_deps, kind="lm")
    daytona_test = connectivity_result_from_cache(diagnostics=diagnostics_deps, kind="daytona")

    state = ServerState.__new__(ServerState)
    state.config_deps = config_deps
    state.lm_deps = lm_deps
    state.auth_deps = AuthDeps()
    state.session_cache_deps = SessionCacheDeps()
    state.persistence_deps = persistence_deps or PersistenceDeps()
    state.diagnostics_deps = diagnostics_deps

    ready = state.is_ready and bool(daytona_test is not None and daytona_test.ok and lm_test is not None and lm_test.ok)
    mlflow_startup_status = diagnostics_deps.optional_service_status.get("mlflow", "pending")
    mlflow_startup_error = diagnostics_deps.optional_service_errors.get("mlflow")

    guidance: list[str] = []
    guidance.extend(llm_guidance)
    guidance.extend(daytona_guidance)
    if daytona_test is None or lm_test is None:
        guidance.append("Run Runtime connection tests to validate live provider connectivity.")
    for runtime_test in (lm_test, daytona_test):
        if runtime_test is None or runtime_test.ok:
            continue
        if runtime_test.error:
            guidance.append(runtime_test.error)
        guidance.extend(runtime_test.guidance)
    if mlflow_cfg.enabled and mlflow_startup_status == "degraded":
        guidance.append(
            "MLflow startup is degraded. Verify MLFLOW_TRACKING_URI reachability/auth, "
            "set MLFLOW_AUTO_START=false to keep MLflow manual in local dev, or set "
            "MLFLOW_ENABLED=false for this environment."
        )

    deduped_guidance: list[str] = []
    for item in guidance:
        normalized = item.strip()
        if normalized and normalized not in deduped_guidance:
            deduped_guidance.append(normalized)

    mlflow_auto_start_enabled = resolve_mlflow_auto_start_enabled(
        app_env=config_deps.config.app_env,
        mlflow_enabled=mlflow_cfg.enabled,
        tracking_uri=mlflow_cfg.tracking_uri,
    )
    persisted_scorer_names: list[str] = []
    if mlflow_cfg.enabled and not mlflow_cfg.enable_auto_assessment:
        try:
            from fleet_rlm.integrations.observability.auto_assessment import persisted_scorer_names as _scorer_names

            persisted_scorer_names = _scorer_names(mlflow_cfg)
        except Exception:
            logger.debug("Failed to inspect MLflow persisted scorers for runtime status.", exc_info=True)
            persisted_scorer_names = []
    if persisted_scorer_names:
        deduped_guidance.append(
            "MLflow has persisted scorer(s) while Fleet auto-assessment is disabled: "
            f"{', '.join(persisted_scorer_names)}. These can still assess traces; inspect with "
            "`uv run python scripts/mlflow_cli.py scorers list` and stop with "
            "`uv run python scripts/mlflow_cli.py scorers stop --name <name>` if unintended."
        )

    mlflow_experiment_id: str | None = None
    if mlflow_cfg.enabled and mlflow_startup_status == "ready":
        from fleet_rlm.integrations.observability.mlflow_runtime import get_mlflow_experiment_id

        mlflow_experiment_id = get_mlflow_experiment_id()

    resolved_profile_labels = profile_labels or {}
    planner_profile = resolved_profile_labels.get("planner", (None, None))
    delegate_profile = resolved_profile_labels.get("delegate", (None, None))
    delegate_small_profile = resolved_profile_labels.get("delegate_small", (None, None))

    settings_write_enabled = config_deps.config.app_env == "local"
    return RuntimeStatusResponse(
        app_env=config_deps.config.app_env,
        write_enabled=settings_write_enabled,
        settings_write_enabled=settings_write_enabled,
        profile_write_enabled=settings_write_enabled or config_deps.config.auth_mode == "neon",
        ready=ready,
        sandbox_provider="daytona",
        active_models=RuntimeActiveModels(
            planner=resolve_active_model(config_deps.config.agent_model, "DSPY_LM_MODEL"),
            delegate=resolve_active_model(
                config_deps.config.agent_delegate_model,
                "DSPY_DELEGATE_LM_MODEL",
            ),
            delegate_small=resolve_active_model(
                config_deps.config.agent_delegate_small_model,
                "DSPY_DELEGATE_LM_SMALL_MODEL",
            ),
            planner_profile_id=planner_profile[0],
            planner_profile_name=planner_profile[1],
            delegate_profile_id=delegate_profile[0],
            delegate_profile_name=delegate_profile[1],
            delegate_small_profile_id=delegate_small_profile[0],
            delegate_small_profile_name=delegate_small_profile[1],
        ),
        llm={
            **llm_checks,
            "planner_configured": lm_deps.planner_lm is not None,
            "startup_status": diagnostics_deps.optional_service_status.get("planner_lm", "pending"),
            "startup_error": diagnostics_deps.optional_service_errors.get("planner_lm"),
        },
        mlflow=RuntimeMlflowStatus(
            enabled=mlflow_cfg.enabled,
            tracking_uri=mlflow_cfg.tracking_uri,
            experiment_name=mlflow_cfg.experiment,
            experiment_id=mlflow_experiment_id,
            auto_start_enabled=mlflow_auto_start_enabled,
            auto_assessment_enabled=mlflow_cfg.enable_auto_assessment,
            persisted_scorer_count=len(persisted_scorer_names),
            persisted_scorers=persisted_scorer_names,
            startup_status=mlflow_startup_status,
            startup_error=mlflow_startup_error,
        ),
        daytona={
            **daytona_checks,
            "guidance": daytona_guidance,
            "sandbox_slots": sandbox_usage.model_dump(),
        },
        tests=RuntimeTestCache(lm=lm_test, daytona=daytona_test),
        guidance=deduped_guidance,
    )
