"""Evidence bridge substrate tests covering VAL-DAYTONA-003, 009, 010, 011, 019, 020, 021.

VAL-DAYTONA-003: Sandbox code cannot read host credentials.
VAL-DAYTONA-009: Evidence store is host-mediated and credential-safe.
VAL-DAYTONA-010: Evidence fetch enforces run/repository scope.
VAL-DAYTONA-011: Evidence list is scoped and redacted.
VAL-DAYTONA-019: Evidence error paths are redacted.
VAL-DAYTONA-020: Evidence payload bounds and validation are enforced.
VAL-DAYTONA-021: Temporary VFS and evidence test writes are cleaned up.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fleet_rlm.integrations.daytona.isolation import (
    _EVIDENCE_MAX_CONTENT_BYTES,
    _EVIDENCE_MAX_KEY_BYTES,
    _EVIDENCE_MAX_TAG_BYTES,
    _EVIDENCE_MAX_TAGS,
    _redact_error_message,
    fetch_evidence,
    list_evidence,
    store_evidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(
    *,
    tenant_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=tenant_id or uuid.uuid4(),
        workspace_id=workspace_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
    )


def _make_interpreter(
    *,
    repository: Any = None,
    identity: Any = None,
    run_id: Any = None,
) -> MagicMock:
    interp = MagicMock()
    interp._host_repository = repository
    interp._host_identity = identity
    interp._host_run_id = run_id
    interp._tools = {}
    return interp


def _make_mock_item(
    *,
    item_id: uuid.UUID | None = None,
    scope_id: str = "test-key",
    content_text: str = "test content",
    kind_value: str = "context",
    importance: int = 5,
) -> MagicMock:
    item = MagicMock()
    item.id = item_id or uuid.uuid4()
    item.scope_id = scope_id
    item.content_text = content_text
    item.kind = MagicMock()
    item.kind.value = kind_value
    item.importance = importance
    return item


# ---------------------------------------------------------------------------
# VAL-DAYTONA-003: Sandbox cannot read host credentials
# ---------------------------------------------------------------------------


class TestSandboxCredentialIsolation:
    """Sandbox code must not receive host credentials through any evidence bridge path."""

    def test_store_evidence_does_not_expose_repository_handles(self) -> None:
        """VAL-DAYTONA-003: store_evidence return value contains no repository objects."""
        identity = _make_identity()
        mock_repo = MagicMock()
        mock_item = _make_mock_item()

        interp = _make_interpreter(repository=mock_repo, identity=identity, run_id=uuid.uuid4())

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", return_value=mock_item):
            result = store_evidence(interp, key="k", content="v")

        # Result must only contain JSON-safe primitives
        assert isinstance(result, dict)
        assert all(isinstance(v, (str, int, float, bool, type(None))) for v in result.values())
        assert "repository" not in str(result)
        assert "DATABASE_URL" not in str(result)

    def test_fetch_evidence_does_not_expose_repository_handles(self) -> None:
        """VAL-DAYTONA-003: fetch_evidence return value contains no repository objects."""
        identity = _make_identity()
        mock_repo = MagicMock()
        mock_item = _make_mock_item()

        interp = _make_interpreter(repository=mock_repo, identity=identity)

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", return_value=[mock_item]):
            result = fetch_evidence(interp, scope="run")

        result_str = str(result)
        assert "repository" not in result_str.lower()
        assert "DATABASE_URL" not in result_str
        for item in result.get("items", []):
            assert "repository" not in str(item)

    def test_list_evidence_response_contains_no_credentials(self) -> None:
        """VAL-DAYTONA-003: list_evidence response has no credential-bearing fields."""
        identity = _make_identity()
        mock_repo = MagicMock()
        mock_item = _make_mock_item()

        interp = _make_interpreter(repository=mock_repo, identity=identity)

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", return_value=[mock_item]):
            result = list_evidence(interp, scope="run")

        result_str = str(result)
        assert "DATABASE_URL" not in result_str
        assert "postgres://" not in result_str.lower()
        assert "api_key" not in result_str.lower()

    def test_skip_when_no_repository_returns_safe_response(self) -> None:
        """VAL-DAYTONA-003: No repository → skipped response, no credential leakage."""
        interp = _make_interpreter()

        store_result = store_evidence(interp, key="k", content="v")
        fetch_result = fetch_evidence(interp)
        list_result = list_evidence(interp)

        assert store_result["status"] == "skipped"
        assert fetch_result["status"] == "skipped"
        assert list_result["status"] == "skipped"

        # No credential-bearing content in any response
        for result in (store_result, fetch_result, list_result):
            result_str = str(result)
            assert "DATABASE_URL" not in result_str
            assert "postgres://" not in result_str.lower()


# ---------------------------------------------------------------------------
# VAL-DAYTONA-019: Evidence error paths are redacted
# ---------------------------------------------------------------------------


class TestEvidenceErrorRedaction:
    """Induced failures must return structured, redacted error messages."""

    def test_redact_error_message_removes_database_url(self) -> None:
        """VAL-DAYTONA-019: _redact_error_message strips postgres:// URLs."""
        raw = "Could not connect: postgresql://user:secret@neon.tech:5432/fleetdb"
        redacted = _redact_error_message(raw)
        assert "secret" not in redacted
        assert "neon.tech" not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_error_message_removes_jwt_tokens(self) -> None:
        """VAL-DAYTONA-019: _redact_error_message strips JWT-like token strings."""
        raw = "Authentication failed: token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        redacted = _redact_error_message(raw)
        assert "eyJ" not in redacted
        assert "[REDACTED]" in redacted

    def test_redact_error_message_removes_api_key_patterns(self) -> None:
        """VAL-DAYTONA-019: _redact_error_message removes API_KEY / SECRET_KEY references."""
        raw = "Invalid DAYTONA_API_KEY: sk_live_abcdef123456"
        redacted = _redact_error_message(raw)
        assert "[REDACTED]" in redacted

    def test_store_evidence_failure_returns_redacted_error(self) -> None:
        """VAL-DAYTONA-019: Repository failure in store_evidence is redacted."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        credential_url = "postgresql://admin:secret@db.neon.tech/prod"
        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=RuntimeError(f"Connection error: {credential_url}"),
        ):
            result = store_evidence(interp, key="k", content="v")

        assert result["status"] == "error"
        assert "secret" not in result["error"]
        assert "neon.tech" not in result["error"]
        assert "[REDACTED]" in result["error"]

    def test_fetch_evidence_failure_returns_redacted_error(self) -> None:
        """VAL-DAYTONA-019: Repository failure in fetch_evidence is redacted."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        credential_url = "postgresql://user:mypassword@host/db"
        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=RuntimeError(f"Failed: {credential_url}"),
        ):
            result = fetch_evidence(interp, scope="run")

        assert result["status"] == "error"
        assert "mypassword" not in result["error"]
        assert "[REDACTED]" in result["error"]

    def test_list_evidence_failure_returns_redacted_error(self) -> None:
        """VAL-DAYTONA-019: Repository failure in list_evidence is redacted."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=RuntimeError("host=secret-host.neon.tech password=topsecret"),
        ):
            result = list_evidence(interp, scope="run")

        assert result["status"] == "error"
        assert "topsecret" not in result["error"]
        assert "[REDACTED]" in result["error"]


# ---------------------------------------------------------------------------
# VAL-DAYTONA-020: Evidence payload bounds and validation
# ---------------------------------------------------------------------------


class TestEvidencePayloadBounds:
    """Evidence writes enforce documented limits for key, content, tags, and enums."""

    def test_store_evidence_rejects_oversized_key(self) -> None:
        """VAL-DAYTONA-020: Key exceeding _EVIDENCE_MAX_KEY_BYTES is rejected."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())
        oversized_key = "k" * (_EVIDENCE_MAX_KEY_BYTES + 1)

        result = store_evidence(interp, key=oversized_key, content="v")

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"
        assert "key" in result["error"].lower()

    def test_store_evidence_rejects_oversized_content(self) -> None:
        """VAL-DAYTONA-020: Content exceeding _EVIDENCE_MAX_CONTENT_BYTES is rejected."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())
        oversized_content = "x" * (_EVIDENCE_MAX_CONTENT_BYTES + 1)

        result = store_evidence(interp, key="k", content=oversized_content)

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"
        assert "content" in result["error"].lower()

    def test_store_evidence_rejects_too_many_tags(self) -> None:
        """VAL-DAYTONA-020: More tags than _EVIDENCE_MAX_TAGS is rejected."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())
        too_many_tags = [f"tag-{i}" for i in range(_EVIDENCE_MAX_TAGS + 1)]

        result = store_evidence(interp, key="k", content="v", tags=too_many_tags)

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"
        assert "tag" in result["error"].lower()

    def test_store_evidence_rejects_oversized_tag(self) -> None:
        """VAL-DAYTONA-020: A tag exceeding _EVIDENCE_MAX_TAG_BYTES is rejected."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())
        oversized_tag = "t" * (_EVIDENCE_MAX_TAG_BYTES + 1)

        result = store_evidence(interp, key="k", content="v", tags=[oversized_tag])

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"

    def test_store_evidence_rejects_invalid_scope(self) -> None:
        """VAL-DAYTONA-020: Unknown scope enum value is rejected with validation_error."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())

        result = store_evidence(interp, key="k", content="v", scope="INVALID_SCOPE")

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"
        assert "scope" in result["error"].lower()

    def test_store_evidence_rejects_invalid_kind(self) -> None:
        """VAL-DAYTONA-020: Unknown kind enum value is rejected with validation_error."""
        interp = _make_interpreter(repository=MagicMock(), identity=_make_identity())

        result = store_evidence(interp, key="k", content="v", kind="NOT_A_KIND")

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"
        assert "kind" in result["error"].lower()

    def test_store_evidence_accepts_valid_payload(self) -> None:
        """VAL-DAYTONA-020: A valid payload passes validation and reaches the repository."""
        identity = _make_identity()
        mock_repo = MagicMock()
        mock_item = _make_mock_item()
        interp = _make_interpreter(repository=mock_repo, identity=identity, run_id=uuid.uuid4())

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", return_value=mock_item):
            result = store_evidence(
                interp,
                key="valid-key",
                content="valid content",
                kind="context",
                scope="run",
                tags=["tag1", "tag2"],
            )

        assert result["status"] == "ok"
        assert "id" in result

    def test_fetch_evidence_rejects_invalid_scope(self) -> None:
        """VAL-DAYTONA-020: fetch_evidence with invalid scope returns validation error."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        result = fetch_evidence(interp, scope="NOT_VALID_SCOPE")

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"

    def test_list_evidence_rejects_invalid_scope(self) -> None:
        """VAL-DAYTONA-020: list_evidence with invalid scope returns validation error."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        result = list_evidence(interp, scope="GARBAGE")

        assert result["status"] == "error"
        assert result["reason"] == "validation_error"

    def test_store_evidence_validation_error_does_not_persist(self) -> None:
        """VAL-DAYTONA-020: Validation error means no partial persistence — repo not called."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        oversized_key = "k" * (_EVIDENCE_MAX_KEY_BYTES + 1)
        result = store_evidence(interp, key=oversized_key, content="v")

        assert result["status"] == "error"
        mock_repo.store_memory_item.assert_not_called()

    @pytest.mark.parametrize(
        "limit,expected_capped",
        [
            (1, 1),
            (50, 50),
            (1000, 500),  # exceeds _EVIDENCE_MAX_LIMIT
        ],
    )
    def test_fetch_evidence_caps_limit(self, limit: int, expected_capped: int) -> None:
        """VAL-DAYTONA-020: fetch_evidence limit is capped at _EVIDENCE_MAX_LIMIT."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)
        captured_limit: list[int] = []

        def _fake_compat(fn, *args, **kwargs):
            captured_limit.append(kwargs.get("limit", args[-1] if args else 0))
            return []

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", side_effect=_fake_compat):
            fetch_evidence(interp, scope="run", limit=limit)

        assert captured_limit[0] == expected_capped


