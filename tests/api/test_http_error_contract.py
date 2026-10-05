"""Public HTTP error envelope contract.

Routing errors (unknown route, unsupported method) and explicitly raised domain
errors must share one closed ``{code, message}`` body, because generated clients
and the TUI parse that shape. Unexpected server faults are deliberately outside
that contract: they are Fleet defects, reported as a plain 500 so operators keep
the server-side traceback.
"""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from fleet_rlm.api.errors import http_error, install_error_handlers
from fleet_rlm.api.routes.files import _READ_ERRORS, _WRITE_ERRORS
from tests.support.testing_app import create_testing_app


def test_unknown_route_uses_the_closed_error_envelope() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Resource not found"}


def test_unsupported_method_uses_the_envelope_and_preserves_allow() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        response = client.delete("/api/sessions")

    assert response.status_code == 405
    assert response.json() == {"code": "request_failed", "message": "Request failed"}
    # Framework routing information stays authoritative under normalization.
    # FastAPI reports a single allowed method here rather than the full union;
    # this test only locks that the header survives the Fleet handler.
    assert response.headers["allow"].strip()


def test_explicit_domain_error_keeps_its_public_code() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        response = client.get(f"/api/sessions/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"code": "session_not_found", "message": "Session not found"}


def test_request_validation_error_uses_the_envelope() -> None:
    app = create_testing_app()

    with TestClient(app) as client:
        response = client.get("/api/sessions", params={"limit": 0})

    assert response.status_code == 422
    assert response.json() == {"code": "invalid_request", "message": "Invalid request"}


def test_handler_preserves_headers_set_by_the_raising_route() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/teapot")
    def teapot() -> None:
        raise http_error(418, "teapot", "I am a teapot", headers={"Retry-After": "30"})

    with TestClient(app) as client:
        response = client.get("/teapot")

    assert response.status_code == 418
    assert response.json() == {"code": "teapot", "message": "I am a teapot"}
    assert response.headers["retry-after"] == "30"


def test_unexpected_server_faults_stay_outside_the_closed_envelope() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/fault")
    def fault() -> None:
        raise RuntimeError("internal detail that must not be projected")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/fault")

    assert response.status_code == 500
    assert "internal detail" not in response.text
    assert "code" not in response.text


@pytest.mark.parametrize("status", [204, 205, 304])
def test_bodyless_statuses_keep_no_body_under_normalization(status: int) -> None:
    """HTTP forbids a body on these statuses, so they bypass the JSON envelope.

    Guards the framework's bodyless rule against a future normalizer change:
    FastAPI's default handler already applied it before Fleet overrode the
    handler for the base ``HTTPException`` class.
    """
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/bodyless")
    def bodyless() -> None:
        raise StarletteHTTPException(status_code=status)

    with TestClient(app) as client:
        response = client.get("/bodyless")

    assert response.status_code == status
    assert not response.content


# Operations that can return a public error, and the statuses they return.
# Kept explicit so a schema that is perfectly synchronized with the generator is
# also *complete*: every entry here is reachable from the route or its
# dependencies, and every entry must carry the shared envelope schema.
_DECLARED_ERRORS: tuple[tuple[str, str, frozenset[int]], ...] = (
    ("/api/artifacts/{artifact_id}", "get", frozenset({404, 503})),
    ("/api/artifacts/{artifact_id}/content", "get", frozenset({404, 503})),
    ("/api/attachments", "post", frozenset({400, 503})),
    ("/api/attachments/{attachment_id}", "get", frozenset({404, 503})),
    ("/api/files", "get", frozenset({400, 404, 503})),
    ("/api/files/stat", "get", frozenset({400, 404, 503})),
    ("/api/files/content", "get", frozenset({400, 404, 503})),
    ("/api/files/content", "put", frozenset({400, 404, 409, 503})),
    ("/api/files/append", "post", frozenset({400, 404, 409, 503})),
    ("/api/files/content", "delete", frozenset({400, 404, 409, 503})),
    ("/api/files/content", "patch", frozenset({400, 404, 409, 503})),
    ("/api/runs/{run_id}/cancellation", "put", frozenset({404, 503})),
    ("/api/sessions", "get", frozenset({503})),
    ("/api/sessions", "post", frozenset({503})),
    ("/api/sessions/{session_id}", "get", frozenset({404, 503})),
    ("/api/sessions/{session_id}", "patch", frozenset({404, 422, 503})),
    ("/api/sessions/{session_id}/turns", "get", frozenset({404, 503})),
    ("/api/sessions/{session_id}/turns", "post", frozenset({503})),
    ("/api/sessions/{session_id}/traces/feedback", "post", frozenset({404, 503})),
    ("/api/settings", "get", frozenset({503})),
    ("/api/settings", "patch", frozenset({409, 422, 503})),
    ("/api/skills", "get", frozenset({503})),
    ("/api/skills/{skill_id}", "get", frozenset({404, 503})),
    ("/api/volume/tree", "get", frozenset({400, 503})),
    ("/health/ready", "get", frozenset({503})),
)


def test_operations_declare_the_error_responses_they_can_raise() -> None:
    schema = create_testing_app().openapi()

    for path, method, expected in _DECLARED_ERRORS:
        responses = schema["paths"][path][method]["responses"]
        missing = {str(status) for status in expected} - set(responses)
        assert not missing, f"{method.upper()} {path} does not declare {sorted(missing)}"
        for status in sorted(expected):
            content = responses[str(status)]["content"]["application/json"]["schema"]
            assert content == {"$ref": "#/components/schemas/ErrorResponse"}, (method, path, status)


def test_shared_declared_responses_survive_schema_generation() -> None:
    """The shared response mappings must not be mutated by registration.

    ``_READ_ERRORS`` and ``_WRITE_ERRORS`` are passed by reference to seven path
    operations, so any mutation during OpenAPI generation would leak one route's
    declared statuses onto the others.
    """
    read_before = deepcopy(_READ_ERRORS)
    write_before = deepcopy(_WRITE_ERRORS)

    schema = create_testing_app().openapi()

    assert read_before == _READ_ERRORS
    assert write_before == _WRITE_ERRORS
    # A read-only operation must not inherit the write-only precondition status.
    assert "409" not in schema["paths"]["/api/files/stat"]["get"]["responses"]
    assert "409" in schema["paths"]["/api/files/content"]["put"]["responses"]
