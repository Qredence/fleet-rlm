from __future__ import annotations

import importlib
from types import SimpleNamespace

from starlette.requests import Request


def _build_request(app) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
        }
    )


def test_session_key_uses_owner_fingerprint_and_default_session():
    dependencies_module = importlib.import_module("fleet_rlm.api.dependencies")
    identity_module = importlib.import_module("fleet_rlm.utils.identity")

    key = dependencies_module.session_key("tenant-a", "user-a")
    explicit_key = dependencies_module.session_key("tenant-a", "user-a", " session-1 ")

    owner_id = identity_module.owner_fingerprint("tenant-a", "user-a")
    assert key == f"owner:{owner_id}:__default__"
    assert explicit_key == f"owner:{owner_id}:session-1"


def test_compose_server_state_preserves_dependency_slices(clean_runtime_env):
    dependencies_module = importlib.import_module("fleet_rlm.api.dependencies")
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(database_required=False)
    config_deps = dependencies_module.ConfigDeps(config=cfg)
    lm_deps = dependencies_module.LmDeps(planner_lm=None, delegate_lm=None)
    auth_deps = dependencies_module.AuthDeps(auth_provider=object())  # ty: ignore[invalid-argument-type]
    session_cache_deps = dependencies_module.SessionCacheDeps(sessions={"owner:abc:__default__": {"history": []}})
    persistence_deps = dependencies_module.PersistenceDeps(local_store=object())
    diagnostics_deps = dependencies_module.DiagnosticsDeps()

    state = dependencies_module.compose_server_state(
        config_deps,
        lm_deps,
        auth_deps,
        session_cache_deps,
        persistence_deps,
        diagnostics_deps,
    )

    assert state.config_deps.config is cfg
    assert state.session_cache_deps.sessions == {"owner:abc:__default__": {"history": []}}
    assert state.auth_deps.auth_provider is auth_deps.auth_provider
    assert state.persistence_deps.local_store is persistence_deps.local_store
    assert state.is_ready is False

    state.lm_deps.planner_lm = object()
    assert state.is_ready is True


def test_dependency_getters_use_direct_state_slices_and_server_state_fallback(clean_runtime_env):
    dependencies_module = importlib.import_module("fleet_rlm.api.dependencies")
    config_module = importlib.import_module("fleet_rlm.api.config")

    cfg = config_module.AppConfig(database_required=False)
    config_deps = dependencies_module.ConfigDeps(config=cfg)
    lm_deps = dependencies_module.LmDeps(planner_lm=object())
    auth_deps = dependencies_module.AuthDeps(auth_provider=object())  # ty: ignore[invalid-argument-type]
    session_cache_deps = dependencies_module.SessionCacheDeps(sessions={})
    persistence_deps = dependencies_module.PersistenceDeps(local_store=object())
    diagnostics_deps = dependencies_module.DiagnosticsDeps()

    direct_app = SimpleNamespace(
        state=SimpleNamespace(
            config_deps=config_deps,
            lm_deps=lm_deps,
            auth_deps=auth_deps,
            session_cache_deps=session_cache_deps,
            persistence_deps=persistence_deps,
            diagnostics_deps=diagnostics_deps,
        )
    )
    direct_request = _build_request(direct_app)
    assert dependencies_module.get_config_deps(direct_request) is config_deps
    assert dependencies_module.get_lm_deps(direct_request) is lm_deps
    assert dependencies_module.get_auth_deps(direct_request) is auth_deps
    assert dependencies_module.get_session_cache_deps(direct_request) is session_cache_deps
    assert dependencies_module.get_persistence_deps(direct_request) is persistence_deps
    assert dependencies_module.get_diagnostics_deps(direct_request) is diagnostics_deps


def test_get_persistence_prefers_repository_then_local_store(clean_runtime_env):
    dependencies_module = importlib.import_module("fleet_rlm.api.dependencies")

    repository = object()
    local_store = object()
    app = SimpleNamespace(
        state=SimpleNamespace(
            persistence_deps=dependencies_module.PersistenceDeps(repository=repository, local_store=local_store)  # ty: ignore[invalid-argument-type]
        )
    )
    request = _build_request(app)
    assert dependencies_module.get_persistence(request) is repository

    app = SimpleNamespace(
        state=SimpleNamespace(persistence_deps=dependencies_module.PersistenceDeps(local_store=local_store))
    )
    request = _build_request(app)
    assert dependencies_module.get_persistence(request) is local_store
