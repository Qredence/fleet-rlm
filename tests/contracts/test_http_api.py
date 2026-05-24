from __future__ import annotations

from fastapi import FastAPI

CANONICAL_PATHS = {
    "/health",
    "/ready",
    "/api/v1/auth/me",
    "/api/v1/sessions/state",
    "/api/v1/traces/feedback",
    "/api/v1/ws/execution",
    "/api/v1/ws/execution/events",
}
RETIRED_PREFIXES = (
    "/api/v1/taxonomy",
    "/api/v1/skills",
    "/api/v1/memory",
    "/api/v1/analytics",
)


def _route_paths(app: FastAPI) -> set[str]:
    return {route.path for route in app.routes if hasattr(route, "path")}  # ty: ignore[invalid-return-type]


def test_canonical_http_routes_return_stable_status_codes(no_db_client, auth_headers: dict[str, str]) -> None:
    no_db_client.app.state.lm_deps.planner_lm = object()

    health = no_db_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "live"

    ready = no_db_client.get("/ready")
    assert ready.status_code == 200

    auth_me = no_db_client.get("/api/v1/auth/me", headers=auth_headers)
    assert auth_me.status_code in {200, 401}

    session_state = no_db_client.get("/api/v1/sessions/state", headers=auth_headers)
    assert session_state.status_code in {200, 500}


def test_route_tree_contains_canonical_paths_and_excludes_retired_routes(no_db_app) -> None:
    paths = _route_paths(no_db_app)

    assert CANONICAL_PATHS.issubset(paths)
    assert any(path.startswith("/api/v1/runtime/") for path in paths)

    for retired_prefix in RETIRED_PREFIXES:
        assert not any(path == retired_prefix or path.startswith(f"{retired_prefix}/") for path in paths)