# ---------------------------------------------------------------------------
# VAL-DAYTONA-010: Evidence scope enforcement
# ---------------------------------------------------------------------------


class TestEvidenceScopeEnforcement:
    """fetch_evidence and list_evidence scope requests to the authenticated identity."""

    def test_store_evidence_passes_identity_to_repository(self) -> None:
        """VAL-DAYTONA-010: store_evidence uses the interpreter's host identity."""
        identity = _make_identity()
        run_id = uuid.uuid4()
        mock_repo = MagicMock()
        mock_item = _make_mock_item()
        interp = _make_interpreter(repository=mock_repo, identity=identity, run_id=run_id)
        captured_request = []

        def _capture_compat(fn, request, *args, **kwargs):
            captured_request.append(request)
            return mock_item

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", side_effect=_capture_compat):
            store_evidence(interp, key="k", content="v")

        assert len(captured_request) == 1
        req = captured_request[0]
        assert req.tenant_id == identity.tenant_id
        assert req.workspace_id == identity.workspace_id
        assert req.user_id == identity.user_id
        assert req.run_id == run_id

    def test_fetch_evidence_passes_identity_to_repository(self) -> None:
        """VAL-DAYTONA-010: fetch_evidence uses the interpreter's host identity."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)
        captured_kwargs: list[dict] = []

        def _capture_compat(fn, *args, **kwargs):
            captured_kwargs.append(kwargs)
            return []

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", side_effect=_capture_compat):
            fetch_evidence(interp, scope="run", scope_id="my-key")

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert kw["tenant_id"] == identity.tenant_id
        assert kw["workspace_id"] == identity.workspace_id
        assert kw["user_id"] == identity.user_id
        assert kw["scope_id"] == "my-key"

    def test_fetch_evidence_returns_only_requested_scope(self) -> None:
        """VAL-DAYTONA-010: fetch_evidence items are the ones returned by the repository."""
        identity = _make_identity()
        run_id_a = uuid.uuid4()
        run_id_b = uuid.uuid4()
        interp_a = _make_interpreter(
            repository=MagicMock(),
            identity=identity,
            run_id=run_id_a,
        )
        interp_b = _make_interpreter(
            repository=MagicMock(),
            identity=identity,
            run_id=run_id_b,
        )

        item_a = _make_mock_item(scope_id="result-a")
        item_b = _make_mock_item(scope_id="result-b")

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            return_value=[item_a],
        ):
            result_a = fetch_evidence(interp_a, scope="run", scope_id="result-a")

        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            return_value=[item_b],
        ):
            result_b = fetch_evidence(interp_b, scope="run", scope_id="result-b")

        assert len(result_a["items"]) == 1
        assert result_a["items"][0]["scope_id"] == "result-a"
        assert len(result_b["items"]) == 1
        assert result_b["items"][0]["scope_id"] == "result-b"


# ---------------------------------------------------------------------------
# VAL-DAYTONA-011: Evidence list is scoped and redacted
# ---------------------------------------------------------------------------


class TestEvidenceListScopedAndRedacted:
    """list_evidence returns only metadata (no full content) for the authorized scope."""

    def test_list_evidence_returns_metadata_only(self) -> None:
        """VAL-DAYTONA-011: list_evidence items do not include content_text."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)
        mock_item = _make_mock_item(scope_id="evidence-key", content_text="secret content")

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", return_value=[mock_item]):
            result = list_evidence(interp, scope="run")

        assert result["status"] == "ok"
        assert len(result["items"]) == 1
        item = result["items"][0]
        # Content is NOT included in list responses
        assert "content" not in item
        assert "content_text" not in item
        # Only metadata fields
        assert "id" in item
        assert "scope_id" in item
        assert "kind" in item

    def test_list_evidence_uses_authenticated_identity(self) -> None:
        """VAL-DAYTONA-011: list_evidence passes the authenticated user_id to the repository."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)
        captured_kwargs: list[dict] = []

        def _capture(fn, *args, **kwargs):
            captured_kwargs.append(kwargs)
            return []

        with patch("fleet_rlm.integrations.daytona.isolation._run_async_compat", side_effect=_capture):
            list_evidence(interp, scope="run")

        assert captured_kwargs[0]["user_id"] == identity.user_id

    def test_list_evidence_does_not_expose_raw_database_errors(self) -> None:
        """VAL-DAYTONA-011: Raw database error messages are redacted in error responses."""
        identity = _make_identity()
        mock_repo = MagicMock()
        interp = _make_interpreter(repository=mock_repo, identity=identity)

        db_url = "postgresql://user:password@neon.tech/db"
        with patch(
            "fleet_rlm.integrations.daytona.isolation._run_async_compat",
            side_effect=RuntimeError(f"Query failed on {db_url}"),
        ):
            result = list_evidence(interp, scope="run")

        assert result["status"] == "error"
        assert "password" not in result["error"]
        assert "neon.tech" not in result["error"]
