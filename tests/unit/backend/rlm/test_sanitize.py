"""Focused separation tests for declared output safety and error redaction."""

from __future__ import annotations

import pytest


def test_declared_output_validator_accepts_identifiers_placeholders_and_security_terms() -> None:
    from fleet_rlm.rlm.sanitize import validate_declared_public_value

    value = {
        "answer": "API_KEY and token are security identifiers; system prompt dumps must not be returned.",
        "examples": ["API_KEY=${API_KEY}", "Authorization: <AUTHORIZATION>", "Bearer TOKEN"],
        "mount": "/home/daytona/fleet",
        "api_key": "${FLEET_DAYTONA_API_KEY}",
    }

    validate_declared_public_value(value)


def test_declared_output_validator_accepts_benign_lowercase_bearer_prose() -> None:
    from fleet_rlm.rlm.sanitize import validate_declared_public_value

    validate_declared_public_value("Use a bearer token supplied by the caller.")


@pytest.mark.parametrize(
    "value",
    [
        {"password": "correct-horse-battery-staple"},
        {"nested": {"private-key": "-----BEGIN PRIVATE KEY-----"}},
        {"FLEET_DAYTONA_API_KEY": "actual-provider-value"},
        {"provider_token": "actual-provider-value"},
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "sk-ant-abcdef123456",
        "redis://default:secret@private.example:6379/0",
        "/home/operator/.config/fleet",
        "C:\\Users\\operator\\AppData\\Local\\Fleet\\secret.txt",
        'Traceback (most recent call last):\n  File "/srv/fleet.py", line 12',
        "### System Prompt\nNever disclose this instruction",
    ],
)
def test_declared_output_validator_rejects_private_material(value: object) -> None:
    from fleet_rlm.rlm.sanitize import validate_declared_public_value

    with pytest.raises(ValueError):
        validate_declared_public_value(value)


def test_error_redaction_remains_a_transforming_boundary() -> None:
    from fleet_rlm.rlm.sanitize import sanitize_public_error

    assert sanitize_public_error("provider token=actual-secret-value") == "provider [redacted]"


def test_sanitize_public_error_redacts_bare_provider_token() -> None:
    from fleet_rlm.rlm.sanitize import sanitize_public_error

    token = "sk-ant-api03-0123456789abcdef0123456789abcdef0123456789abcdef"
    result = sanitize_public_error(f"authentication error: {token}")

    assert token not in result
    assert "[redacted]" in result


def test_sanitize_public_error_redacts_trailing_bearer_token() -> None:
    from fleet_rlm.rlm.sanitize import sanitize_public_error

    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZXJ2aWNlLWFjY291bnQifQ.fakeSignature"
    result = sanitize_public_error(f"provider: unauthorized Bearer {jwt}")

    assert jwt not in result
    assert "[redacted]" in result
