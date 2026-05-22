from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fleet_rlm.api.auth.types import NormalizedIdentity
from fleet_rlm.api.config import ServerRuntimeConfig
from fleet_rlm.api.dependencies import (
    ConfigDeps,
    DiagnosticsDeps,
    LmDeps,
    PersistenceDeps,
    get_config_deps,
    get_diagnostics_deps,
    get_lm_deps,
    get_persistence,
    get_persistence_deps,
    require_http_identity,
)
from fleet_rlm.api.errors import add_exception_handlers
from fleet_rlm.api.routers import auth, health, info, runtime
from fleet_rlm.api.schemas.runtime import RuntimeConnectivityTestResponse


def _identity() -> NormalizedIdentity:
    return NormalizedIdentity(
        tenant_claim="tenant-contract",
        user_claim="user-contract",
        email="user@example.com",
        name="Contract User",
    )


def _config(**overrides: object) -> ConfigDeps:
    values: dict[str, object] = {
        "agent_model": None,
        "database_url": None,
        "auth_required": False,
        "serve_ui": False,
        "expose_root": False,
        **overrides,
    }
    return ConfigDeps(config=ServerRuntimeConfig(**values))


def _core_app(
    *,
    config_deps: ConfigDeps | None = None,
    lm_deps: LmDeps | None = None,
    diagnostics_deps: DiagnosticsDeps | None = None,
    persistence_deps: PersistenceDeps | None = None,
) -> FastAPI:
    app = FastAPI()
    add_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(info.router, prefix="/api/v1")
    app.include_router(runtime.router, prefix="/api/v1")

    resolved_config = config_deps or _config()
    app.dependency_overrides[get_config_deps] = lambda: resolved_config
    app.dependency_overrides[get_lm_deps] = lambda: lm_deps or LmDeps(planner_lm=object())
    app.dependency_overrides[get_diagnostics_deps] = lambda: diagnostics_deps or DiagnosticsDeps()
    app.dependency_overrides[get_persistence_deps] = lambda: persistence_deps or PersistenceDeps()
    app.dependency_overrides[get_persistence] = lambda: SimpleNamespace()
    app.dependency_overrides[require_http_identity] = _identity
    return app


def test_health_uses_canonical_liveness_schema() -> None:
    app = _core_app()

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "live"
    assert "version" in body
    assert "ok" not in body


def test_ready_returns_503_with_structured_diagnostics_when_critical_dependency_missing() -> None:
    app = _core_app(
        config_deps=_config(database_required=True),
        lm_deps=LmDeps(planner_lm=None),
        persistence_deps=PersistenceDeps(db_manager=None, repository=None, local_store=None),
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body == {
        "ready": False,
        "planner": "missing",
        "database": "missing",
        "database_required": True,
        "sandbox_provider": "daytona",
    }


def test_auth_me_returns_only_auth_derived_identity_claims_in_dev_mode() -> None:
    app = _core_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me?tenant_id=forged&user_id=forged")

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_claim"] == "tenant-contract"
    assert body["user_claim"] == "user-contract"
    assert body["email"] == "user@example.com"
    assert body["tenant_id"] is None
    assert body["user_id"] is None
    assert "forged" not in str(body)


def test_service_info_contract_is_non_secret_and_canonical() -> None:
    app = _core_app(
        config_deps=_config(
            app_env="local",
            auth_mode="dev",
            sandbox_provider="daytona",
            database_url="configured-database",
            agent_model="openai/gpt-4o-mini",
        )
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/info")

    assert response.status_code == 200
    body = response.json()
    assert body["app_env"] == "local"
    assert body["auth_mode"] == "dev"
    assert body["sandbox_provider"] == "daytona"
    assert body["database_enabled"] is True
    assert body["agent_model"] == "openai/gpt-4o-mini"
    serialized = response.text
    assert "configured-database" not in serialized
    assert "compat" not in serialized.lower()


def test_validation_errors_use_canonical_http_envelope() -> None:
    app = _core_app()

    with TestClient(app) as client:
        response = client.get("/api/v1/runtime/volume/tree?max_depth=0")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"code", "message", "detail"}
    assert body["code"] == "validation_error"
    assert body["message"] == "Request validation failed."
    assert isinstance(body["detail"], list)
    assert "traceback" not in response.text.lower()


def test_runtime_settings_writes_are_local_only_and_unknown_fields_use_error_envelope() -> None:
    production_app = _core_app(config_deps=_config(app_env="production"))
    local_app = _core_app(config_deps=_config(app_env="local"))

    with TestClient(production_app) as client:
        blocked = client.patch("/api/v1/runtime/settings", json={"updates": {"DSPY_LM_MODEL": "openai/gpt-4o-mini"}})

    with TestClient(local_app) as client:
        malformed = client.patch("/api/v1/runtime/settings", json={"updates": {"UNKNOWN_FIELD": "value"}})

    assert blocked.status_code == 403
    assert blocked.json()["code"] == "forbidden"
    assert blocked.json()["message"] == "Runtime settings updates are allowed only when APP_ENV=local."
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "bad_request"
    assert malformed.json()["message"].startswith("Unsupported settings key(s): UNKNOWN_FIELD.")


@pytest.mark.asyncio
async def test_lm_diagnostic_route_returns_live_structured_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_lm_test(**kwargs: object) -> RuntimeConnectivityTestResponse:
        return RuntimeConnectivityTestResponse(
            kind="lm",
            ok=True,
            preflight_ok=True,
            checked_at="2026-05-22T10:00:00Z",
            checks={"planner_live_check": True, "delegate_live_check": True},
            guidance=[],
            latency_ms=12,
            output_preview="OK",
        )

    monkeypatch.setattr(runtime, "run_lm_connection_test", _fake_lm_test)
    app = _core_app()

    with TestClient(app) as client:
        response = client.post("/api/v1/runtime/tests/lm")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "lm"
    assert body["ok"] is True
    assert body["checks"] == {"planner_live_check": True, "delegate_live_check": True}
    assert "api_key" not in response.text.lower()
