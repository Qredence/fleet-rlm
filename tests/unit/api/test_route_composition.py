from __future__ import annotations

import importlib


def test_build_api_router_includes_expected_route_tree():
    composition_module = importlib.import_module("fleet_rlm.api.routers._composition")

    router = composition_module.build_api_router()
    route_paths = {route.path for route in router.routes}  # ty: ignore[unresolved-attribute]

    assert router.prefix == "/api/v1"
    assert {
        "/api/v1/auth/me",
        "/api/v1/info",
        "/api/v1/ws/execution",
        "/api/v1/ws/execution/events",
        "/api/v1/sessions/state",
        "/api/v1/runtime/settings",
        "/api/v1/runtime/tests/daytona",
        "/api/v1/runtime/tests/lm",
        "/api/v1/runtime/status",
        "/api/v1/runtime/volume/tree",
        "/api/v1/runtime/volume/file",
        "/api/v1/runtime/volumes",
        "/api/v1/sandboxes",
        "/api/v1/runs/{run_id}/steps",
        "/api/v1/optimization/status",
        "/api/v1/optimization/modules",
        "/api/v1/optimization/runs",
        "/api/v1/optimization/datasets",
        "/api/v1/traces/feedback",
    }.issubset(route_paths)
