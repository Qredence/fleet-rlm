"""Route composition tests.

Verify that the central API router composition registers every canonical
route group at the expected paths and that health routes stay top-level.
"""

from __future__ import annotations

import pytest

from fleet_rlm.api.main import create_app
from fleet_rlm.api.routers._composition import build_api_router


@pytest.fixture()
def app():
    """Create a minimal app instance for route introspection."""
    return create_app()


def _route_paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


# --- Health routes stay top-level ---


class TestHealthRoutesTopLevel:
    def test_health_exists_top_level(self, app):
        assert "/health" in _route_paths(app)

    def test_ready_exists_top_level(self, app):
        assert "/ready" in _route_paths(app)

    def test_health_not_under_api_v1(self, app):
        paths = _route_paths(app)
        assert "/api/v1/health" not in paths
        assert "/api/v1/ready" not in paths


# --- All canonical router groups registered ---


_EXPECTED_ROUTE_SAMPLES: dict[str, str] = {
    "auth": "/api/v1/auth/me",
    "ws_execution": "/api/v1/ws/execution",
    "ws_events": "/api/v1/ws/execution/events",
    "sessions": "/api/v1/sessions",
    "runtime_status": "/api/v1/runtime/status",
    "sandboxes": "/api/v1/sandboxes",
    "runs": "/api/v1/runs",
    "memory": "/api/v1/memory",
    "optimization": "/api/v1/optimization",
    "traces_feedback": "/api/v1/traces/feedback",
}


class TestCanonicalRouterGroups:
    @pytest.mark.parametrize(
        "label,expected_path",
        list(_EXPECTED_ROUTE_SAMPLES.items()),
        ids=list(_EXPECTED_ROUTE_SAMPLES.keys()),
    )
    def test_route_exists(self, app, label, expected_path):
        paths = _route_paths(app)
        # Some paths may include dynamic segments; check prefix match
        match = expected_path in paths or any(
            p.startswith(expected_path) for p in paths
        )
        assert match, (
            f"Expected route '{expected_path}' ({label}) not found. "
            f"Available /api/v1 paths: {sorted(p for p in paths if '/api/v1' in p)}"
        )


# --- build_api_router contract ---


class TestBuildApiRouter:
    def test_prefix(self):
        router = build_api_router()
        assert router.prefix == "/api/v1"

    def test_has_routes(self):
        router = build_api_router()
        assert len(router.routes) > 0, "API router should contain registered routes"

    def test_returns_fresh_instance(self):
        r1 = build_api_router()
        r2 = build_api_router()
        assert r1 is not r2, "Factory should return a new router each call"


# --- OpenAPI path-key regression ---


class TestOpenAPIPathKeys:
    def test_canonical_paths_present(self, app):
        schema = app.openapi()
        paths = set(schema.get("paths", {}).keys())

        must_have = {
            "/health",
            "/ready",
            "/api/v1/auth/me",
            "/api/v1/runtime/status",
            "/api/v1/traces/feedback",
        }
        missing = must_have - paths
        assert not missing, f"OpenAPI spec missing expected paths: {sorted(missing)}"

    def test_health_not_duplicated_under_api_v1(self, app):
        schema = app.openapi()
        paths = set(schema.get("paths", {}).keys())
        assert "/api/v1/health" not in paths
        assert "/api/v1/ready" not in paths
