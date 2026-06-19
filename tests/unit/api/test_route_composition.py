from __future__ import annotations

import importlib


def _join_route_path(prefix: str, path: str) -> str:
    joined = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    return joined if joined.startswith("/") else f"/{joined}"


def _route_paths(routes, *, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for route in routes:
        raw_path = getattr(route, "path", None)
        if raw_path:
            paths.add(_join_route_path(prefix, raw_path))
            continue
        original_router = getattr(route, "original_router", None)
        nested_routes = getattr(original_router, "routes", None)
        include_context = getattr(route, "include_context", None)
        include_prefix = str(getattr(include_context, "prefix", "") or "")
        if nested_routes is not None:
            paths.update(_route_paths(nested_routes, prefix=_join_route_path(prefix, include_prefix)))
    return paths


def test_build_api_router_includes_expected_route_tree():
    composition_module = importlib.import_module("fleet_rlm.api.routers._composition")

    router = composition_module.build_api_router()
    route_paths = _route_paths(router.routes)

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
