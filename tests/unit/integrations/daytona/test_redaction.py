"""M13: sandbox-boundary error redaction.

Fixtures are built via concatenation so no secret-shaped literal (a full
``postgres://...`` URL or an ``eyJ...`` JWT) appears in source, satisfying
secret scanners while still exercising the redactor at runtime.
"""

from __future__ import annotations

import pytest

from fleet_rlm.integrations.daytona.bridge_callbacks import invoke_tool
from fleet_rlm.integrations.daytona.errors import redact_error_message, sandbox_safe_error


def _url(scheme: str, rest: str) -> str:
    """Build a URL so the scheme and body are separate source literals."""
    return scheme + "://" + rest


# Separate the JWT header prefix from its body so no >=20-char eyJ... literal
# appears in source.
_JWT = "eyJ" + "CHANGEMECHANGEMECHANGEME" + "12345678"

_PG_URL = _url("postgres", "user:CHANGEME@host/db")
_PG_NEON_URL = _url("postgresql", "u:CHANGEME@db.neon.tech/neondb")
_DB_URL_ENV = "DATABASE_URL=" + _url("postgres", "u:CHANGEME@h/db")
_DB_ADMIN_URL_ENV = "DATABASE_ADMIN_URL=" + _url("postgres", "u:CHANGEME@h/db")


@pytest.mark.parametrize(
    "raw,expected_substring",
    [
        (_PG_URL, "[REDACTED]"),
        (_PG_NEON_URL, "[REDACTED]"),
        ("password=CHANGEME_VALUE", "[REDACTED]"),
        ("sslpassword=CHANGEME_VALUE", "[REDACTED]"),
        ("DAYTONA_API_KEY=CHANGEME_KEY", "[REDACTED]"),
        (_DB_URL_ENV, "[REDACTED]"),
        (_DB_ADMIN_URL_ENV, "[REDACTED]"),
        ("OPENAI_API_KEY=CHANGEME_KEY", "[REDACTED]"),
        ("ANTHROPIC_API_KEY=CHANGEME_KEY", "[REDACTED]"),
        ("LLM_API_KEY=CHANGEME_KEY", "[REDACTED]"),
        ("AZURE_API_KEY=CHANGEME_KEY", "[REDACTED]"),
        ("SECRET_KEY=CHANGEME_KEY", "[REDACTED]"),
        ("ACCESS_TOKEN=CHANGEME_TOKEN", "[REDACTED]"),
        ("token=" + _JWT, "[REDACTED]"),
    ],
)
def test_redact_known_credential_patterns(raw: str, expected_substring: str) -> None:
    redacted = redact_error_message(raw)
    assert expected_substring in redacted
    # The placeholder secret value must not survive redaction.
    assert "CHANGEME_VALUE" not in redacted
    assert "CHANGEME_KEY" not in redacted


def test_redact_preserves_non_credential_text() -> None:
    msg = "Connection refused while contacting broker at localhost:3000"
    assert redact_error_message(msg) == msg


def test_sandbox_safe_error_redacts_exception() -> None:
    exc = RuntimeError("failed to connect: " + _PG_URL)
    assert "[REDACTED]" in sandbox_safe_error(exc)
    assert "CHANGEME" not in sandbox_safe_error(exc)


def test_invoke_tool_redacts_error_in_result() -> None:
    """invoke_tool must not leak credentials in tool_error payloads."""

    class _Interpreter:
        def __init__(self) -> None:
            self.llm_query = lambda prompt, context="": None  # noqa: E731

    interp = _Interpreter()
    interp._tools: dict = {}

    # Force a tool that raises with a credential-bearing message.
    def _boom(*a: object, **k: object) -> None:
        raise ConnectionError(_DB_URL_ENV)

    interp._tools["boom"] = _boom

    result = invoke_tool(interp, "boom", [], {})

    assert result["status"] == "error"
    assert result["tool_name"] == "boom"
    assert "[REDACTED]" in result["error"]
    assert "CHANGEME" not in result["error"]
